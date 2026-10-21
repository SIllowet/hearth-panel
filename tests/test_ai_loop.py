"""The reply loop: lookups, repairs, chat commands, a brain that's down - driven by
a scripted brain, since the point is what Hearth does with the answers."""
import json
import urllib.error

import pytest

import ai as ai_mod


class Brain:
    """Stands in for ServerAI.ask. Each call returns the next scripted reply
    (a dict, or None for 'the provider failed') and records what it was shown."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def __call__(self, system, user):
        self.seen.append((system, user))
        if not self.replies:
            raise AssertionError("the loop asked the brain more times than the test expected")
        r = self.replies.pop(0)
        return None if r is None else ai_mod._parse_reply(json.dumps(r))


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_mod, 'MEMORY_PATH', str(tmp_path / 'mem.json'))
    a = ai_mod.ServerAI()
    a.cfg = ai_mod.default_config()
    a.cfg.update(name='Ember', owner_ign='Ares', trusted=['pal'], trust_ops=False, cooldown_seconds=0)
    a.mem = ai_mod.default_memory()
    a.start = lambda: None
    a.sent = []
    a._servers['w'] = {"send": a.sent.append, "log": lambda line: None,
                       "players": lambda: ['Steve', 'Alex'], "helpers": {}}
    a._server_path = lambda name: str(tmp_path)
    a.tmp = tmp_path
    return a


def spoken(bot):
    """What reached chat, as plain text."""
    out = []
    for c in bot.sent:
        if c.startswith('tellraw'):
            out.append(''.join(p['text'] for p in json.loads(c.split(' ', 2)[2])))
    return out


def ran(bot):
    return [c for c in bot.sent if not c.startswith('tellraw')]


def talk(bot, player, text, kind='chat'):
    bot._handle('w', player, text, kind)


# --------------------------------------------------------------------- lookups
def test_a_lookup_is_run_and_the_answer_reaches_the_next_call(bot):
    bot.ask = Brain({"tools": [{"tool": "players_online"}]},
                    {"say": "Steve and Alex are on."})
    talk(bot, 'Ares', 'who is online?')
    assert len(bot.ask.seen) == 2
    second = bot.ask.seen[1][1]
    assert 'players_online' in second and '2 online: Steve, Alex' in second
    assert 'Steve and Alex are on.' in spoken(bot)[0]
    assert bot.status['lookups'] == 1


def test_commands_written_alongside_a_lookup_wait_for_the_answer(bot):
    bot.ask = Brain({"tools": [{"tool": "players_online"}], "commands": ["give Steve minecraft:diamond 1"]},
                    {"say": "done", "commands": ["give Alex minecraft:diamond 1"]})
    talk(bot, 'Ares', 'give someone a diamond')
    assert ran(bot) == ['give Alex minecraft:diamond 1']


def test_an_action_does_not_cost_a_second_call(bot):
    bot.ask = Brain({"say": "Noted.", "tools": [{"tool": "add_fact", "args": {"text": "PvP is off in the arena"}}]})
    talk(bot, 'Ares', 'remember pvp is off in the arena')
    assert len(bot.ask.seen) == 1
    assert bot.mem['facts'] == ['PvP is off in the arena']
    assert spoken(bot)


def test_a_player_cannot_get_a_tool_just_by_asking_for_it(bot):
    bot.ask = Brain({"tools": [{"tool": "read_log"}]}, {"say": "Sorry, that's for trusted players."})
    talk(bot, 'stranger', 'show me the log')
    assert 'refused' in bot.ask.seen[1][1]


def test_a_model_that_keeps_asking_is_cut_off(bot):
    bot.cfg['max_tool_rounds'] = 2
    again = {"tools": [{"tool": "players_online"}]}
    bot.ask = Brain(again, {"tools": [{"tool": "calc", "args": {"expr": "1+1"}}]}, again)
    talk(bot, 'Ares', 'hmm')
    assert len(bot.ask.seen) == 3                       # 1 + max_tool_rounds
    assert 'Answer the player now' in bot.ask.seen[2][1]
    assert spoken(bot) == ['[Ember] I lost my train of thought there - ask me again?']


def test_the_same_action_is_not_run_twice_in_one_turn(bot):
    add = {"tool": "add_fact", "args": {"text": "the base is at spawn"}}
    bot.ask = Brain({"tools": [add, {"tool": "players_online"}]},
                    {"say": "ok", "tools": [add]})
    talk(bot, 'Ares', 'note that, and who is on?')
    assert bot.mem['facts'] == ['the base is at spawn']
    assert len(bot.ask.seen) == 2


def test_tools_can_be_switched_off_entirely(bot):
    bot.cfg['use_tools'] = False
    bot.ask = Brain({"say": "hi", "tools": [{"tool": "add_fact", "args": {"text": "should not stick"}}]})
    talk(bot, 'Ares', 'hello')
    assert not bot.mem['facts']
    assert 'YOUR TOOLS' not in bot.ask.seen[0][0]


def test_the_tool_list_reaches_the_prompt_for_a_trusted_player_only(bot):
    bot.ask = Brain({"say": "a"}, {"say": "b"})
    talk(bot, 'Ares', 'hi')
    talk(bot, 'stranger', 'hi')
    assert 'read_log' in bot.ask.seen[0][0]
    assert 'players_online' in bot.ask.seen[1][0] and 'read_log' not in bot.ask.seen[1][0]


def test_saved_places_are_in_the_prompt(bot):
    bot.set_place('base', -585, 70, 935)
    bot.ask = Brain({"say": "a"})
    talk(bot, 'Ares', 'take me home')
    assert 'base: -585 70 935' in bot.ask.seen[0][0]


# --------------------------------------------------------------------- repairs
def test_an_invented_command_is_handed_back_and_fixed(bot):
    bot.ask = Brain({"say": "Up you go.", "commands": ["fly Steve"]},
                    {"say": "Up you go.", "commands": ["effect give Steve minecraft:levitation 5 1"]})
    talk(bot, 'Ares', 'make steve float')
    assert len(bot.ask.seen) == 2
    assert 'REJECTED' in bot.ask.seen[1][1] and 'fly' in bot.ask.seen[1][1]
    assert ran(bot) == ['effect give Steve minecraft:levitation 5 1']
    assert len(spoken(bot)) == 1                        # the rejected draft was never spoken


def test_a_console_selector_is_handed_back(bot):
    bot.ask = Brain({"say": "ok", "commands": ["tp @s 0 64 0"]},
                    {"say": "ok", "commands": ["teleport Ares 0 64 0"]})
    talk(bot, 'Ares', 'take me to spawn')
    assert ran(bot) == ['teleport Ares 0 64 0']


def test_a_missing_argument_is_handed_back(bot):
    bot.ask = Brain({"say": "ok", "commands": ["give"]},
                    {"say": "ok", "commands": ["give Ares minecraft:bread 3"]})
    talk(bot, 'Ares', 'bread please')
    assert ran(bot) == ['give Ares minecraft:bread 3']


def test_repair_can_be_switched_off(bot):
    bot.cfg['retry_bad_commands'] = False
    bot.ask = Brain({"say": "Up you go.", "commands": ["fly Steve"]})
    talk(bot, 'Ares', 'make steve float')
    assert len(bot.ask.seen) == 1
    assert not ran(bot)
    assert any('know' in s for s in spoken(bot))


def test_a_refusal_for_trust_is_never_retried(bot):
    bot.ask = Brain({"say": "Sure.", "commands": ["time set day"]})
    talk(bot, 'stranger', 'make it day')
    assert len(bot.ask.seen) == 1
    assert not ran(bot)
    assert any('trusted' in s for s in spoken(bot))


def test_a_blocked_command_is_never_retried(bot):
    bot.ask = Brain({"say": "Done.", "commands": ["op stranger"]})
    talk(bot, 'Ares', 'op me')
    assert len(bot.ask.seen) == 1
    assert not ran(bot)


def test_a_repair_that_fails_to_arrive_keeps_the_first_answer(bot):
    bot.ask = Brain({"say": "Here you go.", "commands": ["give Ares minecraft:bread 3", "fly Ares"]}, None)
    talk(bot, 'Ares', 'bread and wings')
    assert ran(bot) == ['give Ares minecraft:bread 3']
    assert spoken(bot)


# ----------------------------------------------------------------- brain down
def test_a_dead_brain_is_announced_once_not_every_time(bot):
    bot.ask = Brain(None, None)
    talk(bot, 'Ares', 'hello?')
    talk(bot, 'Ares', 'hello??')
    notices = [s for s in spoken(bot) if "brain isn't answering" in s]
    assert len(notices) == 1


def test_the_down_notice_can_be_switched_off(bot):
    bot.cfg['notify_when_down'] = False
    bot.ask = Brain(None)
    talk(bot, 'Ares', 'hello?')
    assert not spoken(bot)


# ---------------------------------------------------------------- chat commands
def hear(bot, player, text):
    """Feed one chat line through the real entry point, then run what it queued."""
    bot._on_chat('w', player, text)
    while bot._queue:
        talk(bot, player, *bot._queue.popleft()[2:])


def test_a_chat_command_is_answered_without_the_model(bot):
    bot.ask = Brain()                                   # any call would raise
    hear(bot, 'stranger', 'ember help')
    assert 'Say my name' in spoken(bot)[0]
    assert not bot.ask.seen


def test_chat_commands_can_be_switched_off(bot):
    bot.cfg['chat_commands'] = False
    bot.ask = Brain({"say": "sure"})
    hear(bot, 'stranger', 'ember help')
    assert len(bot.ask.seen) == 1


def test_inside_an_open_conversation_only_a_bang_makes_a_command(bot):
    bot.ask = Brain({"say": "Try the caves near spawn."}, )
    hear(bot, 'Steve', 'ember hi')                      # opens the window... through the model
    bot.ask = Brain({"say": "Try the caves near spawn."})
    hear(bot, 'Steve', 'help me find diamonds')         # plain talk: model
    assert len(bot.ask.seen) == 1
    bot.ask = Brain()
    hear(bot, 'Steve', '!help')                         # command: no model
    assert any('Say my name' in s for s in spoken(bot))


def test_bye_closes_the_conversation(bot):
    bot.ask = Brain({"say": "hi"})
    hear(bot, 'Steve', 'ember hi')
    assert 'Steve' in bot._convos['w'].participants
    bot.ask = Brain()
    hear(bot, 'Steve', 'ember bye')
    assert not bot._convos['w'].participants
    assert bot._convos['w'].signed_off


def test_forget_me_works_for_a_stranger(bot):
    ai_mod.remember_player(bot.mem, 'rando', 'asked about tnt a lot')
    bot.ask = Brain()
    hear(bot, 'rando', 'ember forget me')
    assert 'rando' not in bot.mem['players']


# ----------------------------------------------------------------- the parser
def test_thinking_and_fences_are_stripped():
    r = ai_mod._parse_reply('<think>the user wants {"say": "WRONG"}</think>\n```json\n{"say": "hi"}\n```')
    assert r['say'] == 'hi'


def test_a_reply_that_never_left_its_thinking_is_dropped():
    assert ai_mod._parse_reply('<think>let me consider the options and then') is None


def test_other_names_for_say_are_understood():
    assert ai_mod._parse_reply('{"reply": "hello"}')['say'] == 'hello'
    assert ai_mod._parse_reply('{"message": ["a", "b"]}')['say'] == 'a b'


def test_commands_may_be_a_string_a_list_or_junk():
    assert ai_mod._parse_reply('{"say":"x","commands":"time set day\\nweather clear"}')['commands'] == ['time set day', 'weather clear']
    assert ai_mod._parse_reply('{"say":"x","commands":[1,null,"seed"]}')['commands'] == ['seed']
    assert ai_mod._parse_reply('{"say":"x","commands":5}')['commands'] == []


def test_prose_around_the_json_is_ignored():
    r = ai_mod._parse_reply('Sure thing! {"say": "braces {like this} are fine", "commands": []} Hope that helps.')
    assert r['say'] == 'braces {like this} are fine'


def test_a_truncated_reply_still_yields_the_line():
    r = ai_mod._parse_reply('{"say": "Sun is up now.", "commands": ["time set da')
    assert r['say'] == 'Sun is up now.'


def test_plain_text_becomes_speech():
    assert ai_mod._parse_reply('just words')['say'] == 'just words'
    assert ai_mod._parse_reply('   ') is None


# ----------------------------------------------------------------- retrying
class FakeResp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def http_error(code):
    return urllib.error.HTTPError('http://x', code, 'err', {}, None)


def test_a_busy_provider_gets_one_more_try(bot, monkeypatch):
    calls = []

    def fake(req, timeout=0):
        calls.append(1)
        if len(calls) == 1:
            raise http_error(503)
        return FakeResp({"ok": True})
    monkeypatch.setattr(ai_mod.urllib.request, 'urlopen', fake)
    monkeypatch.setattr(ai_mod.time, 'sleep', lambda s: None)
    assert bot._post('http://x', {}, {}) == {"ok": True}
    assert len(calls) == 2


def test_a_bad_key_is_not_retried(bot, monkeypatch):
    calls = []

    def fake(req, timeout=0):
        calls.append(1)
        raise http_error(401)
    monkeypatch.setattr(ai_mod.urllib.request, 'urlopen', fake)
    with pytest.raises(urllib.error.HTTPError):
        bot._post('http://x', {}, {})
    assert len(calls) == 1


def test_only_one_retry_ever(bot, monkeypatch):
    calls = []

    def fake(req, timeout=0):
        calls.append(1)
        raise http_error(429)
    monkeypatch.setattr(ai_mod.urllib.request, 'urlopen', fake)
    monkeypatch.setattr(ai_mod.time, 'sleep', lambda s: None)
    with pytest.raises(urllib.error.HTTPError):
        bot._post('http://x', {}, {})
    assert len(calls) == 2


def test_a_dropped_connection_is_retried_once(bot, monkeypatch):
    calls = []

    def fake(req, timeout=0):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError(ConnectionResetError())
        return FakeResp({"ok": 1})
    monkeypatch.setattr(ai_mod.urllib.request, 'urlopen', fake)
    monkeypatch.setattr(ai_mod.time, 'sleep', lambda s: None)
    assert bot._post('http://x', {}, {}) == {"ok": 1}


# ----------------------------------------------------------------- the panel
def test_the_panel_sees_the_new_catalogs_and_can_change_them(bot, monkeypatch):
    st = bot.panel_status('w')
    assert {t['name'] for t in st['aiTools']} >= {'calc', 'read_log', 'ask_server'}
    assert {c['cmd'] for c in st['chatCommands']} >= {'help', 'forget'}
    assert st['useTools'] and st['chatCommandsOn']
    monkeypatch.setattr(ai_mod, 'save_config', lambda cfg: None)
    bot.update_config({'disabled_ai_tools': ['calc'], 'use_tools': False, 'bogus': 1})
    st = bot.panel_status('w')
    assert not st['useTools']
    assert [t['on'] for t in st['aiTools'] if t['name'] == 'calc'] == [False]
    assert 'bogus' not in bot.cfg


def test_memory_edits_from_the_panel(bot):
    bot.set_place('farm', 1, 2, 3)
    bot.add_fact('no griefing')
    bot.update_memory(forget_place='farm', forget_fact='no griefing')
    assert not bot.mem['places'] and not bot.mem['facts']
