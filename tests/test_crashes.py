"""Telling a crash from a stop, saying why, and bringing the world back.

Nothing here runs Minecraft. A world's process is stood in for by a few lines
of Python that print what a real server would and then exit.
"""
import subprocess
import sys
import threading

import app


def fake_world(tmp_path, name, **extra):
    path = tmp_path / name
    path.mkdir()
    (path / 'server.properties').write_text('server-port=25598\n')
    server = dict({'name': name, 'path': str(path), 'type': 'vanilla'}, **extra)
    app.CONFIG['servers'] = [server]
    return server


def run_fake(name, lines, code=0, ready=False):
    """Launch a stand-in server that prints `lines` and exits, and follow it
    to the end exactly as the panel follows a real one."""
    script = 'import sys\nfor l in %r: print(l)\nsys.exit(%d)' % (lines, code)
    mp = app.proc_for(name)
    mp.p = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    mp.ready = ready
    app._run_world(name, mp, mp.p, None)
    return mp


DONE = '[12:00:00] [Server thread/INFO]: Done (3.2s)! For help, type "help"'


# ------------------------------------------------------------------ the reasons

def test_a_java_that_is_too_old_is_named_with_the_version_it_needs():
    d = app.diagnose_crash([
        'Exception in thread "main" java.lang.UnsupportedClassVersionError: '
        'net/minecraft/server/Main has been compiled by a more recent version of the '
        'Java Runtime (class file version 65.0), this version of the Java Runtime only '
        'recognizes class file versions up to 61.0'])
    assert 'Java 21' in d['why']
    assert d['retry'] is False


def test_running_out_of_memory_is_worth_a_restart():
    d = app.diagnose_crash(['java.lang.OutOfMemoryError: Java heap space'])
    assert 'memory' in d['why']
    assert d['retry'] is True


def test_a_port_someone_else_holds_is_not_worth_a_restart():
    d = app.diagnose_crash(['[Server thread/WARN]: **** FAILED TO BIND TO PORT!'])
    assert 'port' in d['why']
    assert d['retry'] is False


def test_a_mod_that_does_not_fit_is_named():
    d = app.diagnose_crash([
        '[main/ERROR]: Incompatible mods found!',
        " - Mod 'Waystones' (waystones) 21.1.4 requires version 21.1.0 or later of balm, which is missing!",
    ])
    assert 'Waystones' in d['why']
    assert d['retry'] is False


def test_the_crash_report_file_is_pointed_at():
    d = app.diagnose_crash([
        '[Server thread/ERROR]: Encountered an unexpected exception',
        '[Server thread/ERROR]: This crash report has been saved to: '
        'C:\\Servers\\W\\crash-reports\\crash-2026-10-02_12.00.00-server.txt',
    ])
    assert 'crash-2026-10-02_12.00.00-server.txt' in d['fix']
    assert d['retry'] is True


def test_silence_still_gets_an_answer():
    d = app.diagnose_crash([])
    assert d['why'] and d['fix']


# ------------------------------------------------------------- crash or stop?

def test_a_world_that_dies_by_itself_is_a_crash(tmp_path):
    fake_world(tmp_path, 'CrashBoot')
    mp = run_fake('CrashBoot', ['java.lang.OutOfMemoryError: Java heap space'], code=1)
    assert mp.crash is not None
    assert mp.crash['code'] == 1
    assert 'memory' in mp.crash['why']
    assert any('stopped on its own' in l for l in mp.log)


def test_stop_typed_in_game_is_not_a_crash(tmp_path):
    """An op typing /stop logs this line, and the panel never sees the
    command go through its own pipe."""
    fake_world(tmp_path, 'OpStop')
    mp = run_fake('OpStop', [DONE, '[Server thread/INFO]: Stopping the server'], ready=True)
    assert mp.crash is None


def test_stop_from_the_panel_is_not_a_crash(tmp_path):
    fake_world(tmp_path, 'PanelStop')
    mp = app.proc_for('PanelStop')
    mp.stopping = True
    app.on_world_exit('PanelStop', mp, mp.p, 0)
    assert mp.crash is None


def test_stop_typed_in_the_panel_console_is_not_a_crash(tmp_path):
    fake_world(tmp_path, 'ConsoleStop')
    mp = app.proc_for('ConsoleStop')
    mp.p = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.readline()'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    ok, _ = app.send_command('ConsoleStop', '/stop')
    assert ok and mp.stopping
    mp.p.wait(timeout=10)


# ------------------------------------------------------------------ restarting

def watch_restarts(monkeypatch):
    started = threading.Event()
    calls = []

    def fake_start(name, auto=False):
        calls.append((name, auto))
        started.set()
        return True, 'ok'
    monkeypatch.setattr(app, 'start_server', fake_start)
    monkeypatch.setattr(app, 'CRASH_RESTART_DELAY', 0.05)
    return started, calls


def test_a_crash_while_people_play_brings_it_back(tmp_path, monkeypatch):
    fake_world(tmp_path, 'Comeback')
    started, calls = watch_restarts(monkeypatch)
    mp = run_fake('Comeback', [DONE, 'Exception in server tick loop'], code=1, ready=True)
    assert mp.crash['restarting'] is True
    assert started.wait(5)
    assert calls == [('Comeback', True)]


def test_a_world_that_never_finished_booting_is_left_off(tmp_path, monkeypatch):
    """It would only fail the same way again."""
    fake_world(tmp_path, 'NeverBooted')
    started, calls = watch_restarts(monkeypatch)
    mp = run_fake('NeverBooted', ['Exception in server tick loop'], code=1, ready=False)
    assert mp.crash['restarting'] is False
    assert not started.wait(0.3)


def test_restarting_can_be_switched_off(tmp_path, monkeypatch):
    fake_world(tmp_path, 'NoComeback', autoRestart=False)
    started, calls = watch_restarts(monkeypatch)
    mp = run_fake('NoComeback', [DONE, 'Exception in server tick loop'], code=1, ready=True)
    assert mp.crash['restarting'] is False
    assert not started.wait(0.3)


def test_it_gives_up_after_too_many_crashes(tmp_path, monkeypatch):
    fake_world(tmp_path, 'Crashy')
    monkeypatch.setattr(app, 'start_server', lambda name, auto=False: (True, 'ok'))
    monkeypatch.setattr(app, 'CRASH_RESTART_DELAY', 60)       # never fires in the test
    mp = app.proc_for('Crashy')
    for _ in range(app.CRASH_RESTART_LIMIT):
        mp.ready = True
        app.on_world_exit('Crashy', mp, mp.p, 1)
        assert mp.crash['restarting'] is True
    mp.ready = True
    app.on_world_exit('Crashy', mp, mp.p, 1)
    assert mp.crash['gaveUp'] is True
    assert mp.crash['restarting'] is False
    mp.restart_token += 1                                      # call off the timers


def test_stop_during_the_countdown_calls_the_restart_off(tmp_path, monkeypatch):
    fake_world(tmp_path, 'CalledOff')
    started, calls = watch_restarts(monkeypatch)
    monkeypatch.setattr(app, 'CRASH_RESTART_DELAY', 0.5)
    mp = app.proc_for('CalledOff')
    mp.ready = True
    app.on_world_exit('CalledOff', mp, mp.p, 1)
    ok, msg = app.stop_server('CalledOff')
    assert ok and 'Called off' in msg
    assert not started.wait(1.0)


def test_the_panel_can_see_the_crash(tmp_path):
    fake_world(tmp_path, 'Visible')
    run_fake('Visible', ['java.lang.OutOfMemoryError: Java heap space'], code=1)
    w = next(s for s in app.build_state()['servers'] if s['name'] == 'Visible')
    assert w['crash'] and 'memory' in w['crash']['why']
    assert w['autoRestart'] is True
    app.clear_crash('Visible')
    w = next(s for s in app.build_state()['servers'] if s['name'] == 'Visible')
    assert w['crash'] is None
