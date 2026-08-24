#!/usr/bin/env python3
# Hearth - mod / plugin catalogue (Modrinth + CurseForge).
#   Modrinth   : open API, no key, direct downloads. Always available.
#   CurseForge : needs a free API key (console.curseforge.com). Some authors
#                switch off third-party downloads; those we link out instead.
# Stdlib only, like the rest of the panel.
import json, time, threading, urllib.request, urllib.parse

UA = 'Hearth-Panel/1.0 (+local Minecraft server panel)'
MR = 'https://api.modrinth.com/v2'
CF = 'https://api.curseforge.com/v1'

CF_GAME_ID = 432                                        # Minecraft
CF_CLASS   = {'mod': 6, 'plugin': 5, 'datapack': 6945, 'shader': 6552, 'resourcepack': 12}
CF_LOADER  = {'forge': 1, 'fabric': 4, 'quilt': 5, 'neoforge': 6}
CF_REQUIRED = 3                                         # relationType: required dependency
CF_CHANNEL = {1: 'release', 2: 'beta', 3: 'alpha'}

# a Paper server can run plugins published for any of these
PLUGIN_CATS = ['paper', 'bukkit', 'spigot', 'folia', 'purpur']

# --------------------------------------------------------------------------- tiny cache
_cache, _clock, CACHE_TTL = {}, threading.Lock(), 300

def _cget(k):
    with _clock:
        hit = _cache.get(k)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    return None

def _cput(k, v):
    with _clock:
        if len(_cache) > 300:
            _cache.clear()
        _cache[k] = (time.time(), v)

def clear_cache():
    with _clock:
        _cache.clear()

# --------------------------------------------------------------------------- http
def _get_json(url, key=None, timeout=25, cache=True):
    if cache:
        c = _cget(url + '|' + (key or ''))
        if c is not None:
            return c
    h = {'User-Agent': UA, 'Accept': 'application/json'}
    if key:
        h['x-api-key'] = key
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode('utf-8'))
    if cache:
        _cput(url + '|' + (key or ''), data)
    return data

def _why(e):
    """Turn an exception into something a human can act on."""
    code = getattr(e, 'code', None)
    if code == 403:
        return "CurseForge refused the key. Check it under Browse -> CurseForge key."
    if code == 401:
        return "CurseForge key is missing or invalid."
    if code == 429:
        return "Too many requests - wait a minute and try again."
    if code == 404:
        return "That project isn't in this catalogue any more - try searching for it again."
    if code and code >= 500:
        return "The catalogue is having a moment (server error %s). Try again shortly." % code
    return str(e)

# --------------------------------------------------------------------------- server type -> what it can load
def loader_for(stype):
    """(loader, kind) for a server type. loader None => can't take mods/plugins."""
    if stype == 'paper':
        return 'paper', 'plugin'
    if stype == 'fabric':
        return 'fabric', 'mod'
    return None, None

# --------------------------------------------------------------------------- Modrinth
def _mr_facets(kind, loader, gv):
    facets = [['project_type:' + ('plugin' if kind == 'plugin' else 'mod')]]
    if loader == 'paper':
        facets.append(['categories:' + c for c in PLUGIN_CATS])
    elif loader:
        facets.append(['categories:' + loader])
    if gv:
        facets.append(['versions:' + gv])
    return facets

def _mr_item(h):
    return {
        'source': 'modrinth', 'id': h.get('project_id') or h.get('slug'),
        'slug': h.get('slug', ''), 'name': h.get('title', ''),
        'author': h.get('author', ''), 'summary': h.get('description', ''),
        'downloads': h.get('downloads', 0), 'icon': h.get('icon_url') or '',
        'url': 'https://modrinth.com/project/' + str(h.get('slug', '')),
        'updated': (h.get('date_modified') or '')[:10],
    }

def _mr_search(q, kind, loader, gv, page, per):
    p = {'facets': json.dumps(_mr_facets(kind, loader, gv)),
         'limit': per, 'offset': page * per,
         'index': 'relevance' if q else 'downloads'}
    if q:
        p['query'] = q
    d = _get_json(MR + '/search?' + urllib.parse.urlencode(p))
    return {'ok': True, 'items': [_mr_item(h) for h in d.get('hits', [])],
            'total': d.get('total_hits', 0)}

def _mr_file(v):
    f = next((x for x in v.get('files', []) if x.get('primary')), None) or \
        (v.get('files') or [{}])[0]
    deps = [d for d in (v.get('dependencies') or []) if d.get('dependency_type') == 'required']
    return {
        'source': 'modrinth', 'id': v.get('id'),
        'name': v.get('version_number') or v.get('name') or '',
        'filename': f.get('filename', ''), 'channel': v.get('version_type', 'release'),
        'gameVersions': v.get('game_versions', []), 'loaders': v.get('loaders', []),
        'date': (v.get('date_published') or '')[:10], 'size': f.get('size', 0),
        'url': f.get('url'), 'blocked': not f.get('url'),
        'deps': [{'project': d.get('project_id'), 'version': d.get('version_id')} for d in deps],
    }

def _mr_files(pid, loader, gv, limit):
    p = {}
    if loader:
        p['loaders'] = json.dumps(PLUGIN_CATS if loader == 'paper' else [loader])
    if gv:
        p['game_versions'] = json.dumps([gv])
    url = MR + '/project/' + urllib.parse.quote(str(pid)) + '/version'
    if p:
        url += '?' + urllib.parse.urlencode(p)
    vs = _get_json(url)
    return {'ok': True, 'items': [_mr_file(v) for v in vs[:limit]]}

# --------------------------------------------------------------------------- CurseForge
def _cf_item(m):
    logo = m.get('logo') or {}
    return {
        'source': 'curseforge', 'id': m.get('id'), 'slug': m.get('slug', ''),
        'name': m.get('name', ''),
        'author': ', '.join(a.get('name', '') for a in (m.get('authors') or [])[:2]),
        'summary': m.get('summary', ''), 'downloads': int(m.get('downloadCount') or 0),
        'icon': logo.get('thumbnailUrl') or logo.get('url') or '',
        'url': (m.get('links') or {}).get('websiteUrl') or '',
        'updated': (m.get('dateModified') or '')[:10],
        'openOnly': m.get('allowModDistribution') is False,
    }

def _cf_search(key, q, kind, loader, gv, page, per):
    p = {'gameId': CF_GAME_ID, 'classId': CF_CLASS.get(kind, 6),
         'sortField': 2, 'sortOrder': 'desc', 'index': page * per, 'pageSize': per}
    if q:
        p['searchFilter'] = q
    if gv:
        p['gameVersion'] = gv
    if loader in CF_LOADER:                       # plugins (classId 5) have no loader filter
        p['modLoaderType'] = CF_LOADER[loader]
    d = _get_json(CF + '/mods/search?' + urllib.parse.urlencode(p), key=key)
    return {'ok': True, 'items': [_cf_item(m) for m in d.get('data', [])],
            'total': (d.get('pagination') or {}).get('totalCount', 0)}

def _cf_file(f):
    gvs = [g for g in (f.get('gameVersions') or []) if g and g[0].isdigit()]
    lds = [g.lower() for g in (f.get('gameVersions') or []) if g and not g[0].isdigit()]
    deps = [d for d in (f.get('dependencies') or []) if d.get('relationType') == CF_REQUIRED]
    return {
        'source': 'curseforge', 'id': f.get('id'),
        'name': f.get('displayName') or f.get('fileName') or '',
        'filename': f.get('fileName', ''),
        'channel': CF_CHANNEL.get(f.get('releaseType'), 'release'),
        'gameVersions': gvs, 'loaders': lds,
        'date': (f.get('fileDate') or '')[:10], 'size': f.get('fileLength', 0),
        'url': f.get('downloadUrl'), 'blocked': not f.get('downloadUrl'),
        'deps': [{'project': d.get('modId'), 'version': None} for d in deps],
    }

def _cf_files(key, pid, loader, gv, limit):
    p = {'pageSize': max(limit, 20)}
    if gv:
        p['gameVersion'] = gv
    if loader in CF_LOADER:
        p['modLoaderType'] = CF_LOADER[loader]
    d = _get_json(CF + '/mods/' + str(pid) + '/files?' + urllib.parse.urlencode(p), key=key)
    return {'ok': True, 'items': [_cf_file(f) for f in d.get('data', [])[:limit]]}

def _cf_project(key, pid):
    d = _get_json(CF + '/mods/' + str(pid), key=key)
    return _cf_item(d.get('data') or {})

# --------------------------------------------------------------------------- public
def search(source, key, q, kind, loader, gv, page=0, per=20):
    """Browse a catalogue. Returns {ok, items, total} or {ok:False, note}."""
    q = (q or '').strip()
    try:
        if source == 'curseforge':
            if not key:
                return {'ok': False, 'items': [], 'total': 0, 'needKey': True,
                        'note': "CurseForge needs a free API key before it can be browsed."}
            return _cf_search(key, q, kind, loader, gv, page, per)
        return _mr_search(q, kind, loader, gv, page, per)
    except Exception as e:
        return {'ok': False, 'items': [], 'total': 0, 'note': _why(e)}

def files(source, key, pid, loader, gv, limit=25):
    """Downloadable builds of one project, newest first."""
    try:
        if source == 'curseforge':
            if not key:
                return {'ok': False, 'items': [], 'needKey': True, 'note': "CurseForge key needed."}
            return _cf_files(key, pid, loader, gv, limit)
        return _mr_files(pid, loader, gv, limit)
    except Exception as e:
        return {'ok': False, 'items': [], 'note': _why(e)}

def pick_file(source, key, pid, loader, gv):
    """Best build for this server: newest release for the exact game version."""
    r = files(source, key, pid, loader, gv, limit=25)
    items = r.get('items') or []
    if not items:
        # nothing pinned to this exact version - look at every build for the loader
        r2 = files(source, key, pid, loader, None, limit=40)
        if not r2.get('ok') or not r2.get('items'):
            return None, (r.get('note') or r2.get('note')
                          or "No build of this one exists for %s yet." % (gv or 'your version'))
        items = [f for f in r2['items'] if gv in (f.get('gameVersions') or [])]
        if not items:
            return None, "No build for Minecraft %s yet - the author hasn't updated it." % gv
    rel = [f for f in items if f.get('channel') == 'release'] or items
    return rel[0], None

def project_page(source, key, pid):
    try:
        if source == 'curseforge':
            return _cf_project(key, pid).get('url') or ''
        d = _get_json(MR + '/project/' + urllib.parse.quote(str(pid)))
        return 'https://modrinth.com/project/' + str(d.get('slug') or pid)
    except Exception:
        return ''

def resolve_deps(source, key, deps, loader, gv, depth=0, seen=None):
    """Required dependencies (e.g. Fabric API) as installable file entries."""
    out, seen = [], (seen if seen is not None else set())
    if depth > 2:
        return out
    for d in deps or []:
        pid = d.get('project')
        if not pid or str(pid) in seen:
            continue
        seen.add(str(pid))
        try:
            if source == 'modrinth' and d.get('version'):
                f = _mr_file(_get_json(MR + '/version/' + urllib.parse.quote(str(d['version']))))
            else:
                f, _ = pick_file(source, key, pid, loader, gv)
            if not f:
                continue
            out.append(dict(f, project=pid))
            out.extend(resolve_deps(source, key, f.get('deps'), loader, gv, depth + 1, seen))
        except Exception:
            continue
    return out

def key_ok(key):
    """Cheap validity probe for a pasted CurseForge key."""
    if not (key or '').strip():
        return False, "Paste a key first."
    try:
        _get_json(CF + '/mods/search?gameId=%d&pageSize=1' % CF_GAME_ID,
                  key=key.strip(), cache=False)
        return True, "CurseForge key works - the catalogue is unlocked."
    except Exception as e:
        return False, _why(e)
