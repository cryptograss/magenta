"""The tidings page (/moods/tidings/ and /api/tidings/): pictures and prompts.

What Justin asked for, while building the status page (block ~26,228,235):
  1. Pictures: the last 20 images shared in the Moods, in order, with who
     shared each one, whether it has gone up to PickiPedia, and whether the
     faces service has looked at it.
  2. Prompts: the last 20 prompts issued by each biological human, each with
     the tokens and cost of the turn that answered it.

A prompt is a post that addresses an agent, as the Moods themselves count
it (mood_view.addressed_in): an @mention, or a command that names one.
A very short post (under 80 characters, Justin's "very short") that
addresses an agent counts too -- but the same person's own post right
before it gave it its meaning, so the two are shown together as one
prompt. If that preceding post itself addresses an agent, it is already
a prompt on its own, and both stand alone. A post of 80 characters or
more stands alone, whatever came before it; so does a command
("/compact magent ..."), and a short post whose author said nothing in
that Mood in the PAIR_WITHIN before it.

A prompt's turn is the answered agent's run of messages that follows it:
messages in a row from that agent, ended by anyone else's post -- which is
how the page draws a turn, and an event line splits one there too. A run
counts as the answer to the newest prompt before it (the last thing said
before the agent acted), and only within TURN_WITHIN; an agent may answer
one prompt with two runs (a look, then words), and both are summed. The
tokens and cost are each message's own usage, counted once per response:
one response can be stored as several rows (thinking, words, a tool call),
each repeating its usage, and repeated usage is one response
(mood_view.how_payload's own rule). The cost is what the harness said it
cost (Pi does); otherwise how_payload's price-list call.

Reading is public, like the rest of the record: pictures and prompts are
already there in the Moods for anyone to see. How far back a Mood is
looked is a bound, not a claim: MAX_PER_MOOD visible human posts, with
the other senders' posts from the same stretch.
"""

import bisect
import heapq
from collections import defaultdict
from datetime import timedelta

from conversations.models import FaceLook, FaceName, Media, Message, Mood, ThinkingEntity
from conversations.services.mood_view import addressed_in, from_wiki_tier, how_payload, prose, snippet_of

PICTURES = 20
PROMPTS_PER_HUMAN = 20
SHORT_POST = 80   # under this many characters, a post that addresses an agent pairs with the one before it
PAIR_WITHIN = timedelta(minutes=10)  # a short post's nudge is about what its author just said
TURN_WITHIN = timedelta(hours=2)     # how long after a prompt its answering turn may start
MAX_PER_MOOD = 300   # how many of a Mood's visible human posts the prompt scan looks at
OTHER_CAP = 3000     # ... and how many other senders' posts from the same stretch it reads for turns
MAX_POSTS_WITH_PICTURES = 400  # how many of the newest posts showing pictures the picture scan looks at

IMAGE_MIMES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
# image/heic is not listed: a HEIC photo is kept as an original beside the
# JPEG it is shown as (services/media.store), and is not a second picture.


def _text(message):
    return prose(message.content)


def _brief(message, mood_slug, agents):
    """One post, as the page shows it."""
    text = _text(message)
    return {
        'id': str(message.id),
        'mood': mood_slug,
        'at': message.created_at,
        'block': message.eth_blockheight,
        'text': text,
        'snippet': snippet_of(text)[:280],
        'agents': agents or [],
    }


def _entry_at(entry):
    """When the prompt happened: the short post's time in a pair, its own otherwise."""
    return (entry['trigger'] if entry['kind'] == 'paired' else entry)['at']


def _run_metrics(run):
    """One agent turn's tokens and cost, one response's usage counted once.

    `run` is that agent's messages in a row. A response stored as several
    rows repeats its usage on each; consecutive rows with the identical
    usage are one response. None when a turn carried no measured usage.
    """
    totals = {'in': 0, 'out': 0, 'usd': 0.0, 'paid': False, 'calls': 0, 'model': ''}
    last_usage = None
    for message in run:
        payload = how_payload(message)
        if 'out' not in payload:
            last_usage = None
            continue
        usage = (payload.get('ctx', 0), payload['out'])
        if usage == last_usage:
            continue
        if not totals['model'] and payload.get('model'):
            totals['model'] = payload['model']
        totals['in'] += usage[0]
        totals['out'] += usage[1]
        if payload.get('usd') is not None:
            totals['usd'] += payload['usd']
        totals['paid'] = totals['paid'] or payload.get('paid', False)
        totals['calls'] += 1
        last_usage = usage
    if not totals['calls']:
        return None
    totals['in'], totals['out'] = int(totals['in']), int(totals['out'])
    totals['usd'] = round(totals['usd'], 4)
    return totals


def _attach_turns(entries, finished):
    """Each prompt gets the metrics of the turn that answered it, when one did.

    `entries` is one Mood's prompts, oldest first. `finished` maps each
    agent's name to its runs in that Mood. A run answers the newest prompt
    before it (the last thing said before the agent acted); a run older
    than TURN_WITHIN behind any prompt answers none.
    """
    for agent, runs in finished.items():
        prompted = sorted(((_entry_at(entry), entry) for entry in entries if agent in entry['agents']),
                          key=lambda pair: pair[0])
        if not prompted:
            continue
        times = [pair[0] for pair in prompted]
        for run in runs:
            metrics = _run_metrics(run)
            if metrics is None:
                continue
            start = run[0].created_at
            index = bisect.bisect_right(times, start) - 1
            if index < 0 or start - times[index] > TURN_WITHIN:
                continue
            entry = prompted[index][1]
            existing = next((turn for turn in entry['turns'] if turn['agent'] == agent), None)
            if existing:
                existing['in'] += metrics['in']
                existing['out'] += metrics['out']
                existing['usd'] = round(existing['usd'] + metrics['usd'], 4)
                existing['paid'] = existing['paid'] or metrics['paid']
                existing['calls'] += metrics['calls']
            else:
                entry['turns'].append({'agent': agent, **metrics})


def prompt_tidings(per_human=PROMPTS_PER_HUMAN):
    """Every biological human's prompts, newest first, at most `per_human` each.

    One pass over each Mood's visible posts, oldest first, so a short
    post that addresses an agent can look back one post: the same person's
    own post right before it, in that Mood (magent's reading of "the message
    immediately preceding", which Justin let stand), and only if that post
    does not itself address an agent -- then each is shown on its own,
    neither twice. Posts that address nobody are never prompts.

    Agents' messages on the same pass become their turns, for the tokens
    and cost of what answered each prompt (_attach_turns).
    """
    humans = list(ThinkingEntity.objects.filter(is_biological_human=True))
    human_names = [human.name for human in humans]
    human_set = set(human_names)
    agents = set(ThinkingEntity.objects.filter(is_biological_human=False)
                 .values_list('name', flat=True))
    grouped = defaultdict(list)

    for mood in Mood.objects.order_by('id'):
        human_rows = (mood.messages.filter(is_sidechain=False, sender_id__in=human_names)
                      .select_related('sender').order_by('-created_at')[:MAX_PER_MOOD])
        human_rows = list(reversed(list(human_rows)))
        if not human_rows:
            continue
        other_rows = list(mood.messages.filter(is_sidechain=False, created_at__gte=human_rows[0].created_at)
                          .exclude(sender_id__in=human_names).select_related('sender')
                          .order_by('created_at')[:OTHER_CAP])
        last = {}          # each human's own previous visible post in this Mood: what pairing looks back to
        runs = defaultdict(list)     # each agent's turn being built up
        finished = defaultdict(list)  # each agent's completed turns
        entries = []       # this Mood's prompts, oldest first

        def flush(except_agent=None):
            for name, run in list(runs.items()):
                if except_agent is not None and name == except_agent:
                    continue
                finished[name].append(run)
                del runs[name]

        for message in heapq.merge(human_rows, other_rows, key=lambda m: m.created_at):
            if message.sender_id in agents:
                flush(except_agent=message.sender_id)
                runs[message.sender_id].append(message)
                continue
            if message.sender_id in human_set:
                flush()  # people's posts end every turn, as on the page
                if from_wiki_tier(message):
                    # From a PickiPedia device, an @agent is just text: no agent
                    # is waking for it, so it is not a prompt (mood_view.activities).
                    last[message.sender_id] = message
                    continue
                text = _text(message)
                named = [n for n in addressed_in(text, agents, by=message.sender_id)]
                if not named:
                    last[message.sender_id] = message
                    continue
                previous = last.get(message.sender_id)
                if (previous and len(text) < SHORT_POST and not text.startswith('/')
                        and message.created_at - previous.created_at <= PAIR_WITHIN
                        and not addressed_in(_text(previous), agents, by=previous.sender_id)):
                    entry = _brief(previous, mood.slug, named)
                    entry.update(kind='paired', trigger=_brief(message, mood.slug, named), by=message.sender_id)
                else:
                    entry = _brief(message, mood.slug, named)
                    entry.update(kind='mention', by=message.sender_id)
                entry['turns'] = []
                entries.append(entry)
                last[message.sender_id] = message
                continue
            flush()  # an event line ends a turn: the page splits one there too
        flush()
        _attach_turns(entries, finished)
        for entry in entries:
            grouped[entry['by']].append(entry)

    out = []
    for human in humans:
        prompts = sorted(grouped[human.name], key=_entry_at, reverse=True)
        out.append({'name': human.name, 'prompts': prompts[:per_human]})
    with_prompts = sorted([h for h in out if h['prompts']],
                          key=lambda h: _entry_at(h['prompts'][0]), reverse=True)
    without_prompts = sorted([h for h in out if not h['prompts']], key=lambda h: h['name'])
    return with_prompts + without_prompts


def picture_tidings(limit=PICTURES):
    """The latest `limit` images, newest first, each with its two marks.

    Only pictures a visible Mood post shows: a picture pasted into a
    terminal session is someone's work, not public, and stays where it is.
    Pushed: a 'wiki-upload' event line in its Mood (services/wiki_upload.py
    records one when a picture goes up). Faces: a FaceLook row, which exists
    once the faces service has been run on it, with how many faces it found
    and how many have been named. Each picture carries a small `preview`
    beside its `url` (views_moods.media_preview), so a gallery of them
    costs thumbnails, not the pictures.
    """
    from conversations.services.media import MEDIA_PATH
    posts = (Message.objects.filter(mood__isnull=False, is_sidechain=False, content__icontains='/media/',
                                    sender__in=ThinkingEntity.objects.all())
             .select_related('sender').order_by('-created_at')[:MAX_POSTS_WITH_PICTURES])
    shown = {}  # sha -> the newest post showing it
    for post in posts:
        for match in MEDIA_PATH.finditer(prose(post.content)):
            shown.setdefault(match.group(1), post)
    media = {row.sha256: row for row in Media.objects.filter(pk__in=list(shown), mime__in=IMAGE_MIMES)
             .select_related('added_by')}
    shas = [sha for sha in shown if sha in media][:limit]  # newest post first
    looked = {look.pk: look for look in FaceLook.objects.filter(pk__in=shas)}
    named = defaultdict(int)
    for media_id in FaceName.objects.filter(media_id__in=shas).values_list('media_id', flat=True):
        named[media_id] += 1
    uploads = {}  # sha -> the wiki-upload event line, newest per picture (one is all there is)
    for event in Message.objects.filter(source_file='wiki-upload', content__sha__in=shas) \
                                .order_by('created_at'):
        uploads[event.content['sha']] = event

    out = []
    for sha in shas:
        row, post = media[sha], shown[sha]
        look = looked.get(sha)
        if look:
            faces = {'looked': True, 'count': len(look.faces or []),
                     'named': named[sha], 'model': look.model, 'at': look.looked_at}
        else:
            faces = {'looked': False}
        event = uploads.get(sha)
        out.append({
            'sha256': sha,
            'url': row.url,
            'preview': f'{row.url}/preview',
            'mime': row.mime,
            'size': row.size,
            'added_by': row.added_by.name if row.added_by_id else post.sender_id,
            'at': post.created_at,
            'mood': post.mood_slug,
            'message': str(post.id),
            'faces': faces,
            'wiki': (None if event is None else {
                'page': event.content.get('page', ''),
                'file': event.content.get('file', ''),
                'by': event.content.get('by', ''),
                'mood': event.mood_slug,
                'at': event.created_at,
            }),
        })
    return out


def tidings_state():
    """Everything /moods/tidings/ shows: each human's Wiki name added, naught else."""
    from conversations.services import wiki_auth
    state = {'pictures': picture_tidings(), 'prompts': prompt_tidings()}
    wiki = wiki_auth.names()
    for human in state['prompts']:
        human['wiki_name'] = wiki.get(human['name'], '')
    return state