"""The guard on the panel's own HTTP server.

127.0.0.1 is not private: any page open in the browser can send requests here,
and a form posting text/plain does not even trigger a preflight. These are the
checks that stop another site starting, stopping and deleting worlds.
"""
import json
import threading
import urllib.error
import urllib.request

import pytest

import app


@pytest.fixture(scope='module')
def panel():
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    port = httpd.server_address[1]
    # the guard checks Host against the address the panel serves on
    app.ALLOWED_HOSTS.add('127.0.0.1:%d' % port)
    app.ALLOWED_ORIGINS.add('http://127.0.0.1:%d' % port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield 'http://127.0.0.1:%d' % port
    httpd.shutdown()


def call(url, method='GET', headers=None, body=None):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body or {}).encode() if method == 'POST' else None,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_the_panels_own_request_is_allowed(panel):
    code, _body = call(panel + '/api/state', headers={'Host': panel[7:]})
    assert code == 200


def test_a_post_from_another_site_is_refused(panel):
    """A form on evil.example posting text/plain: no preflight, browser sends
    it, and the body is still valid JSON on the way in."""
    code, body = call(panel + '/api/server/delete', 'POST',
                      {'Origin': 'https://evil.example', 'Content-Type': 'text/plain'},
                      {'name': 'anything'})
    assert code == 403
    assert 'another site' in body


def test_a_post_without_the_panels_header_is_refused(panel):
    code, body = call(panel + '/api/quit', 'POST', {'Content-Type': 'text/plain'}, {})
    assert code == 403
    assert 'did not come from the Hearth panel' in body


def test_a_post_with_the_panels_header_is_allowed(panel):
    code, body = call(panel + '/api/server/setactive', 'POST',
                      {'X-Hearth': '1', 'Content-Type': 'application/json'},
                      {'name': 'no-such-world'})
    assert code == 200
    assert json.loads(body)['ok'] is False      # rejected on its merits, not by the guard


def test_a_referer_from_another_site_is_refused(panel):
    code, body = call(panel + '/api/quit', 'POST',
                      {'X-Hearth': '1', 'Referer': 'https://evil.example/page'}, {})
    assert code == 403


def test_a_rebound_hostname_is_refused(panel):
    """DNS rebinding: evil.example resolves to 127.0.0.1, so the request
    arrives here - carrying its own name in Host, which is how we can tell."""
    code, body = call(panel + '/api/state', headers={'Host': 'evil.example'})
    assert code == 403
    assert 'localhost' in body


def test_a_null_origin_is_refused(panel):
    code, _body = call(panel + '/api/state',
                       headers={'Host': panel[7:], 'Origin': 'null'})
    assert code == 403


def test_the_ui_is_served_at_the_root(panel):
    code, body = call(panel + '/', headers={'Host': panel[7:]})
    assert code == 200
    assert '<html' in body.lower()
