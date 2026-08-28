"""Filenames off the internet, properties round-tripping, and config writes."""
import json
import os

import app
import hearth_setup


# ------------------------------------------------------------------ filenames
def test_a_plain_filename_survives():
    assert app.safe_jar_name('fabric-api-0.158.0.jar') == 'fabric-api-0.158.0.jar'


def test_an_extension_is_added_when_missing():
    assert app.safe_jar_name('somemod').endswith('.jar')


def test_a_download_url_gives_up_only_its_last_segment():
    assert app.safe_jar_name('https://cdn.example/files/1/2/cool-mod.jar?token=x') == 'cool-mod.jar'


def test_backslashes_cannot_walk_out_of_the_mods_folder():
    """The one that mattered on Windows: a backslash survives a split on '/',
    so the name lands somewhere else entirely."""
    for nasty in (r'a\..\..\evil.jar',
                  r'..\..\..\Windows\System32\evil.jar',
                  '../../evil.jar',
                  '....//evil.jar'):
        out = app.safe_jar_name(nasty)
        assert os.sep not in out and '/' not in out and '\\' not in out
        assert '..' not in out.replace('...', '')
        assert os.path.dirname(os.path.join('/mods', out)) == '/mods'


def test_a_name_made_only_of_dots_falls_back():
    assert app.safe_jar_name('...') == 'mod.jar'
    assert app.safe_jar_name('') == 'mod.jar'


# ------------------------------------------------------------------ properties
def test_properties_round_trip(tmp_path):
    path = tmp_path / 'World'
    path.mkdir()
    (path / 'server.properties').write_text(
        '#comment\nmotd=A Minecraft Server\npvp=true\nlevel-name=world\n')
    app.CONFIG['servers'] = [{'name': 'World', 'path': str(path), 'type': 'vanilla'}]

    props = app.read_properties('World')
    assert props['motd'] == 'A Minecraft Server'
    assert props['pvp'] == 'true'

    ok, _msg = app.write_properties('World', {'pvp': 'false', 'max-players': '8'})
    assert ok
    after = app.read_properties('World')
    assert after['pvp'] == 'false'
    assert after['max-players'] == '8'
    assert after['motd'] == 'A Minecraft Server'      # untouched keys stay


# ------------------------------------------------------------------ config
def test_config_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    target = tmp_path / 'config.json'
    monkeypatch.setattr(app, 'CONFIG_PATH', str(target))
    app.save_config({'servers': [], 'active': None})
    assert json.loads(target.read_text()) == {'servers': [], 'active': None}
    assert not (tmp_path / 'config.json.tmp').exists()


# ------------------------------------------------------------------ update sums
def test_checksums_are_parsed_the_way_sha256sum_writes_them():
    sums = hearth_setup.parse_sums(
        "# a comment\n"
        "%s  app.py\n"
        "%s  ui/index.html\n" % ('a' * 64, 'b' * 64))
    assert sums == {'app.py': 'a' * 64, 'ui/index.html': 'b' * 64}


def test_a_rubbish_line_is_ignored():
    assert hearth_setup.parse_sums("not a checksum at all\nzz  app.py\n") == {}


def test_a_changed_file_is_caught(tmp_path):
    (tmp_path / 'app.py').write_text('the real thing')
    good = hearth_setup._sha256(str(tmp_path / 'app.py'))
    assert hearth_setup.verify_tree(str(tmp_path), {'app.py': good}) == []

    (tmp_path / 'app.py').write_text('something else')
    bad = hearth_setup.verify_tree(str(tmp_path), {'app.py': good})
    assert bad and 'does not match' in bad[0]


def test_a_file_nobody_published_is_caught(tmp_path):
    """An extra file smuggled into the zip is not 'fine because unlisted'."""
    (tmp_path / 'surprise.py').write_text('hello')
    bad = hearth_setup.verify_tree(str(tmp_path), {})
    assert bad and 'not in the published list' in bad[0]


def test_the_published_checksums_match_the_code_in_this_repo():
    """SHA256SUMS is what the updater checks a download against, so a release
    with a stale one cannot be installed. Regenerate: python tools/make_sums.py"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sums = hearth_setup.parse_sums(open(os.path.join(base, 'SHA256SUMS'), encoding='utf-8').read())
    for name in hearth_setup.APP_FILES:
        p = os.path.join(base, name)
        if os.path.isfile(p):
            assert sums.get(name) == hearth_setup._sha256(p), \
                "%s changed - run python tools/make_sums.py" % name
