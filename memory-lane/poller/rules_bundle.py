"""An agent's rules for one Mood, gathered without a model: Artificial Dumb
(https://pickipedia.xyz/wiki/PickiPedia:Artificial_Dumb).

Three layers, broadest first, each a page on PickiPedia:

    standing   Cryptograss:Agents/Rules           what every agent keeps
    identity   Cryptograss:Agents/<Name>/Rules    who this agent is, how it works
    mood       Cryptograss:Moods/<slug>/Rules     how it carries itself there

On each page, what is inside {{Sent to agents|text=...}} is sent word for word
and the rest is for people (wiki_rules.blocks). Only a revision by a trusted
editor counts (wiki_rules.EDITORS): anyone can edit a wiki, and an agent can
edit pages too, so the newest trusted revision is read and anything after it
waits until one of them makes or verifies one.

bundle() joins the three into one text, each part labelled with its page and
revision, for the harness to take as system prompt (--append-system-prompt):
an agent never searches for its rules, never gets them wrong by inference, and
they never enter its context's history, so a compaction can't lose them
(Justin, magent-self, 2026-10-19). The same stamps go in its journal.

When a layer can't be had -- PickiPedia down, no trusted revision, the page
gone -- the copy this machine last read stands, and the bundle opens with a
block saying so: what failed, since when. That is an all-hands problem, and
the agents are told to treat it as one. With no copy ever read, the recovery
rules beside this file (recovery_rules.md) stand in. A Mood with no rules page
of its own is ordinary, not a failure.

    python poller/rules_bundle.py magent magent-self    # what magent is told there
    python poller/rules_bundle.py blu --stamps          # its versions, as JSON
"""

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    from poller.wiki_rules import EDITORS, WikiTexts, blocks, page_url
except ImportError:  # run as a script from its own directory, as deployed
    from wiki_rules import EDITORS, WikiTexts, blocks, page_url

STANDING = 'Cryptograss:Agents/Rules'
RECOVERY = Path(__file__).with_name('recovery_rules.md')
STATE = '~/.local/state/magenta'
CACHE_FILE = 'rules-cache.json'

PREAMBLE = ("Your rules, in three layers, broadest first, gathered from PickiPedia by rules_bundle.py (no model "
            "chose them). Where they disagree, the narrower wins: this Mood's over your own, your own over the "
            "standing ones; but nothing narrower loosens a standing rule. Each part names its page and revision: "
            "say which when it matters, to people and to other agents.")


def identity_title(agent):
    return f'Cryptograss:Agents/{agent[:1].upper()}{agent[1:]}/Rules'


def mood_title(slug):
    return f'Cryptograss:Moods/{slug}/Rules'


def now_iso(now=None):
    return (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%MZ')


@dataclass
class Layer:
    name: str               # standing, identity or mood
    title: str
    text: str = ''
    revision: int = None
    editor: str = None
    read_at: str = None     # when this machine read the revision whose text this is
    problem: str = None     # why the newest trusted revision couldn't be had now
    since: str = None       # since when it has been failing
    absent: bool = False    # a Mood with no rules page: nothing of its own

    def stamp(self):
        return {'page': self.title, 'revision': self.revision, 'by': self.editor, 'read_at': self.read_at,
                'problem': self.problem, 'since': self.since, 'absent': self.absent}


@dataclass
class Bundle:
    agent: str
    mood: str
    layers: list
    gathered: str
    recovery: str = ''      # the recovery rules, when a layer was never read here
    text: str = field(default='', repr=False)

    @property
    def degraded(self):
        """One line for each layer that couldn't be had as it is now; [] when all is well."""
        lines = []
        for layer in self.layers:
            if not layer.problem:
                continue
            kept = (f'the copy read here at {layer.read_at} (revision {layer.revision}) stands' if layer.text
                    else 'never read here: the recovery rules stand in')
            lines.append(f'{layer.name} ({layer.title}): {layer.problem}, since {layer.since}; {kept}')
        return lines

    def stamps(self):
        """What the journal records: every layer's page and revision, and what was wrong."""
        return {'agent': self.agent, 'mood': self.mood, 'gathered': self.gathered,
                'layers': {layer.name: layer.stamp() for layer in self.layers},
                'degraded': self.degraded, 'recovery': bool(self.recovery)}


class Cache:
    """The last trusted revision of each page this machine read, and since when each has been failing:
    the copy that stands when PickiPedia can't be read. One JSON file, written whole."""

    def __init__(self, path):
        self.path = Path(path).expanduser() if path else None
        self.data = {'pages': {}, 'failing': {}}
        if self.path and self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
                self.data = {'pages': dict(loaded.get('pages') or {}), 'failing': dict(loaded.get('failing') or {})}
            except (OSError, ValueError):
                pass  # an unreadable cache is no cache: every layer is read afresh or fails loudly

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix='.rules-cache.')
        with os.fdopen(fd, 'w') as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)


def read_layer(name, title, get, cache, now, editors, optional=False):
    """One layer: its newest trusted revision, else the copy read last, saying what's wrong."""
    trusted_names = {e.strip().lower().replace('_', ' ') for e in editors}
    problem = None
    try:
        history = get(title)
    except Exception as e:  # PickiPedia down, slow, or answering nonsense
        problem = f"PickiPedia couldn't be read ({type(e).__name__}: {str(e)[:120]})"
    else:
        if not history:
            if optional:
                cache.data['failing'].pop(title, None)
                return Layer(name, title, absent=True)
            problem = 'the page does not exist'
        else:
            trusted = next(((rev, editor, wikitext) for rev, editor, wikitext in history
                            if (editor or '').lower().replace('_', ' ') in trusted_names), None)
            if trusted is None:
                if optional:  # a Mood page nobody trusted has written: no rules of its own yet
                    cache.data['failing'].pop(title, None)
                    return Layer(name, title, absent=True)
                problem = f'no revision by a trusted editor ({", ".join(editors)}) among the last {len(history)}'
            else:
                revision, editor, wikitext = trusted
                text = '\n\n'.join(b['text'] for b in blocks(wikitext))
                if not text.strip():
                    problem = f'revision {revision} has no {{{{Sent to agents}}}} text'
                else:
                    cache.data['pages'][title] = {'revision': revision, 'editor': editor, 'text': text,
                                                  'read_at': now}
                    cache.data['failing'].pop(title, None)
                    return Layer(name, title, text, revision, editor, now)
    since = cache.data['failing'].setdefault(title, now)
    kept = cache.data['pages'].get(title) or {}
    return Layer(name, title, kept.get('text', ''), kept.get('revision'), kept.get('editor'), kept.get('read_at'),
                 problem=problem, since=since)


def attrs(layer):
    found = [f'page="{layer.title}"']
    if layer.revision is not None:
        found.append(f'revision="{layer.revision}"')
    if layer.editor:
        found.append(f'by="{layer.editor}"')
    if layer.problem:
        found.append('stale="yes"' if layer.text else 'missing="yes"')
    return ' '.join(found)


def render(b):
    out = [f'<rules agent="{b.agent}"' + (f' mood="{b.mood}"' if b.mood else '') + f' gathered="{b.gathered}">']
    if b.degraded:
        since = min(layer.since for layer in b.layers if layer.problem)
        out += [f'<rules-degraded since="{since}">',
                'Your rules could not all be gathered. That is an all-hands problem: drop other work until they '
                'are back.',
                *[f'- {line}' for line in b.degraded],
                'Until it is fixed: make sure the people here know; help find and fix the cause (the recovery rules '
                'say how); and answer anything else with "not until the rules are back".',
                '</rules-degraded>', '']
    out += [PREAMBLE, '']
    for layer in b.layers:
        if layer.absent:
            out += [f'<mood slug="{b.mood}" page="{layer.title}">This Mood has no rules of its own.</mood>', '']
        elif layer.text:
            tag = {'mood': f'mood slug="{b.mood}"', 'identity': f'identity agent="{b.agent}"'}.get(layer.name,
                                                                                                   layer.name)
            out += [f'<{tag} {attrs(layer)}>', layer.text, f'</{layer.name}>', '']
    if b.recovery:
        out += ['<recovery source="memory-lane/poller/recovery_rules.md">', b.recovery, '</recovery>', '']
    out.append('</rules>')
    return '\n'.join(out)


def bundle(agent, mood=None, get=None, state_dir=STATE, now=None, editors=EDITORS, recovery_path=RECOVERY):
    """The agent's rules for `mood` (or with no Mood: standing and identity alone, as a terminal gets)."""
    get = get or WikiTexts().get
    cache = Cache(Path(state_dir).expanduser() / CACHE_FILE if state_dir else None)
    stamp = now_iso(now)
    layers = [read_layer('standing', STANDING, get, cache, stamp, editors),
              read_layer('identity', identity_title(agent), get, cache, stamp, editors)]
    if mood:
        layers.append(read_layer('mood', mood_title(mood), get, cache, stamp, editors, optional=True))
    cache.save()
    recovery = ''
    if any(layer.problem and not layer.text for layer in layers):
        try:
            recovery = Path(recovery_path).read_text().strip()
        except OSError:
            recovery = ('The recovery rules could not be read either. Do nothing but read, tell the people here '
                        'what you see, and wait for a person.')
    b = Bundle(agent, mood, layers, stamp, recovery)
    b.text = render(b)
    return b


def main(argv=None):
    parser = argparse.ArgumentParser(description="An agent's rules for a Mood, as its harness is given them.")
    parser.add_argument('agent', help='magent, re, blu...')
    parser.add_argument('mood', nargs='?', help="the Mood's slug; none: standing and identity alone")
    parser.add_argument('--stamps', action='store_true', help='print the versions, as JSON, not the text')
    parser.add_argument('--state-dir', default=STATE, help="where the last copies are kept ('' for nowhere)")
    args = parser.parse_args(argv)
    b = bundle(args.agent, args.mood, state_dir=args.state_dir or None)
    print(json.dumps(b.stamps(), indent=1) if args.stamps else b.text)
    for line in b.degraded:
        print(f'degraded: {line}', file=sys.stderr)
    for layer in b.layers:
        print(f'{layer.name}: {page_url(layer.title)}', file=sys.stderr)
    return 1 if b.degraded else 0


if __name__ == '__main__':
    sys.exit(main())
