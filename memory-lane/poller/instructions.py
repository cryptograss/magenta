"""An agent's instructions for one Mood, gathered without a model: Artificial Dumb
(https://pickipedia.xyz/wiki/PickiPedia:Artificial_Dumb).

First, one line naming the emergent (Justin, magent-self, 2026-10-24: "you are
<name>, the <color> agent. <greeting>"), so the "you" of everything after it
is already someone. Its wording is Cryptograss:Emergents/Greeting, the line with
blanks; each emergent's colour and greeting are on its own page,
Cryptograss:Emergents/<Name>, as Semantic MediaWiki properties (Template:Emergent),
the only place they are written, and queryable from there by anything else
(a User: page, magenta):

    You are {name}, the {colour} emergent of cryptograss. {greeting}  (the Greeting page)
    {{Emergent|colour=red|greeting=...|image=...}}                    (Cryptograss:Emergents/Re)

{name}, {mood} and {block} are data, filled here: the name from --agent only
(the agent's page is found by it, and never names it, so no page can rename
an agent), the Mood from the wake, the block only if one was fetched and
handed in. {colour} and {greeting} are authored, one field each (re, 04:15Z
and 04:33Z). Both pages are read from their newest trusted revisions, as the
instructions are: what SMW shows elsewhere is the newest revision, trusted or not,
which is why this reads the page and not a query. A sentence with a blank that
can't be filled is left out, never guessed at. Without the line, or the
agent's page, the line is greeting_fallback.md, the name alone, and the stamps
say why. That is not all-hands: an agent missing its greeting still has its
instructions.

Instructions, not rules (Justin, magent-self, 2026-10-24: "'your instructions'
is much better. More general, more kind, less presumptuous about your role"):
much of what an agent is told is who it is, what it is for and advice, not
constraints. Within them, General Orders is the standing layer that binds.

Then its layers, each a page on PickiPedia:

    standing   Cryptograss:Emergents/GeneralOrders         what every emergent keeps
    clips      Cryptograss:Clips                           the team's clips, and what each is for
    identity   Cryptograss:Emergents/<Name>/Instructions   who this emergent is, how it works
    moods      Cryptograss:Moods/Instructions              what it keeps in every Mood (with a Mood only)
    mood       Cryptograss:Moods/<slug>/Instructions       how it carries itself there

Each page is sent whole, as its wikitext, less <!-- comments -->: what you see
on the page is what the agent is told, and talk about the instructions goes on the
talk page. Only a revision by a trusted editor counts (wiki_rules.EDITORS):
anyone can edit a wiki, and an agent can edit pages too, so the newest trusted
revision is read and anything after it waits until one of them makes or
verifies one.

No instruction is written here (Justin, magent-self, 2026-10-20: "keep the text of
our rules outside of python files"). What the code adds is only what it found:
each part's page and revision, and when one can't be had, what failed and
since when. The words around that come from files beside this one, because
they are for when the wiki is what's down: instructions_degraded.md, and
recovery_instructions.md.

bundle() joins them into one text, each part labelled with its page and
revision, for the harness to take as system prompt (--append-system-prompt):
an agent never searches for its instructions, never gets them wrong by inference, and
they never enter its context's history, so a compaction can't lose them
(Justin, magent-self, 2026-10-19). The same stamps go in its journal.

When a layer can't be had -- PickiPedia down, no trusted revision, the page
gone -- the copy this machine last read stands, and the bundle opens with a
block saying so: what failed, since when, then instructions_degraded.md. With no copy ever read, the recovery
instructions beside this file (recovery_instructions.md) stand in. A Mood with no instructions page
of its own is ordinary, not a failure.

Every bundle a turn is given is recorded (Justin, magent-self, 2026-10-24: "a
list of every bundle action ... with a button that lets me reconstruct and read
the bundle"): not its text, but its parts -- the words this file wrote, as
they were, and in each layer's place only its page and revision -- with a
sha256 of the whole. Joining the revisions' texts back in (rebuild) gives the
bundle again, byte for byte, without this code at that commit: the
transformation from a revision to its part is only layer_body() (or, for an older record, the one it names). A revision
deleted from the wiki since can't be rebuilt, and rebuild says so.

    python poller/instructions.py magent magent-self      # what magent is told there
    python poller/instructions.py blu --stamps            # its versions, as JSON
    python poller/instructions.py --rebuild <id>          # a recorded bundle, rebuilt and checked
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    from poller.wiki_rules import API, EDITORS, USER_AGENT, WikiTexts, page_url
except ImportError:  # run as a script from its own directory, as deployed
    from wiki_rules import API, EDITORS, USER_AGENT, WikiTexts, page_url

STANDING = 'Cryptograss:Emergents/GeneralOrders'
# What every emergent keeps in every Mood (etiquette, silence, greeting, handoffs, settings, to-do lists), read when
# it is woken in one; until a trusted revision is here, the wake goes on carrying the old words.
MOODS = 'Cryptograss:Moods/Instructions'
# The team's clips and what each is for (Justin, 1 Nov): the one place an emergent learns when to play which.
CLIPS = 'Cryptograss:Clips'
GREETING = 'Cryptograss:Emergents/Greeting'
GREETING_FALLBACK = Path(__file__).with_name('greeting_fallback.md')
AGENT_TEMPLATE = 'Emergent'
EMERGENT_TEMPLATES = ('Emergent', 'Agent')  # both accepted while trusted revisions still say {{Agent|...}}
AGENT_FIELDS = ('colour', 'hex', 'greeting', 'image')  # what an agent's own page may put in its line
ROSTER_FIELDS = ('colour', 'for')  # what each other agent's page puts in the roster: '<name> (<colour>) <for>'
BLANK = re.compile(r'(?<!\{)\{([a-z]+)\}(?!\})')
SENTENCE_END = re.compile(r'(?<=[.!?])\s+')
RECOVERY = Path(__file__).with_name('recovery_instructions.md')
DEGRADED = Path(__file__).with_name('instructions_degraded.md')
COMMENTS = re.compile(r'<!--.*?-->', re.S)
STATE = '~/.local/state/magenta'
CACHE_FILE = 'instructions-cache.json'
MAGENTA = 'https://memory-lane.maybelle.cryptograss.live'  # where bundles are recorded
# How a revision becomes its part of a bundle, named in each record: a rebuild applies the one its record names.
TRANSFORM = ('layer_body: the wikitext less <!-- comments -->, {{verified|X}} as [✓ verified: X] and '
             '{{source|X}} as [✓ source: X], stripped')
TRANSFORM_V1 = 'layer_body: the wikitext less <!-- comments -->, stripped'
# A person's verification, as the wiki marks it (Template:Verified, Template:Source): read as raw wikitext
# it was a template call in the prompt; it says someone in a position to know vouched, and by what (Justin,
# 26 Oct: "the wiki needs to reflect that"), so it's kept, in words.
VERIFIED = re.compile(r'\{\{\s*(verified|source)\s*\|([^{}]*)\}\}', re.I)


def layer_body(wikitext):
    """A revision's wikitext as it goes into a bundle."""
    text = COMMENTS.sub('', wikitext or '')
    text = VERIFIED.sub(lambda m: f'[✓ {m.group(1).lower()}: {m.group(2).strip()}]', text)
    return text.strip()


def layer_body_v1(wikitext):
    """As bundles recorded before 26 Oct were made."""
    return COMMENTS.sub('', wikitext or '').strip()


TRANSFORMS = {TRANSFORM: layer_body, TRANSFORM_V1: layer_body_v1}


def agent_title(agent):
    return f'Cryptograss:Emergents/{agent[:1].upper()}{agent[1:]}'


emergent_title = agent_title


def identity_title(agent):
    return f'Cryptograss:Emergents/{agent[:1].upper()}{agent[1:]}/Instructions'


def mood_title(slug):
    return f'Cryptograss:Moods/{slug}/Instructions'


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
    absent: bool = False    # a Mood with no instructions page: nothing of its own
    optional: bool = False  # a page that may not exist (a Mood's, the every-Mood one): never read, it isn't all-hands

    def stamp(self):
        return {'page': self.title, 'revision': self.revision, 'by': self.editor, 'read_at': self.read_at,
                'problem': self.problem, 'since': self.since, 'absent': self.absent}


@dataclass
class Bundle:
    agent: str
    mood: str
    layers: list
    gathered: str
    recovery: str = ''      # the recovery instructions, when a layer was never read here
    text: str = field(default='', repr=False)
    greeting: Layer = None  # the line's page, and the agent's own (colour, greeting), read like layers
    agent_page: Layer = None
    roster: list = field(default_factory=list)  # [(name, Layer)]: the other agents' own pages, for {others}
    greeting_line: str = ''
    block: int = None
    id: str = None          # its record's, when it is recorded: named in the wake, so the agent can name it
    parts: list = field(default_factory=list, repr=False)  # the text, as the record keeps it (assemble)

    @property
    def sha256(self):
        return hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def degraded(self):
        """One line for each layer that couldn't be had as it is now; [] when all is well."""
        lines = []
        for layer in self.layers:
            if not layer.problem:
                continue
            kept = (f'the copy read here at {layer.read_at} (revision {layer.revision}) stands' if layer.text
                    else 'never read here, so left out' if layer.optional
                    else 'never read here: the recovery instructions stand in')
            lines.append(f'{layer.name} ({layer.title}): {layer.problem}, since {layer.since}; {kept}')
        return lines

    def stamps(self):
        """What the journal records: every layer's page and revision, and what was wrong."""
        return {'agent': self.agent, 'mood': self.mood, 'gathered': self.gathered,
                'greeting': {**self.greeting.stamp(), 'line': self.greeting_line,
                             'agent_page': self.agent_page.stamp() if self.agent_page else None,
                             'roster': {name: page.stamp() for name, page in self.roster}} if self.greeting else None,
                'layers': {layer.name: layer.stamp() for layer in self.layers},
                'degraded': self.degraded, 'recovery': bool(self.recovery)}

    def record(self):
        """What is kept of this bundle: enough to rebuild it, and its sha256 to prove the rebuild."""
        return {'id': self.id, 'agent': self.agent, 'mood': self.mood or '', 'gathered': self.gathered,
                'block': self.block, 'sha256': self.sha256, 'parts': self.parts, 'transform': TRANSFORM,
                'stamps': self.stamps()}


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
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix='.instructions-cache.')
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
                if optional:  # a Mood page nobody trusted has written: no instructions of its own yet
                    cache.data['failing'].pop(title, None)
                    return Layer(name, title, absent=True)
                problem = f'no revision by a trusted editor ({", ".join(editors)}) among the last {len(history)}'
            else:
                revision, editor, wikitext = trusted
                text = layer_body(wikitext)
                if not text:
                    problem = f'revision {revision} is empty'
                else:
                    cache.data['pages'][title] = {'revision': revision, 'editor': editor, 'text': text,
                                                  'read_at': now}
                    cache.data['failing'].pop(title, None)
                    return Layer(name, title, text, revision, editor, now)
    since = cache.data['failing'].setdefault(title, now)
    kept = cache.data['pages'].get(title) or {}
    return Layer(name, title, kept.get('text', ''), kept.get('revision'), kept.get('editor'), kept.get('read_at'),
                 problem=problem, since=since, optional=optional)


def attrs(layer):
    found = [f'page="{layer.title}"']
    if layer.revision is not None:
        found.append(f'revision="{layer.revision}"')
    if layer.editor:
        found.append(f'by="{layer.editor}"')
    if layer.problem:
        found.append('stale="yes"' if layer.text else 'missing="yes"')
    return ' '.join(found)


def template_fields(text, name=EMERGENT_TEMPLATES):
    """{field: value} from the first {{Emergent|...}} (or {{Agent|...}}) in a page's text; {} if there is none."""
    names = (name,) if isinstance(name, str) else tuple(name)
    heads = ['{{' + n for n in names]
    at, head = -1, ''
    for i in range(len(text)):
        for h in heads:
            if text.startswith(h, i) and text[i + len(h):i + len(h) + 1] in ('|', '}', '\n'):
                at, head = i, h
                break
        if at >= 0:
            break
    if at < 0:
        return {}
    depth, j = 0, at
    while j < len(text):
        two = text[j:j + 2]
        if two == '{{':
            depth, j = depth + 1, j + 2
        elif two == '}}':
            depth, j = depth - 1, j + 2
            if not depth:
                break
        else:
            j += 1
    fields = {}
    for part in text[at + len(head):j - 2].split('|')[1:]:
        key, eq, value = part.partition('=')
        if eq:
            fields[key.strip().lower()] = value.strip()
    return fields


def roster_text(pages):
    """The other agents, as {others} in the line: '<name> (<colour>) <for>; ...', each from that agent's own
    page (a Layer) at its trusted revision, in the order given. No comma before `for`: it is a predicate
    ('works on the connectome: ...'), the same frame Template:Emergent gives the card, so 'blu (blue), the
    connectome' no longer reads as blu being the connectome (Justin, emergence, 28 Oct). An agent whose page
    has no trusted revision, or no {{Emergent}}, is left out: nothing about it is vouched for. '' if none."""
    entries = []
    for name, page in pages:
        fields = template_fields(page.text or '') if page.text else {}
        if not fields:
            continue
        entry = name + (f" ({fields['colour']})" if fields.get('colour') else '')
        entries.append(entry + (f" {fields['for']}" if fields.get('for') else ''))
    return '; '.join(entries)


def greeting_line(line, mine, agent, mood=None, block=None, fallback_path=GREETING_FALLBACK, roster=()):
    """(the line, why it is the fallback or None): the Greeting page's line (a Layer) with its blanks filled
    for `agent`: colour, hex, greeting and image from the agent's own page (a Layer), and {others} from the
    other agents' pages (`roster`: [(name, Layer)], roster_text)."""
    data = {'name': agent, 'mood': mood or '', 'block': f'{block:,}' if isinstance(block, int) else '',
            'others': roster_text(roster)}
    why = None
    template = ' '.join((line.text or '').split())
    fields = template_fields(mine.text or '') if mine.text else {}
    if not template:
        why = line.problem or 'no trusted revision of the line yet'
    elif not fields:
        why = mine.problem or (f'no trusted revision of {mine.title} yet' if not mine.text
                               else f'no {{{{{AGENT_TEMPLATE}}}}} on {mine.title}')
    if why:
        template, fields = ' '.join(file_text(fallback_path).split()), {}
    values = {**{k: v for k, v in fields.items() if k in AGENT_FIELDS}, **data}
    # Fill what can be filled, then leave out each sentence a blank is still in.
    filled = BLANK.sub(lambda m: values.get(m.group(1)) or m.group(0), template)
    kept = [sentence for sentence in SENTENCE_END.split(filled) if not BLANK.search(sentence)]
    return ' '.join(kept).strip(), why


def file_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError as e:
        return f'({Path(path).name} could not be read: {e})'


def assemble(out):
    """[str or Layer] joined by newlines, as parts: runs of the bundle's own words as strings, and in each
    layer's place {'page', 'revision'}."""
    parts = []
    for i, item in enumerate(out):
        for piece in (['\n'] if i else []) + [item if isinstance(item, str)
                                                else {'page': item.title, 'revision': item.revision}]:
            if isinstance(piece, str) and parts and isinstance(parts[-1], str):
                parts[-1] += piece
            else:
                parts.append(piece)
    return parts


def rebuild(parts, revision_text, transform=TRANSFORM):
    """A recorded bundle's text again: `revision_text(revision)` gives that revision's wikitext; `transform`,
    as its record names it (one recorded before any was named: the first)."""
    body = TRANSFORMS.get(transform or TRANSFORM_V1, layer_body)
    return ''.join(part if isinstance(part, str) else body(revision_text(part['revision'])) for part in parts)


def revision_texts(revisions, api=API):
    """{revision: wikitext} for revisions on PickiPedia; one deleted or never there is left out."""
    found, wanted = {}, sorted({int(r) for r in revisions})
    for at in range(0, len(wanted), 50):
        url = api + '?' + urllib.parse.urlencode({
            'action': 'query', 'prop': 'revisions', 'revids': '|'.join(map(str, wanted[at:at + 50])),
            'rvprop': 'content|ids', 'rvslots': 'main', 'format': 'json', 'formatversion': '2'})
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=10) as r:
            for page in json.load(r).get('query', {}).get('pages', []):
                for rev in page.get('revisions') or []:
                    if 'content' in rev.get('slots', {}).get('main', {}):
                        found[rev['revid']] = rev['slots']['main']['content']
    return found


def rebuilt(record, texts=None, api=API):
    """{'text', 'sha256', 'matches', 'missing'}: the record's bundle rebuilt from the wiki and checked
    against the sha256 it was given with. `missing`: revisions the wiki no longer has."""
    revisions = [part['revision'] for part in record['parts'] if not isinstance(part, str)]
    texts = revision_texts(revisions, api) if texts is None else texts
    missing = sorted({r for r in revisions if r not in texts})
    if missing:
        return {'text': None, 'sha256': None, 'matches': False, 'missing': missing}
    text = rebuild(record['parts'], texts.__getitem__, record.get('transform'))
    sha = hashlib.sha256(text.encode()).hexdigest()
    return {'text': text, 'sha256': sha, 'matches': sha == record['sha256'], 'missing': []}


def render(b, degraded_path=DEGRADED):
    """(text, parts)."""
    # Nothing here changes from turn to turn unless a page does: the system prompt comes first in what a
    # harness sends, and a provider's prompt cache matches it byte for byte, so a bundle that differed each
    # turn (its id, when it was gathered, the block) would make every wake re-read the whole session at
    # full price. Those go in the wake instead (mood_poller.MoodPoller.run_turn_for), and in the record.
    out = [f'<instructions agent="{b.agent}"' + (f' mood="{b.mood}"' if b.mood else '') + '>']
    if b.greeting_line:
        g = b.greeting
        where = f' {attrs(g)}' if g and g.revision is not None and not g.problem else ''
        out += [f'<greeting{where}>{b.greeting_line}</greeting>', '']
    if b.degraded:
        since = min(layer.since for layer in b.layers if layer.problem)
        out += [f'<instructions-degraded since="{since}">', *[f'- {line}' for line in b.degraded], '',
                file_text(degraded_path), '</instructions-degraded>', '']
    for layer in b.layers:
        if layer.absent:
            if layer.name == 'mood':
                out += [f'<mood slug="{b.mood}" page="{layer.title}">This Mood has no instructions of its own.</mood>', '']
        elif layer.text:
            tag = {'mood': f'mood slug="{b.mood}"', 'identity': f'identity agent="{b.agent}"'}.get(layer.name,
                                                                                                   layer.name)
            out += [f'<{tag} {attrs(layer)}>', layer, f'</{layer.name}>', '']
    if b.recovery:
        out += ['<recovery source="memory-lane/poller/recovery_instructions.md">', b.recovery, '</recovery>', '']
    out.append('</instructions>')
    return '\n'.join(item if isinstance(item, str) else item.text for item in out), assemble(out)


def bundle(agent, mood=None, get=None, state_dir=STATE, now=None, editors=EDITORS, recovery_path=RECOVERY,
           degraded_path=DEGRADED, block=None, fallback_path=GREETING_FALLBACK, record_id=None, others=(),
           parent=None):
    """The agent's instructions for `mood` (or with no Mood: standing and identity alone, as a terminal gets).
    `parent`: the Mood a thread was opened from; a thread with no instructions page of its own has that Mood's,
    as its page and revision, so the record rebuilds it as any other (Justin, 25 Oct: threads; re: "forked
    continuations should keep the same standing rules").
    `others`: the other agents' names, for the roster ({others} in the line), from magenta's list of agents
    (no wiki query names them); each is read from its own page at its trusted revision, in name order, so
    the instructions stay the same turn to turn."""
    get = get or WikiTexts().get
    cache = Cache(Path(state_dir).expanduser() / CACHE_FILE if state_dir else None)
    stamp = now_iso(now)
    greeting = read_layer('greeting', GREETING, get, cache, stamp, editors, optional=True)
    agent_page = read_layer('agent', agent_title(agent), get, cache, stamp, editors, optional=True)
    roster = [(name, read_layer(f'roster:{name}', agent_title(name), get, cache, stamp, editors, optional=True))
              for name in sorted({o.strip().lower() for o in others if o and o.strip().lower() != agent})
              if re.fullmatch(r'[a-z0-9-]{1,40}', name)]
    layers = [read_layer('standing', STANDING, get, cache, stamp, editors),
              read_layer('clips', CLIPS, get, cache, stamp, editors, optional=True),
              read_layer('identity', identity_title(agent), get, cache, stamp, editors)]
    if mood:
        layers.append(read_layer('moods', MOODS, get, cache, stamp, editors, optional=True))
        layers.append(read_layer('mood', mood_title(mood), get, cache, stamp, editors, optional=True))
        if layers[-1].absent and parent:
            layers[-1] = read_layer('mood', mood_title(parent), get, cache, stamp, editors, optional=True)
    cache.save()
    recovery = ''
    if any(layer.problem and not layer.text and not layer.optional for layer in layers):
        recovery = file_text(recovery_path)
    b = Bundle(agent, mood, layers, stamp, recovery, block=block, id=record_id)
    b.greeting, b.agent_page, b.roster = greeting, agent_page, roster
    b.greeting_line, why = greeting_line(greeting, agent_page, agent, mood, block, fallback_path, roster)
    if why and not greeting.problem:
        greeting.problem = why  # in its stamps; not all-hands (the layers above say that)
    b.text, b.parts = render(b, degraded_path)
    return b


def main(argv=None):
    parser = argparse.ArgumentParser(description="An agent's instructions for a Mood, as its harness is given them.")
    parser.add_argument('agent', nargs='?', help='magent, re, blu...')
    parser.add_argument('mood', nargs='?', help="the Mood's slug; none: standing and identity alone")
    parser.add_argument('--stamps', action='store_true', help='print the versions, as JSON, not the text')
    parser.add_argument('--state-dir', default=STATE, help="where the last copies are kept ('' for nowhere)")
    parser.add_argument('--rebuild', metavar='ID', help='a recorded bundle, rebuilt from the wiki and checked')
    parser.add_argument('--magenta', default=MAGENTA, help='where the records are (--rebuild)')
    args = parser.parse_args(argv)
    if args.rebuild:
        url = f'{args.magenta.rstrip("/")}/api/bundles/{urllib.parse.quote(args.rebuild)}/'
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=30) as r:
            again = rebuilt(json.load(r))
        if again['missing']:
            print(f'cannot rebuild: the wiki no longer has revision(s) {", ".join(map(str, again["missing"]))}',
                  file=sys.stderr)
            return 2
        print(again['text'])
        print('identical to what was given' if again['matches'] else
              f'DIFFERS from what was given (rebuilt sha256 {again["sha256"]})', file=sys.stderr)
        return 0 if again['matches'] else 1
    if not args.agent:
        parser.error('an agent, or --rebuild')
    b = bundle(args.agent, args.mood, state_dir=args.state_dir or None)
    print(json.dumps(b.stamps(), indent=1) if args.stamps else b.text)
    for line in b.degraded:
        print(f'degraded: {line}', file=sys.stderr)
    for layer in b.layers:
        print(f'{layer.name}: {page_url(layer.title)}', file=sys.stderr)
    return 1 if b.degraded else 0


if __name__ == '__main__':
    sys.exit(main())
