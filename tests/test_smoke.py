"""Does the thing actually start, and do the endpoints answer?

Cheap, but it is the test that catches an import-time mistake before anyone
double-clicks start-panel.bat and gets a black window.
"""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import app


@pytest.fixture(scope='module')
def panel():
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    port = httpd.server_address[1]
    app.ALLOWED_HOSTS.add('127.0.0.1:%d' % port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield 'http://127.0.0.1:%d' % port
    httpd.shutdown()


def get(url, host):
    req = urllib.request.Request(url, headers={'Host': host})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


@pytest.mark.parametrize('path', [
    '/api/state', '/api/bank', '/api/doctor', '/api/ai', '/api/shortcut',
    '/api/playit', '/api/backups?name=nope', '/api/log?name=nope',
    '/api/properties?name=nope', '/api/mods?name=nope',
])
def test_read_endpoints_answer(panel, path):
    """None of these should need the network, a world, or Windows."""
    body = get(panel + path, panel[7:])
    assert isinstance(body, dict)


def test_an_unknown_endpoint_says_so(panel):
    import urllib.error
    req = urllib.request.Request(panel + '/api/nonsense', method='POST',
                                 data=b'{}', headers={'Host': panel[7:], 'X-Hearth': '1'})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 404


def test_the_ui_folder_cannot_be_escaped(panel):
    """config.json holds the tunnel secret and lives one level up."""
    import urllib.error
    for path in ('/../config.json', '/..%2fconfig.json', '/ui/../../config.json'):
        req = urllib.request.Request(panel + path, headers={'Host': panel[7:]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                assert 'secret' not in r.read().decode(errors='replace')
        except urllib.error.HTTPError as err:
            assert err.code == 404
