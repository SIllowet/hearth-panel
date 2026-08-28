"""Backup and restore.

The failure this guards against is not an exception - it is a backup that
succeeds while quietly leaving out half of somebody's world.
"""
import os
import zipfile

import app


def make_world(tmp_path, name='TestWorld', level='world', paper=False):
    path = tmp_path / name
    (path / level / 'region').mkdir(parents=True)
    (path / level / 'region' / 'r.0.0.mca').write_bytes(b'overworld')
    (path / level / 'level.dat').write_bytes(b'dat')
    if paper:                      # Paper splits the other dimensions out
        for suffix in ('_nether', '_the_end'):
            d = path / (level + suffix) / 'region'
            d.mkdir(parents=True)
            (d / 'r.0.0.mca').write_bytes(suffix.encode())
    (path / 'server.properties').write_text('level-name=%s\nserver-port=25599\n' % level)
    (path / 'ops.json').write_text('[{"name":"Ares"}]')
    server = {'name': name, 'path': str(path), 'type': 'paper' if paper else 'vanilla'}
    app.CONFIG['servers'] = [server]
    return server, path


def zip_names(z):
    with zipfile.ZipFile(z) as zf:
        return set(n.replace('\\', '/') for n in zf.namelist())


def test_a_vanilla_backup_holds_the_world_and_the_settings(tmp_path):
    _s, path = make_world(tmp_path)
    dest = app.auto_backup('TestWorld', 'test')
    assert dest
    names = zip_names(dest)
    assert 'world/region/r.0.0.mca' in names
    assert 'server.properties' in names
    assert 'ops.json' in names


def test_a_paper_backup_holds_the_nether_and_the_end(tmp_path):
    """The bug this pins down: Paper keeps the other dimensions in folders
    beside the world, so backing up level-name alone loses everything anyone
    built through a portal."""
    _s, path = make_world(tmp_path, paper=True)
    dest = app.auto_backup('TestWorld', 'test')
    names = zip_names(dest)
    assert 'world_nether/region/r.0.0.mca' in names
    assert 'world_the_end/region/r.0.0.mca' in names


def test_restore_puts_every_dimension_back(tmp_path):
    s, path = make_world(tmp_path, paper=True)
    dest = app.auto_backup('TestWorld', 'test')

    (path / 'world' / 'region' / 'r.0.0.mca').write_bytes(b'ruined')
    (path / 'world_nether' / 'region' / 'r.0.0.mca').write_bytes(b'ruined')

    ok, msg = app.restore_backup('TestWorld', os.path.basename(dest))
    assert ok, msg
    assert (path / 'world' / 'region' / 'r.0.0.mca').read_bytes() == b'overworld'
    assert (path / 'world_nether' / 'region' / 'r.0.0.mca').read_bytes() == b'_nether'


def test_restore_keeps_the_old_world_when_the_backup_is_rubbish(tmp_path):
    s, path = make_world(tmp_path)
    bad = path / 'backups' / 'auto_broken_0.zip'
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b'not a zip at all')

    ok, msg = app.restore_backup('TestWorld', 'auto_broken_0.zip')
    assert not ok
    assert 'untouched' in msg or 'safe' in msg
    # the point: the world is still there under its own name
    assert (path / 'world' / 'region' / 'r.0.0.mca').read_bytes() == b'overworld'


def test_restore_refuses_a_backup_that_writes_outside_the_world(tmp_path):
    s, path = make_world(tmp_path)
    bdir = path / 'backups'
    bdir.mkdir(exist_ok=True)
    nasty = bdir / 'auto_nasty_0.zip'
    with zipfile.ZipFile(nasty, 'w') as z:
        z.writestr('../../escaped.txt', 'nope')
    ok, msg = app.restore_backup('TestWorld', 'auto_nasty_0.zip')
    assert not ok
    assert not (tmp_path.parent / 'escaped.txt').exists()
    assert (path / 'world' / 'level.dat').exists()


def test_old_backups_are_pruned_but_the_newest_are_kept(tmp_path, monkeypatch):
    _s, path = make_world(tmp_path)
    monkeypatch.setattr(app, 'BACKUP_KEEP', 3)
    made = []
    for i in range(6):
        d = app.auto_backup('TestWorld', 'test')
        os.utime(d, (1000 + i, 1000 + i))       # distinct, increasing mtimes
        made.append(d)
    left = os.listdir(path / 'backups')
    assert len(left) == 3
    assert os.path.basename(made[-1]) in left


def test_backups_can_live_outside_the_world_folder(tmp_path):
    s, path = make_world(tmp_path)
    elsewhere = tmp_path / 'somewhere-else'
    app.CONFIG['backupsRoot'] = str(elsewhere)
    try:
        dest = app.auto_backup('TestWorld', 'test')
        assert dest.startswith(str(elsewhere))
        assert app.list_backups('TestWorld')
    finally:
        app.CONFIG.pop('backupsRoot', None)


def test_a_restore_does_not_undo_settings_you_changed_since(tmp_path):
    """The settings are in the backup so they can be recovered by hand, but a
    restore is about the world - quietly reverting server.properties would
    undo a max-players change nobody asked to undo."""
    _s, path = make_world(tmp_path)
    dest = app.auto_backup('TestWorld', 'test')

    (path / 'server.properties').write_text('level-name=world\nserver-port=25599\nmax-players=99\n')
    ok, msg = app.restore_backup('TestWorld', os.path.basename(dest))
    assert ok, msg
    assert 'max-players=99' in (path / 'server.properties').read_text()
    assert (path / 'world' / 'level.dat').exists()


def test_a_half_written_restore_still_gives_the_world_back(tmp_path, monkeypatch):
    """If the extract dies partway, what is left behind is a fragment of a
    backup we have given up on - the real world is the copy set aside, and it
    has to end up back under its own name."""
    _s, path = make_world(tmp_path)
    dest = app.auto_backup('TestWorld', 'test')

    real = app._safe_extract
    def dies_partway(zf, d, only_tops=None):
        os.makedirs(os.path.join(d, 'world'), exist_ok=True)
        with open(os.path.join(d, 'world', 'half.dat'), 'wb') as f:
            f.write(b'partial')
        raise IOError("disk went away")
    monkeypatch.setattr(app, '_safe_extract', dies_partway)

    ok, msg = app.restore_backup('TestWorld', os.path.basename(dest))
    assert not ok
    assert 'untouched' in msg
    monkeypatch.setattr(app, '_safe_extract', real)
    assert (path / 'world' / 'region' / 'r.0.0.mca').read_bytes() == b'overworld'
    assert not (path / 'world' / 'half.dat').exists()
    assert not list(path.glob('*_prerestore_*'))


def test_the_prerestore_copies_do_not_pile_up_forever(tmp_path):
    _s, path = make_world(tmp_path)
    dest = app.auto_backup('TestWorld', 'test')
    for _ in range(4):
        ok, msg = app.restore_backup('TestWorld', os.path.basename(dest))
        assert ok, msg
    assert len(list(path.glob('*_prerestore_*'))) <= 2
