# Hearth — a Minecraft server control panel for your own PC

A small web panel for running Minecraft: Java Edition servers on your own machine.
Start and stop worlds, watch the console, change settings without touching
`server.properties` by hand, back the world up, and hand your friends an address
they can join from anywhere.

It runs entirely on your computer. There is no cloud service, no account, and no
telemetry — it's a single Python file and a single HTML file talking to each other
on `127.0.0.1`.

```
python app.py     →     http://127.0.0.1:8765
```

---

## What it does

- **Start / stop worlds** — one click. Stopping sends a proper `stop` so the world saves cleanly.
- **Run several worlds side by side** — one per friend group, each on its own port.
- **Live console** — see chat, joins, deaths and errors as they happen, and type commands back.
- **Who's online** — a live list of players currently in the world.
- **Settings without the text file** — toggles for PvP, difficulty, whitelist, max players, MOTD (with a colour preview), plus a full `server.properties` editor behind an "Advanced" section.
- **Create new worlds** — pick a version, vanilla or Paper, optional seed. The correct server jar is downloaded from Mojang (or PaperMC) automatically.
- **Convert vanilla → Paper** — keeps the world, backs up the old jar, and creates a `plugins/` folder.
- **Plugins / mods** — add by URL or local file path, remove with a click.
- **World icons** — the little picture next to your server in the Minecraft multiplayer list. Any image, auto-resized to 64×64.
- **Backups** — automatic on every stop and every 2 hours (keeps the last 15), plus "Back up now" and one-click restore.
- **An AI that lives in your server** — optional. You name it, you pick its brain (a free local model or any paid API), and it talks to players in chat and runs commands for people you trust. [Details below.](#the-ai)
- **Public address via playit.gg** — optional. Runs the tunnel agent for you so friends outside your network can join.
- **A safety guard** — the panel refuses to start a world whose port is already in use. Two servers running on one world folder will overwrite each other's saves, and this stops that happening.

---

## Before you install

You need:

| | |
|---|---|
| **Windows 10 / 11** | The panel shells out to `netstat`, `tasklist` and `taskkill`, so it is Windows-only as written. |
| **Python 3.10 or newer** | [python.org/downloads](https://www.python.org/downloads/) — tick **"Add Python to PATH"** during setup. |
| **Java** | Minecraft servers are Java programs. Match the version to your Minecraft version — modern versions need Java 21+, older ones need Java 17. [Microsoft OpenJDK](https://learn.microsoft.com/java/openjdk/download) works well. |
| **Pillow** *(optional)* | Only needed for the world-icon feature: `pip install Pillow` |
| **A playit.gg account** *(optional)* | Only if you want friends outside your home network to join. Free. |
| **An AI provider** *(optional)* | Only for the AI companion. [Ollama](https://ollama.com) runs free on your own PC; any paid API works too. No Python packages needed either way. |

The panel itself needs no other Python packages — everything else is the standard library.

---

## Install

1. **Get the files.**

   ```bash
   git clone https://github.com/YOUR-USERNAME/hearth-panel.git
   ```

   Or download the ZIP from the green **Code** button and unzip it anywhere you like.

2. **Start it.**

   ```bash
   python app.py
   ```

   On Windows you can just double-click **`start-panel.bat`** instead — that opens it
   with no black console window hanging around.

3. Your browser opens at **http://127.0.0.1:8765**. On the very first run you'll see
   *"No worlds yet"*.

To stop the panel, click **"Put the panel to bed"** at the bottom left. Any Minecraft
world that is currently running keeps running — closing the panel does not kick your friends.

---

## First world

### Option A — make a new one

Go to **Worlds → ＋ New world**, give it a name, pick a Minecraft version, choose
**Vanilla** or **Paper** (Paper if you want plugins), and optionally paste a seed.

The panel downloads the server jar, creates the folder under
`C:\Users\YOU\MinecraftServers\<name>`, and writes a sensible `server.properties`.

> **Note on the Minecraft EULA:** creating a world writes `eula=true` into the
> server folder. That is you accepting the
> [Minecraft End User Licence Agreement](https://www.minecraft.net/eula) — the server
> will not run otherwise. If you don't agree to it, don't use this.

### Option B — use a server folder you already have

Stop the panel, open `config.json` next to `app.py`, and add an entry to `servers`:

```json
{
  "name": "MyOldWorld",
  "path": "C:\\Users\\YOU\\Desktop\\MyMinecraftServer",
  "type": "vanilla"
}
```

The folder needs a `server.jar` in it. Use double backslashes in the path. Start the
panel again and it will appear in the sidebar.

---

## Letting friends join

There are three levels, and you probably want the third.

**1. Just you, on this PC.** Connect to `localhost`. Nothing to set up.

**2. People in your house, on the same Wi-Fi.** They connect to this PC's local IP —
run `ipconfig` and give them the IPv4 address, e.g. `192.168.1.42`. You may need to
allow Java through Windows Firewall the first time.

**3. Friends anywhere.** Your home router blocks incoming connections, so you need a
tunnel. This panel is built around [playit.gg](https://playit.gg), which is free and
needs no port forwarding:

1. Make a playit.gg account and download `playit.exe`.
2. In playit.gg, create a **Minecraft Java** tunnel pointing at local port `25565`
   (or whatever port your world uses — the Worlds tab shows it).
3. playit gives you an address like `some-words.gl.joinmc.link`.
4. Copy your **agent secret key** from the playit dashboard.
5. Stop the panel and put both into `config.json`:

   ```json
   "tunnel": {
     "exe": "C:\\path\\to\\playit.exe",
     "secret": "your-playit-agent-secret",
     "address": "some-words.gl.joinmc.link"
   }
   ```

   The `secret` only needs to be on **one** server entry — that's the agent, and it
   serves every tunnel on your account. Other worlds just need their own `address`.
6. Start the panel. On the Home tab, **"Open it"** starts the tunnel; the address
   becomes a tap-to-copy button to hand to your friends.

### Tunnel bank

If you run several worlds, pre-make a batch of tunnels on playit.gg and paste them
into **Worlds → 🎟️ Tunnel bank**, one per line:

```
25565  world-one.gl.joinmc.link
25566  world-two.gl.joinmc.link
25567  world-three.gl.joinmc.link
```

New worlds then grab a free tunnel automatically, and deleting a world returns its
tunnel to the pool.

---

## The AI

Optional. Off until you name it.

Open the **AI** tab and give it a name — that name is what players say in chat to
get its attention. It has no name of its own and this panel won't pick one for
you; it's yours to name.

Once named, it sits in the server console: it reads chat, joins in when someone
says its name, and can run Minecraft commands for players you trust.

```
<Steve> ember can you make it daytime
[Ember] Sure. Sun's up.
        → time set day
```

After it answers, a **30-second window** stays open — nobody has to keep saying
its name to carry on the conversation. When the window goes quiet it signs off
and stops listening.

### Picking a brain

It is not tied to any one AI company. The **Brain** section has a dropdown:

| Provider | Cost | Notes |
|---|---|---|
| **Ollama** | Free | Runs on your own PC. Install [Ollama](https://ollama.com), `ollama pull llama3.2`, done. Nothing leaves your computer. |
| **Google Gemini** | Free tier | Key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey). |
| **OpenAI** | Paid | Key from [platform.openai.com](https://platform.openai.com/api-keys). |
| **Anthropic (Claude)** | Paid | Key from [console.anthropic.com](https://console.anthropic.com). |
| **Anything OpenAI-compatible** | Depends | OpenRouter, Groq, DeepSeek, Mistral, Together, LM Studio, vLLM… paste the base URL (ending in `/v1`), the model id and your key. |

Pick one, paste a key if it needs one, and hit **Test connection** — it asks the
model to say hello and shows you exactly what came back. Switching providers
later swaps in that provider's default model automatically, so you can move from
a local model to a paid one (or back) without breaking anything.

Bigger models follow instructions and Minecraft syntax noticeably better. A small
local model works fine for chat and simple commands.

### The toolbelt

The AI cannot type whatever it likes into your console.

Every command it's allowed to touch lives in `mc_tools.py` as a catalog — 65 of
them, each with its real syntax, an example and a risk level. That catalog
is handed to the model on every reply, so it picks a tool off a list instead of
writing commands from memory. On the way back, anything that isn't on the list is
thrown away before it reaches the server.

That means a model that hallucinates `/fly` or invents a flag simply fails safely
and says so, instead of spraying errors into your console.

Every tool has a switch in the panel. Turn off `fill` and `clone` if you don't
want it reshaping terrain; turn off everything and it's a chat companion.

Some commands are **never** available, to anyone, including you:

```
stop  op  deop  ban  ban-ip  pardon  whitelist  save-off  reload  debug  jfr
```

Those either kill the server, hand out permanent power, or switch off the
saving that protects your world. They aren't a setting you can toggle.

That's the whole vanilla command set bar three — `return`, `publish` and `test`
only work inside a datapack function, a singleplayer world, or the gametest
harness, so a chat message can't reach them. Add them to `mc_tools.py` if you
disagree; the panel reads whatever is in that list.

### Who it listens to

Chat is for everyone. **Commands are for people you trust.**

Add players on the AI tab, or leave *"Ops are trusted"* on so everyone in
`ops.json` counts automatically. Anyone else who asks for a command gets a polite
no — the check happens in the panel, not in the model's judgement, so no amount
of sweet-talking in chat gets around it.

### Memory

It keeps a small memory file, `ai_memory.json`, next to `app.py`:

- **What you write** — a box on the AI tab. Server rules, who's who, where the
  base is, what the server is for. It reads this before every reply.
- **What it notices** — a few short notes per player, picked up from conversation.
  You can see them all on the AI tab and forget any player with one click.

It's plain JSON, so you can edit it by hand if you'd rather. Nothing is sent
anywhere except to the provider you chose, as part of the reply it's working on.

### Notes

- It only works on worlds **started from this panel** — it needs the console pipe.
  A world left running from a previous session has no AI attached until you
  restart it here.
- If you edit `ai.py`, restart the panel.
- `ai_config.json` holds your API key and is in `.gitignore`. Keep it there.

---

## config.json

Created automatically on first run, next to `app.py`. Stop the panel before editing it.

```json
{
  "serversRoot": "C:\\Users\\YOU\\MinecraftServers",
  "servers": [
    {
      "name": "MyWorld",
      "path": "C:\\Users\\YOU\\MinecraftServers\\MyWorld",
      "type": "vanilla",
      "version": "1.21.4",
      "group": "College friends",
      "tunnel": {
        "exe": "C:\\path\\to\\playit.exe",
        "secret": "your-playit-agent-secret",
        "address": "myworld.gl.joinmc.link"
      }
    }
  ],
  "active": "MyWorld",
  "memory": { "min": "2G", "max": "4G" },
  "protected": ["MyWorld"],
  "bank": []
}
```

| Key | What it's for |
|---|---|
| `serversRoot` | Where newly created worlds get put. |
| `servers[].type` | `vanilla`, `paper` or `fabric`. Decides whether plugins/mods are offered. |
| `servers[].group` | A label, e.g. "College friends". Cosmetic. |
| `memory` | RAM given to every world (`-Xms` / `-Xmx`). Give it about half your total RAM, and never more than you actually have. |
| `protected` | Names that can't be deleted from the panel — a guard for your main world. |
| `bank` | The tunnel bank, described above. Also editable from the UI. |

**`config.json` holds your playit secret. It is in `.gitignore` — keep it that way,
and don't paste it anywhere public.** Anyone with that key can run tunnels on your
playit account.

---

## Backups

Every world is zipped to `<server folder>\backups\`:

- when you stop it,
- every 2 hours while it's running,
- whenever you hit **Back up now**.

The last 15 automatic backups are kept; manual ones are never deleted automatically.

**Restore** is on the Backups tab. The world must be stopped first. Your current world
folder isn't thrown away — it's renamed to `world_prerestore_<timestamp>` and left
beside the new one, so a wrong restore is always undoable.

---

## Security, plainly

- The panel listens on `127.0.0.1` only. Nothing outside your PC can reach it.
- **There is no password.** Anyone who can use your computer can use the panel, and
  the panel can run any server command. Don't expose port 8765 to your network or
  the internet — it is not built to survive that.
- The playit tunnel exposes your **Minecraft server**, not the panel.
- **The AI has real console access.** It is gated two ways — the player has to be
  on your trusted list, and the command has to be on the toolbelt — and the
  destructive commands are blocked outright. But it is still a language model
  running commands on your server, so keep the trusted list to people you'd hand
  the console to anyway, and back your world up (the panel already does).
- Your AI provider sees the chat it's replying to, the recent console lines and
  your memory file. If that matters to you, run Ollama and nothing leaves the PC.
- New worlds are created with `online-mode=true`, which means Minecraft verifies that
  players own the game. If you set it to `false`, anyone who knows your address can
  join under any name — and be aware that **flipping this setting on an existing world
  changes how player saves are looked up**, so everyone's inventory and position will
  appear to reset until you flip it back. Pick one and leave it alone.

---

## Troubleshooting

**"server.jar missing"** — the folder in `config.json` doesn't have a `server.jar`. Check the path.

**The world starts and immediately stops** — open the Console tab and read the last few
lines. Almost always one of: wrong Java version for that Minecraft version, `eula=true`
not set, or the port already taken.

**"BLOCKED: port … is already in use"** — that's the safety guard. Something is already
listening on that port, usually a server that's still running from before. Hit **Stop**
first (the panel can stop orphaned servers too), then start it again.

**Java not found** — the panel looks for `C:\Program Files\Microsoft\jdk-*` first, then
falls back to whatever `java` is on your PATH. If you installed Java elsewhere, make sure
`java -version` works in a terminal.

**Icons don't work** — install Pillow: `pip install Pillow`.

**The panel shows a world as running with an empty console** — that world was started by
a previous run of the panel, so this one has no pipe to its output. Stop and start it
from the panel to get the console back.

**I edited `app.py` and nothing changed** — restart the panel.

---

## How it fits together

```
app.py            the whole backend — HTTP server, process management,
                  backups, jar downloads, config. Standard library only.
ui/index.html     the whole frontend — one file, no build step, no dependencies.
ai.py             the console companion: chat, providers, trust, memory.
                  Optional — delete it and the panel runs exactly as before.
mc_tools.py       the toolbelt: every Minecraft command the AI may use, with
                  syntax and risk level. Edit this to widen or narrow its reach.
config.json       your servers and secrets. Created on first run. Never committed.
ai_config.json    the AI's name, personality, provider and API key. Never committed.
ai_memory.json    what the AI knows about your server and your players.
```

Every world runs as a child process of the panel, with its stdin/stdout piped — which
is how the live console and the clean `stop` work.

---

## Licence

MIT — see [LICENSE](LICENSE). Do what you like with it.

Not affiliated with Mojang, Microsoft, PaperMC or playit.gg.
