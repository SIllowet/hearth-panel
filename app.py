#!/usr/bin/env python3
# Hearth - Minecraft Server Control Panel (local, localhost-only)
import json, os, subprocess, threading, time, glob, shutil, urllib.request, hashlib, re, webbrowser, socket, datetime, zipfile
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from ai import AI                # the console companion (optional)
except Exception as _e:              # the panel must still boot if it's broken
    AI = None
    print("AI module not loaded:", _e)

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, 'config.json')
UI_DIR = os.path.join(BASE, 'ui')
HOST, PORT = '127.0.0.1', 8765
MANIFEST_URL = 'https://launchermeta.mojang.com/mc/game/version_manifest_v2.json'
PAPER_API = 'https://api.papermc.io/v2/projects/paper'
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
    with _cfg_lock:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(c, f, indent=2)

CONFIG = load_config()

def get_server(name):
    for s in CONFIG['servers']:
        if s['name'] == name:
            return s
    return None

# --------------------------------------------------------------------------- processes
class ManagedProc:
    def __init__(self):
        self.p = None
        self.log = deque(maxlen=500)
        self.ready = False
    def running(self):
        return self.p is not None and self.p.poll() is None

SERVERS = {}      # name -> ManagedProc (minecraft)
TUNNEL = ManagedProc()

def proc_for(name):
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

def port_listening(port):
    try:
        port = int(port)
    except Exception:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect(('127.0.0.1', port)); return True
    except Exception:
        return False
    finally:
        try: s.close()
        except Exception: pass

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

def _reader(mp, stream, watcher=None):
    try:
        for line in iter(stream.readline, ''):
            if line == '':
                break
            line = line.rstrip('\n')
            mp.log.append(line)
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
    if mp.running():
        return True, "Already running."
    sport = read_properties(name).get('server-port')
    if sport and port_listening(sport):
        return False, "BLOCKED: port " + sport + " is already in use — a server is already running on this world. Refusing to launch a second one (that's what wiped the world before)."
    path = s['path']
    jar = os.path.join(path, 'server.jar')
    if not os.path.exists(jar):
        return False, "server.jar missing in " + path
    java = find_java()
    mem = CONFIG.get('memory') or {}
    args = [java, '-Xms' + str(mem.get('min', '2G')), '-Xmx' + str(mem.get('max', '4G')),
            '-XX:+UseG1GC', '-jar', 'server.jar', 'nogui']
    mp.log.clear(); mp.ready = False
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
        if sport and port_listening(sport):
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

def playit_proc_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq playit.exe'],
                             capture_output=True, text=True, creationflags=NO_WINDOW).stdout or ''
        return 'playit.exe' in out.lower()
    except Exception:
        return False

def tunnel_alive():
    return TUNNEL.running() or playit_proc_running()

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
    try:
        TUNNEL.p = subprocess.Popen([exe, '--secret', t['secret']],
                                    cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1, creationflags=NO_WINDOW)
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
        props[k] = v
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

def _download(url, dest):
    with urllib.request.urlopen(url, timeout=300) as r, open(dest, 'wb') as f:
        shutil.copyfileobj(r, f)

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
    _download(sv['url'], dest)
    if sv.get('sha1'):
        h = hashlib.sha1(open(dest, 'rb').read()).hexdigest()
        if h != sv['sha1']:
            return False, "Checksum mismatch."
    return True, "ok"

def fetch_paper_jar(version, dest):
    try:
        with urllib.request.urlopen(PAPER_API + '/versions/' + version, timeout=20) as r:
            info = json.loads(r.read().decode('utf-8'))
        builds = info.get('builds', [])
        if not builds:
            return False, "Paper has no build for " + version + " yet."
        build = builds[-1]
        burl = PAPER_API + '/versions/' + version + '/builds/' + str(build)
        with urllib.request.urlopen(burl, timeout=20) as r:
            bi = json.loads(r.read().decode('utf-8'))
        fname = bi['downloads']['application']['name']
        _download(burl + '/downloads/' + fname, dest)
        return True, "ok"
    except Exception as e:
        return False, "Paper not available for " + version + " (" + str(e) + ")"

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
    if stype == 'paper':
        ok, msg = fetch_paper_jar(version, jar)
    else:
        ok, msg = fetch_vanilla_jar(version, jar)
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
    if CONFIG.get('active') == name and CONFIG['servers']:
        CONFIG['active'] = CONFIG['servers'][0]['name']
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
    if not d:
        return {"supported": False, "items": [],
                "note": "Vanilla can't load mods/plugins. Convert this server to Paper (Servers tab) to add plugins."}
    os.makedirs(d, exist_ok=True)
    items = [f for f in os.listdir(d) if f.lower().endswith('.jar')]
    return {"supported": True, "kind": kind, "items": sorted(items)}

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
            fn = url.split('/')[-1].split('?')[0]
            if not fn.lower().endswith('.jar'):
                fn += '.jar'
            _download(url, os.path.join(d, fn))
            return True, "Added " + fn
        else:
            if not os.path.exists(url):
                return False, "File not found: " + url
            fn = os.path.basename(url)
            shutil.copy2(url, os.path.join(d, fn))
            return True, "Added " + fn
    except Exception as e:
        return False, "Failed: " + str(e)

def remove_mod(name, fn):
    s = get_server(name)
    d, kind = mods_dir(s) if s else (None, None)
    if not d:
        return False, "Not applicable."
    fn = os.path.basename(fn)
    target = os.path.join(d, fn)
    if os.path.exists(target):
        os.remove(target)
        return True, "Removed " + fn
    return False, "Not found."

def convert_to_paper(name):
    s = get_server(name)
    if not s:
        return False, "Not found."
    if proc_for(name).running():
        return False, "Stop the server first."
    version = s.get('version')
    vt = os.path.join(s['path'], 'current-version.txt')
    if not version and os.path.exists(vt):
        version = open(vt).read().strip()
    if not version:
        version = get_versions().get('latest')
    if not version:
        return False, "Could not determine version."
    if os.path.exists(os.path.join(s['path'], 'server.jar')):
        shutil.copy2(os.path.join(s['path'], 'server.jar'),
                     os.path.join(s['path'], 'server.jar.vanilla-backup'))
    ok, msg = fetch_paper_jar(version, os.path.join(s['path'], 'server.jar'))
    if not ok:
        return False, msg
    os.makedirs(os.path.join(s['path'], 'plugins'), exist_ok=True)
    s['type'] = 'paper'
    save_config(CONFIG)
    return True, "Converted '" + name + "' to Paper " + version + ". You can add plugins now."

# --------------------------------------------------------------------------- backups
BACKUP_KEEP = 15

def _zip_dir(src_dir, dest_zip):
    base = os.path.dirname(src_dir)
    with zipfile.ZipFile(dest_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(src_dir):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    z.write(fp, os.path.relpath(fp, base))
                except Exception:
                    pass

def auto_backup(name, tag):
    s = get_server(name)
    if not s:
        return None
    level = read_properties(name).get('level-name', 'world')
    world = os.path.join(s['path'], level)
    if not os.path.isdir(world):
        return None
    bdir = os.path.join(s['path'], 'backups')
    os.makedirs(bdir, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    dest = os.path.join(bdir, 'auto_%s_%s.zip' % (tag, ts))
    try:
        _zip_dir(world, dest)
    except Exception:
        return None
    autos = sorted(glob.glob(os.path.join(bdir, 'auto_*.zip')), key=os.path.getmtime, reverse=True)
    for old in autos[BACKUP_KEEP:]:
        try: os.remove(old)
        except Exception: pass
    return dest

def manual_backup(name):
    d = auto_backup(name, 'manual')
    return (True, "Backup saved: " + os.path.basename(d)) if d else (False, "Backup failed (world not found).")

def list_backups(name):
    s = get_server(name)
    if not s:
        return []
    bdir = os.path.join(s['path'], 'backups')
    if not os.path.isdir(bdir):
        return []
    out = []
    for f in sorted(glob.glob(os.path.join(bdir, '*.zip')), key=os.path.getmtime, reverse=True):
        out.append({"file": os.path.basename(f),
                    "size": round(os.path.getsize(f) / 1048576, 1),
                    "when": datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d %H:%M')})
    return out

def restore_backup(name, fname):
    s = get_server(name)
    if not s:
        return False, "Server not found."
    sport = read_properties(name).get('server-port')
    if proc_for(name).running() or (sport and port_listening(sport)):
        return False, "Stop the server first, then restore."
    fname = os.path.basename(fname)
    zip_path = os.path.join(s['path'], 'backups', fname)
    if not os.path.isfile(zip_path):
        return False, "Backup not found."
    level = read_properties(name).get('level-name', 'world')
    world = os.path.join(s['path'], level)
    if os.path.isdir(world):
        ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        os.rename(world, os.path.join(s['path'], '%s_prerestore_%s' % (level, ts)))
    try:
        zipfile.ZipFile(zip_path).extractall(s['path'])
    except Exception as e:
        return False, "Restore failed: " + str(e)
    if not os.path.isdir(world):
        return False, "That backup didn't contain a world folder."
    return True, "Restored '%s'. Start the server to load it." % fname

def periodic_backups():
    while True:
        try:
            time.sleep(2 * 3600)
            for s in list(CONFIG['servers']):
                mp = SERVERS.get(s['name'])
                if mp and mp.running():
                    try:
                        mp.p.stdin.write('save-all flush\n'); mp.p.stdin.flush()
                    except Exception:
                        pass
                    time.sleep(5)
                    auto_backup(s['name'], 'periodic')
        except Exception:
            pass

# --------------------------------------------------------------------------- state
def online_players(name):
    mp = SERVERS.get(name)
    if not mp:
        return []
    out = []
    for l in mp.log:
        if 'Starting minecraft server' in l:
            out = []
        m = re.search(r']:\s+([A-Za-z0-9_]{2,16}) joined the game', l)
        if m:
            if m.group(1) not in out:
                out.append(m.group(1))
            continue
        m = re.search(r']:\s+([A-Za-z0-9_]{2,16}) (?:left the game|lost connection)', l)
        if m and m.group(1) in out:
            out.remove(m.group(1))
    return out

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
            "iconVer": int(os.path.getmtime(icon_path) * 1000) if has_icon else 0
        })
    bk = bank_status()
    return {"servers": servers, "active": CONFIG.get('active'),
            "tunnel": {"running": tunnel_alive(), "available": bool(account_tunnel())},
            "bank": {"total": bk["total"], "free": bk["free"]}}

# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

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
                self._send(200, {"lines": list(TUNNEL.log)}); return
            mp = SERVERS.get(q.get('name', ''))
            self._send(200, {"lines": list(mp.log) if mp else []}); return
        if path == '/api/mods':
            self._send(200, list_mods(q.get('name', ''))); return
        if path == '/api/backups':
            self._send(200, {"list": list_backups(q.get('name', ''))}); return
        if path == '/api/bank':
            self._send(200, bank_status()); return
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
            CONFIG['active'] = name; save_config(CONFIG)
            self._send(200, {"ok": True, "msg": "Active server: " + name}); return
        if path == '/api/quit':
            self._send(200, {"ok": True, "msg": "Shutting down panel..."})
            threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()
            return
        if path in routes:
            ok, msg = routes[path]()
            self._send(200, {"ok": ok, "msg": msg}); return
        self._send(404, {"ok": False, "msg": "unknown endpoint"})

import urllib.parse  # noqa (used in handler)

def main():
    url = "http://%s:%d/" % (HOST, PORT)
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        # panel already running on this port -> just open the browser to it and exit
        if not os.environ.get('HEARTH_NO_OPEN'):
            try: webbrowser.open(url)
            except Exception: pass
        return
    print("Hearth Control Panel running at " + url)
    threading.Thread(target=periodic_backups, daemon=True).start()
    if not os.environ.get('HEARTH_NO_OPEN'):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    httpd.serve_forever()

if __name__ == '__main__':
    main()
