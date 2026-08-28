# Hearth — a Minecraft server control panel for your own PC

A small web panel for running Minecraft: Java Edition servers on your own machine.
Start and stop worlds, watch the console, change settings without touching
`server.properties` by hand, back the world up, and hand your friends an address
they can join from anywhere.

It runs entirely on your computer. There is no cloud service, no account, and no
telemetry — it's a single Python file and a single HTML file talking to each other
on `127.0.0.1`.

```
double-click start-panel.bat   →   Hearth opens in its own window
```

It is not a browser tab. Hearth gets its own window and its own button on the
taskbar, so it minimises and closes like any other program.

---

## What it does

- **Start / stop worlds** — one click. Stopping sends a proper `stop` so the world saves cleanly.
- **Run several worlds side by side** — one per friend group, each on its own port.
- **Live console** — see chat, joins, deaths and errors as they happen, and type commands back.
- **Who's online** — a live list of players currently in the world.
- **Settings without the text file** — toggles for PvP, difficulty, whitelist, max players, MOTD (with a colour preview), plus a full `server.properties` editor behind an "Advanced" section.
- **Create new worlds** — pick a version, Vanilla, Paper or Fabric, optional seed. The correct server jar is downloaded from Mojang, PaperMC or FabricMC automatically.
- **Switch loader any time** — Vanilla ⇄ Paper ⇄ Fabric in one click. Your world, settings and players are untouched, and the old jar is kept so you can go back.
- **Browse mods without leaving the panel** — search **CurseForge** and **Modrinth** right in the Mods tab, see downloads and descriptions, and install with one click. Required dependencies come along automatically. [Details below.](#mods-and-plugins)
- **Change Minecraft version** — see what you're on, what's newest, and update with the world backed up first. One-click rollback if it goes wrong. [Details below.](#changing-minecraft-version)
- **World icons** — the little picture next to your server in the Minecraft multiplayer list. Any image, auto-resized to 64×64.
- **Backups** — automatic on every stop and every 2 hours (keeps the last 15), plus "Back up now" and one-click restore.
- **An AI that lives in your server** — optional. You name it, you pick its brain (a free local model or any paid API), and it talks to players in chat and runs commands for people you trust. [Details below.](#the-ai)
- **Public address via playit.gg** — optional. Downloads the tunnel agent for you, checks it is really playit's, and runs it alongside your world so friends outside your network can join.
- **Tells you what is missing** — Hearth checks what it needs when it starts and explains anything absent in plain words, with a link that fixes it. It never installs anything on your behalf.
- **Helps you pick how people join** — say who is coming, and Hearth looks at your connection and recommends playit, Tailscale or forwarding a port, with a reason. Some connections cannot forward a port at all, and it will tell you so.
- **Its own window** — not a browser tab. Own taskbar button, and it can put a shortcut on your desktop.
- **Updates itself** — when you ask it to. Your worlds, backups and settings are never touched. [Details below.](#keeping-it-up-to-date)
- **A safety guard** — the panel refuses to start a world whose port is already in use. Two servers running on one world folder will overwrite each other's saves, and this stops that happening.
- **Tests** — `python -m pytest tests -q`. They run on any OS, need no Minecraft server and touch nothing of yours.

---

## Before you install

You need:

| | |
|---|---|
| **Windows 10 / 11** | The panel shells out to `netstat`, `tasklist` and `taskkill`, so it is Windows-only as written. |
| **Python 3.10 or newer** | [python.org/downloads](https://www.python.org/downloads/) — tick **"Add Python to PATH"** during setup. |
| **Java** | Minecraft servers are Java programs. Match the version to your Minecraft version — modern versions need Java 21+, older ones need Java 17. [Microsoft OpenJDK](https://learn.microsoft.com/java/openjdk/download) works well. |
| **Pillow** *(optional)* | Only needed for the world-icon feature: `pip install Pillow` |
| **A playit.gg account** *(optional)* | Only if you want friends outside your home network to join. Free — and Hearth downloads the program for you. |
| **An AI provider** *(optional)* | Only for the AI companion. [Ollama](https://ollama.com) runs free on your own PC; any paid API works too. No Python packages needed either way. |

The panel itself needs no other Python packages — everything else is the standard library.

**You do not have to check any of this yourself.** Hearth looks when it starts,
and if something is missing it opens on **Setup** and tells you what and why, in
plain words, with a link that fixes it. It never installs anything behind your
back.

---

## Install

1. **Get the files.**

   ```bash
   git clone https://github.com/SIllowet/hearth-panel.git
   ```

   Or download the ZIP from the green **Code** button and unzip it anywhere you like.

2. **Start it.** Double-click **`start-panel.bat`**.

   It finds Python wherever it lives — you do not need to have ticked "Add Python
   to PATH", which is what usually causes *"Python was not found"* on a PC that
   has it installed. If Python genuinely is not there, it says so and takes you
   to the download page.

   `python app.py` works too if you prefer a terminal.

3. **Hearth opens in its own window** at `127.0.0.1:8765` — no address bar, its
   own taskbar button. On the very first run you will see *"No worlds yet"*.

4. **Optional: put it on your desktop.** Setup → *Add it to my desktop*. One
   double-click to open it after that.

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

There is no single right answer, and Hearth will work it out with you.

Open **Setup → Letting people in**, say who is joining, and press
**Check my connection**. Hearth looks at your network and recommends one of the
options below, with a reason. Two things decide it, and neither is guessable:

- **Some providers hand out shared addresses** (carrier-grade NAT). If yours
  does, forwarding a port cannot work no matter what you change on the router.
  Worth knowing before you spend an evening on it, not after.
- **Two routers stacked** — your provider's box plus your own — means any
  forwarding rule has to go on both, and you may not have the password for the
  first one.

The check runs only when you ask it to. It traces your first few routers and
asks a public service what your address looks like from outside, which is the
only way to find that out. Nothing is stored or sent anywhere else.

**Just you, on this PC.** Connect to `localhost`. Nothing to set up.

**Everyone on your Wi-Fi.** They connect to this PC's local address, which
Setup shows you. You may need to allow Java through Windows Firewall the first
time.

**A few friends, regularly.** [Tailscale](https://tailscale.com/download) puts
you and them on one private network, as though you were in the same house.
Nothing is exposed to the internet and your home address stays private.
Everyone installs one small app, so it suits a regular group rather than
strangers.

**Anyone you send the address to.** A tunnel is the option that works on every
connection, including the ones that cannot forward a port.
[playit.gg](https://playit.gg) is free and needs no router changes:

1. In Setup, press **Get it for me**. Hearth downloads the agent from playit's
   own signed release and checks the signature before keeping it. About 4 MB,
   saved next to Hearth.
2. Make a playit.gg account, add an agent, and copy its **secret key**.
3. Paste the key into the box in Setup and press **Save**. Hearth writes it into
   your settings — there is no config file to edit.
4. Light the hearth in **Worlds**. The tunnel starts with your world, and the
   address becomes a tap-to-copy button to hand to your friends.

**Forwarding a port yourself.** No third party involved, but it is the most
fiddly option and it publishes your home address to everyone who joins. Send
port `25565` to this PC in your router's settings — on *both* routers if you
have two — and allow Java through Windows Firewall. Most home connections
change address every so often, so whatever you hand out will stop working
eventually unless you add a free dynamic DNS service.

### Tunnel bank

If you run several worlds, pre-make a batch of tunnels on playit.gg and paste them
into **Worlds → Tunnel bank**, one per line:

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
  "backupsRoot": "D:\\HearthBackups",
  "protected": ["MyWorld"],
  "curseforgeKey": "your-curseforge-api-key",
  "bank": []
}
```

| Key | What it's for |
|---|---|
| `serversRoot` | Where newly created worlds get put. |
| `servers[].type` | `vanilla`, `paper` or `fabric`. Decides whether plugins/mods are offered. |
| `servers[].group` | A label, e.g. "College friends". Cosmetic. |
| `memory` | RAM given to every world (`-Xms` / `-Xmx`). Give it about half your total RAM, and never more than you actually have. |
| `backupsRoot` | Optional. Keep backups here instead of inside each world folder — an external drive, say. Each world gets its own folder under it. |
| `protected` | Names that can't be deleted from the panel — a guard for your main world. |
| `curseforgeKey` | Your free CurseForge API key, so the Mods tab can search CurseForge. Optional — Modrinth needs no key. Easiest set from the Mods tab. |
| `bank` | The tunnel bank, described above. Also editable from the UI. |

**`config.json` holds your playit secret and your CurseForge key. It is in
`.gitignore` — keep it that way, and don't paste it anywhere public.** Anyone with
the playit secret can run tunnels on your account.

---

## Mods and plugins

The **Mods** tab is a search box for CurseForge and Modrinth. You never have to
find a download link yourself.

### First: your world needs a loader

A **vanilla** server cannot load anything — that is Minecraft's design, not a limit
of the panel. So the Mods tab will tell you to switch first. Open the **Version**
tab and pick one:

| | What you get | What your friends have to do |
|---|---|---|
| **Paper** | *Plugins* — claims, homes, teleports, anti-grief, world edit. Server-side only. | **Nothing.** They join with ordinary Minecraft. |
| **Fabric** | *Mods* — Create, JEI, Waystones, new blocks and mobs. | **Install the same mods themselves**, or they can't join. |

Switching keeps your world, your settings and your players. The old jar is kept, so
you can switch back.

> **Pick Paper if your friends aren't technical.** Mods are more fun, but every
> single player needs the identical mod list in their own launcher. Plugins ask
> nothing of them.

### Browsing and installing

1. Open the **Mods** tab and click **Browse**.
2. Choose **CurseForge** or **Modrinth** with the toggle.
3. Type what you want — `waystones`, `jei`, `create` — and hit search.

Results are already filtered to your Minecraft version and your loader, so anything
you see will actually run. Each result shows its icon, author, download count and
last-updated date.

Click **Install** and the panel downloads the right file into `mods/` (or `plugins/`),
**plus every mod it depends on**. Installing Waystones, for example, quietly brings
Fabric API, Balm and Shogi with it — you don't have to know that list.

Then **restart the world** to load it.

**Updating a mod** is just installing it again — the panel matches it by name and
replaces the old jar instead of leaving two copies fighting each other.

### The CurseForge key

**Modrinth works immediately and needs nothing.** CurseForge requires a free API
key — their rule, not the panel's. If you never touch CurseForge you can ignore
this entirely.

To add one:

1. Sign in at [console.curseforge.com](https://console.curseforge.com/).
2. Copy the API key it gives you.
3. In the Mods tab, click **Add CurseForge key**, paste, save.

The panel checks the key against CurseForge before saving it, so you'll know at once
if it's wrong. It's stored in `config.json` as `curseforgeKey` — which is
`.gitignore`d, and should stay that way.

Some CurseForge authors switch off third-party downloads. Those show as
**open page** instead of **Install**; that opens the mod's own page so you can
download it yourself, then use **Add from file** at the bottom of the tab.

### Adding something by hand

The Mods tab still takes a direct `.jar` URL or a file from your PC, for anything
that isn't in either catalogue. Remove any mod with the **×** next to it.

---

## Changing Minecraft version

The **Version** tab shows what your world runs, what the newest release is, and
whether Paper and Fabric support it yet.

Updating **backs the world up first**, keeps the old jar as `server.jar.previous`,
and gives you a **Rollback** button that flips between the two.

> **The big warning:** everyone who plays has to change their launcher to the
> matching version on the same day you do. A server on 26.2 will simply refuse a
> player still on 26.1.2, with an unhelpful error. Tell your friends *before* you
> click, not after.

Two more things worth knowing:

- **Newer Minecraft needs newer Java.** The tab checks the Java you have installed
  and warns you if the version you picked needs a newer one.
- **Mods are version-locked.** Updating Minecraft usually means re-installing your
  mods for the new version. Do it on a copy of the world first if the world matters.

Minecraft moved to year-based version numbers (26.1, 26.2 …). Don't be surprised
when the newest release isn't a `1.21.x`.

---

## Keeping it up to date

Hearth checks for a new version and, if you want it, fetches it for you. Open
**Setup → Updates**.

The new copy is put aside and swapped in the next time you start Hearth — the
running program has its own files open, so they cannot be replaced underneath
it. The swap takes about a second.

**Only Hearth's own files are replaced.** Your worlds, backups, `config.json`,
your playit secret and your AI key are never touched. The version it replaced is
kept in `.update/` so you can put it back by hand if something looks wrong.

If you would rather not have it check at all, you are welcome to ignore the
card — nothing downloads unless you press the button.

---

## Backups

Every world is zipped to `<server folder>\backups\`:

- when you stop it,
- every 2 hours while it's running,
- whenever you hit **Back up now**.

**What goes in.** The overworld, **the Nether and the End**, and the settings that
make the world what it is — `server.properties`, `ops.json`, `whitelist.json`, the
ban lists. Paper and Spigot keep the other dimensions in folders *beside* the world
rather than inside it, so a backup that saves only `level-name` quietly loses
everything anyone built through a portal. Hearth takes all of them together.

**What a restore puts back** is the world — every dimension of it. The settings are
in the zip so you can recover them if the whole folder is ever lost, but a restore
leaves the live ones alone: quietly reverting `server.properties` would undo changes
you made since, which is not what anyone means by "restore my world".

A world that is running is told to save first, so **Back up now** copies what is
actually in the world rather than whatever happened to be on disk.

The last 15 automatic backups are kept; manual ones are never deleted automatically.

**Somewhere else, if you prefer.** By default backups sit inside the world folder,
which is convenient but means they share its disk and its fate. Set `backupsRoot`
in `config.json` to put them somewhere else — an external drive, say — and each
world gets its own folder under it.

**Restore** is on the Backups tab. The world must be stopped first. Your current world
folders aren't thrown away — each is renamed to `<name>_prerestore_<timestamp>` and
left beside the new one, so a wrong restore is always undoable. All the dimensions
move aside together, so you never end up with an overworld from one day and a Nether
from another. The two most recent of these are kept and older ones are cleared away.

---

## Security, plainly

- The panel listens on `127.0.0.1` only. Nothing outside your PC can reach it.
- **Other web pages can't drive it.** Listening on `127.0.0.1` keeps other *computers*
  out, but any page open in your browser can still send requests to it. Hearth checks
  that a request came from the panel itself — the address it was sent to, where it came
  from, and a header only the panel's own page can set — and refuses the rest. That
  also covers the trick of pointing a hostile domain name at `127.0.0.1`.
- **There is no password.** Anyone who can use your computer can use the panel, and
  the panel can run any server command. Don't expose port 8765 to your network or
  the internet — it is not built to survive that.
- **Downloads are checked, not just fetched.** Server jars are verified against the
  checksum Mojang or PaperMC published, mods against the one the catalogue gave for
  that exact build, and Hearth's own updates against a `SHA256SUMS` file fetched
  separately from the code. Anything that doesn't match is thrown away rather than
  installed, and nothing is written into place until it has passed.
- **Your playit secret stays off the command line**, where every other program on the
  PC could read it — Hearth hands it to the agent through a file or the environment
  instead, where the agent supports it.
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

**"Python was not found"** — usually Python *is* installed, but the
*"Add Python to PATH"* box was never ticked. `start-panel.bat` checks the usual
install folders and the `py` launcher as well as PATH, so try that before
reinstalling anything.

**Java not found** — Setup tells you if Java is missing or too old, and links to
the right download. The panel looks in the Microsoft, Adoptium, Zulu and Oracle
install folders and then on your PATH. If you put Java somewhere unusual, make
sure `java -version` works in a terminal.

**Hearth opened in my browser instead of its own window** — it uses Edge or
Chrome's app mode for that, so if neither is installed it falls back to your
normal browser. Everything still works; it is just a tab.

**Icons don't work** — install Pillow: `pip install Pillow`.

**The panel shows a world as running with an empty console** — that world was started by
a previous run of the panel, so this one has no pipe to its output. Stop and start it
from the panel to get the console back.

**I edited `app.py` and nothing changed** — restart the panel. If you edited a file
that ships with Hearth, also run `python tools/make_sums.py`, or the updater will
refuse the next update as not matching what was published.

**"That request did not come from the Hearth panel"** — something other than the
panel's own page tried to POST to it. If you were doing something ordinary in the
panel when this appeared, a stale tab is the usual cause: reload it.

**I want to see what happened before the panel started** — the Console tab only holds
output from worlds this run of the panel started. **Open the full log file** on that
tab reads the world's own `logs/latest.log`, which goes back further.

---

## How it fits together

```
app.py            the whole backend — HTTP server, process management,
                  backups, jar downloads, config. Standard library only.
ui/index.html     the whole frontend — one file, no build step, no dependencies.
modstore.py       the mod catalogue: CurseForge + Modrinth search, version and
                  loader filtering, dependency resolution. Standard library only.
ai.py             the console companion: chat, providers, trust, memory.
                  Optional — delete it and the panel runs exactly as before.
mc_tools.py       the toolbelt: every Minecraft command the AI may use, with
                  syntax and risk level. Edit this to widen or narrow its reach.
hearth_setup.py   first-run checks, the network probe, and the self-updater.
config.json       your servers and secrets. Created on first run. Never committed.
ai_config.json    the AI's name, personality, provider and API key. Never committed.
ai_memory.json    what the AI knows about your server and your players.
SHA256SUMS        the checksum of every file an update replaces. Regenerate with
                  `python tools/make_sums.py` before publishing a version.
tests/            run with `python -m pytest tests -q`. No servers, no network,
                  no Windows needed — they run anywhere Python does.
```

Every world runs as a child process of the panel, with its stdin/stdout piped — which
is how the live console and the clean `stop` work.

---

## Licence

MIT — see [LICENSE](LICENSE). Do what you like with it.

Not affiliated with Mojang, Microsoft, PaperMC or playit.gg.
