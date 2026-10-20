"""The tidings page (/moods/tidings/ and /api/tidings/): pictures and prompts.

What Justin asked for, while building the status page (block ~26,228,235):
  1. Pictures: the last 20 images shared in the Moods, in order, with who
     shared each one, whether it has gone up to PickiPedia, and whether the
     faces service has looked at it.
  2. Prompts: the last 20 prompts issued by each biological human.

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

Reading is public, like the rest of the record: pictures and prompts are
already there in the Moods for anyone to see. How far back a Mood is
looked is a bound, not a claim: MAX_PER_MOOD visible human posts, which
is far more than any person's last 20 prompts here.
"""

from collections import defaultdict
from datetime import timedelta

from conversations.models import FaceLook, FaceName, Media, Message, Mood, ThinkingEntity
from conversations.services.mood_view import addressed_in, from_wiki_tier, prose, snippet_of

PICTURES = 20
PROMPTS_PER_HUMAN = 20
SHORT_POST = 80   # under this many characters, a post that addresses an agent pairs with the one before it
PAIR_WITHIN = timedelta(minutes=10)  # a short post's nudge is about what its author just said
MAX_PER_MOOD = 300  # how many of a Mood's visible human posts the prompt scan looks at
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


def prompt_tidings(per_human=PROMPTS_PER_HUMAN):
    """Every biological human's prompts, newest first, at most `per_human` each.

    One pass over each Mood's visible human posts, oldest first, so a short
    post that addresses an agent can look back one post: the same person's
    own post right before it, in that Mood (magent's reading of "the message
    immediately preceding", which Justin let stand), and only if that post
    does not itself address an agent -- then each is shown on its own,
    neither twice. Posts that address nobody are never prompts.
    """
    humans = list(ThinkingEntity.objects.filter(is_biological_human=True))
    human_names = [human.name for human in humans]
    agents = set(ThinkingEntity.objects.filter(is_biological_human=False)
                 .values_list('name', flat=True))
    grouped = defaultdict(list)

    for mood in Mood.objects.order_by('id'):
        rows = (mood.messages.filter(is_sidechain=False, sender_id__in=human_names)
                .select_related('sender').order_by('-created_at')[:MAX_PER_MOOD])
        last = {}
        for message in reversed(list(rows)):  # oldest first, pairing looks back one post
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
                entry.update(kind='paired', trigger=_brief(message, mood.slug, named))
            else:
                entry = _brief(message, mood.slug, named)
                entry['kind'] = 'mention'
            grouped[message.sender_id].append(entry)
            last[message.sender_id] = message

    out = []
    for human in humans:
        prompts = sorted(grouped[human.name], key=_entry_at, reverse=True)
        out.append({'name': human.name, 'prompts': prompts[:per_human]})
    with_prompts = sorted([h for h in out if h['prompts']],
                          key=lambda h: _entry_at(h['prompts'][0]), reverse=True)
    without_prompts = sorted([h for h in out if not h['prompts']], key=lambda h: h['name'])
    return with_prompts + without_prompts


def picture_tidings(limit=PICTURES):
    """The latest `limit` pictures posted in a Mood, newest first, each with its two marks.

    Only what a Mood's own posts show: a picture pasted into a terminal
    session is kept as Media too (services/media.py), and isn't anyone's to
    publish here. Who: whoever shared it (Media.added_by), else who posted it.
    Pushed: a 'wiki-upload' event line in its Mood (services/wiki_upload.py
    records one when a picture goes up). Faces: a FaceLook row, which exists
    once the faces service has been run on it, with how many faces it found
    and how many have been named.
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
