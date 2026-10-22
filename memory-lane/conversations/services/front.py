"""magenta's front page (Justin, 22 Oct): the mark, a line about who it's for, the top three links on
Tidings, and who has spoken lately and where -- their names and the Moods', not what they said.

Read by anyone, signed in or not, so it's kept for a little while rather than asked of the
database on every visit.
"""

from django.core.cache import cache

TOP = 3       # links from Tidings
LATELY = 5    # who spoke lately, and where
KEEP_FOR = 30  # seconds
_KEY = 'front:state'
# Each agent's own colour, as the Moods page draws them (AGENT_COLORS in moods.html, Justin, 21 Oct).
AGENT_COLOURS = {'magent': '#ff67fd', 're': '#ff3c00', 'blu': '#1976d2'}


def top_tidings(limit=TOP):
    """The links on Tidings most upvoted, newest first among equals: [{'id', 'url', 'title', 'host', 'score',
    'comments'}]. From the newest links (tiding_links.LISTED), so an old favourite gives way in time."""
    from . import tiding_links
    links = tiding_links.listed()
    links.sort(key=lambda link: (link['score'], link['at']), reverse=True)
    return [{k: link[k] for k in ('id', 'url', 'title', 'host', 'score', 'comments')} for link in links[:limit]]


def lately(limit=LATELY, scan=300):
    """Who spoke last, and in which Mood, newest first: [{'who', 'mood', 'title', 'at', 'ago', 'colour'}]. What
    was said (a person's post, an agent speaking), not an agent's thinking or tool calls, its silences,
    the poller's wakes or the system's lines; one agent's turn of several lines is one. Archived Moods
    aren't shown."""
    from django.utils.timesince import timesince
    from conversations.models import Message, ThinkingEntity
    from . import settings as knobs
    from .mood_view import is_wrapper, prose, quiet_reason
    archived = knobs.archived_slugs()
    people = set(ThinkingEntity.objects.values_list('name', flat=True))
    rows = (Message.objects.filter(mood__isnull=False, is_sidechain=False, thought__isnull=True, tooluse__isnull=True,
                                   toolresult__isnull=True, sender_id__in=people)
            .select_related('mood').order_by('-created_at').only('content', 'created_at', 'sender_id', 'mood')[:scan])
    out = []
    for msg in rows:
        if msg.mood.slug in archived:
            continue
        text = prose(msg.content)
        if not text or is_wrapper(text) or quiet_reason(text) is not None:
            continue
        if out and (out[-1]['who'], out[-1]['mood']) == (msg.sender_id, msg.mood.slug):
            continue  # the same voice, still in the same Mood: one line
        out.append({'who': msg.sender_id, 'mood': msg.mood.slug, 'title': msg.mood.title or msg.mood.slug,
                    'at': msg.created_at, 'ago': timesince(msg.created_at).split(',')[0],  # "1 day", not "1 day, 9 hours"
                    'colour': AGENT_COLOURS.get(msg.sender_id, '')})
        if len(out) == limit:
            break
    return out


def state():
    found = cache.get(_KEY)
    if found is None:
        found = {'tidings': top_tidings(), 'lately': lately()}
        cache.set(_KEY, found, KEEP_FOR)
    return found
