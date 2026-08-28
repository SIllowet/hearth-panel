"""The gate between the AI and a live server console.

Everything here is about what a model is allowed to type into a world that
other people are playing in, which makes it the part worth pinning down.
"""
import mc_tools


def test_invented_commands_are_refused():
    ok, _tool, why = mc_tools.check('summonlots zombie')
    assert not ok
    assert 'know' in why


def test_blocked_command_is_refused_by_name():
    blocked = [t['cmd'] for t in mc_tools.TOOLS if t['risk'] == 'blocked']
    assert blocked, "the point of the list is that it is not empty"
    ok, _t, _why = mc_tools.check(blocked[0] + ' anything')
    assert not ok


def test_a_command_switched_off_in_the_panel_is_refused():
    ok, _t, why = mc_tools.check('weather clear', disabled=['weather'])
    assert not ok
    assert 'switched off' in why


def test_a_normal_command_goes_through():
    ok, tool, why = mc_tools.check('time set day')
    assert ok, why
    assert tool == 'time'


def test_leading_slash_and_case_do_not_matter():
    assert mc_tools.check('/TIME set day')[0]


def test_aliases_resolve_to_the_real_tool():
    for alias, real in list(mc_tools.ALIASES.items())[:5]:
        assert mc_tools.resolve(alias) == real


def test_disabled_tools_are_left_out_of_the_prompt():
    block = mc_tools.prompt_block(disabled=['weather'])
    assert 'Switched off in the panel right now: weather' in block
