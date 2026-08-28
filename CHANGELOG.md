# Changelog

## 1.4.0

- **Other web pages can no longer drive the panel.** Listening on `127.0.0.1`
  keeps other computers out, but any page open in your browser could still
  send it commands, and a plain form was enough to do it — no warning, nothing
  in the console. Hearth now checks that a request came from its own page and
  turns away everything else, including a hostile domain name pointed at
  `127.0.0.1`.
- **Backups no longer lose the Nether and the End.** Paper and Spigot keep
  those in folders beside the world rather than inside it, so a backup of
  "the world" was quietly saving the overworld and nothing else. Every
  dimension now goes in together, along with `server.properties`, the ops
  list and the whitelist — so if you ever lose the whole folder, the zip has
  what you need. A restore puts every dimension back together; it leaves your
  current settings alone rather than quietly reverting them.
- **Back up now saves the world first.** It used to copy whatever happened to
  already be on disk, which on a busy server is not what you just did.
- **Players stop disappearing from the panel.** Who is online was worked out
  by re-reading the console, so on a chatty server the "joined the game" line
  scrolled off the end and the player vanished from the panel while still
  standing in the world.
- **Downloads are checked before they are installed.** Mods are verified
  against the checksum the catalogue published for that exact build, Fabric
  jars are checked for being jars at all, and nothing is written into place
  until it has passed. Hearth's own updates are now verified against a
  checksum file fetched separately from the code — if they do not match, the
  update is thrown away rather than installed.
- **Your playit secret is off the command line**, where every other program on
  the PC could read it.
- **See the whole log.** The Console tab only ever held output from this run of
  the panel. "Open the full log file" reads the world's own `logs/latest.log`,
  for when you want to know why it stopped last night.
- The console now keeps 2000 lines instead of 500, and the panel fetches only
  what is new instead of the whole console every second and a half.
- Backups can live outside the world folder — set `backupsRoot` in
  `config.json`.
- Two backups taken in the same second no longer overwrite each other, and two
  restores in the same second no longer collide.
- A restore that fails partway now leaves your world back under its own name
  rather than a half-written one beside it.
- `config.json` is written whole or not at all, so a crash partway through
  can't leave it unreadable.
- Hearth now has a test suite: `python -m pytest tests -q`.

## 1.3.0

- **Hearth fetches playit for you.** One button. It comes from playit's own
  signed release, and Hearth checks the signature before keeping it — if it is
  not signed by playit, it is thrown away.
- **No more editing config.json by hand.** Paste your playit secret into the
  box in Setup and Hearth saves it for you. That was the fiddliest step in the
  whole setup and it is gone.
- Setting up playit is now three plain steps that tick themselves off as you
  go, instead of a wall of instructions.

## 1.2.0

- **Hearth now helps you pick how people join.** Tell it who is coming — your
  house, a few friends, or anyone you send the address to — and it looks at
  your connection and says which option will actually work, and why.
- It can tell when your provider gives you a shared address, in which case
  forwarding a port cannot work at all, and when you have two routers stacked,
  which makes it a chore. Better to know before you spend an evening on it.
- playit, Tailscale and forwarding a port are now three equal choices rather
  than one assumed default. Anything you already have installed is marked.
- playit is no longer listed as something Hearth is missing. It never was.

## 1.1.0

- **Hearth opens in its own window.** No address bar, no browser tab. It gets
  its own button on the taskbar, so it minimises, alt-tabs and closes like any
  other program. Closing your browser no longer closes Hearth.
- **Desktop shortcut.** Setup can put Hearth on your desktop for you, with its
  own icon. One double-click to open it.
- Hearth now has an icon.

## 1.0.0

First tracked version.

- **Setup checks.** Hearth now looks at what it needs when it starts and, if
  something is missing, says so in plain words with a link that fixes it. It
  never installs anything for you.
- **Updates.** Hearth checks for a newer version and can fetch it for you. The
  new copy is swapped in the next time you start it. Your worlds, backups,
  settings and API keys are never touched.
- **Sharing.** Notes on the ways to let friends in: the playit tunnel,
  Tailscale, or forwarding a port on your own router.
