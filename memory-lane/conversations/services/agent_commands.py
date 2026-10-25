"""Commands for agents, said in a Mood, in one form only (Justin, 17 Oct):

    /compact <agent>[,<agent>...] <what to keep>
    /context <agent>[,<agent>...]
    /usage <agent>[,<agent>...]
    /cost <agent>[,<agent>...]

The post starts with the command; the agents it's for come next, by name
(several with commas); then, for /compact, what the compaction should keep,
which it must say. Anything else that looks like one -- a command missing its
agent or its words, an agent who isn't one, or "@magent /compact" the old way
-- is refused before it's posted, with the form it should take, so nobody
believes a command ran that didn't. A command named in passing ("should we
/compact soon?") is just words.

The poller reads the same form (poller/mood_poller.py command_request); it's
the server that holds people to it.
"""

import re

# Each command: what it's for (the composer's suggestions say so), and whether it
# takes words after its agents (required, for /compact).
COMMANDS = {
    'compact': ('compact an agent: shorten its memory of this Mood, keeping what you say', True),
    'context': ("what fills an agent's context here, by kind", False),
    'usage': ("the subscription's limits, used so far", False),
    'cost': ('the same as /usage', False),
}
FORM = {name: f'/{name} <agent>[,<agent>…]' + (' <what to keep>' if words else '')
        for name, (_, words) in COMMANDS.items()}


class Invalid(ValueError):
    """It looks like a command for an agent, but isn't written as one."""


def parse(text, agents):
    """A post that is a command for agents: (name, [agents], words). None if it isn't one.
    Raises Invalid if it means to be one and isn't written as one. `agents`: the agents' names."""
    text = (text or '').strip()
    agents = {a.lower() for a in agents}
    head = re.match(r'/([a-z]+)(?=\s|\Z)', text, re.I)
    if not head or head.group(1).lower() not in COMMANDS:
        # The old way, "@magent /compact ..." (anywhere: "hey @magent /compact"): said how it's written now.
        prose = re.sub(r'```.*?```|`[^`\n]*`', '', text, flags=re.S)  # quoted as code, it's talk about one
        old = re.search(r'@([\w.-]+)[\s,:]*/([a-z]+)\b', prose, re.I)
        if old and old.group(1).lower() in agents and old.group(2).lower() in COMMANDS:
            name, agent = old.group(2).lower(), old.group(1).lower()
            raise Invalid(f'commands for agents start the message now: '
                          f'{FORM[name].replace("<agent>[,<agent>…]", agent)}')
        return None
    name = head.group(1).lower()
    rest = text[head.end():].strip()
    listed = re.match(r'@?[\w.-]+(?:\s*,\s*@?[\w.-]+)*', rest)
    if not listed:
        raise Invalid(f'which agent? {FORM[name]}')
    named = [n.strip().lstrip('@').lower() for n in listed.group(0).split(',')]
    unknown = [n for n in named if n not in agents]
    if unknown:
        raise Invalid(f"{', '.join(unknown)}: not an agent here (agents: {', '.join(sorted(agents))}). {FORM[name]}")
    named = list(dict.fromkeys(named))
    words = rest[listed.end():].strip()
    takes_words = COMMANDS[name][1]
    if takes_words and not words:
        raise Invalid(f'say what the compaction should keep: {FORM[name]}')
    if words and not takes_words:
        raise Invalid(f'/{name} takes nothing after its agents: {FORM[name]}')
    return name, named, words


def suggestions():
    """Every slash command the composer offers, as [name, form, what it does]: these, /clips and /poll."""
    out = [[name, FORM[name], what] for name, (what, _) in COMMANDS.items()]
    return out + [['clips', '/clips <name> | save <name> <yarn link> | forget <name>',
                   "the team's saved clips: post one by name, save one, forget one, or list them"],
                  ['poll', '/poll <question> | <choice> | <choice> …',
                   'ask everyone, agents too: a button per choice, and who chose what (none given: yes and no)']]


def commanded(text, agents):
    """The agents a post commands, if it's a well-formed command; [] otherwise (never raises)."""
    try:
        found = parse(text, agents)
    except Invalid:
        return []
    return found[1] if found else []


def doing(text, agent, agents):
    """'compact' etc. if this post commands `agent`; None otherwise."""
    try:
        found = parse(text, agents)
    except Invalid:
        return None
    return found[0] if found and agent.lower() in found[1] else None
