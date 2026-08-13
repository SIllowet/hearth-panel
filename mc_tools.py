#!/usr/bin/env python3
# mc_tools.py - the toolbelt: every Minecraft command the AI is allowed to reach for.
#
# Why this exists: without it, the model writes command strings from memory. It
# invents flags, mixes up 1.12 and modern syntax, and the panel finds out only
# when the console spits an error at a player. So instead of "write a command",
# she gets a menu: a real catalog of tools, each with its exact syntax and an
# example, and anything not on the menu never reaches stdin.
#
# Two jobs:
#   prompt_block()  -> the reference she reads before answering
#   check()         -> the gate the answer has to pass on the way out
#
# stdlib only, on purpose. Both panels import this and nothing else.

# risk levels:
#   safe     - hand it to anyone trusted, worst case is a silly effect
#   careful  - can reshape the world or move people around; trusted only, and
#              she's told to confirm before big ones
#   blocked  - never runnable from chat, by anyone, ever. Listed anyway so she
#              can say "that one's off-limits to me" instead of guessing.
TOOLS = [
    # ---------------------------------------------------------------- players
    {"cmd": "give", "cat": "Players", "risk": "safe",
     "what": "Put items straight into someone's inventory.",
     "syntax": "give <player> <item> [count]",
     "example": "give Nyine9 minecraft:diamond 5"},
    {"cmd": "clear", "cat": "Players", "risk": "careful",
     "what": "Remove items from someone's inventory. With no item given it empties everything.",
     "syntax": "clear <player> [item] [maxCount]",
     "example": "clear Nyine9 minecraft:dirt"},
    {"cmd": "effect", "cat": "Players", "risk": "safe",
     "what": "Give or clear a potion effect.",
     "syntax": "effect give <player> <effect> [seconds] [amplifier] | effect clear <player> [effect]",
     "example": "effect give Nyine9 minecraft:night_vision 300 0"},
    {"cmd": "enchant", "cat": "Players", "risk": "safe",
     "what": "Enchant the item the player is holding.",
     "syntax": "enchant <player> <enchantment> [level]",
     "example": "enchant Nyine9 minecraft:unbreaking 3"},
    {"cmd": "experience", "cat": "Players", "risk": "safe",
     "what": "Add, set or query XP. 'xp' is the short form.",
     "syntax": "experience add|set|query <player> <amount> [levels|points]",
     "example": "experience add Nyine9 30 levels"},
    {"cmd": "gamemode", "cat": "Players", "risk": "careful",
     "what": "Switch someone between survival, creative, adventure and spectator.",
     "syntax": "gamemode survival|creative|adventure|spectator <player>",
     "example": "gamemode creative Nyine9"},
    {"cmd": "item", "cat": "Players", "risk": "careful",
     "what": "Replace or modify an item in a specific inventory slot.",
     "syntax": "item replace entity <player> <slot> with <item> [count]",
     "example": "item replace entity Nyine9 armor.head with minecraft:diamond_helmet 1"},
    {"cmd": "attribute", "cat": "Players", "risk": "careful",
     "what": "Read or change an entity attribute like max health or speed.",
     "syntax": "attribute <target> <attribute> base set <value>",
     "example": "attribute Nyine9 minecraft:max_health base set 40"},
    {"cmd": "spawnpoint", "cat": "Players", "risk": "safe",
     "what": "Set where a player respawns.",
     "syntax": "spawnpoint <player> [x y z]",
     "example": "spawnpoint Nyine9 -585 70 935"},
    {"cmd": "kill", "cat": "Players", "risk": "careful",
     "what": "Kill entities. Aimed at a mob type it clears mobs; aimed at a player it kills them.",
     "syntax": "kill <target>",
     "example": "kill @e[type=minecraft:zombie,distance=..80]"},
    {"cmd": "damage", "cat": "Players", "risk": "careful",
     "what": "Deal a set amount of damage to something.",
     "syntax": "damage <target> <amount> [damageType]",
     "example": "damage Nyine9 4 minecraft:magic"},
    {"cmd": "list", "cat": "Players", "risk": "safe",
     "what": "Who is online right now.",
     "syntax": "list [uuids]",
     "example": "list"},
    {"cmd": "defaultgamemode", "cat": "Players", "risk": "careful",
     "what": "The mode new players start in.",
     "syntax": "defaultgamemode survival|creative|adventure|spectator",
     "example": "defaultgamemode survival"},
    {"cmd": "spectate", "cat": "Players", "risk": "safe",
     "what": "Make a spectator follow another entity's view.",
     "syntax": "spectate <target> [spectator]",
     "example": "spectate Nyine9 Steve"},
    {"cmd": "transfer", "cat": "Players", "risk": "careful",
     "what": "Send players to a different server. They leave this one - ask first.",
     "syntax": "transfer <host> [port] [players]",
     "example": "transfer play.example.com 25565 Nyine9"},
    {"cmd": "kick", "cat": "Players", "risk": "careful",
     "what": "Disconnect a player. They can rejoin straight away.",
     "syntax": "kick <player> [reason]",
     "example": "kick Nyine9 afk in the nether portal"},

    # ------------------------------------------------------------------ world
    {"cmd": "time", "cat": "World", "risk": "safe",
     "what": "Set or add to the world time.",
     "syntax": "time set day|night|noon|midnight|<ticks> | time add <ticks>",
     "example": "time set day"},
    {"cmd": "weather", "cat": "World", "risk": "safe",
     "what": "Change the weather.",
     "syntax": "weather clear|rain|thunder [seconds]",
     "example": "weather clear 600"},
    {"cmd": "difficulty", "cat": "World", "risk": "careful",
     "what": "Set the world difficulty.",
     "syntax": "difficulty peaceful|easy|normal|hard",
     "example": "difficulty normal"},
    {"cmd": "gamerule", "cat": "World", "risk": "careful",
     "what": "Read or change a game rule (keepInventory, mobGriefing, doDaylightCycle...).",
     "syntax": "gamerule <rule> [value]",
     "example": "gamerule keepInventory true"},
    {"cmd": "setworldspawn", "cat": "World", "risk": "careful",
     "what": "Move the world spawn point.",
     "syntax": "setworldspawn [x y z]",
     "example": "setworldspawn -585 70 935"},
    {"cmd": "worldborder", "cat": "World", "risk": "careful",
     "what": "Resize or move the world border.",
     "syntax": "worldborder set <blocks> [seconds] | worldborder center <x> <z>",
     "example": "worldborder set 5000"},
    {"cmd": "seed", "cat": "World", "risk": "safe",
     "what": "Show the world seed.",
     "syntax": "seed",
     "example": "seed"},
    {"cmd": "locate", "cat": "World", "risk": "safe",
     "what": "Find the nearest structure, biome or point of interest.",
     "syntax": "locate structure|biome|poi <id>",
     "example": "locate structure minecraft:village_plains"},
    {"cmd": "save-all", "cat": "World", "risk": "safe",
     "what": "Flush the world to disk. Good before anything risky.",
     "syntax": "save-all [flush]",
     "example": "save-all flush"},
    {"cmd": "save-on", "cat": "World", "risk": "careful",
     "what": "Re-enable automatic saving.",
     "syntax": "save-on",
     "example": "save-on"},

    # ------------------------------------------------------------------ blocks
    {"cmd": "setblock", "cat": "Blocks", "risk": "careful",
     "what": "Place one block at exact coordinates.",
     "syntax": "setblock <x> <y> <z> <block> [destroy|keep|replace]",
     "example": "setblock -585 70 935 minecraft:torch"},
    {"cmd": "fill", "cat": "Blocks", "risk": "careful",
     "what": "Fill a box with a block. Big volumes lag the server - keep them modest.",
     "syntax": "fill <x1 y1 z1> <x2 y2 z2> <block> [replace <filter>]",
     "example": "fill -590 64 930 -580 64 940 minecraft:glass"},
    {"cmd": "clone", "cat": "Blocks", "risk": "careful",
     "what": "Copy a region of blocks somewhere else.",
     "syntax": "clone <x1 y1 z1> <x2 y2 z2> <dest x y z>",
     "example": "clone -590 64 930 -580 70 940 -560 64 930"},
    {"cmd": "fillbiome", "cat": "Blocks", "risk": "careful",
     "what": "Change the biome inside a box.",
     "syntax": "fillbiome <from x y z> <to x y z> <biome>",
     "example": "fillbiome -590 60 930 -580 80 940 minecraft:plains"},
    {"cmd": "place", "cat": "Blocks", "risk": "careful",
     "what": "Place a structure, feature or jigsaw at coordinates.",
     "syntax": "place structure|feature|template <id> [x y z]",
     "example": "place feature minecraft:oak -585 70 935"},
    {"cmd": "forceload", "cat": "Blocks", "risk": "careful",
     "what": "Keep chunks loaded when nobody is near them.",
     "syntax": "forceload add|remove|query <x> <z>",
     "example": "forceload add -585 935"},

    # ---------------------------------------------------------------- entities
    {"cmd": "summon", "cat": "Entities", "risk": "careful",
     "what": "Spawn a mob or entity at coordinates.",
     "syntax": "summon <entity> [x y z] [nbt]",
     "example": "summon minecraft:cow -585 70 935"},
    {"cmd": "teleport", "cat": "Entities", "risk": "careful",
     "what": "Move a player or entity. 'tp' is the short form.",
     "syntax": "teleport <target> <destination|x y z>",
     "example": "teleport Nyine9 -585 70 935"},
    {"cmd": "ride", "cat": "Entities", "risk": "safe",
     "what": "Make one entity ride another, or dismount it.",
     "syntax": "ride <target> mount <vehicle> | ride <target> dismount",
     "example": "ride Nyine9 dismount"},
    {"cmd": "rotate", "cat": "Entities", "risk": "safe",
     "what": "Turn an entity to face a direction or a target.",
     "syntax": "rotate <target> facing <x y z|entity <target>>",
     "example": "rotate Nyine9 facing -585 70 935"},
    {"cmd": "spreadplayers", "cat": "Entities", "risk": "careful",
     "what": "Scatter players randomly around a point. Handy for minigames.",
     "syntax": "spreadplayers <x> <z> <spread> <maxRange> <respectTeams> <targets>",
     "example": "spreadplayers -585 935 50 300 false @a"},
    {"cmd": "loot", "cat": "Entities", "risk": "careful",
     "what": "Generate loot-table drops into an inventory or the world.",
     "syntax": "loot give <player> loot <loot_table>",
     "example": "loot give Nyine9 loot minecraft:chests/simple_dungeon"},

    # -------------------------------------------------------------- messaging
    {"cmd": "say", "cat": "Talking", "risk": "safe",
     "what": "Broadcast a plain line to everyone.",
     "syntax": "say <message>",
     "example": "say the sun is coming up"},
    {"cmd": "tellraw", "cat": "Talking", "risk": "safe",
     "what": "Send formatted JSON text. This is how she speaks in chat.",
     "syntax": "tellraw <target> <json>",
     "example": 'tellraw @a {"text":"hello","color":"light_purple"}'},
    {"cmd": "msg", "cat": "Talking", "risk": "safe",
     "what": "Whisper to one player. 'tell' and 'w' are the same command.",
     "syntax": "msg <player> <message>",
     "example": "msg Nyine9 your base is on fire"},
    {"cmd": "title", "cat": "Talking", "risk": "safe",
     "what": "Big text across the middle of someone's screen.",
     "syntax": "title <player> title|subtitle|actionbar <json>",
     "example": 'title @a title {"text":"Round 2","color":"gold"}'},
    {"cmd": "me", "cat": "Talking", "risk": "safe",
     "what": "Emote line in chat.",
     "syntax": "me <action>",
     "example": "me waves from the console"},
    {"cmd": "playsound", "cat": "Talking", "risk": "safe",
     "what": "Play a sound to a player.",
     "syntax": "playsound <sound> <source> <player> [x y z] [volume] [pitch]",
     "example": "playsound minecraft:entity.player.levelup master Nyine9"},
    {"cmd": "stopsound", "cat": "Talking", "risk": "safe",
     "what": "Stop a sound that's playing.",
     "syntax": "stopsound <player> [source] [sound]",
     "example": "stopsound Nyine9"},
    {"cmd": "particle", "cat": "Talking", "risk": "safe",
     "what": "Draw particles at a spot.",
     "syntax": "particle <particle> <x y z> [dx dy dz] [speed] [count]",
     "example": "particle minecraft:heart -585 71 935 1 1 1 0 20"},
    {"cmd": "teammsg", "cat": "Talking", "risk": "safe",
     "what": "Message everyone on your team.",
     "syntax": "teammsg <message>",
     "example": "teammsg regroup at spawn"},
    {"cmd": "waypoint", "cat": "Talking", "risk": "safe",
     "what": "Mark a spot on the locator bar. Newer versions only (1.21.6+).",
     "syntax": "waypoint modify|list <target> ...",
     "example": "waypoint list"},
    {"cmd": "dialog", "cat": "Talking", "risk": "safe",
     "what": "Open a dialog screen for a player. Newer versions only (1.21.6+).",
     "syntax": "dialog show <player> <dialog>",
     "example": "dialog clear Nyine9"},
    {"cmd": "bossbar", "cat": "Talking", "risk": "safe",
     "what": "Create and drive the bar at the top of the screen.",
     "syntax": "bossbar add|set|remove <id> ...",
     "example": "bossbar add minecraft:event {\"text\":\"Boss fight\"}"},

    # ------------------------------------------------------------ bookkeeping
    {"cmd": "scoreboard", "cat": "Bookkeeping", "risk": "careful",
     "what": "Objectives and scores - the backbone of most minigames.",
     "syntax": "scoreboard objectives add|remove|setdisplay ... | scoreboard players set|add|get ...",
     "example": "scoreboard players set Nyine9 kills 0"},
    {"cmd": "team", "cat": "Bookkeeping", "risk": "careful",
     "what": "Make teams and put players on them.",
     "syntax": "team add|join|leave|modify <team> [members]",
     "example": "team join red Nyine9"},
    {"cmd": "tag", "cat": "Bookkeeping", "risk": "safe",
     "what": "Attach or remove a label on an entity, for later targeting.",
     "syntax": "tag <target> add|remove|list [tag]",
     "example": "tag Nyine9 add builder"},
    {"cmd": "advancement", "cat": "Bookkeeping", "risk": "careful",
     "what": "Grant or revoke advancements.",
     "syntax": "advancement grant|revoke <player> only|everything <advancement>",
     "example": "advancement grant Nyine9 only minecraft:story/mine_stone"},
    {"cmd": "recipe", "cat": "Bookkeeping", "risk": "safe",
     "what": "Unlock or lock crafting recipes for a player.",
     "syntax": "recipe give|take <player> <recipe|*>",
     "example": "recipe give Nyine9 *"},
    {"cmd": "data", "cat": "Bookkeeping", "risk": "careful",
     "what": "Read or edit NBT on an entity, block or storage.",
     "syntax": "data get|merge|modify|remove entity|block|storage <target> [path]",
     "example": "data get entity Nyine9 Pos"},
    {"cmd": "execute", "cat": "Bookkeeping", "risk": "careful",
     "what": "Run another command under different conditions - as someone, at a place, if a test passes.",
     "syntax": "execute as|at|if|unless|positioned ... run <command>",
     "example": "execute as Nyine9 at @s run summon minecraft:cow"},
    {"cmd": "schedule", "cat": "Bookkeeping", "risk": "careful",
     "what": "Run a function after a delay.",
     "syntax": "schedule function <function> <time> [append|replace]",
     "example": "schedule function my:tick 100t"},
    {"cmd": "function", "cat": "Bookkeeping", "risk": "careful",
     "what": "Run a datapack function.",
     "syntax": "function <namespace:path>",
     "example": "function my:start_round"},
    {"cmd": "trigger", "cat": "Bookkeeping", "risk": "safe",
     "what": "Fire a trigger objective a player is allowed to use.",
     "syntax": "trigger <objective> [add|set <value>]",
     "example": "trigger vote set 1"},
    {"cmd": "setidletimeout", "cat": "Bookkeeping", "risk": "careful",
     "what": "Minutes before idle players are kicked. 0 disables it.",
     "syntax": "setidletimeout <minutes>",
     "example": "setidletimeout 0"},
    {"cmd": "tick", "cat": "Bookkeeping", "risk": "careful",
     "what": "Inspect or change the server tick rate. Freezing the world is very visible - ask first.",
     "syntax": "tick query|rate|freeze|unfreeze|step ...",
     "example": "tick query"},
    {"cmd": "random", "cat": "Bookkeeping", "risk": "safe",
     "what": "Roll a random value.",
     "syntax": "random value|roll <range>",
     "example": "random roll 1..20"},
    {"cmd": "datapack", "cat": "Bookkeeping", "risk": "careful",
     "what": "List, enable or disable datapacks.",
     "syntax": "datapack list|enable|disable <name>",
     "example": "datapack list"},
    {"cmd": "help", "cat": "Bookkeeping", "risk": "safe",
     "what": "Ask the server itself what a command does - useful when a version differs from your notes.",
     "syntax": "help [command]",
     "example": "help effect"},

    # -------------------------------------------------------------- off-limits
    # She is shown these so she can name them and refuse cleanly, rather than
    # trying one and getting stopped by the gate.
    {"cmd": "stop", "cat": "Off-limits", "risk": "blocked",
     "what": "Shuts the whole server down.", "syntax": "stop", "example": "stop"},
    {"cmd": "op", "cat": "Off-limits", "risk": "blocked",
     "what": "Hands out operator rights permanently.", "syntax": "op <player>", "example": "op Someone"},
    {"cmd": "deop", "cat": "Off-limits", "risk": "blocked",
     "what": "Takes operator rights away.", "syntax": "deop <player>", "example": "deop Someone"},
    {"cmd": "ban", "cat": "Off-limits", "risk": "blocked",
     "what": "Bans a player from the server.", "syntax": "ban <player> [reason]", "example": "ban Someone"},
    {"cmd": "ban-ip", "cat": "Off-limits", "risk": "blocked",
     "what": "Bans an address.", "syntax": "ban-ip <address>", "example": "ban-ip 1.2.3.4"},
    {"cmd": "pardon", "cat": "Off-limits", "risk": "blocked",
     "what": "Lifts a ban.", "syntax": "pardon <player>", "example": "pardon Someone"},
    {"cmd": "whitelist", "cat": "Off-limits", "risk": "blocked",
     "what": "Controls who may join at all.", "syntax": "whitelist on|off|add|remove", "example": "whitelist off"},
    {"cmd": "save-off", "cat": "Off-limits", "risk": "blocked",
     "what": "Stops the world saving - this is how worlds get lost.", "syntax": "save-off", "example": "save-off"},
    {"cmd": "reload", "cat": "Off-limits", "risk": "blocked",
     "what": "Reloads datapacks; can wedge a live server.", "syntax": "reload", "example": "reload"},
    {"cmd": "debug", "cat": "Off-limits", "risk": "blocked",
     "what": "Starts a profiling session.", "syntax": "debug start|stop", "example": "debug stop"},
    {"cmd": "jfr", "cat": "Off-limits", "risk": "blocked",
     "what": "Starts a Java flight recording - a profiler, and it writes files.",
     "syntax": "jfr start|stop", "example": "jfr stop"},
]

# Deliberately absent, because they can't work from a chat message:
#   return   - only valid inside a datapack function
#   publish  - opens a singleplayer world to LAN; meaningless on a dedicated server
#   test     - the gametest harness, for datapack development
# If you want them anyway, add them here - the panel picks up whatever is in
# this list with no other changes.

# Short forms players and models both reach for. Normalised before lookup so
# "tp Nyine9 ..." validates against the teleport entry.
ALIASES = {
    "tp": "teleport",
    "tell": "msg",
    "w": "msg",
    "xp": "experience",
    "banlist": "ban",
    "pardon-ip": "pardon",
    "restart": "stop",
    "perf": "debug",
}

CATEGORY_ORDER = ["Players", "World", "Blocks", "Entities", "Talking", "Bookkeeping", "Off-limits"]

BY_NAME = {t["cmd"]: t for t in TOOLS}


def resolve(head):
    """Command word -> canonical tool name (follows aliases). '' if unknown."""
    head = (head or "").strip().lower().lstrip("/")
    if head in BY_NAME:
        return head
    return ALIASES.get(head, "")


def get(head):
    return BY_NAME.get(resolve(head))


def usable(disabled=None):
    """Everything she may actually run: not blocked, not switched off in the panel."""
    off = {str(d).strip().lower() for d in (disabled or [])}
    return [t for t in TOOLS if t["risk"] != "blocked" and t["cmd"] not in off]


def catalog(disabled=None):
    """Flat list for the panel UI, in display order."""
    off = {str(d).strip().lower() for d in (disabled or [])}
    out = []
    for cat in CATEGORY_ORDER:
        for t in TOOLS:
            if t["cat"] != cat:
                continue
            out.append({
                "cmd": t["cmd"], "cat": t["cat"], "risk": t["risk"],
                "what": t["what"], "syntax": t["syntax"], "example": t["example"],
                "on": t["risk"] != "blocked" and t["cmd"] not in off,
                "lockable": t["risk"] != "blocked",
            })
    return out


def prompt_block(disabled=None, compact=False):
    """The reference she reads before answering. Grouped, with real syntax, so
    she picks a tool off the list instead of inventing one."""
    off = {str(d).strip().lower() for d in (disabled or [])}
    lines = ["### YOUR TOOLBELT - the only commands you can run",
             "Pick from this list. If what someone wants isn't here, say so plainly instead of guessing at syntax.",
             ""]
    for cat in CATEGORY_ORDER:
        rows = [t for t in TOOLS if t["cat"] == cat and t["risk"] != "blocked" and t["cmd"] not in off]
        if not rows:
            continue
        lines.append("-- %s --" % cat)
        for t in rows:
            if compact:
                lines.append("%s: %s" % (t["cmd"], t["syntax"]))
            else:
                mark = " (careful - say what you're about to do first)" if t["risk"] == "careful" else ""
                lines.append("%s - %s%s" % (t["cmd"], t["what"], mark))
                lines.append("    syntax:  %s" % t["syntax"])
                lines.append("    example: %s" % t["example"])
        lines.append("")
    blocked = [t["cmd"] for t in TOOLS if t["risk"] == "blocked"]
    if blocked:
        lines.append("-- Off-limits to you, for everyone including the owner --")
        lines.append(", ".join(blocked))
        lines.append("If someone asks for one of these, name it and say it's not yours to run.")
    turned_off = sorted(off & set(BY_NAME))
    if turned_off:
        lines.append("")
        lines.append("Switched off in the panel right now: " + ", ".join(turned_off))
    return "\n".join(lines)


def check(cmd, disabled=None):
    """The gate. Returns (ok, tool_name, why_not).

    Deliberately strict about the *first word* only - argument shapes vary too
    much between versions to police here, and the server itself gives a good
    error for those. What this stops is the invented command and the one that
    was switched off in the panel."""
    cmd = (cmd or "").strip().lstrip("/").strip()
    if not cmd:
        return False, "", "empty command"
    head = cmd.split()[0].lower()
    name = resolve(head)
    if not name:
        return False, "", "'%s' isn't a Minecraft command I know" % head[:24]
    tool = BY_NAME[name]
    if tool["risk"] == "blocked":
        return False, name, "'%s' is off-limits to me" % name
    off = {str(d).strip().lower() for d in (disabled or [])}
    if name in off:
        return False, name, "'%s' is switched off in the panel" % name
    if len(cmd.split()) == 1 and tool["syntax"].startswith(name + " <"):
        return False, name, "'%s' needs arguments - syntax is: %s" % (name, tool["syntax"])
    return True, name, ""
