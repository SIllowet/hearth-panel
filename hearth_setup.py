#!/usr/bin/env python3
"""
Hearth - first-run checks and updates.

Two jobs, both deliberately boring:

  doctor()  - works out what's missing and says so in words a friend would
              understand. It never installs anything. Every problem comes with
              a link and a one-line explanation of why Hearth wants it.

  updates   - checks a VERSION file on GitHub, stages a new copy, and applies
              it on the next start. It only ever replaces Hearth's own files;
              your worlds, backups, config and API keys are never touched.

Standard library only, like the rest of the panel. No pip install.
"""

import json, os, re, shutil, socket, subprocess, sys, time, glob
import urllib.request, urllib.error, zipfile, io

BASE = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

APP_VERSION = "1.0.0"

REPO = "SIllowet/hearth-panel"
BRANCH = "main"
VERSION_URL = "https://raw.githubusercontent.com/%s/%s/VERSION" % (REPO, BRANCH)
CHANGELOG_URL = "https://raw.githubusercontent.com/%s/%s/CHANGELOG.md" % (REPO, BRANCH)
ZIP_URL = "https://github.com/%s/archive/refs/heads/%s.zip" % (REPO, BRANCH)
RELEASES_PAGE = "https://github.com/%s" % REPO

STAGE_DIR = os.path.join(BASE, '.update')
STAGE_PAYLOAD = os.path.join(STAGE_DIR, 'payload')
STAGE_MARK = os.path.join(STAGE_DIR, 'ready.json')

# Hearth's own files. An update replaces exactly these and nothing else.
# Anything not on this list - your worlds, backups, config.json, API keys,
# server jars - is never read, moved or deleted by the updater.
APP_FILES = [
    'app.py', 'ai.py', 'mc_tools.py', 'modstore.py', 'hearth_setup.py',
    'start-panel.bat', 'README.md', 'config.example.json', 'VERSION',
    'CHANGELOG.md', 'LICENSE',
]
APP_DIRS = ['ui']

# Never touched, whatever else happens. Mirrors .gitignore.
PROTECTED = {
    'config.json', 'ai_config.json', 'ai_memory.json', 'panel.log',
    'servers', 'backups', 'worlds',
}


# --------------------------------------------------------------- small helpers
def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'Hearth/%s' % APP_VERSION})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _ver_tuple(s):
    parts = re.findall(r'\d+', s or '')
    return tuple(int(p) for p in parts[:4]) or (0,)


def _run(cmd, timeout=6):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WINDOW)
        return (p.stdout or '') + (p.stderr or '')
    except Exception:
        return ''


# --------------------------------------------------------------------- doctor
def _check_python():
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    return {
        "id": "python", "label": "Python", "ok": ok,
        "detail": "Python %d.%d.%d" % (v.major, v.minor, v.micro),
        "why": "Hearth itself is written in Python. You already have it or you "
               "would not be reading this.",
        "fix": None if ok else {
            "kind": "link", "label": "Get a newer Python",
            "url": "https://www.python.org/downloads/",
            "note": "Hearth needs 3.10 or newer. Tick \"Add Python to PATH\" "
                    "during setup."},
    }


def _find_java():
    cands = []
    for pat in (r"C:\Program Files\Microsoft\jdk-*",
                r"C:\Program Files\Java\*",
                r"C:\Program Files\Eclipse Adoptium\*",
                r"C:\Program Files\Zulu\*"):
        cands += sorted(glob.glob(pat))
    for c in reversed(cands):
        exe = os.path.join(c, 'bin', 'java.exe')
        if os.path.exists(exe):
            return exe
    found = shutil.which('java')
    return found or None


def _check_java():
    exe = _find_java()
    ver = ''
    if exe:
        out = _run([exe, '-version'])
        m = re.search(r'version "?(\d+)', out)
        if m:
            ver = m.group(1)
    ok = bool(exe)
    old = bool(ver) and int(ver) < 17
    if ok and old:
        return {
            "id": "java", "label": "Java", "ok": False,
            "detail": "Java %s found - too old" % ver,
            "why": "Minecraft 1.20 and newer need Java 17 or later. Yours is "
                   "older, so the server would refuse to start.",
            "fix": {"kind": "link", "label": "Get Java 21",
                    "url": "https://adoptium.net/temurin/releases/?version=21",
                    "note": "Pick the .msi installer for Windows x64."},
        }
    return {
        "id": "java", "label": "Java", "ok": ok,
        "detail": ("Java %s" % ver) if ver else (exe or "not found"),
        "why": "Minecraft servers run on Java. Without it the server cannot "
               "start, though the panel itself will still open.",
        "fix": None if ok else {
            "kind": "link", "label": "Get Java 21",
            "url": "https://adoptium.net/temurin/releases/?version=21",
            "note": "Pick the .msi installer for Windows x64, then reopen Hearth."},
    }


def _check_playit():
    local = os.path.join(BASE, 'playit.exe')
    found = os.path.exists(local) or bool(shutil.which('playit'))
    return {
        "id": "playit", "label": "playit tunnel", "ok": found, "optional": True,
        "detail": "found" if found else "not found",
        "why": "This is what gives your friends an address they can join from "
               "anywhere. Without it the server still runs - but only people "
               "on your own network can join.",
        "fix": None if found else {
            "kind": "link", "label": "Get playit",
            "url": "https://playit.gg/download",
            "note": "Download playit.exe and drop it in the Hearth folder. "
                    "There are other ways to let friends in - see Sharing."},
    }


def _check_server_files():
    # config.json is written on first launch, so its mere existence proves
    # nothing. Count the worlds actually configured in it instead.
    worlds = []
    try:
        cfg = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
        worlds = [w for w in (cfg.get('servers') or []) if w]
    except Exception:
        worlds = []
    jars = glob.glob(os.path.join(BASE, '**', '*.jar'), recursive=True)
    ok = bool(worlds)
    return {
        "id": "server", "label": "A Minecraft world", "ok": ok, "optional": True,
        "detail": ("%d world%s set up" % (len(worlds), '' if len(worlds) == 1 else 's'))
                  if worlds else ("a server file is here, but no world yet"
                                  if jars else "none yet"),
        "why": "This is the world itself. Hearth can download one for you - "
               "open Worlds and pick New world.",
        "fix": None if ok else {
            "kind": "route", "label": "Make a world", "url": "#worlds",
            "note": "Hearth downloads the server for you. Nothing to install by hand."},
    }


def _check_write_access():
    ok, detail = True, "folder is writable"
    try:
        p = os.path.join(BASE, '.hearth-write-test')
        with open(p, 'w') as f:
            f.write('ok')
        os.remove(p)
    except Exception as e:
        ok, detail = False, str(e)[:120]
    return {
        "id": "write", "label": "Folder permissions", "ok": ok, "detail": detail,
        "why": "Hearth saves your settings and backups next to itself. If this "
               "folder is read-only nothing can be saved.",
        "fix": None if ok else {
            "kind": "note", "label": "Move the folder",
            "url": "",
            "note": "Move the Hearth folder somewhere like Documents. Program "
                    "Files is protected by Windows and blocks saving."},
    }


def doctor():
    """Everything Hearth needs, and plain-language help for whatever is missing."""
    checks = [_check_python(), _check_java(), _check_write_access(),
              _check_playit(), _check_server_files()]
    blocking = [c for c in checks if not c["ok"] and not c.get("optional")]
    optional = [c for c in checks if not c["ok"] and c.get("optional")]
    return {
        "version": APP_VERSION,
        "checks": checks,
        "ready": not blocking,
        "blocking": len(blocking),
        "suggestions": len(optional),
    }


# -------------------------------------------------------------------- updates
def _local_version():
    p = os.path.join(BASE, 'VERSION')
    if os.path.exists(p):
        try:
            return open(p, encoding='utf-8').read().strip() or APP_VERSION
        except Exception:
            pass
    return APP_VERSION


def check_update(timeout=8):
    """Ask GitHub what the current version is. Never downloads anything."""
    local = _local_version()
    try:
        remote = _get(VERSION_URL, timeout).decode('utf-8').strip()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "local": local,
                    "msg": "No version information published yet - nothing to "
                           "update to."}
        return {"ok": False, "local": local,
                "msg": "GitHub answered with an error (%s). Try again later." % e.code}
    except Exception:
        return {"ok": False, "local": local,
                "msg": "Could not reach GitHub. You are offline, or it is down."}
    if not re.match(r'^\d+(\.\d+)*$', remote):
        return {"ok": False, "local": local, "msg": "Version file looked wrong."}
    newer = _ver_tuple(remote) > _ver_tuple(local)
    notes = ""
    if newer:
        try:
            notes = _get(CHANGELOG_URL, timeout).decode('utf-8')[:4000]
        except Exception:
            notes = ""
    return {"ok": True, "local": local, "remote": remote, "update": newer,
            "notes": notes, "staged": is_staged(),
            "page": RELEASES_PAGE,
            "msg": ("Version %s is available." % remote) if newer
                   else "Hearth is up to date."}


def is_staged():
    try:
        if os.path.exists(STAGE_MARK):
            return json.load(open(STAGE_MARK, encoding='utf-8'))
    except Exception:
        pass
    return None


def stage_update(timeout=60):
    """
    Download the new version and put it aside. Nothing is replaced yet - the
    running panel has its own files open, so swapping them now would break it.
    The swap happens on the next start, which takes under a second.
    """
    info = check_update()
    if not info.get("ok"):
        return {"ok": False, "msg": info.get("msg", "Could not check for updates.")}
    if not info.get("update"):
        return {"ok": False, "msg": "Already up to date."}
    try:
        blob = _get(ZIP_URL, timeout)
    except Exception:
        return {"ok": False, "msg": "The download failed. Check your connection "
                                    "and try again."}
    try:
        shutil.rmtree(STAGE_DIR, ignore_errors=True)
        os.makedirs(STAGE_PAYLOAD, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = z.namelist()
            root = names[0].split('/')[0] + '/' if names else ''
            for n in names:
                if n.endswith('/') or not n.startswith(root):
                    continue
                rel = n[len(root):]
                top = rel.split('/')[0]
                if top in PROTECTED:
                    continue
                if top not in APP_FILES and top not in APP_DIRS:
                    continue
                dst = os.path.join(STAGE_PAYLOAD, *rel.split('/'))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with z.open(n) as src, open(dst, 'wb') as out:
                    shutil.copyfileobj(src, out)
    except Exception as e:
        shutil.rmtree(STAGE_DIR, ignore_errors=True)
        return {"ok": False, "msg": "The download was damaged (%s)." % str(e)[:80]}

    mark = {"version": info["remote"], "from": info["local"], "at": int(time.time())}
    with open(STAGE_MARK, 'w', encoding='utf-8') as f:
        json.dump(mark, f)
    return {"ok": True, "staged": mark,
            "msg": "Version %s is ready. Restart Hearth to finish."
                   % info["remote"]}


def apply_staged():
    """
    Called once at startup, before the panel opens. Swaps in anything that was
    downloaded last session and keeps a copy of what it replaced, so a bad
    update can be undone by hand.
    Returns a short message, or None if there was nothing to do.
    """
    mark = is_staged()
    if not mark or not os.path.isdir(STAGE_PAYLOAD):
        return None
    backup = os.path.join(STAGE_DIR, 'previous-%s' % mark.get("from", "unknown"))
    replaced = 0
    try:
        for r, _dirs, files in os.walk(STAGE_PAYLOAD):
            for fn in files:
                src = os.path.join(r, fn)
                rel = os.path.relpath(src, STAGE_PAYLOAD)
                top = rel.replace('\\', '/').split('/')[0]
                if top in PROTECTED:
                    continue
                dst = os.path.join(BASE, rel)
                if os.path.exists(dst):
                    bdst = os.path.join(backup, rel)
                    os.makedirs(os.path.dirname(bdst), exist_ok=True)
                    shutil.copy2(dst, bdst)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                replaced += 1
    except Exception as e:
        return "Update could not be applied (%s). Nothing was lost - the old " \
               "version is still running." % str(e)[:80]
    shutil.rmtree(STAGE_PAYLOAD, ignore_errors=True)
    try:
        os.remove(STAGE_MARK)
    except Exception:
        pass
    return "Updated to %s (%d files). The previous version is in .update/ if " \
           "anything looks wrong." % (mark.get("version", "?"), replaced)


# ------------------------------------------------------------- app window
# Hearth is a web app, but it should not feel like a browser tab. Edge and
# Chrome both have an "app mode" that opens a plain window with no address bar,
# its own taskbar button, and its own icon - so it minimises, alt-tabs and
# closes like any other program. Edge ships with Windows, so this works out of
# the box; if neither is here we fall back to the normal browser.
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _app_browser():
    for b in BROWSERS:
        if os.path.exists(b):
            return b
    for name in ('msedge', 'chrome'):
        p = shutil.which(name)
        if p:
            return p
    return None


def open_app_window(url, width=1280, height=880):
    """Open Hearth in its own window. Returns True if app mode was used."""
    exe = _app_browser()
    if exe:
        # A separate profile folder keeps this window out of the user's normal
        # browsing session, so closing their browser never closes Hearth.
        prof = os.path.join(BASE, '.window')
        try:
            subprocess.Popen(
                [exe, '--app=%s' % url,
                 '--window-size=%d,%d' % (width, height),
                 '--user-data-dir=%s' % prof,
                 '--no-first-run', '--no-default-browser-check'],
                creationflags=NO_WINDOW)
            return True
        except Exception:
            pass
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    return False


# -------------------------------------------------------------- shortcut
def _desktop():
    for p in (os.path.join(os.path.expanduser('~'), 'Desktop'),
              os.path.join(os.environ.get('USERPROFILE', ''), 'OneDrive', 'Desktop')):
        if os.path.isdir(p):
            return p
    return os.path.join(os.path.expanduser('~'), 'Desktop')


def shortcut_path():
    return os.path.join(_desktop(), 'Hearth.lnk')


def shortcut_status():
    return {"exists": os.path.exists(shortcut_path()), "path": shortcut_path()}


def make_shortcut():
    """
    Put a Hearth icon on the desktop. Uses pythonw so double-clicking it opens
    the panel with no console window behind it.
    """
    if os.name != 'nt':
        return {"ok": False, "msg": "Shortcuts are a Windows thing."}
    exe = sys.executable or ''
    # prefer pythonw so no black console window appears
    if exe.lower().endswith('python.exe'):
        w = exe[:-len('python.exe')] + 'pythonw.exe'
        if os.path.exists(w):
            exe = w
    if not exe or not os.path.exists(exe):
        return {"ok": False, "msg": "Could not work out which Python to point at."}
    target = os.path.join(BASE, 'app.py')
    icon = os.path.join(BASE, 'hearth.ico')
    lnk = shortcut_path()
    ps = (
        "$s=(New-Object -COM WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';"
        "$s.Arguments='\"%s\"';"
        "$s.WorkingDirectory='%s';"
        "$s.Description='Hearth - your Minecraft server panel';"
        "%s"
        "$s.Save()"
    ) % (lnk, exe, target, BASE,
         ("$s.IconLocation='%s,0';" % icon) if os.path.exists(icon) else "")
    try:
        p = subprocess.run(['powershell', '-NoProfile', '-NonInteractive',
                            '-Command', ps],
                           capture_output=True, text=True, timeout=20,
                           creationflags=NO_WINDOW)
    except Exception as e:
        return {"ok": False, "msg": "Windows would not make the shortcut (%s)."
                                    % str(e)[:70]}
    if not os.path.exists(lnk):
        err = (p.stderr or '').strip().splitlines()
        return {"ok": False, "msg": "Windows would not make the shortcut. %s"
                                    % (err[0][:110] if err else '')}
    return {"ok": True, "path": lnk, "msg": "Added Hearth to your desktop."}
