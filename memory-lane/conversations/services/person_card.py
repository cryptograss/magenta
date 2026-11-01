"""A person's card, opened by clicking their name (Justin, 31 Oct): their User page in brief, as its hover
shows; their last few messages, each with its Mood and what answering it took (which emergents, tokens,
cost: the prompts page's figures, services/tidings); the PickiPedia pages they edited last; and their trail
across the Moods, the ones they opened (services/presence.trail) and the ones they posted in.
"""

import hashlib
import time

from django.core.cache import cache

from conversations.models import Message, Mood, ThinkingEntity
from conversations.services import presence
from conversations.services.mood_view import pickipedia_url, prose, snippet_of

MESSAGES = 5
EDITS = 5
SNIPPET = 160
LOOK_BACK = 40       # their newest posts looked through, for the five with words and for the trail's ✎
EDITS_KEPT = 300     # seconds: what PickiPedia says they edited


def wiki_name(name):
    from conversations.services import wiki_auth
    return wiki_auth.names().get(name) or name[:1].upper() + name[1:]


def _prompts_by_id(name):
    """{message id: its prompt entry}: a pair's short post and the post it pairs with both find it."""
    from conversations.services import tidings
    found = {}
    for human in tidings.prompt_tidings():
        if human['name'] != name:
            continue
        for entry in human['prompts']:
            found[entry['id']] = entry
            if entry.get('trigger'):
                found[entry['trigger']['id']] = entry
    return found


def _answered(entry):
    """Which emergents were asked and which answered, with each answer's tokens and cost."""
    if not entry:
        return {'asked': [], 'turns': []}
    return {'asked': list(entry.get('agents') or []),
            'turns': [{'agent': t['agent'], 'tokens': t['tokens'], 'cost': t['cost'], 'model': t.get('model', '')}
                      for t in entry.get('turns', [])]}


def edits(name, http=None):
    """[{'title', 'at', 'comment', 'url'}]: the last EDITS distinct pages `name` edited on PickiPedia."""
    import requests
    user = wiki_name(name)
    key = 'person-edits:' + hashlib.sha1(user.encode()).hexdigest()
    found = cache.get(key)
    if found is not None:
        return found
    try:
        rows = (http or requests).get(
            f'{pickipedia_url()}/api.php', timeout=6, headers={'User-Agent': 'memory-lane (magenta; person card)'},
            params={'action': 'query', 'list': 'usercontribs', 'ucuser': user, 'uclimit': 50,
                    'ucprop': 'title|timestamp|comment', 'format': 'json', 'formatversion': 2},
        ).json()['query']['usercontribs']
    except Exception:
        return []  # not kept: asked again at the next card
    found, seen = [], set()
    for row in rows:
        if row['title'] in seen:
            continue
        seen.add(row['title'])
        found.append({'title': row['title'], 'at': row.get('timestamp', ''), 'comment': (row.get('comment') or '')[:120],
                      'url': f"{pickipedia_url()}/wiki/{row['title'].replace(' ', '_')}"})
        if len(found) == EDITS:
            break
    cache.set(key, found, EDITS_KEPT)
    return found


def card(name, http=None):
    """What the card shows, or None if `name` isn't a person here."""
    from conversations.services import wiki_preview
    person = ThinkingEntity.objects.filter(name=name, is_biological_human=True).first()
    if person is None:
        return None
    user = wiki_name(name)
    posts = list(Message.objects.filter(sender_id=name, is_sidechain=False, mood__isnull=False)
                 .select_related('mood').order_by('-created_at')[:LOOK_BACK])

    prompts = _prompts_by_id(name)
    messages = []
    for post in posts:
        text = prose(post.content).strip()
        if not text or text == '[deleted]':
            continue
        messages.append({'id': str(post.id), 'mood': post.mood.slug, 'mood_title': post.mood.title,
                         'at': post.created_at.isoformat(), 'block': post.eth_blockheight,
                         'snippet': snippet_of(text)[:SNIPPET], **_answered(prompts.get(str(post.id)))})
        if len(messages) == MESSAGES:
            break

    # The trail: each Mood once, at the latest they opened or posted in it; ✎ where they posted.
    since = time.time() - presence.TRAIL_FOR
    steps = {step['mood']: step['at'] for step in presence.trail(name)}
    posted = set()
    for post in posts:
        at = post.created_at.timestamp()
        if at < since:
            break
        posted.add(post.mood.slug)
        steps[post.mood.slug] = max(steps.get(post.mood.slug, 0), at)
    latest = sorted(steps.items(), key=lambda kv: kv[1], reverse=True)[:presence.TRAIL_KEPT]
    titles = dict(Mood.objects.filter(slug__in=[slug for slug, _ in latest]).values_list('slug', 'title'))
    trail = [{'mood': slug, 'title': titles.get(slug, slug), 'at': at, 'posted': slug in posted}
             for slug, at in latest if slug in titles]

    return {'name': name, 'wiki_name': user, 'page': f"{pickipedia_url()}/wiki/User:{user.replace(' ', '_')}",
            'profile': wiki_preview.preview(f'User:{user}', http=http), 'messages': messages,
            'edits': edits(name, http=http), 'trail': trail}
