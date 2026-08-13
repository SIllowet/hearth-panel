#!/usr/bin/env python3
# ai.py - the companion that lives in your server console.
#
# It reads the console, answers in-game chat when someone says its name, and can
# run server commands for players you trust. You give it a name, a personality
# and a brain (any provider you like), and it keeps a small memory file about
# your server and the people on it.
#
# Three things worth knowing about the design:
#
#  1. stdlib only. The panel has no required dependencies and this keeps it that
#     way - every provider is called over plain urllib. No SDKs to install.
#
#  2. Any provider. Ollama on your own machine, or Gemini / OpenAI / Anthropic /
#     anything OpenAI-compatible (OpenRouter, Groq, LM Studio, DeepSeek...) if
#     you'd rather pay for a bigger model. They all answer the same JSON shape,
#     so nothing else in the file cares which one you picked.
#
#  3. Nothing blocks the console reader. on_console_line() parses and queues; a
#     worker thread does the slow network call.
#
# Commands are gated twice before they reach the server: the player has to be
# trusted, and the command has to be on the toolbelt in mc_tools.py.
import json
import os
import re
import threading
import time
import urllib.request
import urllib.error
from collections import deque

try:
    import mc_tools
except Exception:
    mc_tools = None

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, 'ai_config.json')
MEMORY_PATH = os.path.join(BASE, 'ai_memory.json')

# Console chat lines, vanilla and Paper:
#   [12:34:56] [Server thread/INFO]: <Steve> hey
#   [12:34:56 INFO]: <Steve> hey
# Anchored so a player typing "<Fake> ..." in chat can't spoof a second name.
CHAT_RE = re.compile(r'^\[[^\]]+\](?:\s*\[[^\]]+\])?:\s*(?:\[Not Secure\]\s*)?<([A-Za-z0-9_]{1,16})>\s*(.+)$')
JOIN_RE = re.compile(r'\]:\s*([A-Za-z0-9_]{2,16}) joined the game')
LEAVE_RE = re.compile(r'\]:\s*([A-Za-z0-9_]{2,16}) (?:left the game|lost connection)')

SIGNOFFS = [
    "If that's all, I'll leave you to it. Say my name if you need me.",
    "Going quiet. Ping me whenever.",
    "That's me done for now. I'm around.",
    "Alright - shout if something comes up.",
]

# Never runnable from chat, no matter who asks. These either kill the server,
# hand out permanent power, or switch off the safety rails.
HARD_BLOCKED = [
    'stop', 'op', 'deop', 'ban', 'ban-ip', 'pardon', 'pardon-ip', 'banlist',
    'whitelist', 'save-off', 'reload', 'restart', 'debug', 'perf',
]

# ---------------------------------------------------------------------------
# Providers. Everything the panel needs to offer a sensible dropdown lives here;
# add a new one and it shows up in the UI without touching the frontend.
PROVIDERS = {
    "ollama": {
        "label": "Ollama (free, runs on this PC)",
        "needsKey": False,
        "model": "llama3.2",
        "base": "http://127.0.0.1:11434",
        "hint": "Install Ollama, run `ollama pull llama3.2`, and leave the URL alone. Nothing leaves your computer and it costs nothing.",
    },
    "gemini": {
        "label": "Google Gemini",
        "needsKey": True,
        "model": "gemini-2.0-flash",
        "base": "https://generativelanguage.googleapis.com",
        "hint": "Free tier available. Key from aistudio.google.com/apikey",
    },
    "openai": {
        "label": "OpenAI",
        "needsKey": True,
        "model": "gpt-4o-mini",
        "base": "https://api.openai.com/v1",
        "hint": "Paid. Key from platform.openai.com/api-keys",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "needsKey": True,
        "model": "claude-sonnet-4-5",
        "base": "https://api.anthropic.com/v1",
        "hint": "Paid. Key from console.anthropic.com",
    },
    "openai_compatible": {
        "label": "Anything OpenAI-compatible",
        "needsKey": True,
        "model": "",
        "base": "",
        "hint": "OpenRouter, Groq, DeepSeek, Mistral, Together, LM Studio, vLLM... paste the base URL (ending in /v1), the model id, and your key.",
    },
}


def default_config():
    return {
        # --- who it is -----------------------------------------------------
        "name": "",                  # YOU name it. Blank until you do.
        "colour": "aqua",            # Minecraft chat colour for its name tag
        "persona": (
            "Calm, dry and friendly. Talks like someone who's been playing on this "
            "server for years, not like a help desk. Says less rather than more."
        ),
        "owner_ign": "",             # your Minecraft username - it treats you as the owner
        "enabled": True,

        # --- brain ---------------------------------------------------------
        "provider": "ollama",
        "model": "llama3.2",
        "api_key": "",
        "base_url": "",              # blank -> the provider's default above
        "temperature": 0.8,

        # --- how it behaves in chat -----------------------------------------
        "wake_words": [],            # blank -> its own name
        "window_seconds": 30,        # follow-up window: no need to repeat the name
        "open_window_to_all": True,
        "greet_on_join": False,
        "cooldown_seconds": 1.5,
        "max_calls_per_minute": 20,

        # --- commands -------------------------------------------------------
        "allow_commands": True,
        "trusted": [],
        "trust_ops": True,
        "trust_whitelist": False,
        "blocked_commands": list(HARD_BLOCKED),
        "disabled_tools": [],
        "restrict_to_tools": True,
        "max_commands_per_reply": 3,

        # --- memory ---------------------------------------------------------
        "remember_players": True,
    }


def load_config():
    cfg = default_config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    else:
        save_config(cfg)
    return cfg


def save_config(cfg):
    tmp = CONFIG_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def chunk_for_chat(text, width=140, max_lines=4):
    """Minecraft chat is cramped - wrap on words, cap the number of lines."""
    words = (text or "").split()
    lines, cur = [], ""
    for w in words:
        if len(w) > width:
            w = w[:width]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines[:max_lines]


class Conversation:
    """One live chat thread. Anyone who says its name joins; it stays open while
    people keep talking and signs off after window_seconds of quiet."""

    def __init__(self):
        self.participants = {}
        self.history = deque(maxlen=14)
        self.last_activity = 0.0
        self.signed_off = False

    def touch(self, player):
        now = time.time()
        self.participants[player] = now
        self.last_activity = now
        self.signed_off = False


class ServerAI:
    def __init__(self):
        self.cfg = load_config()
        self.transcript = deque(maxlen=200)
        self.status = {"last_error": "", "calls": 0, "replies": 0, "commands": 0}
        self._servers = {}
        self._convos = {}
        self._console = {}
        self._queue = deque()
        self._qlock = threading.Lock()
        self._wake = threading.Event()
        self._calls = deque()
        self._last_reply_at = {}
        self._signoff_i = 0
        self._started = False
        self.mem = load_memory()

    # ------------------------------------------------------------------ basics
    def display_name(self):
        return (self.cfg.get("name") or "").strip()

    def ready(self):
        """Named, switched on, and pointed at a brain."""
        if not self.display_name() or not self.cfg.get("enabled"):
            return False
        p = PROVIDERS.get(self.cfg.get("provider") or "", {})
        if p.get("needsKey") and not (self.cfg.get("api_key") or "").strip():
            return False
        return True

    def base_url(self):
        b = (self.cfg.get("base_url") or "").strip().rstrip('/')
        if b:
            return b
        return (PROVIDERS.get(self.cfg.get("provider") or "", {}).get("base") or "").rstrip('/')

    # ------------------------------------------------------------------ wiring
    def attach(self, name, send_command, log_append, players_fn):
        """Called by the panel when it launches a world. send_command(cmd) runs a
        console command; log_append(line) writes into the panel console."""
        self._servers[name] = {"send": send_command, "log": log_append, "players": players_fn}
        self._console.setdefault(name, deque(maxlen=80))
        self._convos.setdefault(name, Conversation())
        self.start()

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._ticker, daemon=True).start()

    # --------------------------------------------------------------- ingestion
    def on_console_line(self, name, line):
        """Fed every console line by the panel's reader thread. Stays cheap."""
        try:
            self._console.setdefault(name, deque(maxlen=80)).append(line)
            if not self.ready():
                return
            m = CHAT_RE.match(line)
            if m:
                self._on_chat(name, m.group(1), m.group(2).strip())
                return
            if self.cfg.get("greet_on_join"):
                j = JOIN_RE.search(line)
                if j:
                    self._enqueue(name, j.group(1), "", kind="join")
                    return
            lv = LEAVE_RE.search(line)
            if lv:
                convo = self._convos.get(name)
                if convo:
                    convo.participants.pop(lv.group(1), None)
                return
            if 'Stopping the server' in line:
                # don't let the ticker say goodbye into a world that's gone
                convo = self._convos.get(name)
                if convo:
                    convo.participants.clear()
                    convo.signed_off = True
        except Exception as e:
            self.status["last_error"] = "console: " + str(e)

    def wake_words(self):
        words = [w.strip().lower() for w in (self.cfg.get("wake_words") or []) if str(w).strip()]
        n = self.display_name().lower()
        if n and n not in words:
            words.append(n)
        return words

    def _on_chat(self, name, player, text):
        if player.lower() == self.display_name().lower():
            return
        convo = self._convos.setdefault(name, Conversation())
        convo.history.append((player, text))
        low = text.lower()
        called = any(w in low for w in self.wake_words())
        window = float(self.cfg.get("window_seconds", 30))
        # While the window is open nobody has to say its name again. It resets on
        # every message and closes after `window` seconds of quiet.
        window_open = (
            bool(convo.participants)
            and not convo.signed_off
            and (time.time() - convo.last_activity) <= window
        )
        in_window = window_open and (
            player in convo.participants or self.cfg.get("open_window_to_all", True)
        )
        if not (called or in_window):
            return
        convo.touch(player)
        self._enqueue(name, player, text, kind="chat")

    def _enqueue(self, name, player, text, kind):
        self.start()
        with self._qlock:
            if len(self._queue) > 12:      # a spam flood shouldn't queue forever
                return
            self._queue.append((name, player, text, kind))
        self._wake.set()

    # ------------------------------------------------------------------ trust
    def trusted_names(self, name=None):
        names = {n.strip().lower() for n in (self.cfg.get("trusted") or []) if str(n).strip()}
        owner = (self.cfg.get("owner_ign") or "").strip().lower()
        if owner:
            names.add(owner)
        path = self._server_path(name)
        if path:
            if self.cfg.get("trust_ops", True):
                for e in _read_json_list(os.path.join(path, 'ops.json')):
                    if e.get("name"):
                        names.add(e["name"].strip().lower())
            if self.cfg.get("trust_whitelist", False):
                for e in _read_json_list(os.path.join(path, 'whitelist.json')):
                    if e.get("name"):
                        names.add(e["name"].strip().lower())
        return names

    def is_trusted(self, player, name=None):
        return (player or "").strip().lower() in self.trusted_names(name)

    def _server_path(self, name):
        """Where this world lives, so ops.json / whitelist.json can be read.
        Reads config.json directly - importing app.py would re-execute it."""
        if not name:
            return None
        try:
            with open(os.path.join(BASE, 'config.json'), 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            for s in cfg.get('servers', []):
                if s.get('name') == name:
                    return s.get('path')
        except Exception:
            pass
        return None

    def blocked_set(self):
        blocked = {b.strip().lower() for b in (self.cfg.get("blocked_commands") or []) if str(b).strip()}
        blocked.update(HARD_BLOCKED)   # the hard list can't be configured away
        return blocked

    def screen_command(self, cmd, player, name=None):
        """The gate everything passes before it reaches stdin.
        Returns (allowed, cleaned, why-not)."""
        cmd = (cmd or "").strip().lstrip('/').strip()
        if not cmd:
            return False, "", "empty command"
        if '\n' in cmd or '\r' in cmd:
            cmd = cmd.splitlines()[0].strip()
        if not self.cfg.get("allow_commands", True):
            return False, cmd, "commands are switched off in the panel"
        if not self.is_trusted(player, name):
            return False, cmd, "%s is not on the trusted list" % player
        head = cmd.split()[0].lower()
        if head in self.blocked_set():
            return False, cmd, "'%s' is on the blocked list" % head
        inner = ""
        if head == 'execute':
            tail = cmd.lower().split(' run ', 1)
            if len(tail) > 1 and tail[1].strip():
                inner = tail[1].strip()
                if inner.split()[0] in self.blocked_set():
                    return False, cmd, "that hides a blocked command inside /execute"
        # The toolbelt. Without it the model writes plausible-looking commands
        # from memory and the console is where you find out they don't exist.
        if mc_tools and self.cfg.get("restrict_to_tools", True):
            disabled = self.cfg.get("disabled_tools") or []
            ok, _t, why = mc_tools.check(cmd, disabled)
            if not ok:
                return False, cmd, why
            if inner:
                ok, _t, why = mc_tools.check(inner, disabled)
                if not ok:
                    return False, cmd, "inside that /execute, " + why
        return True, cmd, ""

    # --------------------------------------------------------------- speaking
    def say(self, name, text):
        srv = self._servers.get(name)
        who = self.display_name() or "AI"
        colour = (self.cfg.get("colour") or "aqua").strip() or "aqua"
        lines = chunk_for_chat(text)
        stamp = time.strftime('[%H:%M:%S]')
        for ln in lines:
            payload = json.dumps([
                {"text": "[%s] " % who, "color": colour, "bold": True},
                {"text": ln, "color": "white"},
            ], ensure_ascii=False)
            if srv:
                try:
                    srv["send"]("tellraw @a " + payload)
                except Exception as e:
                    self.status["last_error"] = "say: " + str(e)
                try:
                    srv["log"]("%s [%s/CHAT]: [%s] %s" % (stamp, who, who, ln))
                except Exception:
                    pass
        self.transcript.append({"t": time.time(), "who": who, "text": " ".join(lines) or text})
        self.status["replies"] += 1

    def _note(self, text):
        self.transcript.append({"t": time.time(), "who": "system", "text": text})

    # ---------------------------------------------------------------- worker
    def _worker(self):
        while True:
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            while True:
                with self._qlock:
                    if not self._queue:
                        break
                    job = self._queue.popleft()
                try:
                    self._handle(*job)
                except Exception as e:
                    self.status["last_error"] = "worker: " + str(e)

    def _handle(self, name, player, text, kind):
        now = time.time()
        # Throttle a fast typer rather than dropping them: a second message waits
        # its turn instead of vanishing (dropping it looks like it stopped listening).
        cool = float(self.cfg.get("cooldown_seconds", 1.5))
        wait = cool - (now - self._last_reply_at.get(player, 0))
        if kind == "chat" and 0 < wait <= cool:
            time.sleep(wait)
        if not self._rate_ok():
            self._note("rate limit hit - skipped a reply")
            return
        self.transcript.append({"t": now, "who": player, "text": text})

        system, user = self._build_prompt(name, player, text, kind)
        reply = self.ask(system, user)
        if reply is None:
            return
        self._last_reply_at[player] = time.time()
        convo = self._convos.setdefault(name, Conversation())
        convo.touch(player)

        say = (reply.get("say") or "").strip()
        cmds = reply.get("commands") or []
        if isinstance(cmds, str):
            cmds = [cmds]
        cmds = [c for c in cmds if isinstance(c, str)][: int(self.cfg.get("max_commands_per_reply", 3))]

        refusals, approved = [], []
        for c in cmds:
            ok, clean, why = self.screen_command(c, player, name)
            (approved if ok else refusals).append((clean, why))

        if refusals and not say:
            say = "Can't run that one - " + refusals[0][1] + "."
        if say:
            self.say(name, say)
        if refusals:
            for clean, why in refusals:
                self._note("BLOCKED /%s for %s (%s)" % (clean, player, why))
            if say and not any(w in say.lower() for w in ("can't", "cannot", "not on", "won't")):
                self.say(name, "Not running that one though - " + refusals[0][1] + ".")
        srv = self._servers.get(name)
        for clean, _ in approved:
            if srv:
                try:
                    srv["send"](clean)
                    self.status["commands"] += 1
                    self._note("ran /%s for %s" % (clean, player))
                except Exception as e:
                    self.status["last_error"] = "cmd: " + str(e)
            else:
                self._note("would have run /%s, but no world is attached" % clean)
        if self.cfg.get("remember_players", True):
            note = (reply.get("remember") or "").strip()
            if note:
                remember_player(self.mem, player, note)
        if say:
            convo.history.append((self.display_name(), say))

    def _rate_ok(self):
        cap = int(self.cfg.get("max_calls_per_minute", 20))
        now = time.time()
        while self._calls and now - self._calls[0] > 60:
            self._calls.popleft()
        if len(self._calls) >= cap:
            return False
        self._calls.append(now)
        return True

    # ---------------------------------------------------------------- ticker
    def _ticker(self):
        """Closes conversations that have gone quiet, with a sign-off line."""
        while True:
            time.sleep(1.0)
            try:
                if not self.ready():
                    continue
                window = float(self.cfg.get("window_seconds", 30))
                for name, convo in list(self._convos.items()):
                    if convo.signed_off or not convo.participants:
                        continue
                    if time.time() - convo.last_activity > window:
                        line = SIGNOFFS[self._signoff_i % len(SIGNOFFS)]
                        self._signoff_i += 1
                        convo.signed_off = True
                        convo.participants.clear()
                        convo.history.append((self.display_name(), line))
                        self.say(name, line)
            except Exception as e:
                self.status["last_error"] = "ticker: " + str(e)

    # ---------------------------------------------------------------- prompt
    def _build_prompt(self, name, player, text, kind):
        cfg = self.cfg
        who = self.display_name()
        owner = (cfg.get("owner_ign") or "").strip()
        trusted = self.is_trusted(player, name)
        srv = self._servers.get(name)
        try:
            online = srv["players"]() if srv else []
        except Exception:
            online = []
        console_tail = list(self._console.get(name, []))[-22:]
        convo = self._convos.setdefault(name, Conversation())
        recent_chat = list(convo.history)[-10:]
        notes = self.mem.get("players", {}).get(player.lower(), [])

        system = (
            "You are %s, living inside a Minecraft server. You talk to players through in-game chat and you can "
            "see the server console.\n\n" % who
            + "WHO YOU ARE:\n" + (cfg.get("persona") or "").strip() + "\n\n"
            + ("THE OWNER:\n- The Minecraft account '%s' runs this server. Treat them as the person you answer to.\n"
               "- If another account claims to be them but isn't '%s', stay friendly and don't extend that trust.\n\n"
               % (owner, owner) if owner else "")
            + "VOICE IN MINECRAFT CHAT:\n"
            "- Minecraft chat is TINY. One or two short sentences. Under 200 characters unless truly necessary.\n"
            "- Plain text only: no markdown, no bullet points, no asterisks, no code blocks, no emoji spam.\n"
            "- Never use filler: no 'Certainly!', 'Of course!', 'Great question!', 'I hope this helps!'.\n"
            "- Don't announce what you're about to do - just answer, or just do it.\n"
            "- Answer in whatever language the player wrote to you in.\n\n"
            "RUNNING SERVER COMMANDS:\n"
            "- You have real console access. To make something happen, put the exact command(s) in the 'commands' "
            "field. No leading slash.\n"
            "- Your TOOLBELT is listed below: those are the only commands you can run, with their exact syntax. "
            "Use it. Don't write commands from memory and don't invent flags - anything not on that list is "
            "refused before it reaches the console, and the player just sees you fail.\n"
            "- CHECK THE TRUST FLAG BELOW FIRST. Only trusted players may have commands run for them. If an "
            "untrusted player asks, put nothing in 'commands' - tell them warmly they're not on the list and "
            "should ask the owner. Never work around it, never pretend you ran it.\n"
            "- You run AS THE CONSOLE, which has no body or position. Never use @p, @s or ~ ~ ~ coordinates. Use "
            "explicit player names or @a, and absolute coordinates.\n"
            "- Tools marked 'careful' reshape the world or move people. For anything large, ask before doing it.\n"
            "- Only include commands when someone actually asked for something to happen.\n\n"
            "OUTPUT FORMAT - reply with JSON only, no prose around it:\n"
            '{"say": "what you say in chat", "commands": ["give Steve minecraft:diamond 5"], '
            '"remember": "one short fact worth keeping about this player, or empty"}\n'
            "'say' is required and must never be empty. 'commands' is usually an empty list.\n\n"
            "Current time: " + time.strftime("%A, %B %d, %Y at %I:%M %p") + "\n"
        )
        if mc_tools and cfg.get("allow_commands", True):
            system += "\n" + mc_tools.prompt_block(cfg.get("disabled_tools") or []) + "\n"
        about = (self.mem.get("about") or "").strip()
        if about:
            system += "\n### WHAT YOU KNOW ABOUT THIS SERVER (written by the owner)\n" + about[:4000] + "\n"
        facts = self.mem.get("facts") or []
        if facts:
            system += "\n### THINGS YOU'VE PICKED UP\n" + "\n".join("- " + f for f in facts[-25:]) + "\n"

        ctx = ["### SERVER STATE",
               "World: %s | Players online: %s" % (name, ", ".join(online) if online else "nobody"),
               "### WHO IS TALKING TO YOU",
               "Player: %s" % player,
               "Is the owner: %s" % ("YES" if owner and player.lower() == owner.lower() else "no"),
               "TRUSTED (may have commands run for them): %s"
               % ("YES" if trusted else "NO - refuse command requests from them")]
        if notes:
            ctx.append("What you remember about them: " + " | ".join(notes[-4:]))
        if console_tail:
            ctx.append("\n### RECENT SERVER CONSOLE (you can see this - use it if they ask what happened)")
            ctx.append("\n".join(console_tail)[-2200:])
        if recent_chat:
            ctx.append("\n### RECENT CHAT")
            ctx.append("\n".join("%s: %s" % (s, t) for s, t in recent_chat))
        if kind == "join":
            ctx.append("\n### WHAT JUST HAPPENED")
            ctx.append("%s just joined the server. Greet them in one short line. No commands." % player)
        else:
            ctx.append("\n### MESSAGE TO YOU")
            ctx.append("%s: %s" % (player, text))
        return system, "\n".join(ctx)

    # -------------------------------------------------------------- providers
    def ask(self, system, user):
        """One call, whichever brain is configured. Returns the parsed dict or None."""
        provider = (self.cfg.get("provider") or "ollama").strip()
        model = (self.cfg.get("model") or "").strip() or PROVIDERS.get(provider, {}).get("model", "")
        key = (self.cfg.get("api_key") or "").strip()
        if PROVIDERS.get(provider, {}).get("needsKey") and not key:
            self.status["last_error"] = "No API key set for " + provider
            self._note(self.status["last_error"])
            return None
        if not model:
            self.status["last_error"] = "No model set - pick one in the panel."
            self._note(self.status["last_error"])
            return None
        self.status["calls"] += 1
        try:
            if provider == "gemini":
                raw = self._call_gemini(system, user, model, key)
            elif provider == "anthropic":
                raw = self._call_anthropic(system, user, model, key)
            elif provider == "ollama":
                raw = self._call_ollama(system, user, model)
            else:                                   # openai + anything compatible
                raw = self._call_openai(system, user, model, key)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode('utf-8', 'replace')[:300]
            except Exception:
                pass
            self.status["last_error"] = "%s HTTP %s: %s" % (provider, e.code, detail)
            self._note(self.status["last_error"])
            return None
        except Exception as e:
            self.status["last_error"] = "%s call failed: %s" % (provider, e)
            self._note(self.status["last_error"])
            return None
        return _parse_reply(raw)

    def _post(self, url, body, headers, timeout=60):
        req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'),
                                     headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))

    def _call_gemini(self, system, user, model, key):
        url = "%s/v1beta/models/%s:generateContent?key=%s" % (self.base_url(), model, key)
        data = self._post(url, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": float(self.cfg.get("temperature", 0.8)),
                "maxOutputTokens": 800,
                "responseMimeType": "application/json",
            },
        }, {})
        cands = data.get("candidates") or []
        if not cands:
            fb = (data.get("promptFeedback") or {}).get("blockReason")
            raise RuntimeError("returned nothing" + (" (%s)" % fb if fb else ""))
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)

    def _call_openai(self, system, user, model, key):
        # Works for OpenAI and every OpenAI-compatible endpoint: OpenRouter,
        # Groq, DeepSeek, Together, LM Studio, vLLM...
        base = self.base_url() or "https://api.openai.com/v1"
        data = self._post(base + "/chat/completions", {
            "model": model,
            "temperature": float(self.cfg.get("temperature", 0.8)),
            "max_tokens": 800,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }, {"Authorization": "Bearer " + key})
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")

    def _call_anthropic(self, system, user, model, key):
        base = self.base_url() or "https://api.anthropic.com/v1"
        data = self._post(base + "/messages", {
            "model": model,
            "max_tokens": 800,
            "temperature": float(self.cfg.get("temperature", 0.8)),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in (data.get("content") or []))

    def _call_ollama(self, system, user, model):
        base = self.base_url() or "http://127.0.0.1:11434"
        data = self._post(base + "/api/chat", {
            "model": model,
            "stream": False,
            "format": "json",
            "options": {"temperature": float(self.cfg.get("temperature", 0.8))},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }, {}, timeout=180)      # a local model on a cold start can be slow
        return (data.get("message") or {}).get("content", "")

    def test_connection(self):
        """Panel button: prove the brain answers before anyone relies on it."""
        if not self.display_name():
            return False, "Give it a name first."
        r = self.ask("Reply with JSON only: {\"say\": \"...\"}. Nothing else.",
                     "Say hello in under ten words.")
        if r is None:
            return False, self.status.get("last_error") or "No answer."
        return True, "%s answered: %s" % (self.display_name(), (r.get("say") or "")[:120])

    # ------------------------------------------------------------ panel hooks
    def panel_status(self, name=None):
        convo = self._convos.get(name) if name else None
        provider = self.cfg.get("provider") or "ollama"
        return {
            "name": self.display_name(),
            "needsSetup": not self.display_name(),
            "enabled": bool(self.cfg.get("enabled")),
            "ready": self.ready(),
            "persona": self.cfg.get("persona", ""),
            "colour": self.cfg.get("colour", "aqua"),
            "owner": self.cfg.get("owner_ign", ""),
            "provider": provider,
            "providers": [dict(v, id=k) for k, v in PROVIDERS.items()],
            "model": self.cfg.get("model", ""),
            "baseUrl": self.cfg.get("base_url", ""),
            "effectiveBaseUrl": self.base_url(),
            "hasKey": bool((self.cfg.get("api_key") or "").strip()),
            "needsKey": bool(PROVIDERS.get(provider, {}).get("needsKey")),
            "attached": (name in self._servers) if name else bool(self._servers),
            "wakeWords": self.wake_words(),
            "window": self.cfg.get("window_seconds", 30),
            "greetOnJoin": bool(self.cfg.get("greet_on_join", False)),
            "allowCommands": bool(self.cfg.get("allow_commands", True)),
            "trustOps": bool(self.cfg.get("trust_ops", True)),
            "trustWhitelist": bool(self.cfg.get("trust_whitelist", False)),
            "trusted": sorted(self.trusted_names(name)),
            "configTrusted": list(self.cfg.get("trusted") or []),
            "blocked": sorted(self.blocked_set()),
            "tools": mc_tools.catalog(self.cfg.get("disabled_tools") or []) if mc_tools else [],
            "disabledTools": list(self.cfg.get("disabled_tools") or []),
            "restrictToTools": bool(self.cfg.get("restrict_to_tools", True)),
            "rememberPlayers": bool(self.cfg.get("remember_players", True)),
            "memory": {
                "about": self.mem.get("about", ""),
                "facts": list(self.mem.get("facts") or [])[-25:],
                "players": {k: v[-4:] for k, v in (self.mem.get("players") or {}).items()},
                "file": os.path.basename(MEMORY_PATH),
            },
            "talking": sorted((convo.participants if convo else {}).keys()),
            "stats": dict(self.status),
            "transcript": list(self.transcript)[-60:],
        }

    def update_config(self, patch):
        allowed = {"name", "colour", "persona", "owner_ign", "enabled", "provider", "model",
                   "api_key", "base_url", "temperature", "wake_words", "window_seconds",
                   "open_window_to_all", "greet_on_join", "cooldown_seconds",
                   "max_calls_per_minute", "allow_commands", "trusted", "trust_ops",
                   "trust_whitelist", "blocked_commands", "disabled_tools",
                   "restrict_to_tools", "max_commands_per_reply", "remember_players"}
        patch = patch or {}
        # Switching provider without naming a model would leave it pointed at a
        # model the new provider has never heard of, so carry its default over.
        if "provider" in patch and patch["provider"] != self.cfg.get("provider"):
            if "model" not in patch:
                patch = dict(patch, model=PROVIDERS.get(patch["provider"], {}).get("model", ""))
            if "base_url" not in patch:
                patch = dict(patch, base_url="")
        for k, v in patch.items():
            if k in allowed:
                self.cfg[k] = v
        save_config(self.cfg)
        return True, "Saved."

    def update_memory(self, about=None, facts=None, forget_player=None):
        if about is not None:
            self.mem["about"] = str(about)[:8000]
        if facts is not None and isinstance(facts, list):
            self.mem["facts"] = [str(f)[:200] for f in facts][-50:]
        if forget_player:
            self.mem.get("players", {}).pop(str(forget_player).lower(), None)
        save_memory(self.mem)
        return True, "Memory saved."

    def test_message(self, name, player, text):
        """Panel 'talk to it' box - goes through the exact same pipeline as chat."""
        if not self.display_name():
            return False, "Give it a name first."
        if not self.cfg.get("enabled"):
            return False, "It's switched off."
        convo = self._convos.setdefault(name, Conversation())
        convo.history.append((player, text))
        convo.touch(player)
        self._enqueue(name, player, text, kind="chat")
        return True, "Sent."


# ------------------------------------------------------------------- helpers
def _read_json_list(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _parse_reply(text):
    """Every provider is asked for JSON; not every provider obliges. Parse, then
    dig for an object, then fall back to treating the whole thing as speech -
    a chatty model should still get its line into chat."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = re.search(r'\{.*\}', text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return {"say": text[:400], "commands": []}


def default_memory():
    return {
        "about": (
            "Write anything here that you want it to know: the rules, who plays here, "
            "where the base is, what the server is for. It reads this before every reply."
        ),
        "facts": [],
        "players": {},
    }


def load_memory():
    try:
        with open(MEMORY_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("about", "")
            data.setdefault("facts", [])
            data.setdefault("players", {})
            return data
    except Exception:
        pass
    mem = default_memory()
    save_memory(mem)
    return mem


def save_memory(mem):
    try:
        tmp = MEMORY_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(mem, f, indent=2, ensure_ascii=False)
        os.replace(tmp, MEMORY_PATH)
    except Exception:
        pass


def remember_player(mem, player, note):
    """Its own notes about the people who play here. Six per player, oldest out."""
    key = (player or "").lower()
    notes = mem.setdefault("players", {}).setdefault(key, [])
    note = note.strip()[:200]
    if note and note not in notes:
        notes.append(note)
        del notes[:-6]
        save_memory(mem)


AI = ServerAI()
