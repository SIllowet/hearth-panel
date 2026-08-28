#!/usr/bin/env python3
# Hearth - Minecraft Server Control Panel (local, localhost-only)
import json, os, subprocess, threading, time, glob, shutil, urllib.request, urllib.parse, hashlib, re, webbrowser, socket, datetime, zipfile, base64
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from ai import AI                # the console companion (optional)
except Exception as _e:              # the panel must still boot if it's broken
    AI = None
    print("AI module not loaded:", _e)

try:
    import modstore                  # mod / plugin catalogue (Modrinth + CurseForge)
except Exception as _e:              # the panel must still boot without it
    modstore = None
    print("modstore not loaded:", _e)

try:
    import hearth_setup              # first-run checks + self-update
except Exception as _e:              # the panel must still boot without it
    hearth_setup = None
    print("hearth_setup not loaded:", _e)

BASE = os.path.dirname(os.path.abspath(__file__))
# Normally right next to app.py. The override exists so the tests can run
# against a scratch folder instead of your real worlds.
CONFIG_PATH = os.environ.get('HEARTH_CONFIG') or os.path.join(BASE, 'config.json')
UI_DIR = os.path.join(BASE, 'ui')
HOST, PORT = '127.0.0.1', 8765
MANIFEST_URL = 'https://launchermeta.mojang.com/mc/game/version_manifest_v2.json'
PAPER_API = 'https://fill.papermc.io/v3/projects/paper'      # v2 was retired (410 Gone)
FABRIC_META = 'https://meta.fabricmc.net/v2'
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

# --------------------------------------------------------------------------- config
def default_config():
    # Written to config.json on first run. Add your own servers from the panel
    # (Worlds -> New world), or point it at an existing server folder.
    return {
        "serversRoot": os.path.join(os.path.expanduser("~"), "MinecraftServers"),
        "servers": [],
        "active": None,
        "memory": {"min": "2G", "max": "4G"},
        "bank": []
    }

_cfg_lock = threading.Lock()
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    c = default_config(); save_config(c); return c

def save_config(c):
    # Written to one side and moved into place, so a crash or a full disk
    # halfway through leaves the old config intact rather than a truncated file
    # the panel cannot read - it holds your worlds and your tunnel secret.
    with _cfg_lock:
        tmp = CONFIG_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(c, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)

CONFIG = load_config()

def get_server(name):
    for s in CONFIG['servers']:
        if s['name'] == name:
            return s
    return None

# --------------------------------------------------------------------------- processes
LOG_LINES = 2000          # what the console keeps in memory

class ManagedProc:
    def __init__(self):
        self.p = None
        self.log = deque(maxlen=LOG_LINES)
        self.ready = False
        self.seq = 0          # lines seen since launch, so the UI can ask for
                              # only what is new instead of the whole console
        # Who is online, kept up to date line by line. It used to be worked out
        # by re-reading the log on every poll, which meant that on a busy server
        # the "joined the game" lines scrolled off the end and players quietly
        # disappeared from the panel while they were still standing there.
        self.players = []
        # Held while launching. The panel serves requests on threads, so a
        # double-click on Start arrives as two requests at once - without this
        # both would look at a stopped world and both would launch a jar.
        self.lock = threading.Lock()
    def running(self):
        return self.p is not None and self.p.poll() is None

SERVERS = {}      # name -> ManagedProc (minecraft)
_procs_lock = threading.Lock()
TUNNEL = ManagedProc()

def proc_for(name):
    with _procs_lock:
        if name not in SERVERS:
            SERVERS[name] = ManagedProc()
        return SERVERS[name]

def find_java():
    cands = sorted(glob.glob(r"C:\Program Files\Microsoft\jdk-*"))
    for c in reversed(cands):
        exe = os.path.join(c, 'bin', 'java.exe')
        if os.path.exists(exe):
            return exe
    return 'java'

def _probe_port(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect(('127.0.0.1', port)); return True
    except Exception:
        return False
    finally:
        try: s.close()
        except Exception: pass

# The panel asks "is this port up?" for every world on every poll, and each
# miss costs the full timeout. A world does not come up or go down inside a
# second, so a very short memory here takes that off the poll entirely.
PORT_CACHE_TTL = 1.0
_port_cache = {}
_port_cache_lock = threading.Lock()

def port_listening(port, max_age=PORT_CACHE_TTL):
    try:
        port = int(port)
    except Exception:
        return False
    now = time.time()
    if max_age:
        with _port_cache_lock:
            hit = _port_cache.get(port)
        if hit and now - hit[0] < max_age:
            return hit[1]
    up = _probe_port(port)
    with _port_cache_lock:
        _port_cache[port] = (now, up)
    return up

def forget_port(port):
    """After we start or stop something, the remembered answer is stale."""
    try:
        port = int(port)
    except Exception:
        return
    with _port_cache_lock:
        _port_cache.pop(port, None)

def pid_on_port(port):
    try:
        out = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                             creationflags=NO_WINDOW).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if 'LISTENING' not in line:
            continue
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(':' + str(port)) and parts[-1].isdigit():
            return int(parts[-1])
    return None

JOIN_RE = re.compile(r']:\s+([A-Za-z0-9_]{2,16}) joined the game')
LEAVE_RE = re.compile(r']:\s+([A-Za-z0-9_]{2,16}) (?:left the game|lost connection)')

def track_players(players, line):
    """Fold one console line into the list of who is online. Returns the same
    list, edited in place - the server tells us about every join and leave, so
    following along beats re-deriving it from whatever log we still hold."""
    if 'Starting minecraft server' in line:
        del players[:]
        return players
    m = JOIN_RE.search(line)
    if m:
        if m.group(1) not in players:
            players.append(m.group(1))
        return players
    m = LEAVE_RE.search(line)
    if m and m.group(1) in players:
        players.remove(m.group(1))
    return players

def _reader(mp, stream, watcher=None):
    try:
        for line in iter(stream.readline, ''):
            if line == '':
                break
            line = line.rstrip('\n')
            mp.log.append(line)
            mp.seq += 1
            track_players(mp.players, line)
            if 'Done (' in line:
                mp.ready = True
            if watcher:
                try:
                    watcher(line)
                except Exception:
                    pass
    except Exception:
        pass

def start_server(name):
    s = get_server(name)
    if not s:
        return False, "Server not found."
    mp = proc_for(name)
    # Everything from "is it running?" to the jar actually being launched has to
    # happen as one step. A second request that squeezed in between would see a
    # stopped world and launch a second jar onto the same save - that is what
    # wiped a world before, and the port check below is too slow to catch it
    # (the first jar takes seconds to bind).
    with mp.lock:
        if mp.running():
            return True, "Already running."
        sport = read_properties(name).get('server-port')
        if sport and port_listening(sport, max_age=0):
            return False, "BLOCKED: port " + sport + " is already in use — a server is already running on this world. Refusing to launch a second one (that's what wiped the world before)."
        path = s['path']
        jar = os.path.join(path, 'server.jar')
        if not os.path.exists(jar):
            return False, "server.jar missing in " + path
        java = find_java()
        mem = CONFIG.get('memory') or {}
        args = [java, '-Xms' + str(mem.get('min', '2G')), '-Xmx' + str(mem.get('max', '4G')),
                '-XX:+UseG1GC', '-jar', 'server.jar', 'nogui']
        mp.log.clear(); mp.ready = False; mp.seq = 0; del mp.players[:]
        forget_port(sport)
        try:
            mp.p = subprocess.Popen(args, cwd=path, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1, creationflags=NO_WINDOW)
        except Exception as e:
            return False, "Failed to start: " + str(e)
    watcher = attach_ai(name, mp)
    threading.Thread(target=_reader, args=(mp, mp.p.stdout, watcher), daemon=True).start()
    return True, "Starting " + name + "..."

def attach_ai(name, mp):
    """Hand the companion this world's console: it reads every line, and talks and
    runs commands back through the same stdin pipe the panel uses. Only works for
    worlds started from the panel — an orphaned world has no pipe to attach to."""
    if not AI:
        return None
    try:
        AI.attach(
            name,
            lambda cmd: send_command(name, cmd),
            lambda line: mp.log.append(line),
            lambda: online_players(name),
        )
        return lambda line: AI.on_console_line(name, line)
    except Exception as e:
        print("AI attach failed:", e)
        return None

def send_command(name, cmd):
    mp = proc_for(name)
    if not mp.running():
        return False, "Server is not running."
    try:
        mp.p.stdin.write(cmd.strip() + '\n')
        mp.p.stdin.flush()
        return True, "Sent: " + cmd
    except Exception as e:
        return False, "Could not send command: " + str(e)

def stop_server(name):
    mp = proc_for(name)
    if not mp.running():
        # not launched in this panel session, but an orphan may still hold the port
        sport = read_properties(name).get('server-port')
        if sport and port_listening(sport, max_age=0):
            pid = pid_on_port(sport)
            if pid:
                try:
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)], creationflags=NO_WINDOW)
                    threading.Thread(target=lambda: (time.sleep(2), auto_backup(name, 'onstop')), daemon=True).start()
                    return True, "Stopped the server (it was left running from before the panel restarted)."
                except Exception as e:
                    return False, "Couldn't stop it: " + str(e)
        return True, "Already stopped."
    try:
        mp.p.stdin.write('stop\n'); mp.p.stdin.flush()
    except Exception:
        pass
    forget_port(read_properties(name).get('server-port'))
    def waiter():
        try:
            mp.p.wait(timeout=25)
        except Exception:
            try: mp.p.terminate()
            except Exception: pass
        auto_backup(name, 'onstop')
    threading.Thread(target=waiter, daemon=True).start()
    return True, "Stopping " + name + " (saving world + backing up)..."

def account_tunnel():
    for s in CONFIG['servers']:
        t = s.get('tunnel') or {}
        if t.get('secret'):
            return t
    return None

def set_tunnel_secret(secret, name=None):
    """
    Save a playit agent secret without anyone opening config.json in Notepad.
    Hand-editing that file - stop the panel, mind the double backslashes - was
    the single fiddliest step in setting Hearth up.
    """
    secret = (secret or '').strip()
    if len(secret) < 20 or not re.match(r'^[A-Za-z0-9+/=_-]+$', secret):
        return False, ("That does not look like a playit secret. It is a long "
                       "line of letters and numbers from your playit account.")
    exe = os.path.join(BASE, 'playit.exe')
    if not os.path.exists(exe):
        exe = (account_tunnel() or {}).get('exe', '')
    targets = [t for t in CONFIG.get('servers', []) if not name or t.get('name') == name]
    if not targets:
        return False, "Make a world first, then Hearth knows where to put this."
    for srv in targets:
        t = srv.get('tunnel') or {}
        t['secret'] = secret
        if exe:
            t['exe'] = exe
        srv['tunnel'] = t
    save_config(CONFIG)
    return True, "Saved. Light the hearth and the tunnel starts with it."

def playit_proc_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq playit.exe'],
                             capture_output=True, text=True, creationflags=NO_WINDOW).stdout or ''
        return 'playit.exe' in out.lower()
    except Exception:
        return False

def tunnel_alive():
    return TUNNEL.running() or playit_proc_running()

SECRET_FILE = os.path.join(BASE, '.playit-secret')

_playit_help = {}
def playit_help(exe):
    """What this build of the agent understands, asked once and remembered."""
    if exe in _playit_help:
        return _playit_help[exe]
    txt = ''
    try:
        p = subprocess.run([exe, '--help'], capture_output=True, text=True,
                           timeout=15, creationflags=NO_WINDOW)
        txt = ((p.stdout or '') + (p.stderr or '')).lower()
    except Exception:
        txt = ''
    _playit_help[exe] = txt
    return txt

def write_secret_file(secret):
    """Keep the secret in a file only this account can read. Created with the
    mode already set rather than tightened afterwards, so there is no moment
    where it sits there world-readable."""
    try:
        os.remove(SECRET_FILE)
    except OSError:
        pass
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(secret.strip() + '\n')
    return SECRET_FILE          # Windows ignores the mode; there the file
                                # sits in a per-user folder, which is what
                                # protects it

def playit_launch(exe, secret):
    """How to start the agent without putting the secret on the command line.

    Anything passed as an argument is visible to every other program on this
    PC - it shows up in Task Manager and in `tasklist` - and this one is the
    key to your tunnel. Which of these the agent accepts depends on its
    version, so we ask it rather than guess, and fall back to the old way
    rather than leave someone with a tunnel that will not start.
    """
    help_txt = playit_help(exe)
    env = dict(os.environ)
    if '--secret-path' in help_txt:
        return [exe, '--secret-path', write_secret_file(secret)], env, ''
    if 'playit_secret' in help_txt:
        env['PLAYIT_SECRET'] = secret.strip()
        return [exe], env, ''
    env['PLAYIT_SECRET'] = secret.strip()
    return ([exe, '--secret', secret], env,
            "Note: this build of playit only takes the secret on the command line, "
            "where other programs on this PC can see it. A newer playit avoids that.")

def start_tunnel(name=None):
    t = account_tunnel()
    if not t:
        return False, "No tunnel agent set up yet — worlds are LAN/localhost only."
    if tunnel_alive():
        return True, "Tunnel already running."
    exe = t.get('exe')
    if not exe or not os.path.exists(exe):
        return False, "playit.exe not found."
    TUNNEL.log.clear()
    args, env, note = playit_launch(exe, t['secret'])
    if note:
        TUNNEL.log.append(note)
    try:
        TUNNEL.p = subprocess.Popen(args,
                                    cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1, env=env,
                                    creationflags=NO_WINDOW)
    except Exception as e:
        return False, "Failed to start tunnel: " + str(e)
    threading.Thread(target=_reader, args=(TUNNEL, TUNNEL.p.stdout), daemon=True).start()
    return True, "Tunnel starting..."

def stop_tunnel():
    if not tunnel_alive():
        return True, "Tunnel already stopped."
    try:
        if TUNNEL.running():
            TUNNEL.p.terminate()
    except Exception:
        pass
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'playit.exe'], creationflags=NO_WINDOW)
    except Exception:
        pass
    return True, "Tunnel closed."

# --------------------------------------------------------------------------- properties
def read_properties(name):
    s = get_server(name)
    if not s:
        return {}
    path = os.path.join(s['path'], 'server.properties')
    props = {}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\n')
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                props[k.strip()] = v
    return props

def write_properties(name, updates):
    s = get_server(name)
    if not s:
        return False, "Server not found."
    path = os.path.join(s['path'], 'server.properties')
    props = read_properties(name)
    for k, v in updates.items():
        # One setting per line, always. A newline pasted into a free-text field
        # like the MOTD would otherwise land as further settings of its own.
        k = str(k).strip()
        if not k or not re.match(r'^[A-Za-z0-9._\-]+$', k):
            continue
        props[k] = re.sub(r'[\r\n]+', ' ', str(v))
    lines = ["#Minecraft server properties", "#Edited by Hearth Control Panel"]
    for k in sorted(props.keys()):
        lines.append(k + '=' + str(props[k]))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return True, "Settings saved. Restart the server to apply."

# --------------------------------------------------------------------------- versions / create
_versions_cache = {"t": 0, "data": None}
def get_versions():
    if _versions_cache["data"] and time.time() - _versions_cache["t"] < 600:
        return _versions_cache["data"]
    try:
        with urllib.request.urlopen(MANIFEST_URL, timeout=20) as r:
            m = json.loads(r.read().decode('utf-8'))
        rels = [v for v in m['versions'] if v['type'] == 'release']
        data = {"latest": m['latest']['release'],
                "versions": [v['id'] for v in rels[:40]],
                "_meta": {v['id']: v['url'] for v in rels[:40]}}
        _versions_cache.update(t=time.time(), data=data)
        return data
    except Exception as e:
        return {"latest": None, "versions": [], "_meta": {}, "error": str(e)}

UA = 'Hearth-Panel/1.0 (+local Minecraft server panel)'

def _digest(path, algo='sha1'):
    h = hashlib.new(algo)
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def _download(url, dest, algo=None, want=None, expect_jar=False):
    """Fetch to a .part file, check it, and only then put it in place. Writing
    straight to dest leaves a half-file or a wrong file sitting there looking
    like the real thing when anything goes wrong partway.

    algo/want: verify the checksum the publisher gave us, when they give one.
    expect_jar: at minimum make sure we were handed a jar and not an error page.
    """
    if not url.lower().startswith('https://'):
        raise ValueError("refusing to download over an insecure connection")
    tmp = dest + '.part'
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            if not r.geturl().lower().startswith('https://'):
                raise ValueError("that download redirected to an insecure connection")
            with open(tmp, 'wb') as f:
                shutil.copyfileobj(r, f)
        if want:
            got = _digest(tmp, algo or 'sha1')
            if got.lower() != str(want).lower():
                raise ValueError("checksum did not match - the file that arrived is not the one that was published")
        if expect_jar:
            with open(tmp, 'rb') as f:
                if f.read(2) != b'PK':      # every jar is a zip
                    raise ValueError("what arrived is not a jar (probably an error page)")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass

def _api_json(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def fetch_vanilla_jar(version, dest):
    v = get_versions()
    url = v.get("_meta", {}).get(version)
    if not url:
        with urllib.request.urlopen(MANIFEST_URL, timeout=20) as r:
            m = json.loads(r.read().decode('utf-8'))
        e = next((x for x in m['versions'] if x['id'] == version), None)
        if not e:
            return False, "Version not found."
        url = e['url']
    with urllib.request.urlopen(url, timeout=20) as r:
        meta = json.loads(r.read().decode('utf-8'))
    sv = meta.get('downloads', {}).get('server')
    if not sv:
        return False, "No server jar for that version."
    try:
        _download(sv['url'], dest, 'sha1', sv.get('sha1'), expect_jar=True)
    except Exception as e:
        return False, "That download did not arrive intact (%s). Nothing was saved." % str(e)[:90]
    return True, "ok"

_paper_cache = {"t": 0, "data": None}
def paper_versions():
    """Every Minecraft version Paper builds for, newest first."""
    if _paper_cache["data"] and time.time() - _paper_cache["t"] < 900:
        return _paper_cache["data"]
    try:
        d = _api_json(PAPER_API)
        out = []
        for fam in (d.get('versions') or {}).values():   # dict is newest-family-first
            out.extend(fam)
        _paper_cache.update(t=time.time(), data=out)
        return out
    except Exception:
        return _paper_cache["data"] or []

def fetch_paper_jar(version, dest):
    try:
        b = _api_json(PAPER_API + '/versions/' + urllib.parse.quote(version) + '/builds/latest')
        dl = (b.get('downloads') or {}).get('server:default') or {}
        if not dl.get('url'):
            return False, "Paper has no server build for " + version + " yet."
        want = (dl.get('checksums') or {}).get('sha256')
        _download(dl['url'], dest, 'sha256', want, expect_jar=True)
        return True, "ok"
    except Exception as e:
        return False, "Paper isn't available for " + version + " (" + str(e) + ")"

_fabric_cache = {}
def fabric_supports(version):
    """Does Fabric have a loader for this Minecraft version?"""
    if version in _fabric_cache:
        return _fabric_cache[version]
    try:
        ok = bool(_api_json(FABRIC_META + '/versions/loader/' + urllib.parse.quote(version)))
    except Exception:
        ok = False
    _fabric_cache[version] = ok
    return ok

def fetch_fabric_jar(version, dest):
    """Fabric's server launcher. It pulls the real MC server + libraries on first start."""
    try:
        loaders = _api_json(FABRIC_META + '/versions/loader/' + urllib.parse.quote(version))
        if not loaders:
            return False, "Fabric doesn't support Minecraft " + version + " yet."
        pick = next((l for l in loaders if (l.get('loader') or {}).get('stable')), loaders[0])
        lv = pick['loader']['version']
        inst = _api_json(FABRIC_META + '/versions/installer')
        iv = next((i['version'] for i in inst if i.get('stable')), inst[0]['version'])
        # Fabric publishes no checksum for the launcher it builds on request,
        # so the jar check is what we have: it at least tells an error page
        # from a program, and the download is verified TLS either way.
        _download('%s/versions/loader/%s/%s/%s/server/jar'
                  % (FABRIC_META, urllib.parse.quote(version), lv, iv), dest,
                  expect_jar=True)
        return True, "ok"
    except Exception as e:
        return False, "Fabric isn't available for " + version + " (" + str(e) + ")"

def fetch_jar_for(stype, version, dest):
    if stype == 'paper':
        return fetch_paper_jar(version, dest)
    if stype == 'fabric':
        return fetch_fabric_jar(version, dest)
    return fetch_vanilla_jar(version, dest)

# --------------------------------------------------------------------------- version / java checks
_vmeta_cache = {}
def version_meta(version):
    if version in _vmeta_cache:
        return _vmeta_cache[version]
    url = get_versions().get('_meta', {}).get(version)
    if not url:
        m = _api_json(MANIFEST_URL)
        e = next((x for x in m['versions'] if x['id'] == version), None)
        if not e:
            raise ValueError("Unknown Minecraft version: " + str(version))
        url = e['url']
    d = _api_json(url)
    _vmeta_cache[version] = d
    return d

def required_java(version):
    """Java major version Mojang says this build needs (0 = unknown)."""
    try:
        return int((version_meta(version).get('javaVersion') or {}).get('majorVersion') or 0)
    except Exception:
        return 0

_java_ver_cache = {}
def java_major(exe=None):
    """Java major version actually installed on this PC (0 = couldn't tell)."""
    exe = exe or find_java()
    if exe in _java_ver_cache:
        return _java_ver_cache[exe]
    n = 0
    try:
        p = subprocess.run([exe, '-version'], capture_output=True, text=True,
                           timeout=20, creationflags=NO_WINDOW)
        m = re.search(r'version "(\d+)', (p.stderr or '') + (p.stdout or ''))
        if m:
            n = int(m.group(1))
    except Exception:
        pass
    _java_ver_cache[exe] = n
    return n

def world_busy(name):
    """Awake either way: started from the panel, or already listening on its port."""
    if proc_for(name).running():
        return True
    sport = read_properties(name).get('server-port')
    return bool(sport and sport != '?' and port_listening(sport, max_age=0))

def server_version(s):
    """Best guess at which Minecraft version a server folder is on."""
    v = s.get('version')
    if not v:
        vt = os.path.join(s['path'], 'current-version.txt')
        if os.path.exists(vt):
            try:
                v = open(vt, encoding='utf-8').read().strip()
            except Exception:
                v = None
    return v or get_versions().get('latest')

def next_free_port():
    used = set()
    for s in CONFIG['servers']:
        p = read_properties(s['name']).get('server-port')
        if p and p.isdigit():
            used.add(int(p))
    port = 25565
    while port in used:
        port += 1
    return port

# --------------------------------------------------------------------------- tunnel bank
def bank_slots():
    return CONFIG.get('bank', [])

def used_ports():
    return {str(read_properties(s['name']).get('server-port', '')) for s in CONFIG['servers']}

def free_bank_slot():
    used = used_ports()
    for slot in bank_slots():
        if str(slot.get('port')) not in used:
            return slot
    return None

def set_bank(text):
    slots = []
    for line in (text or '').splitlines():
        parts = [p for p in re.split(r'[\s,]+', line.strip()) if p]
        if len(parts) >= 2 and parts[0].isdigit():
            slots.append({"port": int(parts[0]), "address": parts[1]})
    CONFIG['bank'] = slots
    save_config(CONFIG)
    return True, "Saved %d tunnels to the bank." % len(slots)

def bank_status():
    used_by = {}
    for s in CONFIG['servers']:
        used_by[str(read_properties(s['name']).get('server-port', ''))] = s['name']
    out = [{"port": slot.get('port'), "address": slot.get('address', ''),
            "usedBy": used_by.get(str(slot.get('port')))} for slot in bank_slots()]
    return {"slots": out, "total": len(out), "free": sum(1 for x in out if not x['usedBy'])}

def create_server(name, version, stype, seed=''):
    name = re.sub(r'[^A-Za-z0-9_\-]', '', name).strip()
    if not name:
        return False, "Please use a simple name (letters, numbers, - or _)."
    if get_server(name):
        return False, "A server named '" + name + "' already exists."
    root = CONFIG.get('serversRoot') or os.path.join(os.path.expanduser("~"), "MinecraftServers")
    path = os.path.join(root, name)
    if os.path.exists(path):
        return False, "Folder already exists: " + path
    os.makedirs(path, exist_ok=True)
    jar = os.path.join(path, 'server.jar')
    ok, msg = fetch_jar_for(stype, version, jar)
    if not ok:
        shutil.rmtree(path, ignore_errors=True)
        return False, msg
    if bank_slots():
        slot = free_bank_slot()
        if not slot:
            shutil.rmtree(path, ignore_errors=True)
            return False, "Tunnel bank is full (%d worlds max). Delete a world to free a tunnel, or add more on playit.gg." % len(bank_slots())
        port = int(slot['port'])
        slot_address = slot.get('address', '')
    else:
        port = next_free_port()
        slot_address = ''
    with open(os.path.join(path, 'eula.txt'), 'w') as f:
        f.write("eula=true\n")
    base_props = {
        "gamemode": "survival", "difficulty": "normal", "level-name": "world",
        "motd": name, "max-players": "20", "online-mode": "true", "pvp": "true",
        "server-port": str(port), "view-distance": "10", "simulation-distance": "8",
        "spawn-protection": "0", "white-list": "false", "enforce-secure-profile": "false",
        "level-seed": (seed or '').strip()
    }
    lines = ["#Minecraft server properties", "#Created by Hearth Control Panel"]
    for k in sorted(base_props):
        lines.append(k + '=' + base_props[k])
    with open(os.path.join(path, 'server.properties'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    if stype == 'paper':
        os.makedirs(os.path.join(path, 'plugins'), exist_ok=True)
    if stype == 'fabric':
        os.makedirs(os.path.join(path, 'mods'), exist_ok=True)
    with open(os.path.join(path, 'current-version.txt'), 'w', encoding='utf-8') as f:
        f.write(version + "\n")
    entry = {"name": name, "path": path, "type": stype, "version": version, "tunnel": {}}
    if slot_address:
        entry["tunnel"]["address"] = slot_address
    CONFIG['servers'].append(entry)
    save_config(CONFIG)
    msg = "Created '%s' on port %d." % (name, port)
    if slot_address:
        msg = "Created '%s' — auto-assigned tunnel %s (port %d)." % (name, slot_address, port)
    return True, msg

def delete_server(name):
    s = get_server(name)
    if not s:
        return False, "Not found."
    if proc_for(name).running():
        return False, "Stop the server before deleting it."
    if name in (CONFIG.get('protected') or []):
        return False, "'" + name + "' is protected from deletion (listed in config.json -> protected)."
    CONFIG['servers'] = [x for x in CONFIG['servers'] if x['name'] != name]
    if CONFIG.get('active') == name:
        # Move to whatever is left, or nothing at all - never leave the panel
        # pointing at the world we just removed.
        CONFIG['active'] = CONFIG['servers'][0]['name'] if CONFIG['servers'] else None
    save_config(CONFIG)
    return True, "Removed '" + name + "' from the panel (files left on disk for safety)."

# --------------------------------------------------------------------------- mods / plugins
def mods_dir(s):
    if s['type'] == 'paper':
        return os.path.join(s['path'], 'plugins'), 'plugin'
    if s['type'] == 'fabric':
        return os.path.join(s['path'], 'mods'), 'mod'
    return None, None

def list_mods(name):
    s = get_server(name)
    if not s:
        return {"supported": False, "items": [], "note": "Server not found."}
    d, kind = mods_dir(s)
    base = {"version": server_version(s), "type": s['type'] if s else '',
            "hasKey": bool((CONFIG.get('curseforgeKey') or '').strip())}
    if not d:
        return dict(base, supported=False, items=[], note=(
            "This world runs Vanilla, which can't load anything. Switch it to Paper for "
            "server-side plugins, or Fabric for real mods - the Version tab does it in one click "
            "and your world is left alone."))
    os.makedirs(d, exist_ok=True)
    items = []
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith('.jar'):
            continue
        p = os.path.join(d, f)
        items.append({"file": f, "size": os.path.getsize(p),
                      "added": int(os.path.getmtime(p) * 1000)})
    return dict(base, supported=True, kind=kind, items=items)

def add_mod(name, url):
    s = get_server(name)
    if not s:
        return False, "Server not found."
    d, kind = mods_dir(s)
    if not d:
        return False, "This server type can't use mods/plugins. Convert to Paper first."
    os.makedirs(d, exist_ok=True)
    url = url.strip()
    try:
        if url.lower().startswith('http'):
            if not url.lower().startswith('https://'):
                return False, ("That address is not https, so Hearth won't fetch a program from it. "
                               "Download the jar yourself and use 'Install from file'.")
            fn = safe_jar_name(url)
            _download(url, os.path.join(d, fn), expect_jar=True)
            return True, "Added " + fn
        else:
            if not os.path.exists(url):
                return False, "File not found: " + url
            fn = safe_jar_name(url)
            shutil.copy2(url, os.path.join(d, fn))
            return True, "Added " + fn
    except Exception as e:
        return False, "Failed: " + str(e)

def remove_mod(name, fn):
    s = get_server(name)
    d, kind = mods_dir(s) if s else (None, None)
    if not d:
        return False, "Not applicable."
    fn = os.path.basename(str(fn or '').replace('\\', '/').split('/')[-1])
    if not fn or fn in ('.', '..'):
        return False, "Not found."
    target = os.path.join(d, fn)
    if os.path.abspath(os.path.dirname(target)) != os.path.abspath(d):
        return False, "Not found."
    if os.path.exists(target):
        os.remove(target)
        return True, "Removed " + fn
    return False, "Not found."

# --------------------------------------------------------------------------- browse & install from a catalogue
def cf_key():
    return (CONFIG.get('curseforgeKey') or '').strip() or None

def set_cf_key(key):
    key = (key or '').strip()
    if not key:
        CONFIG.pop('curseforgeKey', None)
        save_config(CONFIG)
        if modstore: modstore.clear_cache()
        return True, "CurseForge key removed. Modrinth still works as normal."
    if not modstore:
        return False, "The catalogue module isn't loaded."
    ok, msg = modstore.key_ok(key)
    if ok:
        CONFIG['curseforgeKey'] = key
        save_config(CONFIG)
        modstore.clear_cache()
    return ok, msg

def _target_for(name):
    """(server, mods-dir, kind, loader, game-version) or an error string."""
    s = get_server(name)
    if not s:
        return None, "Server not found."
    d, kind = mods_dir(s)
    if not d:
        return None, ("This world runs Vanilla. Switch it to Paper (plugins) or Fabric (mods) "
                      "in the Version tab first.")
    loader, _k = modstore.loader_for(s['type'])
    return (s, d, kind, loader, server_version(s)), None

def browse_mods(name, source, q, page=0):
    if not modstore:
        return {"ok": False, "items": [], "note": "The catalogue module isn't loaded."}
    s = get_server(name)
    if not s:
        return {"ok": False, "items": [], "note": "Server not found."}
    loader, kind = modstore.loader_for(s['type'])
    gv = server_version(s)
    if not loader:
        return {"ok": False, "items": [], "vanilla": True, "note": (
            "Vanilla can't load anything. Switch this world to Paper (plugins) or Fabric (mods) "
            "in the Version tab, then come back.")}
    r = modstore.search(source, cf_key(), q, kind, loader, gv, int(page or 0), 20)
    r.update(kind=kind, loader=loader, version=gv, source=source,
             hasKey=bool(cf_key()), page=int(page or 0))
    return r

def mod_files(name, source, project):
    if not modstore:
        return {"ok": False, "items": [], "note": "The catalogue module isn't loaded."}
    t, err = _target_for(name)
    if err:
        return {"ok": False, "items": [], "note": err}
    s, d, kind, loader, gv = t
    r = modstore.files(source, cf_key(), project, loader, None, limit=40)
    items = r.get('items') or []
    for f in items:
        f['fits'] = gv in (f.get('gameVersions') or [])
    # builds that actually run on this world first, newest order kept inside each group
    r['items'] = [f for f in items if f['fits']] + [f for f in items if not f['fits']]
    r.update(version=gv, source=source)
    return r

_VER_CUT = re.compile(r'[-_+]v?\d')
def _stem(fn):
    """'fabric-api-0.158.0+26.2.jar' -> 'fabric-api'  (so an upgrade replaces, not duplicates)."""
    base = os.path.basename(fn).lower()
    if base.endswith('.jar'):
        base = base[:-4]
    m = _VER_CUT.search(base)
    return (base[:m.start()] if m and m.start() > 2 else base).strip('-_+.')

def safe_jar_name(filename, fallback='mod.jar'):
    """A filename off the internet is a suggestion, not an instruction. Take
    the last path segment - by both separators, because a backslash survives a
    split on '/' and lands us outside the mods folder on Windows - and keep only
    characters that mean nothing to a file system."""
    fn = str(filename or '').replace('\\', '/').split('/')[-1].split('?')[0].split('#')[0]
    fn = os.path.basename(fn)
    fn = re.sub(r'[^A-Za-z0-9._+\- ]', '_', fn).strip(' .')
    if not fn or set(fn) <= {'.', '_', ' '}:
        fn = fallback
    if not fn.lower().endswith('.jar'):
        fn += '.jar'
    return fn

def _put_jar(d, filename, url, sha1=None):
    """Download one jar, replacing any older build of the same project.

    The download happens first and the old build is only cleared away once the
    new one is safely on disk - an upgrade that fails its checksum should leave
    you with the mod you already had, not with neither.
    """
    fn = safe_jar_name(filename)
    # The catalogue tells us the checksum of the build it just described. If
    # what arrives is something else, it does not go into the mods folder.
    _download(url, os.path.join(d, fn), 'sha1', sha1 or None, expect_jar=True)
    stem, replaced = _stem(fn), None
    for old in os.listdir(d):
        if old.lower().endswith('.jar') and old != fn and _stem(old) == stem:
            try:
                os.remove(os.path.join(d, old)); replaced = old
            except Exception:
                pass
    return fn, replaced

def install_mod(name, source, project, file_id=None, deps=True):
    """Install one project (plus its required dependencies) into a world."""
    if not modstore:
        return False, "The catalogue module isn't loaded."
    t, err = _target_for(name)
    if err:
        return False, err
    s, d, kind, loader, gv = t
    key = cf_key()
    try:
        if file_id:
            r = modstore.files(source, key, project, loader, None, limit=60)
            f = next((x for x in (r.get('items') or []) if str(x['id']) == str(file_id)), None)
            if not f:
                return False, r.get('note') or "That build isn't listed any more."
        else:
            f, err = modstore.pick_file(source, key, project, loader, gv)
            if not f:
                return False, err
        wanted = [f] + (modstore.resolve_deps(source, key, f.get('deps'), loader, gv) if deps else [])
        os.makedirs(d, exist_ok=True)
        got, blocked, swapped = [], [], []
        for w in wanted:
            if w.get('blocked') or not w.get('url'):
                blocked.append(w)
                continue
            fn, replaced = _put_jar(d, w.get('filename') or w.get('name'), w['url'], w.get('sha1'))
            got.append(fn)
            if replaced:
                swapped.append(replaced)
    except Exception as e:
        return False, "Install failed: " + str(e)

    if not got and blocked:
        page = modstore.project_page(source, key, project)
        return False, ("This author only allows downloads from the CurseForge site itself. Open it"
                       + ((" - " + page) if page else "")
                       + ", download the jar, then drop it in with 'Install from file'.")
    msg = "Installed " + got[0]
    extra = len(got) - 1
    if extra:
        msg += " + %d required %s%s (%s)" % (extra, kind, 's' if extra > 1 else '', ", ".join(got[1:]))
    if f.get('channel') in ('beta', 'alpha'):
        msg += (". Heads up: that's a %s build - it's the newest one for Minecraft %s, but expect rough edges"
                % (f['channel'], gv))
    if swapped:
        msg += ". Replaced older " + ", ".join(swapped)
    if blocked:
        msg += ". %d dependency(ies) must be downloaded from CurseForge by hand." % len(blocked)
    return True, msg + ". Restart the world to load it."

MAX_UPLOAD = 200 * 1024 * 1024

def upload_mod(name, filename, data):
    """Install a jar the user downloaded themselves (drag-in / file picker)."""
    t, err = _target_for(name)
    if err:
        return False, err
    s, d, kind, loader, gv = t
    if not str(filename or '').lower().endswith('.jar'):
        return False, "That isn't a .jar file - mods and plugins are always .jar."
    fn = safe_jar_name(filename)
    raw = data or ''
    if ',' in raw[:100] and raw.lstrip().startswith('data:'):
        raw = raw.split(',', 1)[1]
    try:
        blob = base64.b64decode(raw, validate=False)
    except Exception:
        return False, "Couldn't read that file."
    if not blob:
        return False, "That file is empty."
    if len(blob) > MAX_UPLOAD:
        return False, "That file is bigger than 200 MB - install it by hand instead."
    if blob[:2] != b'PK':
        return False, "That doesn't look like a real jar (a jar is a zip - this isn't)."
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fn), 'wb') as f:
        f.write(blob)
    return True, "Installed " + fn + ". Restart the world to load it."

LOADER_LABEL = {'vanilla': 'Vanilla', 'paper': 'Paper', 'fabric': 'Fabric'}

def switch_loader(name, target):
    """Swap a world between Vanilla / Paper (plugins) / Fabric (mods). World is untouched."""
    s = get_server(name)
    if not s:
        return False, "Not found."
    if target not in LOADER_LABEL:
        return False, "Unknown server type."
    if world_busy(name):
        return False, "Stop this world first, then switch it."
    if s['type'] == target:
        return False, "This world already runs " + LOADER_LABEL[target] + "."
    version = server_version(s)
    if not version:
        return False, "Could not work out which Minecraft version this world is on."
    jar = os.path.join(s['path'], 'server.jar')
    tmp = jar + '.new'
    ok, msg = fetch_jar_for(target, version, tmp)
    if not ok:
        try: os.remove(tmp)
        except Exception: pass
        return False, msg
    if os.path.exists(jar):
        shutil.copy2(jar, jar + '.' + s['type'] + '-backup')
    os.replace(tmp, jar)
    os.makedirs(os.path.join(s['path'], 'plugins' if target == 'paper' else 'mods'), exist_ok=True)
    old = s['type']
    s['type'] = target
    s['version'] = version
    save_config(CONFIG)
    tail = {'paper': " You can install plugins now (Mods tab).",
            'fabric': " You can install mods now (Mods tab) - everyone joining needs the same mods in their own client.",
            'vanilla': " Mods and plugins won't load any more."}[target]
    return True, ("Switched '%s' from %s to %s %s.%s"
                  % (name, LOADER_LABEL[old], LOADER_LABEL[target], version, tail))

def convert_to_paper(name):
    return switch_loader(name, 'paper')

# --------------------------------------------------------------------------- minecraft version updates
def version_info(name):
    """Everything the Version tab needs: where this world is, where it could go."""
    s = get_server(name)
    if not s:
        return {"ok": False, "note": "Server not found."}
    cur = server_version(s)
    vs = get_versions()
    avail = list(vs.get('versions') or [])
    stype = s['type']
    if stype == 'paper':
        pv = set(paper_versions())
        avail = [v for v in avail if v in pv]
    have = java_major()
    d, kind = mods_dir(s)
    n_mods = len([f for f in os.listdir(d) if f.lower().endswith('.jar')]) if d and os.path.isdir(d) else 0
    return {
        "ok": True, "type": stype, "typeLabel": LOADER_LABEL.get(stype, stype),
        "current": cur, "latest": vs.get('latest'),
        "behind": bool(cur and vs.get('latest') and cur != vs['latest']),
        "versions": avail[:25],
        "prev": s.get('prevVersion'),
        "hasPrev": os.path.exists(os.path.join(s['path'], 'server.jar.previous')),
        "javaHave": have, "javaNeedLatest": required_java(vs.get('latest')) if vs.get('latest') else 0,
        "mods": n_mods, "modKind": kind or '',
        "running": world_busy(name),
        "fabricOk": fabric_supports(cur) if cur else False,
        "paperOk": bool(cur and cur in set(paper_versions())),
        "error": vs.get('error') or '',
    }

def update_server(name, version):
    """Point a world at a different Minecraft version. Manual and explicit, on purpose."""
    s = get_server(name)
    if not s:
        return False, "Server not found."
    if world_busy(name):
        return False, "Stop this world before changing its Minecraft version."
    version = (version or '').strip()
    if not version:
        return False, "Pick a version first."
    cur = server_version(s)
    if version == cur:
        return False, "This world is already on " + version + "."
    need, have = required_java(version), java_major()
    if need and have and have < need:
        return False, ("Minecraft %s needs Java %d, but this PC's newest Java is %d. Install Microsoft "
                       "OpenJDK %d first, then update - otherwise the server won't boot." % (version, need, have, need))
    jar = os.path.join(s['path'], 'server.jar')
    tmp = jar + '.new'
    ok, msg = fetch_jar_for(s['type'], version, tmp)
    if not ok:
        try: os.remove(tmp)
        except Exception: pass
        return False, msg
    auto_backup(name, 'preupdate')                       # safety net before anything is swapped
    if os.path.exists(jar):
        shutil.copy2(jar, jar + '.previous')
        s['prevVersion'] = cur
    os.replace(tmp, jar)
    s['version'] = version
    save_config(CONFIG)
    try:
        with open(os.path.join(s['path'], 'current-version.txt'), 'w', encoding='utf-8') as f:
            f.write(version + "\n")
    except Exception:
        pass
    out = ["Updated '%s' to Minecraft %s (was %s). World backed up first." % (name, version, cur or '?')]
    out.append("Everyone must switch their own Minecraft launcher to %s - on an older client they simply can't join." % version)
    d, kind = mods_dir(s)
    if d and os.path.isdir(d) and any(f.lower().endswith('.jar') for f in os.listdir(d)):
        out.append("Your %ss were built for %s - update them in the Mods tab or the server may crash on boot." % (kind, cur or 'the old version'))
    if s['type'] == 'fabric':
        out.append("Fabric downloads the new server files on first start, so give it a minute.")
    return True, " ".join(out)

def rollback_server(name):
    """Flip back to the jar we saved before the last update (and flip forward again)."""
    s = get_server(name)
    if not s:
        return False, "Server not found."
    if world_busy(name):
        return False, "Stop this world first."
    jar = os.path.join(s['path'], 'server.jar')
    prev = jar + '.previous'
    if not os.path.exists(prev):
        return False, "There's no previous version saved for this world yet."
    cur, back = server_version(s), s.get('prevVersion')
    swap = jar + '.swap'
    os.replace(jar, swap); os.replace(prev, jar); os.replace(swap, prev)
    s['version'], s['prevVersion'] = back or cur, cur
    save_config(CONFIG)
    if back:
        try:
            with open(os.path.join(s['path'], 'current-version.txt'), 'w', encoding='utf-8') as f:
                f.write(back + "\n")
        except Exception:
            pass
    return True, ("Rolled '%s' back to Minecraft %s. Press it again to go back to %s."
                  % (name, back or 'the previous jar', cur or 'the newer one'))

# --------------------------------------------------------------------------- backups
BACKUP_KEEP = 15

# Vanilla keeps the Nether and the End inside the world folder as DIM-1/DIM1.
# Paper and Spigot do not - they split them into folders alongside it. Backing
# up only level-name on a Paper server quietly saves the overworld and drops
# everything anyone built through a portal.
DIM_SUFFIXES = ('_nether', '_the_end')

# Worth having in the zip even though they are not the world: without them a
# restore brings back the map but not the ops, the whitelist or the settings.
CONFIG_FILES = (
    'server.properties', 'ops.json', 'whitelist.json', 'banned-players.json',
    'banned-ips.json', 'permissions.yml', 'bukkit.yml', 'spigot.yml',
    'paper-global.yml', 'paper-world-defaults.yml', 'current-version.txt',
)

def world_dirs(server_path, level):
    """Every folder that is part of this save, in the order we restore them."""
    out = []
    for cand in [level] + [level + suf for suf in DIM_SUFFIXES]:
        p = os.path.join(server_path, cand)
        if os.path.isdir(p):
            out.append(p)
    return out

def _zip_paths(server_path, dirs, files, dest_zip):
    """Everything goes in relative to the server folder, so a restore is a
    straight extract back over the top of it."""
    with zipfile.ZipFile(dest_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        for d in dirs:
            for root, _dirs, fs in os.walk(d):
                for f in fs:
                    fp = os.path.join(root, f)
                    try:
                        z.write(fp, os.path.relpath(fp, server_path))
                    except Exception:
                        pass
        for f in files:
            fp = os.path.join(server_path, f)
            if os.path.isfile(fp):
                try:
                    z.write(fp, os.path.relpath(fp, server_path))
                except Exception:
                    pass

def backups_dir(s):
    """Where this world's backups live. Default is a 'backups' folder inside
    the world, which is convenient but shares its fate and its disk - set
    backupsRoot in config.json to keep them somewhere else."""
    root = (CONFIG.get('backupsRoot') or '').strip()
    if root:
        return os.path.join(root, s['name'])
    return os.path.join(s['path'], 'backups')

def flush_world(name, wait=5):
    """Ask a running world to write itself to disk, and give it a moment. A
    backup taken without this is whatever happened to have been saved already."""
    mp = SERVERS.get(name)
    if not (mp and mp.running()):
        return False
    try:
        mp.p.stdin.write('save-all flush\n'); mp.p.stdin.flush()
    except Exception:
        return False
    time.sleep(wait)
    return True

def auto_backup(name, tag):
    s = get_server(name)
    if not s:
        return None
    level = read_properties(name).get('level-name', 'world')
    dirs = world_dirs(s['path'], level)
    if not dirs:
        return None
    bdir = backups_dir(s)
    os.makedirs(bdir, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    dest = os.path.join(bdir, 'auto_%s_%s.zip' % (tag, ts))
    # Two backups inside the same second - stopping a world right after backing
    # it up by hand - would otherwise land on the same name, and the second
    # would quietly overwrite the first.
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(bdir, 'auto_%s_%s_%d.zip' % (tag, ts, n))
        n += 1
    try:
        _zip_paths(s['path'], dirs, CONFIG_FILES, dest)
    except Exception:
        try: os.remove(dest)
        except Exception: pass
        return None
    autos = sorted(glob.glob(os.path.join(bdir, 'auto_*.zip')), key=os.path.getmtime, reverse=True)
    for old in autos[BACKUP_KEEP:]:
        try: os.remove(old)
        except Exception: pass
    return dest

def manual_backup(name):
    # "Back up now" is exactly when you expect a clean copy, so save first.
    flush_world(name)
    d = auto_backup(name, 'manual')
    return (True, "Backup saved: " + os.path.basename(d)) if d else (False, "Backup failed (world not found).")

def list_backups(name):
    s = get_server(name)
    if not s:
        return []
    bdir = backups_dir(s)
    if not os.path.isdir(bdir):
        return []
    out = []
    for f in sorted(glob.glob(os.path.join(bdir, '*.zip')), key=os.path.getmtime, reverse=True):
        out.append({"file": os.path.basename(f),
                    "size": round(os.path.getsize(f) / 1048576, 1),
                    "when": datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d %H:%M')})
    return out

def _safe_extract(zf, dest, only_tops=None):
    """Extract, refusing any member that would land outside the server folder.
    These are our own zips, but a backup file is a thing a person can copy in
    from elsewhere, and 'it came from the backups folder' is not proof.

    only_tops limits it to those top-level names. A backup holds the settings
    as well as the world, but a restore is about the world - putting an old
    server.properties back would silently undo settings you changed since, so
    those files stay in the zip for you to take out by hand if you want them.
    """
    dest = os.path.abspath(dest)
    written = 0
    for m in zf.infolist():
        name = m.filename.replace('\\', '/')
        if name.endswith('/'):
            continue
        parts = [p for p in name.split('/') if p not in ('', '.', '..')]
        if not parts:
            continue
        if only_tops is not None and parts[0] not in only_tops:
            continue
        target = os.path.abspath(os.path.join(dest, *parts))
        if not target.startswith(dest + os.sep):
            raise ValueError("backup contains a file that would land outside the world folder")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(m) as srcf, open(target, 'wb') as out:
            shutil.copyfileobj(srcf, out)
        written += 1
    return written

def prune_prerestore(server_path, keep=2):
    """The copy taken aside before a restore is a safety net, not a permanent
    resident - a few restores otherwise leave several worlds' worth on disk."""
    olds = sorted(glob.glob(os.path.join(server_path, '*_prerestore_*')),
                  key=os.path.getmtime, reverse=True)
    for old in olds[keep:]:
        try:
            shutil.rmtree(old, ignore_errors=True)
        except Exception:
            pass

def restore_backup(name, fname):
    s = get_server(name)
    if not s:
        return False, "Server not found."
    sport = read_properties(name).get('server-port')
    if proc_for(name).running() or (sport and port_listening(sport, max_age=0)):
        return False, "Stop the server first, then restore."
    fname = os.path.basename(fname)
    zip_path = os.path.join(backups_dir(s), fname)
    if not os.path.isfile(zip_path):
        return False, "Backup not found."
    level = read_properties(name).get('level-name', 'world')
    world = os.path.join(s['path'], level)

    # Every folder that belongs to this save moves aside together - restoring
    # the overworld from one point in time next to a Nether from another is
    # how you get a portal that opens somewhere unexpected.
    ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    # Two restores inside the same second would otherwise be handed the same
    # name to move the world to, and renaming onto an existing folder fails.
    suffix, n = ts, 2
    while any(os.path.exists(os.path.join(s['path'], '%s_prerestore_%s' % (os.path.basename(d), suffix)))
              for d in world_dirs(s['path'], level)):
        suffix = '%s_%d' % (ts, n)
        n += 1
    moved = []
    for d in world_dirs(s['path'], level):
        kept = os.path.join(s['path'], '%s_prerestore_%s' % (os.path.basename(d), suffix))
        try:
            os.rename(d, kept)
            moved.append((d, kept))
        except Exception as e:
            for orig, back in reversed(moved):     # undo the ones that did move
                try: os.rename(back, orig)
                except Exception: pass
            return False, "Could not set your world aside (%s). Nothing was changed." % str(e)[:70]

    def put_it_back(msg):
        # The world moved aside a moment ago and the restore did not work. Put
        # it back under its own name - otherwise the next start finds no world
        # folder and generates a brand new empty one over the top.
        stuck = []
        for orig, back in moved:
            # Whatever the half-finished restore managed to write is a fragment
            # of a backup we have already given up on. The real world is the
            # copy we set aside, so clear the fragment out of its way.
            if os.path.isdir(orig):
                shutil.rmtree(orig, ignore_errors=True)
            try:
                os.rename(back, orig)
            except Exception:
                stuck.append(os.path.basename(back))
        if stuck:
            return False, msg + " Your old world is still safe, saved as '%s'." % ", ".join(stuck)
        return False, msg + " Your world is untouched."

    tops = set([level] + [level + suf for suf in DIM_SUFFIXES])
    try:
        with zipfile.ZipFile(zip_path) as z:      # closed here - Windows keeps
            _safe_extract(z, s['path'], only_tops=tops)   # the file locked otherwise
    except Exception as e:
        return put_it_back("Restore failed: %s." % e)
    if not os.path.isdir(world):
        return put_it_back("That backup didn't contain a world folder.")
    prune_prerestore(s['path'])
    return True, "Restored '%s'. Start the server to load it." % fname

BACKUP_EVERY = 2 * 3600

def periodic_backups():
    while True:
        try:
            time.sleep(BACKUP_EVERY)
            for s in list(CONFIG['servers']):
                name = s['name']
                mp = SERVERS.get(name)
                if mp and mp.running():
                    flush_world(name)
                    auto_backup(name, 'periodic')
                    continue
                # A world the panel adopted rather than started has no pipe to
                # flush through, but it is still somebody's world and it was
                # getting no backups at all. Take one anyway.
                sport = read_properties(name).get('server-port')
                if sport and port_listening(sport):
                    auto_backup(name, 'periodic')
        except Exception:
            pass

# --------------------------------------------------------------------------- console
def tail_log(mp, since=None):
    """Console lines. Given a cursor, only what has arrived since - the panel
    polls this every 1.5s and used to re-send the whole console each time.

    {seq} is the count of lines seen since launch; the caller sends back the
    one it last saw. If it has fallen further behind than we still hold, we
    say so and send everything we have rather than pretend it is continuous.
    """
    if not mp:
        return {"lines": [], "seq": 0, "reset": True}
    lines, seq = list(mp.log), mp.seq
    try:
        since = int(since)
    except (TypeError, ValueError):
        return {"lines": lines, "seq": seq, "reset": True}
    if since > seq:                 # the world restarted and the count went back
        return {"lines": lines, "seq": seq, "reset": True}
    missed = seq - since
    if missed > len(lines):         # we dropped lines they never saw
        return {"lines": lines, "seq": seq, "reset": True}
    return {"lines": lines[len(lines) - missed:] if missed else [],
            "seq": seq, "reset": False}

LOG_FILE_MAX = 400 * 1024

def server_log_file(name, limit=2000):
    """The end of the world's own logs/latest.log. The panel only holds what
    has happened since it started the world, which is no help when you want to
    know why the world stopped an hour ago."""
    s = get_server(name)
    if not s:
        return {"ok": False, "lines": [], "note": "Server not found."}
    fp = os.path.join(s['path'], 'logs', 'latest.log')
    if not os.path.isfile(fp):
        return {"ok": False, "lines": [],
                "note": "This world has no logs/latest.log yet - it has not run."}
    try:
        size = os.path.getsize(fp)
        with open(fp, 'r', encoding='utf-8', errors='replace') as f:
            if size > LOG_FILE_MAX:
                f.seek(size - LOG_FILE_MAX)
                f.readline()          # drop the half line we landed in
            lines = f.read().splitlines()
    except Exception as e:
        return {"ok": False, "lines": [], "note": "Could not read it (%s)." % str(e)[:70]}
    return {"ok": True, "lines": lines[-limit:], "file": fp,
            "truncated": size > LOG_FILE_MAX}

# --------------------------------------------------------------------------- state
def online_players(name):
    mp = SERVERS.get(name)
    return list(mp.players) if mp else []

def set_meta(name, group, address):
    s = get_server(name)
    if not s:
        return False, "Server not found."
    if group is not None:
        s['group'] = group
    if address is not None:
        s.setdefault('tunnel', {})['address'] = address
    save_config(CONFIG)
    return True, "Saved."

def set_icon(name, url=None, path=None, data=None):
    s = get_server(name)
    if not s:
        return False, "Server not found."
    try:
        import io, base64
        from PIL import Image
    except Exception:
        return False, "Image library missing (need Pillow)."
    try:
        if data:
            raw = base64.b64decode(data.split(',', 1)[-1])
            img = Image.open(io.BytesIO(raw))
        elif url and url.lower().startswith('http'):
            with urllib.request.urlopen(url, timeout=30) as r:
                img = Image.open(io.BytesIO(r.read()))
        elif path and os.path.isfile(path):
            img = Image.open(path)
        else:
            return False, "Give me an image file, URL, or path."
        img.convert('RGBA').resize((64, 64), Image.LANCZOS).save(os.path.join(s['path'], 'server-icon.png'))
        return True, "Icon set! Restart this world to see it in the server list."
    except Exception as e:
        return False, "Couldn't set the icon: " + str(e)

def remove_icon(name):
    s = get_server(name)
    if not s:
        return False, "Server not found."
    ic = os.path.join(s['path'], 'server-icon.png')
    if not os.path.isfile(ic):
        return True, "No icon to remove."
    try:
        os.remove(ic)
    except Exception as e:
        return False, "Couldn't remove the icon: " + str(e)
    return True, "Icon removed. Restart this world to clear it in the server list."

def build_state():
    servers = []
    for s in CONFIG['servers']:
        mp = SERVERS.get(s['name'])
        sport = read_properties(s['name']).get('server-port', '?')
        managed = mp.running() if mp else False
        running = managed or (sport != '?' and port_listening(sport))
        ready = (mp.ready if mp else False) or (running and not managed)
        icon_path = os.path.join(s['path'], 'server-icon.png')
        has_icon = os.path.isfile(icon_path)
        servers.append({
            "name": s['name'], "type": s['type'], "path": s['path'],
            "group": s.get('group', ''),
            "running": running, "ready": ready,
            "port": sport,
            "players": online_players(s['name']) if running else [],
            "address": (s.get('tunnel') or {}).get('address', ''),
            "hasTunnel": bool((s.get('tunnel') or {}).get('address')),
            "hasIcon": has_icon,
            "protected": s['name'] in (CONFIG.get('protected') or []),
            "iconVer": int(os.path.getmtime(icon_path) * 1000) if has_icon else 0,
            "version": s.get('version') or '',
            "hasPrev": os.path.exists(os.path.join(s['path'], 'server.jar.previous'))
        })
    bk = bank_status()
    return {"servers": servers, "active": CONFIG.get('active'),
            "tunnel": {"running": tunnel_alive(), "available": bool(account_tunnel())},
            "bank": {"total": bk["total"], "free": bk["free"]}}

# --------------------------------------------------------------------------- HTTP
# The panel listens on 127.0.0.1, which sounds private but is not. Any web page
# your browser has open can send a request here, and the browser attaches it for
# free. A plain HTML form posting text/plain is a "simple request" - no
# preflight, so nothing stops it arriving - and its body is still valid JSON.
# Without the checks below, a page in another tab could op a stranger, drop a
# jar into your mods folder or delete a world, and you would never see it.
ALLOWED_HOSTS = {"%s:%d" % (HOST, PORT), "localhost:%d" % PORT}
ALLOWED_ORIGINS = {"http://%s:%d" % (HOST, PORT), "http://localhost:%d" % PORT}


def host_ok(host):
    """Only answer to the loopback address we serve on. A hostname pointed at
    127.0.0.1 (DNS rebinding) arrives carrying its own name here, not ours."""
    return (host or "").strip().lower() in ALLOWED_HOSTS


def origin_ok(origin):
    """No Origin is a same-origin GET or a direct request. An Origin that is
    not ours is another site talking to us, whatever it says it wants."""
    if not origin:
        return True
    o = origin.strip().lower()
    if o == "null":          # sandboxed iframe or file:// - never the panel
        return False
    return o in ALLOWED_ORIGINS


def referer_ok(referer):
    if not referer:
        return True
    p = urllib.parse.urlsplit(referer.strip())
    if not p.scheme or not p.netloc:
        return False
    return origin_ok("%s://%s" % (p.scheme.lower(), p.netloc.lower()))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _guard(self, writing):
        """True if this request may go ahead. Writes are held to the stricter
        standard; reads are already covered by the browser's same-origin policy,
        since the panel sends no CORS headers at all."""
        if not host_ok(self.headers.get('Host')):
            self._send(403, {"ok": False,
                             "msg": "Hearth only answers to localhost."}); return False
        if not origin_ok(self.headers.get('Origin')):
            self._send(403, {"ok": False,
                             "msg": "That request came from another site, so Hearth ignored it."}); return False
        if writing:
            # The panel's own fetch() sends this header. A form cannot set one,
            # and a cross-origin fetch that tries is stopped by a preflight we
            # never answer. This is the belt to the Origin check's braces.
            if self.headers.get('X-Hearth') != '1':
                self._send(403, {"ok": False,
                                 "msg": "That request did not come from the Hearth panel, so it was ignored."}); return False
            if not referer_ok(self.headers.get('Referer')):
                self._send(403, {"ok": False,
                                 "msg": "That request came from another site, so Hearth ignored it."}); return False
        return True

    def _send(self, code, body, ctype='application/json', headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for hk, hv in (headers or {}).items():
            self.send_header(hk, hv)
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        ln = int(self.headers.get('Content-Length', 0) or 0)
        if ln == 0:
            return {}
        try:
            return json.loads(self.rfile.read(ln).decode('utf-8'))
        except Exception:
            return {}

    def _serve_file(self, rel):
        rel = rel.replace('\\', '/').lstrip('/')
        fp = os.path.normpath(os.path.join(UI_DIR, rel))
        # keep within UI_DIR (block path traversal like /../config.json, which holds the tunnel secret)
        if not (fp == UI_DIR or fp.startswith(UI_DIR + os.sep)) or not os.path.isfile(fp):
            self._send(404, "not found", 'text/plain'); return
        ctype = 'text/html' if fp.endswith('.html') else \
                'text/css' if fp.endswith('.css') else \
                'application/javascript' if fp.endswith('.js') else 'application/octet-stream'
        with open(fp, 'rb') as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        if not self._guard(False):
            return
        path = self.path.split('?', 1)[0]
        q = {}
        if '?' in self.path:
            for kv in self.path.split('?', 1)[1].split('&'):
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    q[k] = urllib.parse.unquote(v)
        if path == '/' or path == '/index.html':
            self._serve_file('index.html'); return
        if path == '/api/state':
            self._send(200, build_state()); return
        if path == '/api/versions':
            self._send(200, get_versions()); return
        if path == '/api/properties':
            self._send(200, {"props": read_properties(q.get('name', ''))}); return
        if path == '/api/log':
            if q.get('tunnel') == '1':
                self._send(200, tail_log(TUNNEL, q.get('since'))); return
            if q.get('file') == '1':
                self._send(200, server_log_file(q.get('name', ''))); return
            self._send(200, tail_log(SERVERS.get(q.get('name', '')), q.get('since'))); return
        if path == '/api/mods':
            self._send(200, list_mods(q.get('name', ''))); return
        if path == '/api/backups':
            self._send(200, {"list": list_backups(q.get('name', ''))}); return
        if path == '/api/bank':
            self._send(200, bank_status()); return
        if path == '/api/doctor':
            if not hearth_setup:
                self._send(200, {"ready": True, "checks": [],
                                 "msg": "setup checks unavailable"}); return
            self._send(200, hearth_setup.doctor()); return
        if path == '/api/update/check':
            if not hearth_setup:
                self._send(200, {"ok": False, "msg": "updates unavailable"}); return
            self._send(200, hearth_setup.check_update()); return
        if path == '/api/shortcut':
            if not hearth_setup:
                self._send(200, {"exists": False}); return
            self._send(200, hearth_setup.shortcut_status()); return
        if path == '/api/playit':
            if not hearth_setup:
                self._send(200, {"exists": False}); return
            st = hearth_setup.playit_status()
            st["linked"] = bool(account_tunnel())
            self._send(200, st); return
        if path == '/api/network':
            if not hearth_setup:
                self._send(200, {"ran": False}); return
            aud = q.get('audience', 'anyone')
            self._send(200, hearth_setup.network_probe(aud)); return
        if path == '/api/mods/browse':
            self._send(200, browse_mods(q.get('name', ''), q.get('source', 'modrinth'),
                                        q.get('q', ''), q.get('page', 0) or 0)); return
        if path == '/api/mods/files':
            self._send(200, mod_files(q.get('name', ''), q.get('source', 'modrinth'),
                                      q.get('project', ''))); return
        if path == '/api/version':
            self._send(200, version_info(q.get('name', ''))); return
        if path == '/api/ai':
            if not AI:
                self._send(200, {"loaded": False, "msg": "ai.py failed to load."}); return
            st = AI.panel_status(q.get('name') or CONFIG.get('active'))
            st["loaded"] = True
            self._send(200, st); return
        if path == '/api/icon':
            s = get_server(q.get('name', ''))
            ic = os.path.join(s['path'], 'server-icon.png') if s else ''
            if ic and os.path.isfile(ic):
                with open(ic, 'rb') as f:
                    self._send(200, f.read(), 'image/png', {'Cache-Control': 'no-cache'}); return
            self._send(404, b'', 'image/png'); return
        self._serve_file(path.lstrip('/'))

    def do_POST(self):
        if not self._guard(True):
            return
        path = self.path.split('?', 1)[0]
        b = self._json_body()
        name = b.get('name', '')
        routes = {
            '/api/server/start':   lambda: start_server(name),
            '/api/server/stop':    lambda: stop_server(name),
            '/api/server/command': lambda: send_command(name, b.get('cmd', '')),
            '/api/server/create':  lambda: create_server(b.get('name', ''), b.get('version', ''), b.get('stype', 'vanilla'), b.get('seed', '')),
            '/api/server/icon':    lambda: set_icon(name, b.get('url'), b.get('path'), b.get('data')),
            '/api/server/icon/remove': lambda: remove_icon(name),
            '/api/server/delete':  lambda: delete_server(name),
            '/api/server/convert': lambda: convert_to_paper(name),
            '/api/server/meta':    lambda: set_meta(name, b.get('group'), b.get('address')),
            '/api/bank':           lambda: set_bank(b.get('text', '')),
            '/api/server/backup':  lambda: manual_backup(name),
            '/api/server/restore': lambda: restore_backup(name, b.get('file', '')),
            '/api/properties/save':lambda: write_properties(name, b.get('props', {})),
            '/api/tunnel/start':   lambda: start_tunnel(name or CONFIG.get('active')),
            '/api/tunnel/stop':    lambda: stop_tunnel(),
            '/api/mods/add':       lambda: add_mod(name, b.get('url', '')),
            '/api/mods/remove':    lambda: remove_mod(name, b.get('file', '')),
            '/api/mods/install':   lambda: install_mod(name, b.get('source', 'modrinth'), b.get('project', ''),
                                                       b.get('file'), b.get('deps', True)),
            '/api/mods/upload':    lambda: upload_mod(name, b.get('filename', ''), b.get('data', '')),
            '/api/mods/key':       lambda: set_cf_key(b.get('key', '')),
            '/api/server/update':  lambda: update_server(name, b.get('version', '')),
            '/api/server/rollback':lambda: rollback_server(name),
            '/api/server/loader':  lambda: switch_loader(name, b.get('target', '')),
        }
        if path.startswith('/api/ai/'):
            if not AI:
                self._send(200, {"ok": False, "msg": "The AI module isn't loaded."}); return
            if path == '/api/ai/config':
                ok, msg = AI.update_config(b.get('patch') or {})
            elif path == '/api/ai/memory':
                ok, msg = AI.update_memory(b.get('about'), b.get('facts'), b.get('forget'))
            elif path == '/api/ai/test':
                ok, msg = AI.test_connection()
            elif path == '/api/ai/say':
                ok, msg = AI.test_message(name or CONFIG.get('active'),
                                          b.get('player') or AI.cfg.get('owner_ign') or 'owner',
                                          b.get('text', ''))
            else:
                self._send(404, {"ok": False, "msg": "unknown endpoint"}); return
            self._send(200, {"ok": ok, "msg": msg}); return
        if path == '/api/server/setactive':
            # Only ever point at a world the panel actually knows about - a bad
            # name here would be saved to config.json and leave every other
            # screen answering "Server not found."
            if not get_server(name):
                msg = "No world called '%s'." % name if name else "Pick a world first."
                self._send(200, {"ok": False, "msg": msg}); return
            CONFIG['active'] = name; save_config(CONFIG)
            self._send(200, {"ok": True, "msg": "Active server: " + name}); return
        if path == '/api/shortcut':
            if not hearth_setup:
                self._send(200, {"ok": False, "msg": "unavailable"}); return
            self._send(200, hearth_setup.make_shortcut()); return
        if path == '/api/playit/get':
            if not hearth_setup:
                self._send(200, {"ok": False, "msg": "unavailable"}); return
            self._send(200, hearth_setup.fetch_playit()); return
        if path == '/api/playit/secret':
            # do_POST already read the body into b - reading it again would
            # block forever waiting for bytes that were consumed.
            ok, msg = set_tunnel_secret(b.get('secret', ''), b.get('name'))
            self._send(200, {"ok": ok, "msg": msg}); return
        if path == '/api/update/stage':
            if not hearth_setup:
                self._send(200, {"ok": False, "msg": "updates unavailable"}); return
            self._send(200, hearth_setup.stage_update()); return
        if path == '/api/quit':
            self._send(200, {"ok": True, "msg": "Shutting down panel..."})
            threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()
            return
        if path in routes:
            ok, msg = routes[path]()
            self._send(200, {"ok": ok, "msg": msg}); return
        self._send(404, {"ok": False, "msg": "unknown endpoint"})


def main():
    # A download from last session is swapped in here, before anything opens.
    # Only Hearth's own files are touched; worlds and config are left alone.
    if hearth_setup:
        try:
            done = hearth_setup.apply_staged()
            if done:
                print(done)
        except Exception as _e:
            print("update could not be applied:", _e)
    url = "http://%s:%d/" % (HOST, PORT)
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        # panel already running on this port -> just open a window onto it and exit
        if not os.environ.get('HEARTH_NO_OPEN'):
            try:
                if hearth_setup:
                    hearth_setup.open_app_window(url)
                else:
                    webbrowser.open(url)
            except Exception:
                pass
        return
    print("Hearth Control Panel running at " + url)
    threading.Thread(target=periodic_backups, daemon=True).start()
    if not os.environ.get('HEARTH_NO_OPEN'):
        try:
            if hearth_setup:
                hearth_setup.open_app_window(url)
            else:
                webbrowser.open(url)
        except Exception:
            pass
    httpd.serve_forever()

if __name__ == '__main__':
    main()
