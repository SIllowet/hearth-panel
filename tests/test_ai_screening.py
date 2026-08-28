"""ServerAI.screen_command - who may run what, and what cannot be smuggled past."""
import ai as ai_mod


def fresh(**cfg):
    a = ai_mod.ServerAI()
    a.cfg = ai_mod.default_config()
    a.cfg.update(cfg)
    return a


def test_a_stranger_cannot_run_commands():
    a = fresh(trusted=['owner'], trust_ops=False)
    ok, _cmd, why = a.screen_command('time set day', 'randomvisitor')
    assert not ok
    assert 'trusted' in why


def test_a_trusted_player_can():
    a = fresh(trusted=['owner'], trust_ops=False)
    ok, _cmd, why = a.screen_command('time set day', 'Owner')
    assert ok, why


def test_the_owner_is_always_trusted():
    a = fresh(owner_ign='Ares', trusted=[], trust_ops=False)
    assert a.is_trusted('ares')


def test_commands_can_be_switched_off_entirely():
    a = fresh(trusted=['owner'], allow_commands=False, trust_ops=False)
    ok, _cmd, why = a.screen_command('time set day', 'owner')
    assert not ok
    assert 'switched off' in why


def test_a_blocked_command_hidden_in_execute_is_caught():
    a = fresh(trusted=['owner'], trust_ops=False)
    blocked = sorted(a.blocked_set())[0]
    ok, _cmd, why = a.screen_command('execute as @a run %s x' % blocked, 'owner')
    assert not ok
    assert 'execute' in why


def test_the_hard_block_list_cannot_be_configured_away():
    a = fresh(trusted=['owner'], blocked_commands=[], trust_ops=False)
    assert ai_mod.HARD_BLOCKED
    assert set(ai_mod.HARD_BLOCKED) <= a.blocked_set()


def test_only_the_first_line_of_a_multi_line_command_is_taken():
    a = fresh(trusted=['owner'], trust_ops=False)
    ok, cmd, _why = a.screen_command('time set day\nop somebody', 'owner')
    assert ok
    assert cmd == 'time set day'


def test_an_empty_command_is_refused():
    a = fresh(trusted=['owner'], trust_ops=False)
    assert not a.screen_command('   ', 'owner')[0]
