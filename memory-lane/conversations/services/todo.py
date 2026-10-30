"""A Mood's to-do list: what its people have to do, kept on PickiPedia.

Each Mood may have a page, Cryptograss:Moods/<slug>/todo, whose list is
YAML between <todo> tags -- on the wiki so anyone can edit it and see who
changed what, and plain enough to write by hand. PickiPedia shows it as a
checklist with real links (PickiPediaContent's <todo> tag); a list inside
<pre>, as the first ones were, is read too.

    <todo>
    - task: Merge memory-lane#113
      who: justin
      kind: merge
      link: https://github.com/jMyles/memory-lane/pull/113
    - task: Redeploy maybelle
      kind: deploy
      done: true
    </todo>

`task` is the one thing an item needs; `who` (a name, or several),
`kind` (review, merge, deploy, edit, or anything), `link`, `note` and
`done` are as they're useful. The page shows it beside the Mood
(views_moods.api_mood_todo); agents keep it current -- their instructions
point them to it (Cryptograss:Moods/Instructions) -- adding what they leave someone to do,
and ticking off what's been done.

A renamed Mood's list is found under its old name too, until it's moved.

**Merges tick themselves.** An open item whose link is a GitHub pull
request shows as done once GitHub says it was merged ('merged': true) --
whatever the page says, so nobody has to tick a merge by hand. The page
itself is left as it was; an agent tidies it when it next touches the list.
GitHub is asked once per repository every few minutes (its recently closed
pull requests), and a pull request not among them on its own, so sixty
unsigned requests an hour go a long way; settings.GITHUB_TOKEN, if set,
lifts that ceiling.
"""

import re

from django.core.cache import cache

from .repos import same

PAGE = 'Cryptograss:Moods/{slug}/todo'
FOR = 60          # seconds a list is kept before PickiPedia is asked again
MAX_ITEMS = 200
FIELDS = ('task', 'who', 'kind', 'link', 'note', 'done')
_PRE = re.compile(r'<(todo|pre)>\s*\n?(.*?)\n?\s*</\1>', re.S | re.I)


class Unreadable(Exception):
    """The page is there, but its list can't be read: why, to show."""


def page_title(slug):
    return PAGE.format(slug=slug)


def parse(text):
    """The items in a page's text: [{'task', 'who': [...], 'kind', 'link', 'note', 'done'}]. Unreadable if not a list."""
    import yaml
    match = _PRE.search(text or '')
    body = match.group(2) if match else (text or '')
    try:
        loaded = yaml.safe_load(body)
    except yaml.YAMLError as e:
        mark = getattr(e, 'problem_mark', None)
        raise Unreadable(f'its YAML has a problem{f" at line {mark.line + 1}" if mark else ""}: '
                         f'{getattr(e, "problem", None) or e}')
    if loaded is None:
        return []
    if not isinstance(loaded, list):
        raise Unreadable('its YAML should be a list: each item starting "- task: ..."')
    items = []
    for raw in loaded[:MAX_ITEMS]:
        if isinstance(raw, str):
            raw = {'task': raw}
        if not isinstance(raw, dict) or not str(raw.get('task') or '').strip():
            continue
        who = raw.get('who') or []
        who = [w.strip() for w in (who.split(',') if isinstance(who, str) else who) if str(w).strip()]
        items.append({'task': str(raw['task']).strip()[:500], 'who': [str(w)[:60] for w in who][:10],
                      'kind': str(raw.get('kind') or '').strip()[:40], 'link': str(raw.get('link') or '').strip()[:500],
                      'note': str(raw.get('note') or '').strip()[:1000], 'done': bool(raw.get('done'))})
    return items


def _raw(title, http):
    """The page's wikitext, or None if there's no such page."""
    from .mood_view import pickipedia_url
    response = http.get(f'{pickipedia_url()}/index.php', params={'title': title, 'action': 'raw'}, timeout=6,
                        headers={'User-Agent': 'memory-lane (magenta; todo)'})
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise Unreadable(f'PickiPedia answered {response.status_code}')
    return response.text


def for_mood(mood, http=None):
    """{'page', 'edit', 'items', 'error'?}: the Mood's list (under its name now, or one it had), kept a minute."""
    key = f'todo:{mood.slug}'
    found = cache.get(key)
    if found is not None:
        return found
    import requests
    from urllib.parse import quote
    from .mood_view import pickipedia_url
    http = http or requests
    names = [mood.slug] + list(mood.aliases.values_list('slug', flat=True))
    title, text, error = page_title(mood.slug), None, ''
    try:
        for slug in names:
            text = _raw(page_title(slug), http)
            if text is not None:
                title = page_title(slug)
                break
    except Exception as e:  # noqa: BLE001 -- PickiPedia unreachable: say so, and ask again soon
        error = str(e) if isinstance(e, Unreadable) else "PickiPedia couldn't be reached"
    items = []
    if text is not None and not error:
        try:
            items = parse(text)
        except Unreadable as e:
            error = str(e)
    items = with_merges(items, http)
    if text is not None and not error:
        from .todo_ticks import tick_later  # merged, but open on the page: a bot ticks it there too
        for item in items:
            if item.get('merged'):
                tick_later(title, item['link'])
    page = f"{pickipedia_url()}/wiki/{quote(title.replace(' ', '_'))}"
    found = {'page': page, 'edit': f'{page}?action=edit', 'exists': text is not None, 'items': items,
             **({'error': error} if error else {})}
    cache.set(key, found, 15 if error else FOR)
    return found


MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
          'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
_DATE_ISO = re.compile(r'\b(202\d)-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2}))?')
_DATE_DM = re.compile(r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+(\d{4}))?', re.I)
_DATE_MD = re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:,?\s+(\d{4}))?', re.I)
_PULL_NUM = re.compile(r'(?:pull/|#)(\d{2,4})')
_TIME_HM = re.compile(r'(\d{2}):(\d{2})Z?')


def parse_item_date(text):
    m = _DATE_ISO.search(text)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4) or 0), int(m.group(5) or 0))
    m = _DATE_DM.search(text)
    if m:
        d, mo = int(m.group(1)), MONTHS[m.group(2)[:3].lower()]
        y = int(m.group(3) or 2026)
        tm = _TIME_HM.search(text)
        return (y, mo, d, int(tm.group(1)) if tm else 0, int(tm.group(2)) if tm else 0)
    m = _DATE_MD.search(text)
    if m:
        mo, d = MONTHS[m.group(1)[:3].lower()], int(m.group(2))
        y = int(m.group(3) or 2026)
        tm = _TIME_HM.search(text)
        return (y, mo, d, int(tm.group(1)) if tm else 0, int(tm.group(2)) if tm else 0)
    m = _PULL_NUM.search(text)
    if m:
        pr = int(m.group(1))
        day = min(30, max(1, 10 + (pr - 50) // 12))
        return (2026, 10, day, 12, 0)
    return None


def all_todos(http=None):
    """Every Mood's to-do items combined in reverse chronological order:
    {'items': [...], 'total': N, 'open': N, 'done': N, 'moods': [...]}.
    Justin, 30 Oct: another page combining all to-dos across all Moods."""
    key = 'todo:all_moods'
    found = cache.get(key)
    if found is not None:
        return found
    from conversations.models import Mood
    from . import settings as knobs
    archived = set(knobs.archived_slugs())
    moods = list(Mood.objects.all().order_by('slug'))
    all_items = []
    mood_summaries = []

    for m in moods:
        res = for_mood(m, http=http)
        items = res.get('items') or []
        if not items:
            continue
        parsed_dates = []
        for it in items:
            s = f"{it.get('task') or ''} {it.get('note') or ''} {it.get('link') or ''}"
            parsed_dates.append(parse_item_date(s))
        curr = (2026, 10, 1, 0, 0)
        for idx in range(len(parsed_dates)):
            if parsed_dates[idx]:
                curr = parsed_dates[idx]
            else:
                parsed_dates[idx] = curr

        m_open = sum(1 for it in items if not it.get('done') and not it.get('merged'))
        mood_summaries.append({'slug': m.slug, 'title': m.title or m.slug, 'count': len(items), 'open': m_open})

        for idx, it in enumerate(items):
            all_items.append({
                **it,
                'mood': m.slug,
                'mood_title': m.title or m.slug,
                'archived': m.slug in archived,
                '_sort_key': (*parsed_dates[idx], idx),
            })

    all_items.sort(key=lambda x: x['_sort_key'], reverse=True)
    open_count = sum(1 for it in all_items if not it.get('done') and not it.get('merged'))
    done_count = sum(1 for it in all_items if it.get('done') or it.get('merged'))

    result = {
        'items': all_items,
        'total': len(all_items),
        'open': open_count,
        'done': done_count,
        'moods': mood_summaries,
    }
    cache.set(key, result, 30)
    return result


# --- merges, from GitHub ------------------------------------------------------------

_PULL = re.compile(r'^https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)(?:[/#?].*)?$', re.I)
CLOSED_FOR = 300       # seconds a repository's recently closed pull requests are kept
OPEN_FOR = 600         # seconds a pull request on its own, still open, is kept
MERGED_FOR = 86400     # seconds one merged is kept: merged is merged


def _github(path, http, **params):
    from django.conf import settings
    token = getattr(settings, 'GITHUB_TOKEN', '')
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'memory-lane (magenta; todo)',
               **({'Authorization': f'Bearer {token}'} if token else {})}
    response = http.get(f'https://api.github.com{path}', params=params, headers=headers, timeout=6)
    return response.json() if response.status_code == 200 else None


def _recently_merged(owner, repo, http):
    """{number} of a repository's recently closed pull requests that were merged; None if GitHub won't say."""
    key = f'todo:merged:{same(f"{owner}/{repo}")}'
    found = cache.get(key)
    if found is None:
        listed = _github(f'/repos/{owner}/{repo}/pulls', http, state='closed', sort='updated', direction='desc',
                         per_page=100)
        found = {p['number'] for p in listed if p.get('merged_at')} if isinstance(listed, list) else 'unknown'
        cache.set(key, found, CLOSED_FOR)
    return None if found == 'unknown' else found


def _merged(owner, repo, number, http):
    """Whether GitHub says this pull request was merged (False if it won't say)."""
    recent = _recently_merged(owner, repo, http)
    if recent and number in recent:
        return True
    key = f'todo:pull:{same(f"{owner}/{repo}")}#{number}'
    found = cache.get(key)
    if found is None:
        pull = _github(f'/repos/{owner}/{repo}/pulls/{number}', http)
        found = bool(pull and pull.get('merged_at'))
        cache.set(key, found, MERGED_FOR if found else OPEN_FOR)
    return found


def with_merges(items, http):
    """The items, an open one linking a GitHub pull request that's been merged marked done ('merged': True)."""
    out = []
    for item in items:
        match = _PULL.match(item.get('link') or '')
        if match and not item['done']:
            try:
                if _merged(match.group(1), match.group(2), int(match.group(3)), http):
                    item = {**item, 'done': True, 'merged': True}
            except Exception:  # noqa: BLE001 -- GitHub unreachable: the item stands as written
                pass
        out.append(item)
    return out


def forget(mood):
    cache.delete(f'todo:{mood.slug}')
    cache.delete('todo:all_moods')
    changed()


# --- told at once: a merge, by GitHub's webhook; an edit, by the wiki feed ------------
# Asking GitHub on a timer is a few minutes behind. GitHub can say so itself
# the moment a pull request merges (a repository webhook, "Pull requests", to
# /api/github/hook/, signed with settings.GITHUB_WEBHOOK_SECRET). Then every
# list forgets what it kept, and the stamp every open page polls for changes:
# they ask for their list again within a few seconds.

STAMP = 'todo:stamp'


def changed():
    """Tell open pages a list may have changed: they ask again at their next poll."""
    import time
    cache.set(STAMP, int(time.time() * 1000), None)


def stamp():
    return cache.get(STAMP) or 0


def merged_now(owner, repo, number, title='', by='', commit=''):
    """GitHub says this pull request just merged: kept as merged, every list asked again,
    and each Mood whose list links it told so, in its thread (in the background)."""
    from conversations.models import Mood
    repository = same(f'{owner}/{repo}')  # a link to the old place is kept under the new one
    cache.set(f'todo:pull:{repository}#{number}', True, MERGED_FOR)
    recent = cache.get(f'todo:merged:{repository}')
    if isinstance(recent, set):
        cache.set(f'todo:merged:{repository}', recent | {number}, CLOSED_FOR)
    cache.delete_many([f'todo:{slug}' for slug in Mood.objects.values_list('slug', flat=True)])
    cache.delete('todo:all_moods')
    changed()
    _in_background(announce_merge, owner, repo, number, title, by, commit)


MERGED_SOURCE = 'merged'  # an event line (mood_view.EVENT_SOURCES): a pull request on the list merged


def announce_merge(owner, repo, number, title='', by='', commit=''):
    """A line in each Mood whose to-do list links this pull request: "memory-lane#142 (226537a)
    merged by jMyles", and which item it ticks; its title shown on hover."""
    import time
    import uuid
    from conversations.models import ConversationParticipant, Message, Mood
    system = None
    repository = same(f'{owner}/{repo}')
    for mood in Mood.objects.all():
        items = for_mood(mood).get('items', [])
        for item in items:
            found = _PULL.match(item.get('link') or '')
            if not found or (same(f'{found[1]}/{found[2]}'), int(found[3])) != (repository, number):
                continue
            if not cache.add(f'todo:announced:{repository}#{number}:{mood.slug}'.lower(), 1, 7 * 86400):
                break  # said already (GitHub sends a delivery again, now and then)
            if system is None:
                system, _ = ConversationParticipant.objects.get_or_create(name='system',
                                                                           defaults={'participant_type': 'system'})
            Message.objects.create(id=uuid.uuid4(), sender=system, mood=mood, source_file=MERGED_SOURCE,
                                   timestamp=int(time.time() * 1000),
                                   content={'type': 'merged', 'title': title or f'{owner}/{repo}#{number}',
                                            'pull': f"{repository.split('/')[-1]}#{number}",
                                            'commit': commit[:40], 'by': by, 'about': item.get('task', ''),
                                            'commit_url': f'https://github.com/{owner}/{repo}/commit/{commit[:40]}' if commit else '',
                                            'page': item.get('link')})
            break


def heard(event, outcome, refused=False):
    """What GitHub last sent, and what came of it: for anyone wondering whether the webhook works.

    A refused one (unsigned, or signed with another secret) is kept apart, so
    anyone's stray POST can't hide the last good delivery."""
    import time
    key = 'todo:hook:refused' if refused else 'todo:hook:last'
    cache.set(key, {'event': event, 'outcome': outcome, 'at': int(time.time())}, None)


def last_heard():
    return cache.get('todo:hook:last')


def last_refused():
    return cache.get('todo:hook:refused')


def _in_background(fn, *args):
    import threading

    def run():
        from django.db import connection
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 -- a line not said; the list still ticks
            pass
        finally:
            connection.close()
    threading.Thread(target=run, daemon=True).start()
