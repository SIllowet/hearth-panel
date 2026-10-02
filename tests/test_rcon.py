"""RCON: how the panel still reaches a world it did not start.

A small fake RCON server stands in for Minecraft, speaking the same protocol.
"""
import socket
import struct
import threading

import pytest

import app


class FakeRcon:
    """Answers logins and echoes commands back, the way Minecraft does."""

    def __init__(self, password='hunter2', hang_up_on=None):
        self.password, self.hang_up_on = password, hang_up_on
        self.commands = []
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _read(self, conn):
        (n,) = struct.unpack('<i', app._recv_exact(conn, 4))
        data = app._recv_exact(conn, n)
        rid, kind = struct.unpack('<ii', data[:8])
        return rid, kind, data[8:-2].decode()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    rid, kind, body = self._read(conn)
                    ok = kind == app.RCON_LOGIN and body == self.password
                    conn.sendall(app._rcon_packet(rid if ok else -1, 2, ''))
                    if not ok:
                        continue
                    rid, kind, body = self._read(conn)
                    self.commands.append(body)
                    if body == self.hang_up_on:
                        continue
                    conn.sendall(app._rcon_packet(rid, 0, 'did: ' + body))
                except Exception:
                    pass

    def close(self):
        self.sock.close()


@pytest.fixture
def fake():
    f = FakeRcon()
    yield f
    f.close()


def world_with_rcon(tmp_path, name, port, password='hunter2', game_port=None):
    path = tmp_path / name
    path.mkdir()
    game_port = game_port or port
    (path / 'server.properties').write_text(
        'server-port=%d\nenable-rcon=true\nrcon.port=%d\nrcon.password=%s\n'
        % (game_port, port, password))
    app.CONFIG['servers'] = [{'name': name, 'path': str(path), 'type': 'vanilla'}]


def test_a_command_goes_through_and_the_answer_comes_back(fake):
    assert app.rcon(fake.port, 'hunter2', 'list') == 'did: list'
    assert fake.commands == ['list']


def test_a_wrong_password_is_refused(fake):
    with pytest.raises(PermissionError):
        app.rcon(fake.port, 'nope', 'list')
    assert fake.commands == []


def test_stop_that_hangs_up_before_answering_still_counts():
    f = FakeRcon(hang_up_on='stop')
    try:
        assert app.rcon(f.port, 'hunter2', 'stop') is None
        assert f.commands == ['stop']
    finally:
        f.close()


def test_the_console_reaches_a_world_the_panel_did_not_start(tmp_path, fake):
    world_with_rcon(tmp_path, 'Adopted', fake.port)
    ok, _ = app.send_command('Adopted', '/say hi')
    assert ok
    assert fake.commands == ['say hi']             # the slash is Minecraft's chat habit, not RCON's
    assert 'did: say hi' in app.proc_for('Adopted').log


def test_stopping_an_adopted_world_asks_instead_of_killing_it(tmp_path, fake, monkeypatch):
    """The bug this pins down: with no pipe into the console, Stop used to
    taskkill /F the server, losing everything since its last autosave."""
    world_with_rcon(tmp_path, 'Orphan', fake.port)
    monkeypatch.setattr(app, 'auto_backup', lambda *a: None)
    killed = []
    monkeypatch.setattr(app.subprocess, 'run', lambda *a, **k: killed.append(a))
    ok, msg = app.stop_server('Orphan')
    assert ok
    assert fake.commands == ['stop']
    assert killed == []
    assert 'force' not in msg


def test_save_before_backing_up_an_adopted_world(tmp_path, fake):
    world_with_rcon(tmp_path, 'SaveMe', fake.port)
    assert app.flush_world('SaveMe', wait=0) is True
    assert fake.commands == ['save-all flush']


# ------------------------------------------------------------ switching it on

def plain_world(tmp_path, name, props='server-port=25610\n', **extra):
    path = tmp_path / name
    path.mkdir()
    (path / 'server.properties').write_text(props)
    server = dict({'name': name, 'path': str(path), 'type': 'vanilla'}, **extra)
    app.CONFIG.setdefault('servers', []).append(server)
    return path


def test_rcon_is_switched_on_with_a_long_random_password(tmp_path):
    app.CONFIG['servers'] = []
    plain_world(tmp_path, 'Fresh')
    app.ensure_rcon('Fresh')
    p = app.read_properties('Fresh')
    assert p['enable-rcon'] == 'true'
    assert len(p['rcon.password']) >= 30
    assert p['rcon.port'].isdigit()
    assert p['server-port'] == '25610'                 # nothing else disturbed


def test_rcon_someone_set_up_themselves_is_left_alone(tmp_path):
    app.CONFIG['servers'] = []
    plain_world(tmp_path, 'Theirs',
                'server-port=25611\nenable-rcon=true\nrcon.port=30000\nrcon.password=mine\n')
    app.ensure_rcon('Theirs')
    p = app.read_properties('Theirs')
    assert (p['rcon.port'], p['rcon.password']) == ('30000', 'mine')


def test_a_world_can_opt_out(tmp_path):
    app.CONFIG['servers'] = []
    plain_world(tmp_path, 'NoRcon', rcon=False)
    app.ensure_rcon('NoRcon')
    assert 'enable-rcon' not in app.read_properties('NoRcon')


def test_the_rcon_port_does_not_collide_with_other_worlds(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'port_listening', lambda *a, **k: False)
    app.CONFIG['servers'] = []
    plain_world(tmp_path, 'One', 'server-port=25575\n')
    plain_world(tmp_path, 'Two', 'server-port=25565\nenable-rcon=true\nrcon.port=25576\nrcon.password=x\n')
    monkeypatch.setitem(app.CONFIG, 'bank', [{'port': 25577}])
    plain_world(tmp_path, 'Three', 'server-port=25578\n')
    assert app.free_rcon_port('Three') == 25579
