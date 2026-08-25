# Changelog

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
