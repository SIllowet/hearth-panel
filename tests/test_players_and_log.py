"""Who is online, and the console cursor."""
import app


def test_joins_and_leaves_are_followed():
    p = []
    app.track_players(p, '[12:00:00] [Server thread/INFO]: Ares joined the game')
    app.track_players(p, '[12:00:01] [Server thread/INFO]: Bee joined the game')
    assert p == ['Ares', 'Bee']
    app.track_players(p, '[12:00:02] [Server thread/INFO]: Ares left the game')
    assert p == ['Bee']


def test_a_lost_connection_counts_as_leaving():
    p = ['Ares']
    app.track_players(p, '[12:00:02] [Server thread/INFO]: Ares lost connection: Timed out')
    assert p == []


def test_a_restart_empties_the_list():
    p = ['Ares', 'Bee']
    app.track_players(p, '[12:00:00] [Server thread/INFO]: Starting minecraft server version 1.21.4')
    assert p == []


def test_joining_twice_does_not_duplicate():
    p = []
    for _ in range(3):
        app.track_players(p, '[12:00:00] [Server thread/INFO]: Ares joined the game')
    assert p == ['Ares']


def test_players_survive_a_flood_of_chat():
    """The bug this pins down: the list used to be re-derived from the console
    buffer, so once the join line scrolled off the end the player vanished from
    the panel while still standing in the world."""
    mp = app.ManagedProc()
    app.track_players(mp.players, '[12:00:00] [Server thread/INFO]: Ares joined the game')
    for i in range(mp.log.maxlen * 2):
        line = '[12:00:00] [Server thread/INFO]: <Bee> chatter %d' % i
        mp.log.append(line)
        app.track_players(mp.players, line)
    assert mp.players == ['Ares']


def test_the_console_cursor_only_returns_what_is_new():
    mp = app.ManagedProc()
    for i in range(5):
        mp.log.append('line %d' % i)
        mp.seq += 1

    first = app.tail_log(mp, None)
    assert first['reset'] is True
    assert len(first['lines']) == 5

    nothing_new = app.tail_log(mp, first['seq'])
    assert nothing_new['lines'] == []
    assert nothing_new['reset'] is False

    mp.log.append('line 5'); mp.seq += 1
    just_one = app.tail_log(mp, first['seq'])
    assert just_one['lines'] == ['line 5']


def test_the_cursor_starts_over_when_the_caller_has_fallen_too_far_behind():
    mp = app.ManagedProc()
    for i in range(mp.log.maxlen + 50):
        mp.log.append('line %d' % i)
        mp.seq += 1
    r = app.tail_log(mp, 1)
    assert r['reset'] is True
    assert len(r['lines']) == mp.log.maxlen


def test_the_cursor_starts_over_after_a_restart():
    mp = app.ManagedProc()
    mp.log.append('x'); mp.seq = 1
    r = app.tail_log(mp, 9999)          # caller remembers a longer-lived run
    assert r['reset'] is True


def test_no_such_world_is_not_an_error():
    r = app.tail_log(None, 5)
    assert r['lines'] == []
    assert r['reset'] is True
