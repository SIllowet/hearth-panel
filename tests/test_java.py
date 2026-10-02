"""Picking which Java to start a world with."""
import app
import hearth_setup


def installed(monkeypatch, versions):
    """Pretend these Javas are on the PC: {path: major version}."""
    monkeypatch.setattr(hearth_setup, 'java_candidates', lambda: list(versions))
    monkeypatch.setattr(app, 'java_major', lambda exe=None: versions.get(exe, 0))


def test_the_oldest_java_that_is_new_enough_wins(monkeypatch):
    installed(monkeypatch, {'j8': 8, 'j25': 25, 'j17': 17, 'j21': 21})
    assert app.find_java(17) == 'j17'
    assert app.find_java(21) == 'j21'
    assert app.find_java(8) == 'j8'


def test_with_no_requirement_the_newest_wins(monkeypatch):
    """Sorting folder names as text puts jdk-8 after jdk-21. Versions, not names."""
    installed(monkeypatch, {'jdk-8': 8, 'jdk-21': 21, 'jdk-17': 17})
    assert app.find_java(0) == 'jdk-21'


def test_when_nothing_is_new_enough_the_newest_is_offered(monkeypatch):
    installed(monkeypatch, {'j8': 8, 'j17': 17})
    assert app.find_java(21) == 'j17'


def test_with_no_java_at_all_it_falls_back_to_path(monkeypatch):
    installed(monkeypatch, {})
    assert app.find_java(21) == 'java'


def test_java_8_is_not_mistaken_for_java_1():
    assert hearth_setup.parse_java_version('java version "1.8.0_392"') == 8
    assert hearth_setup.parse_java_version('openjdk version "21.0.4" 2024-07-16 LTS') == 21
    assert hearth_setup.parse_java_version('openjdk version "17" 2021-09-14') == 17
    assert hearth_setup.parse_java_version('nonsense') == 0


def test_offline_the_requirement_comes_from_what_mojang_published(monkeypatch):
    monkeypatch.setattr(app, 'required_java', lambda v: 0)
    assert app.java_need('1.16.5') == 8
    assert app.java_need('1.17.1') == 16
    assert app.java_need('1.20.4') == 17
    assert app.java_need('1.20.5') == 21
    assert app.java_need('1.21.4') == 21
    assert app.java_need('24w14a') == 0               # snapshots: no guess


def test_a_world_will_not_start_on_a_java_it_cannot_run_on(tmp_path, monkeypatch):
    path = tmp_path / 'Modern'
    path.mkdir()
    (path / 'server.jar').write_bytes(b'')
    (path / 'server.properties').write_text('server-port=25612\n')
    app.CONFIG['servers'] = [{'name': 'Modern', 'path': str(path), 'type': 'vanilla',
                              'version': '1.21.4'}]
    monkeypatch.setattr(app, 'java_need', lambda v: 21)
    installed(monkeypatch, {'j17': 17})
    launched = []
    monkeypatch.setattr(app.subprocess, 'Popen', lambda *a, **k: launched.append(a))
    ok, msg = app.start_server('Modern')
    assert not ok
    assert 'Java 21' in msg and '17' in msg
    assert launched == []
