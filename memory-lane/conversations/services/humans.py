"""The people pane, under the to-do list (Justin, 3 Nov): who has spoken in this Mood in the last 300 blocks
(at least the three who spoke here last), then, to ten in all, everyone else, each with the Mood or Tidings link
they last said something on. People only, never emergents; archived Moods aren't counted."""

from datetime import timedelta

HERE_BLOCKS = 300   # "in the past 300 blocks"
BLOCK_SECONDS = 12
AT_LEAST = 3        # "if that's fewer than three, show the most recent three"
IN_ALL = 10         # "10-n people" below the rule
SCAN = 3000         # recent messages looked through: plenty for ten people


def _said(msg):
    """Whether a message is something a person said (not a wrapper, a silence, a system line)."""
    from .mood_view import is_wrapper, prose
    text = prose(msg.content)
    return bool(text) and not is_wrapper(text)


def pane(slug, now=None):
    """{'here': [{'name', 'at'}], 'elsewhere': [{'name', 'at', 'where', 'title', 'url'}]}, newest first."""
    from django.utils import timezone
    from conversations.models import Message, ThinkingEntity, TidingComment
    from . import settings as knobs
    now = now or timezone.now()
    people = set(ThinkingEntity.objects.filter(is_biological_human=True).values_list('name', flat=True))
    archived = knobs.archived_slugs()
    rows = (Message.objects.filter(mood__isnull=False, sender_id__in=people, is_sidechain=False, thought__isnull=True,
                                   tooluse__isnull=True, toolresult__isnull=True)
            .select_related('mood').order_by('-created_at').only('content', 'created_at', 'sender_id', 'mood')[:SCAN])
    here, latest = [], {}  # latest: each person's newest word anywhere, as (at, where, title, url)
    for msg in rows:
        if msg.mood.slug in archived or not _said(msg):
            continue
        who = msg.sender_id
        if msg.mood.slug == slug and who not in [h['name'] for h in here]:
            here.append({'name': who, 'at': msg.created_at})
        if who not in latest:
            latest[who] = (msg.created_at, 'mood', msg.mood.title or msg.mood.slug,
                           f'/moods/{msg.mood.slug}/#m-{msg.id}')
    for c in (TidingComment.objects.filter(by_id__in=people).exclude(changed='deleted')
              .select_related('tiding').order_by('-at')[:500]):
        if c.by_id not in latest or c.at > latest[c.by_id][0]:
            latest[c.by_id] = (c.at, 'tiding', c.tiding.title or c.tiding.url, f'/moods/tidings/{c.tiding_id}/#c-{c.id}')
    since = now - timedelta(seconds=HERE_BLOCKS * BLOCK_SECONDS)
    recent = [h for h in here if h['at'] >= since]
    shown = recent if len(recent) >= AT_LEAST else here[:AT_LEAST]
    named = {h['name'] for h in shown}
    rest = sorted(((who, *what) for who, what in latest.items() if who not in named), key=lambda r: r[1], reverse=True)
    out = lambda at: at.isoformat()
    return {'here': [{'name': h['name'], 'at': out(h['at'])} for h in shown],
            'elsewhere': [{'name': who, 'at': out(at), 'where': where, 'title': title, 'url': url}
                          for who, at, where, title, url in rest[:max(0, IN_ALL - len(shown))]]}
