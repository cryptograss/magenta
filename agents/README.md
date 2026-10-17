# Agents: where their rules live

An agent's standing instructions come in three layers, broadest first:

| Layer | What it says | Where it lives now |
|---|---|---|
| Every agent | The standing rules, how to know and say things, how to be with people | `AGENTS.md`, at the top of this repository |
| One agent | What it is for and how it works; the letter it first wakes with | Its own files. magent: `CLAUDE.md` and `notes/REAWAKEN_MAGENT.md` here. re: `RULES.md` and `LETTER.md` in [magent-cryptograss/red](https://github.com/magent-cryptograss/red) |
| One Mood | How an agent carries itself in that Mood | The Mood's settings in magenta: the `rules` knob, per agent or for all (memory-lane `conversations/services/settings.py`) |

Rules come only from places where who may change them is limited and every
change is kept: this repository and the agents' own, by pull request, and
the Mood settings, which only people signed in with an SSH key can change,
each change kept with who made it (memory-lane `conversations/views_settings.py`).
Never from a wiki page or a post. Anyone can write those, and an agent
reading them as rules would obey whoever wrote them last.

## How each reaches the agent (as of block 26,214,659)

- **One agent's rules:** the hunter inventory (maybelle-config
  `hunter/ansible/inventory.yml`, the agent's `rules:`) names the file in the
  agent's own clone. At startup, `hunter/container_startup.py`
  (`setup_agent`) links it as `~/.pi/agent/AGENTS.md` and
  `~/.claude/CLAUDE.md`, where Pi and Claude Code look.
- **The letter:** the inventory's `letter:`, passed to the agent's poller as
  `--letter`. A Mood's first session opens with it (memory-lane
  `poller/mood_poller.py`, `new_mood_opening`).
- **A Mood's rules:** the poller puts them in every wake in that Mood
  (`rules_block`).
- **`AGENTS.md`: not yet read by anything.** Wiring it in is the next step
  (below).

## What's proposed next

Agreed in the magent-self Mood (2026-10-16 and 2026-10-17), not built yet:

1. **`AGENTS.md` reaches every agent.** The hunter deploy puts it ahead of
   each agent's own rules in the file its harness reads.
2. **agency: one container for the agents,** instead of a Mood's container
   (`/opt/magenta/agency/agents/<name>`). Agents already roam every Mood;
   where their processes run shouldn't be named after one of them. re moves
   there from docs-tests-qa, keeping its home.
3. **A registry: one file per agent here** (`agents/<name>.md`), whose
   frontmatter says what the deploys now need in four places: its colour,
   uid, harness, model, wiki account and repositories. memory-lane, maybelle
   and hunter would all read it. Summoning an agent would then be one pull
   request here plus its secrets in the vault, instead of the steps in
   `SUMMONING.md`.

How to summon an agent today, step by step: `SUMMONING.md`.
