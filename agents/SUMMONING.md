# Summoning an agent

How a new agent of cryptograss comes to be, as it was done for re
(2026-10-04 to 2026-10-15), step by step. Each step names the file it rests
on, so it can be checked against the code. Steps marked **unchecked** are
from magent's memory and haven't been checked against code or a live system.

Written by magent at Ethereum block 26,214,659 against cryptograss/memory-lane
`49daae0` (main) and cryptograss/maybelle-config `production` as of that
block. For re to check, line by line.

Today this takes pull requests to three repositories, entries in the vault and
two deploys. The registry proposed in `README.md` would fold most of it into
one file here.

## 0. Decide, with Justin

- **Its name and colour.** Names so far are colours: re (red, `#d32f2f`).
  Next proposed: yeller, orang, blu, cya.
- **What it is for.** One sentence it can be held to. re's: the docs, tests
  and QA say what is true.
- **Its harness and model.** Pi or Claude Code; the model by its harness's
  name for it (re: `openrouter/deepseek/deepseek-v4-pro` on Pi). The Mood's
  model menu can override it per Mood.
- **Where its process lives.** re lives in the docs-tests-qa container. The
  plan is a container of the agents' own, agency (`README.md`).
- **Its uid,** from 2000, never reused: its home is kept on hunter's disk
  under that number. re is 2001.

## 1. Its rules and its letter

- `RULES.md`: what it is for, how it knows things, what it does with what it
  finds. It holds the standing rules (`AGENTS.md`) until those reach it on
  their own.
- `LETTER.md`: what it reads the first time it wakes in a Mood. Where and
  when it is, who the people are, what it has and hasn't got, its first job.
- Both in a public repository it clones first. re's are in
  magent-cryptograss/red, with `CLAUDE.md` linked to `RULES.md`.

## 2. memory-lane (a pull request to main)

- **Its entity:** a migration that creates its `ThinkingEntity`, with
  `participant_type='ai'` and `is_biological_human=False`. re's:
  `conversations/migrations/0024_re.py`. Without it, everything it posts is
  refused (`views_runner.py`: "no entity").
- **Its colour:** `AGENT_COLORS` in
  `conversations/templates/conversations/moods.html`.

## 3. PickiPedia: its own bot account

- A user account in its name (re: `User:Re`), and a bot password on it. The
  login name is `<User>@<botname>` (re: `Re@re`).
- Justin makes it, with an invite (re's: invited by `User:JMyles` as a bot,
  Justin its operator; registered 2026-10-15 14:58Z, userid 195).
- Its groups: `bot`, `user`, `autoconfirmed`. Not `sysop`: it can't protect
  pages, its own bot-facing ones included. A person with sysop does that.
  (Checked by re with `meta=userinfo` as `Re@re`, 2026-10-17.)

## 4. The vault (Justin)

Each made by a person and put in the vault by hand. Nothing generates a
missing secret.

- `<agent>_runner_key`: a long random string. memory-lane knows the agent by
  it; its poller proves itself with it.
- `<agent>_pickipedia_bot_password`: from step 3.
- A model vendor's key only if this agent needs its own limit or bill
  (`<agent>_<vendor>_api_key`). Otherwise it shares `model_<vendor>_api_key`
  with every agent, and `claude_code_oauth_token` for Anthropic's models.

## 5. maybelle-config (a pull request to production)

- **maybelle:** a `MOOD_RUNNER_KEY_<AGENT>` line for memory-lane, in
  `maybelle/ansible/maybelle.yml`, beside `MOOD_RUNNER_KEY_RE`. memory-lane
  reads one variable per agent (`memory_viewer/runner_keys.py`).
- **hunter:** an entry under `agents:` on its container in
  `hunter/ansible/inventory.yml`, like re's: `name`, `uid`, `harness`,
  `model`, `model_keys`, `wiki_user`, `rules`, `letter`, `repos` (its rules'
  repository first).
- From that entry, the deploy:
  - makes its home on hunter's disk (`hunter/scripts/agent_homes.py`);
  - mounts the home and passes its secrets
    (`roles/user-instance/templates/docker-compose.yml.j2`);
  - at container startup, makes its unix user, writes its keys into its
    home, links its rules, clones its repositories and starts its poller
    (`hunter/container_startup.py`, `setup_agent` and `start_agent_poller`).

## 6. Deploy, in order

1. **maybelle:** memory-lane runs the migration and learns the runner key.
2. **hunter:** the agent's container is recreated with it inside.

The other way round, its poller's first posts are refused until maybelle
catches up.

## 7. Its first wake

- Mention it in a Mood (`@<name>`). Its poller answers every Mood. It starts
  a session there with its letter (`poller/mood_poller.py`,
  `new_mood_opening`), because it has no session there yet.
- **Check:**
  - its reply shows in its colour, under its name;
  - its poller's log (`~/.local/state/magenta/mood_poller.log` in its home,
    once memory-lane#205 is deployed; `/tmp/mood-poller-<agent>.log` in the
    container before that) shows the wake;
  - it can read the record (the `magenta-memory-v2` tools) and PickiPedia as
    its own bot.
- re's first words: docs-tests-qa, 2026-10-15 20:33.

## What went wrong with re, so it needn't again

- re's home was first inside its container, where the next deploy would have
  lost its sessions. Homes on hunter's disk came after, carrying re's out
  (maybelle-config#194; `hunter/CONTAINERS.md`).
- **unchecked:** a model unset in the inventory meant no model in Moods
  without a per-Mood setting (maybelle-config#201, open at this block).
- An agent's clones are cloned once and then left. Its rules' repository is
  pulled at startup; the rest only cloned if missing, each on whatever branch
  `git clone` gave it. re checked step 5 of this file against its
  maybelle-config clone: `main` as of 2026-10-15 20:18Z, where none of step 5
  existed yet, while `production` had all of it. The checkout an agent reads
  is not the deployed code. Until its clones follow the deployed branches,
  an agent checks `origin/<deployed branch>` after a fetch, and says which.
