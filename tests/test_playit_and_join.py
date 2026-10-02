"""Letting friends in: linking playit, finding each world's address, and the
addresses Home hands out.

Nothing here talks to playit.gg. A small local server stands in for its API
where the HTTP itself is under test; elsewhere the call is replaced outright.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import app
import hearth_setup

SECRET = 'a' * 64


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    monkeypatch.setitem(app.CONFIG, 'servers', [])
    monkeypatch.delitem(app.CONFIG, 'playit', raising=False)
    monkeypatch.setattr(app, 'save_config', lambda c: None)
    app.playit_claim_cancel()
    yield
    app.playit_claim_cancel()


def world(tmp_path, name, port, **extra):
    path = tmp_path / name
    path.mkdir()
    (path / 'server.properties').write_text('server-port=%d\n' % port)
    s = dict({'name': name, 'path': str(path), 'type': 'vanilla'}, **extra)
    app.CONFIG['servers'].append(s)
    return s


# ---------------------------------------------------------------- the secret

def test_playit_can_be_linked_before_there_is_a_world():
    ok, _ = app.set_tunnel_secret(SECRET)
    assert ok
    assert app.account_tunnel()['secret'] == SECRET


def test_a_secret_kept_on_a_world_by_an_older_hearth_still_counts(tmp_path):
    world(tmp_path, 'Old', 25565, tunnel={'secret': SECRET, 'address': 'x.joinmc.link'})
    assert app.account_tunnel()['secret'] == SECRET


def test_relinking_replaces_the_old_secret_everywhere(tmp_path):
    w = world(tmp_path, 'Old', 25565, tunnel={'secret': 'b' * 64})
    app.set_tunnel_secret(SECRET)
    assert app.account_tunnel()['secret'] == SECRET
    assert w['tunnel']['secret'] == SECRET


def test_something_that_is_not_a_secret_is_refused():
    ok, msg = app.set_tunnel_secret('hello')
    assert not ok and 'playit secret' in msg


# ----------------------------------------------------------------- the claim

def fake_playit(monkeypatch, answers):
    """Replace playit's API with scripted answers, one list per path."""
    calls = []

    def call(path, body, secret=None, timeout=15):
        calls.append((path, body, secret))
        queue = answers[path]
        return queue.pop(0) if len(queue) > 1 else queue[0]
    monkeypatch.setattr(app, '_playit_call', call)
    return calls


def test_approving_in_the_browser_links_the_account(monkeypatch):
    calls = fake_playit(monkeypatch, {
        '/claim/setup': [('success', 'WaitingForUserVisit'), ('success', 'WaitingForUser'),
                         ('success', 'UserAccepted')],
        '/claim/exchange': [('fail', 'NotAccepted'), ('success', {'secret_key': SECRET})],
    })
    synced = []
    monkeypatch.setattr(app, 'playit_sync', lambda: synced.append(1) or (True, ''))
    app.CLAIM.update(code='abc123', state='waiting')
    app._claim_worker('abc123', poll=0)
    assert app.CLAIM['state'] == 'done'
    assert app.account_tunnel()['secret'] == SECRET
    assert synced == [1]                                # and it went looking for addresses
    setup_body = calls[0][1]
    assert setup_body['code'] == 'abc123' and setup_body['agent_type'] == 'self-managed'


def test_turning_it_down_on_playit_says_so(monkeypatch):
    fake_playit(monkeypatch, {'/claim/setup': [('success', 'UserRejected')]})
    app.CLAIM.update(code='abc123', state='waiting')
    app._claim_worker('abc123', poll=0)
    assert app.CLAIM['state'] == 'failed'
    assert 'turned down' in app.CLAIM['msg']
    assert app.account_tunnel() is None


def test_an_expired_link_says_so(monkeypatch):
    fake_playit(monkeypatch, {'/claim/setup': [('fail', 'CodeExpired')]})
    app.CLAIM.update(code='abc123', state='waiting')
    app._claim_worker('abc123', poll=0)
    assert app.CLAIM['state'] == 'failed'
    assert 'CodeExpired' in app.CLAIM['msg']


def test_cancelling_stops_the_wait(monkeypatch):
    seen = []

    def call(path, body, secret=None, timeout=15):
        seen.append(path)
        app.playit_claim_cancel()                       # they pressed Cancel meanwhile
        return 'success', 'WaitingForUserVisit'
    monkeypatch.setattr(app, '_playit_call', call)
    app.CLAIM.update(code='abc123', state='waiting')
    app._claim_worker('abc123', poll=0)
    assert seen == ['/claim/setup']
    assert app.CLAIM['state'] == 'idle'
    assert app.account_tunnel() is None


def test_starting_a_claim_gives_a_playit_link(monkeypatch):
    monkeypatch.setattr(app, '_claim_worker', lambda code, poll=2.0: None)
    r = app.playit_claim_start()
    assert r['url'].startswith('https://playit.gg/claim/')
    assert app.CLAIM['state'] == 'waiting'


# ------------------------------------------------------- finding the address

def tunnel(address, local_port=None, kind='minecraft-java', off=None):
    fields = [{'name': 'local_port', 'value': str(local_port)}] if local_port else []
    return {'display_address': address, 'tunnel_type': kind, 'name': 'mc',
            'agent_config': {'fields': fields}, 'disabled_reason': off}


def rundata(*tunnels, pending=()):
    return ('success', {'agent_id': 'x', 'tunnels': list(tunnels), 'pending': list(pending),
                        'notices': [], 'permissions': {'account_status': 'ready'}})


def test_each_world_gets_the_address_pointed_at_its_port(tmp_path, monkeypatch):
    app.set_tunnel_secret(SECRET)
    one = world(tmp_path, 'One', 25565)
    two = world(tmp_path, 'Two', 25566)
    calls = fake_playit(monkeypatch, {'/v1/agents/rundata': [rundata(
        tunnel('first.gl.joinmc.link', 25565),
        tunnel('second.gl.joinmc.link', 25566))]})
    ok, msg = app.playit_sync()
    assert ok
    assert one['tunnel']['address'] == 'first.gl.joinmc.link'
    assert two['tunnel']['address'] == 'second.gl.joinmc.link'
    assert calls[0][2] == SECRET                        # asked as this PC's agent


def test_a_minecraft_tunnel_with_no_port_given_means_the_usual_one(tmp_path, monkeypatch):
    app.set_tunnel_secret(SECRET)
    w = world(tmp_path, 'Main', 25565)
    fake_playit(monkeypatch, {'/v1/agents/rundata': [rundata(tunnel('main.gl.joinmc.link'))]})
    app.playit_sync()
    assert w['tunnel']['address'] == 'main.gl.joinmc.link'


def test_an_address_typed_in_by_hand_is_left_alone(tmp_path, monkeypatch):
    app.set_tunnel_secret(SECRET)
    w = world(tmp_path, 'Custom', 25565)
    app.set_meta('Custom', None, 'play.mydomain.net')
    fake_playit(monkeypatch, {'/v1/agents/rundata': [rundata(tunnel('auto.gl.joinmc.link', 25565))]})
    app.playit_sync()
    assert w['tunnel']['address'] == 'play.mydomain.net'


def test_an_address_hearth_filled_in_is_kept_up_to_date(tmp_path, monkeypatch):
    app.set_tunnel_secret(SECRET)
    w = world(tmp_path, 'Moved', 25565, tunnel={'address': 'old.gl.joinmc.link', 'auto': True})
    fake_playit(monkeypatch, {'/v1/agents/rundata': [rundata(tunnel('new.gl.joinmc.link', 25565))]})
    app.playit_sync()
    assert w['tunnel']['address'] == 'new.gl.joinmc.link'


def test_a_switched_off_tunnel_is_not_handed_out(tmp_path, monkeypatch):
    app.set_tunnel_secret(SECRET)
    w = world(tmp_path, 'Off', 25565)
    fake_playit(monkeypatch, {'/v1/agents/rundata': [rundata(
        tunnel('off.gl.joinmc.link', 25565, off='ByUser'))]})
    app.playit_sync()
    assert not w['tunnel'].get('address')
    assert app.PLAYIT_INFO['missing'] == [{'name': 'Off', 'port': 25565}]


def test_a_world_with_no_tunnel_is_listed_as_needing_one(tmp_path, monkeypatch):
    app.set_tunnel_secret(SECRET)
    world(tmp_path, 'Has', 25565)
    world(tmp_path, 'Lacks', 25567)
    fake_playit(monkeypatch, {'/v1/agents/rundata': [rundata(tunnel('has.gl.joinmc.link', 25565))]})
    app.playit_sync()
    assert app.PLAYIT_INFO['missing'] == [{'name': 'Lacks', 'port': 25567}]


def test_an_unrecognised_key_says_so(tmp_path, monkeypatch):
    app.set_tunnel_secret(SECRET)
    fake_playit(monkeypatch, {'/v1/agents/rundata': [('error', {'type': 'auth'})]})
    ok, msg = app.playit_sync()
    assert not ok and 'Link it again' in msg


def test_without_playit_there_is_nothing_to_look_up():
    ok, msg = app.playit_sync()
    assert not ok and 'Not linked' in msg


# --------------------------------------------- the HTTP, against a stand-in

class FakeApi(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeApi.seen.append((self.path, self.headers.get('Authorization'), body))
        if self.headers.get('Authorization') != 'Agent-Key ' + SECRET:
            code, out = 401, {'status': 'error', 'data': {'type': 'auth'}}
        else:
            code, out = 200, {'status': 'success', 'data': {'tunnels': []}}
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_api(monkeypatch):
    srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(app, 'PLAYIT_API', 'http://127.0.0.1:%d' % srv.server_address[1])
    FakeApi.seen = []
    yield
    srv.shutdown()


def test_the_agent_key_goes_in_the_header_playit_expects(fake_api):
    st, data = app._playit_call('/v1/agents/rundata', {}, secret=SECRET)
    assert st == 'success' and data == {'tunnels': []}
    assert FakeApi.seen == [('/v1/agents/rundata', 'Agent-Key ' + SECRET, {})]


def test_a_refusal_is_read_rather_than_raised(fake_api):
    st, data = app._playit_call('/v1/agents/rundata', {}, secret='wrong' * 10)
    assert st == 'error'


# ---------------------------------------------------- the door opens itself

def test_lighting_a_world_opens_the_public_door(tmp_path, monkeypatch):
    import subprocess
    import sys
    w = world(tmp_path, 'Lit', 25640, version='1.21.4')
    (tmp_path / 'Lit' / 'server.jar').write_bytes(b'')
    app.set_tunnel_secret(SECRET)
    monkeypatch.setattr(app, 'java_need', lambda v: 21)
    monkeypatch.setattr(app, 'find_java', lambda need=0: 'java')
    monkeypatch.setattr(app, 'java_major', lambda exe=None: 21)
    real_popen = subprocess.Popen
    monkeypatch.setattr(app.subprocess, 'Popen',
                        lambda args, **kw: real_popen([sys.executable, '-c', 'pass'], **kw))
    opened = []
    monkeypatch.setattr(app, 'tunnel_alive', lambda: False)
    monkeypatch.setattr(app, 'start_tunnel', lambda name=None: opened.append(name) or (True, ''))
    ok, msg = app.start_server('Lit')
    assert ok
    assert opened == ['Lit']
    assert 'public door' in msg
    app.proc_for('Lit').stopping = True


def test_without_playit_lighting_a_world_leaves_the_door_alone(tmp_path, monkeypatch):
    import subprocess
    import sys
    world(tmp_path, 'Quiet', 25641, version='1.21.4')
    (tmp_path / 'Quiet' / 'server.jar').write_bytes(b'')
    monkeypatch.setattr(app, 'java_need', lambda v: 21)
    monkeypatch.setattr(app, 'find_java', lambda need=0: 'java')
    monkeypatch.setattr(app, 'java_major', lambda exe=None: 21)
    real_popen = subprocess.Popen
    monkeypatch.setattr(app.subprocess, 'Popen',
                        lambda args, **kw: real_popen([sys.executable, '-c', 'pass'], **kw))
    opened = []
    monkeypatch.setattr(app, 'start_tunnel', lambda name=None: opened.append(name) or (True, ''))
    ok, msg = app.start_server('Quiet')
    assert ok and opened == []
    app.proc_for('Quiet').stopping = True


# ------------------------------------------------- recommending, addresses

def test_setup_recommends_without_tracing_the_network(monkeypatch):
    def no(*a, **k):
        raise AssertionError('the quick answer should not look at the network')
    monkeypatch.setattr(hearth_setup, '_hops', no)
    monkeypatch.setattr(hearth_setup, '_public_ip', no)
    monkeypatch.setattr(hearth_setup, 'tailscale_ip', lambda: '')
    for aud, pick in (('house', 'local'), ('friends', 'tailscale'), ('anyone', 'playit')):
        r = hearth_setup.network_probe(aud, quick=True)
        assert r['recommend']['pick'] == pick
        assert r['ran'] is False


def test_the_tailscale_address_is_read_from_tailscale(monkeypatch):
    monkeypatch.setattr(hearth_setup, '_tailscale_exe', lambda: 'tailscale')
    monkeypatch.setattr(hearth_setup, '_run', lambda cmd, timeout=6: '100.101.102.103\n')
    assert hearth_setup.tailscale_ip() == '100.101.102.103'
    monkeypatch.setattr(hearth_setup, '_run', lambda cmd, timeout=6: 'failed to connect\n')
    assert hearth_setup.tailscale_ip() == ''
    monkeypatch.setattr(hearth_setup, '_tailscale_exe', lambda: None)
    assert hearth_setup.tailscale_ip() == ''


def test_home_is_told_how_this_pc_can_be_reached(monkeypatch):
    monkeypatch.setattr(hearth_setup, 'local_ip', lambda: '192.168.1.20')
    monkeypatch.setattr(hearth_setup, 'tailscale_ip', lambda: '100.70.0.5')
    monkeypatch.setitem(app._reach_cache, 't', 0)
    reach = app.build_state()['reach']
    assert reach == {'lan': '192.168.1.20', 'tailscale': '100.70.0.5'}
