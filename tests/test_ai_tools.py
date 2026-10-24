"""ai_tools - lookups, panel actions and the chat commands that never need a model."""
import pytest

import ai as ai_mod
import ai_tools
import mc_tools


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_mod, 'MEMORY_PATH', str(tmp_path / 'mem.json'))
    a = ai_mod.ServerAI()
    a.cfg = ai_mod.default_config()
    a.cfg.update(name='Ember', owner_ign='Ares', trusted=['pal'], trust_ops=False)
    a.mem = ai_mod.default_memory()
    a.start = lambda: None                      # no worker threads in tests
    a.sent = []
    a._servers['w'] = {"send": a.sent.append, "log": lambda line: None,
                       "players": lambda: ['Steve', 'Alex'], "helpers": {}}
    a._server_path = lambda name: str(tmp_path)
    a.tmp = tmp_path
    return a


def ctx(bot, player='Ares', helpers=None):
    return ai_tools.Ctx(bot, 'w', player, bot.is_trusted(player, 'w'), helpers or bot._servers['w']['helpers'])


def run(bot, tool, args=None, player='Ares', helpers=None, disabled=None):
    return ai_tools.run(tool, args or {}, ctx(bot, player, helpers), disabled)


# ------------------------------------------------------------------- who may
def test_trusted_only_tools_refuse_everyone_else(bot):
    out = run(bot, 'read_log', player='stranger')
    assert out.startswith('refused')
    assert 'stranger' in out


def test_the_model_cannot_name_itself_trusted(bot):
    # the flag comes from the real trusted list, never from anything in the call
    out = ai_tools.run('save_place', {'name': 'x', 'x': 1, 'y': 2, 'z': 3, 'trusted': True},
                       ctx(bot, 'stranger'))
    assert out.startswith('refused')
    assert not bot.mem['places']


def test_a_tool_switched_off_in_the_panel_is_refused(bot):
    assert 'switched off' in run(bot, 'calc', {'expr': '1+1'}, disabled=['calc'])


def test_unknown_tools_list_what_does_exist(bot):
    out = run(bot, 'fly')
    assert 'no tool called' in out
    assert 'players_online' in out


def test_untrusted_players_are_not_shown_trusted_tools():
    block = ai_tools.prompt_block(trusted=False)
    assert 'players_online' in block
    assert 'read_log' not in block
    assert 'read_log' in ai_tools.prompt_block(trusted=True)


def test_every_tool_has_an_implementation_and_a_valid_example():
    import json
    for t in ai_tools.TOOLS:
        assert t['name'] in ai_tools._IMPL
        call = json.loads(t['example'])
        assert call['tool'] == t['name']


# --------------------------------------------------------------- reading calls
@pytest.mark.parametrize('reply', [
    {"tools": [{"tool": "calc", "args": {"expr": "1+1"}}]},
    {"tools": [{"name": "calc", "expr": "1+1"}]},                  # args inline, 'name' for 'tool'
    {"tool": {"tool": "calc", "args": {"expr": "1+1"}}},           # singular, not a list
])
def test_tool_calls_are_read_however_a_small_model_spells_them(reply):
    calls = ai_tools.extract_calls(reply)
    assert calls == [('calc', {'expr': '1+1'})]


def test_a_bare_string_is_a_tool_with_no_arguments():
    assert ai_tools.extract_calls({"tools": ["players_online"]}) == [('players_online', {})]


def test_junk_in_the_tools_field_is_ignored():
    assert ai_tools.extract_calls({"tools": 7}) == []
    assert ai_tools.extract_calls({"tools": [None, 3, []]}) == []
    assert ai_tools.extract_calls({}) == []


# ------------------------------------------------------------------------ calc
@pytest.mark.parametrize('expr,shown', [
    ('1000 / 64', '15.625'), ('4 x 64', '256'), ('2^10', '1024'), ('(3+4)*2', '14'),
    ('-5 + 2', '-3'), ('10 // 3', '3'),
])
def test_calc_does_arithmetic(bot, expr, shown):
    assert run(bot, 'calc', {'expr': expr}).endswith('= ' + shown)


@pytest.mark.parametrize('expr', [
    '__import__("os").system("x")', 'open("f")', '2**99999', '9**9**9', '1/0', '', 'a+1', 'x' * 200,
])
def test_calc_refuses_anything_but_arithmetic(bot, expr):
    out = run(bot, 'calc', {'expr': expr})
    assert '=' not in out


# ---------------------------------------------------------------- server_info
def test_server_info_never_leaks_secrets(bot):
    (bot.tmp / 'server.properties').write_text(
        'motd=Hello\ndifficulty=hard\nrcon.password=hunter2\nserver-ip=1.2.3.4\nmax-players=8\n')
    out = run(bot, 'server_info', player='stranger')
    assert 'difficulty=hard' in out and 'max-players=8' in out
    assert 'hunter2' not in out and '1.2.3.4' not in out


# --------------------------------------------------------------------- read_log
def test_read_log_filters_and_hides_addresses(bot):
    logs = bot.tmp / 'logs'
    logs.mkdir()
    (logs / 'latest.log').write_text(
        '[10:00:00] [Server thread/INFO]: Steve[/203.0.113.7:51234] logged in\n'
        '[10:00:05] [Server thread/INFO]: Alex joined the game\n'
        '[10:00:09] [Server thread/ERROR]: Could not save chunk\n')
    out = run(bot, 'read_log', {'contains': 'steve'})
    assert 'Steve' in out
    assert '203.0.113.7' not in out and '<ip>' in out
    assert 'Alex' not in out
    assert 'Could not save' in run(bot, 'read_log', {'contains': 'error'})
    assert 'nothing in the log' in run(bot, 'read_log', {'contains': 'zzz'})


def test_read_log_without_a_log_says_so(bot):
    assert 'no latest.log' in run(bot, 'read_log')


# ------------------------------------------------------------------ ask_server
@pytest.mark.parametrize('cmd', [
    'list', 'seed', 'time query daytime', 'data get entity Steve Pos', 'gamerule keepInventory',
    'worldborder get', 'scoreboard players get Steve kills', 'tick query', '/list uuids',
])
def test_ask_server_passes_read_only_queries(cmd):
    assert ai_tools.readonly_problem(cmd) == ''


@pytest.mark.parametrize('cmd', [
    'stop', 'op Steve', 'data merge entity Steve {}', 'data modify entity Steve Health set value 1',
    'gamerule keepInventory true', 'time set day', 'give Steve diamond', 'list\nstop',
    'execute as @a run list', 'scoreboard players set Steve kills 0', '', 'whitelist list',
])
def test_ask_server_refuses_anything_that_changes_something(cmd):
    assert ai_tools.readonly_problem(cmd)


def test_ask_server_returns_the_servers_answer(bot):
    asked = []
    helpers = {"query": lambda c: (asked.append(c), (True, 'There are 2 of a max of 20 players online: Steve, Alex'))[1]}
    out = run(bot, 'ask_server', {'command': 'list'}, helpers=helpers)
    assert 'Steve' in out and asked == ['list']


def test_ask_server_respects_the_panel_switches(bot):
    bot.cfg['disabled_tools'] = ['data']
    helpers = {"query": lambda c: (True, 'x')}
    assert 'switched off' in run(bot, 'ask_server', {'command': 'data get entity Steve Pos'}, helpers=helpers)


def test_ask_server_without_rcon_explains(bot):
    assert "can't be asked" in run(bot, 'ask_server', {'command': 'list'}, helpers={})
    helpers = {"query": lambda c: (False, 'this world has no RCON')}
    assert 'no RCON' in run(bot, 'ask_server', {'command': 'list'}, helpers=helpers)


# --------------------------------------------------------------------- places
def test_places_round_trip_and_survive_a_restart(bot):
    assert 'saved' in run(bot, 'save_place', {'name': 'My Base', 'x': -585.4, 'y': 70, 'z': 935, 'dimension': 'the_nether'})
    assert 'my base: -585 70 935 (nether)' in run(bot, 'list_places', player='stranger')
    assert ai_mod.load_memory()['places']['my base']['x'] == -585
    assert 'forgot' in run(bot, 'forget_place', {'name': 'MY BASE'})
    assert 'no places' in run(bot, 'list_places')


@pytest.mark.parametrize('args', [
    {'name': '', 'x': 1, 'y': 2, 'z': 3},
    {'name': 'a', 'x': 'foo', 'y': 2, 'z': 3},
    {'name': 'a', 'x': 99999999999, 'y': 2, 'z': 3},
    {'name': 'a', 'x': 1, 'y': 2, 'z': 3, 'dimension': 'moon'},
])
def test_bad_places_are_refused(bot, args):
    run(bot, 'save_place', args)
    assert not bot.mem['places']


def test_the_place_list_has_a_cap(bot):
    for i in range(40):
        assert bot.set_place('p%d' % i, 0, 64, 0)[0]
    assert not bot.set_place('one more', 0, 64, 0)[0]
    assert bot.set_place('p3', 5, 64, 5)[0]                 # moving an existing one is fine


def test_add_fact_writes_once(bot):
    run(bot, 'add_fact', {'text': 'PvP only in the arena'})
    run(bot, 'add_fact', {'text': 'PvP only in the arena'})
    assert bot.mem['facts'] == ['PvP only in the arena']
    assert not bot.add_fact('x')[0]


# --------------------------------------------------------------------- remind
def test_remind_is_capped_per_player(bot):
    created = []

    class FakeTimer:
        def __init__(self, secs, fn):
            created.append(secs)
            self.daemon = False

        def start(self):
            pass
    import threading
    orig = threading.Timer
    threading.Timer = FakeTimer
    try:
        for _ in range(ai_tools.MAX_REMINDERS):
            assert 'remind' in run(bot, 'remind', {'minutes': 5, 'text': 'furnace'}, player='stranger')
        assert 'already has' in run(bot, 'remind', {'minutes': 5, 'text': 'furnace'}, player='stranger')
        assert 'remind' in run(bot, 'remind', {'minutes': 5, 'text': 'furnace'}, player='someoneelse')
    finally:
        threading.Timer = orig
    assert created[0] == 300


def test_remind_needs_both_halves(bot):
    assert 'need minutes' in run(bot, 'remind', {'minutes': 5})
    assert 'need minutes' in run(bot, 'remind', {'text': 'x'})


# --------------------------------------------------------------------- backups
def test_backup_now_has_a_cooldown(bot):
    calls = []
    helpers = {"backup": lambda: (calls.append(1), (True, 'ok'))[1]}
    first = run(bot, 'backup_now', helpers=helpers)
    assert 'started' in first
    assert 'moment ago' in run(bot, 'backup_now', helpers=helpers)


def test_list_backups_reads_the_panels_list(bot):
    helpers = {"backups": lambda: [{"file": "auto_1.zip", "size": 12.5, "when": "2026-01-01 10:00"}]}
    assert 'auto_1.zip' in run(bot, 'list_backups', helpers=helpers)


# --------------------------------------------------------------- chat commands
WAKE = ['ember']


@pytest.mark.parametrize('text,cmd', [
    ('ember help', 'help'), ('Ember, help', 'help'), ('hey ember, !tools', 'tools'),
    ('ember what can you do?', 'help'), ('EMBER: forget me', 'forget'), ('ember !status', 'status'),
    ('yo ember bye', 'bye'), ('!places', 'places'), ('/help', 'help'),
])
def test_chat_commands_are_recognised(text, cmd):
    assert ai_tools.parse_chat_command(text, WAKE) == (cmd, '')


@pytest.mark.parametrize('text', [
    'help me find diamonds', 'ember can you help me find diamonds', 'ember', 'ember help me',
    'i asked ember for help yesterday', 'status of the build?', '', 'ember tell steve to forget me',
])
def test_ordinary_talk_is_not_mistaken_for_a_command(text):
    assert ai_tools.parse_chat_command(text, WAKE) is None


def test_every_chat_command_has_a_help_line_and_an_alias():
    for cmd in ai_tools.CHAT_COMMANDS:
        assert cmd in ai_tools.CHAT_HELP
    for alias, cmd in ai_tools.CHAT_ALIAS.items():
        assert cmd in ai_tools.CHAT_COMMANDS


def test_help_names_every_command(bot):
    out = ai_tools.answer_chat_command('help', ctx(bot, 'stranger'))
    for names in ai_tools.CHAT_COMMANDS.values():
        assert names[0] in out


def test_whoami_tells_the_truth_about_trust(bot):
    assert 'the owner' in ai_tools.answer_chat_command('whoami', ctx(bot, 'Ares'))
    assert 'trusted' in ai_tools.answer_chat_command('whoami', ctx(bot, 'pal'))
    assert 'not on my trusted list' in ai_tools.answer_chat_command('whoami', ctx(bot, 'stranger'))


def test_forget_me_only_touches_the_asker(bot):
    ai_mod.remember_player(bot.mem, 'Steve', 'likes redstone')
    ai_mod.remember_player(bot.mem, 'Alex', 'builds castles')
    assert 'wiped 1' in ai_tools.answer_chat_command('forget', ctx(bot, 'Steve'))
    assert 'steve' not in bot.mem['players']
    assert bot.mem['players']['alex']
    assert 'Nothing to forget' in ai_tools.answer_chat_command('forget', ctx(bot, 'Steve'))


def test_tools_tells_strangers_commands_are_not_for_them(bot):
    assert 'trusted players' in ai_tools.answer_chat_command('tools', ctx(bot, 'stranger'))
    assert 'Minecraft commands' in ai_tools.answer_chat_command('tools', ctx(bot, 'Ares'))


def test_status_does_not_leak_error_text(bot):
    bot.status['last_error'] = 'openai HTTP 401: invalid key sk-SECRET'
    out = ai_tools.answer_chat_command('status', ctx(bot, 'stranger'))
    assert 'SECRET' not in out and 'sk-' not in out


# ---------------------------------------------------------- console selectors
@pytest.mark.parametrize('cmd', ['tp @s 0 64 0', 'give @p diamond', 'summon cow ~ ~ ~', 'fill ~ ~ ~ ~5 ~5 ~5 stone'])
def test_selectors_that_need_a_body_are_caught(cmd):
    assert mc_tools.console_problem(cmd)


@pytest.mark.parametrize('cmd', [
    'tp Steve 0 64 0', 'give @a diamond', 'say see you ~ soon', 'tellraw @a {"text":"~"}',
    'execute as Steve at @s run summon cow ~ ~ ~', 'msg Steve meet me at @s',
])
def test_everything_else_passes_the_console_check(cmd):
    assert mc_tools.console_problem(cmd) == ''


def test_paper_commands_are_on_the_belt():
    for c in ('tps', 'mspt', 'version', 'plugins'):
        assert mc_tools.check(c)[0]
