"""The Moods' links to PickiPedia, kept on PickiPedia: a ledger.

Every PickiPedia page a Mood message links -- [[Page]], or the page's full
address -- is listed on Cryptograss:Moods/<mood>/links/<YYYY-MM>, a page for
each Mood and month (UTC), inside <moodlinks> (PickiPediaContent's
MoodLinks.php): the page, the message, who said it, when, at what block, and
its first words. PickiPedia shows each page the messages linking it, at its
foot, under "Linked from magenta". So the links live on the wiki, not only
in magenta's record (Justin, 19 Oct 2026, choosing this over PickiPedia
asking magenta whenever a page is viewed).

A month's page is written whole from the record, by the to-do bot
(todo_ticks: DrivingThatTrain, exempt from the verification gate, its edits
flagged a bot's): what the record says now, so a message deleted or edited
here drops out there at the next look, and an edit made on the wiki is
overwritten. No scheduler: the runner's pulse nudges this, and it looks at
most every LOOK_EVERY seconds, at the months of messages new or changed since
its last look; every SWEEP_EVERY, at every month.

A message waits GRACE before it's written there. The wiki's history keeps
every revision, beyond what taking a message back here (services/retract.py)
can reach; an hour is the time to take back something pasted by mistake, and
for the scrubber to find a secret, before the wiki hears of it.
"""

import re
import threading
from datetime import datetime, timedelta, timezone

import yaml
from django.core.cache import cache

MAGENTA = 'https://magenta.cryptograss.live'
GRACE = 3600          # seconds a message waits before it's written to the wiki
LOOK_EVERY = 300      # seconds between looks at what's new
SWEEP_EVERY = 86400   # seconds between looks at every month
WORDS = 120           # characters of a message's first words
_THROUGH = 'wiki-ledger:through'  # the moment messages are ledgered up to
_SWEPT = 'wiki-ledger:swept'


def title(slug, month):
    """The ledger page for a Mood's month ('2026-10')."""
    return f'Cryptograss:Moods/{slug}/links/{month}'


def linked_pages(message, names):
    """The PickiPedia pages a Mood message links, in order: none for tool calls, the
    harness's wrappers, summaries, or anyone not a person or agent here."""
    from .mood_view import is_summary, is_wrapper, prose, wikilinks_in
    if message.sender_id not in names:
        return []
    text = prose(message.content)
    if not text or is_wrapper(text) or is_summary(message):
        return []
    return wikilinks_in(text)


def first_words(content):
    """A message's first words, as plain text, for its line on the wiki."""
    from .mood_view import _URL, _trim_url, pickipedia_page, prose, snippet_of

    def titled(match):  # a page's address as its title (snippet_of would take its underscores out)
        url, tail = _trim_url(match.group(1))
        page = pickipedia_page(url)
        return page[0] + tail if page else match.group(0)
    text = snippet_of(_URL.sub(titled, prose(content)))
    text = re.sub(r'\[\[([^\]|]*)\|([^\]]*)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]*)\]\]', r'\1', text)
    text = text.replace('<', '‹').replace('>', '›')  # nothing in it can close the tag it sits in
    if len(text) > WORDS:
        text = text[:WORDS].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'
    return text


def _linking(messages):
    """Mood messages that could link a page: a cheap look at their text first."""
    from django.db.models import Q, TextField
    from django.db.models.functions import Cast
    from .mood_view import MACHINERY_SENDERS
    return (messages.filter(mood__isnull=False, is_sidechain=False)
            .exclude(sender_id__in=MACHINERY_SENDERS)
            .annotate(text=Cast('content', TextField()))
            .filter(Q(text__contains='[[') | Q(text__contains='/wiki/')))


def rows(mood, month, until):
    """The ledger's rows for a Mood's month, oldest first: messages said before `until`."""
    from conversations.models import Message
    from .mood_view import known_names
    start = datetime.strptime(month, '%Y-%m').replace(tzinfo=timezone.utc)
    end = (start + timedelta(days=32)).replace(day=1)
    names = known_names()
    found = []
    messages = _linking(Message.objects.filter(mood=mood, created_at__gte=start,
                                               created_at__lt=min(end, until))).order_by('created_at', 'id')
    for message in messages:
        pages = linked_pages(message, names)
        if not pages:
            continue
        words = first_words(message.content)
        for page in pages:
            row = {'page': page, 'message': str(message.id), 'who': message.sender_id,
                   'when': message.created_at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
            if message.eth_blockheight:
                row['block'] = message.eth_blockheight
            row['words'] = words
            found.append(row)
    return found


def page_text(slug, month, found):
    """The ledger page's wikitext."""
    named = datetime.strptime(month, '%Y-%m').strftime('%B %Y')
    lines = yaml.safe_dump(found, allow_unicode=True, sort_keys=False, width=1000).rstrip() if found else ''
    return (f'Messages in [{MAGENTA}/moods/{slug}/ #{slug}] that link pages here, {named} (UTC). '
            "magenta writes this page from its record (memory-lane <code>services/wiki_ledger.py</code>), "
            'and each page linked lists them at its foot, under "Linked from magenta". '
            'An edit here is overwritten; a message taken back in magenta goes from here too.\n\n'
            f'<moodlinks>\n{lines}\n</moodlinks>\n')


def months_touched(since, until, changed_since):
    """{(mood id, 'YYYY-MM')} with a linking message said in [since, until), or one changed
    since `changed_since` (edited or deleted: its links may have gone). since=None: every month."""
    from conversations.models import Message, MessageChange
    said = _linking(Message.objects.filter(created_at__lt=until))
    if since is not None:
        said = said.filter(created_at__gte=since)
    touched = {(mood, at.strftime('%Y-%m')) for mood, at in said.values_list('mood_id', 'created_at')}
    if changed_since is not None:
        changed = MessageChange.objects.filter(at__gte=changed_since, message__created_at__lt=until)
        touched |= {(mood, at.strftime('%Y-%m'))
                    for mood, at in changed.values_list('mood_id', 'message__created_at')}
    return {(mood, month) for mood, month in touched if mood}


def look(http=None, now=None):
    """Write the ledger pages that have changed: {title: 'written' | 'same' | 'refused'}."""
    from conversations.models import Mood
    from . import todo_ticks
    now = now or datetime.now(timezone.utc)
    until = now - timedelta(seconds=GRACE)
    through = cache.get(_THROUGH)
    sweep = through is None or cache.get(_SWEPT) is None
    touched = months_touched(None if sweep else through, until, None if sweep else through - timedelta(minutes=10))
    done = {}
    if touched:
        moods = {m.id: m for m in Mood.objects.filter(id__in={mood for mood, _ in touched})}
        wiki = todo_ticks.Bot(http, 'Mood links')
        if not wiki.login():
            return {'': 'refused'}
        for mood_id, month in sorted(touched, key=lambda t: t[1]):
            mood = moods.get(mood_id)
            if not mood:
                continue
            found = rows(mood, month, until)
            name = title(mood.slug, month)
            done[name] = wiki.write(name, page_text(mood.slug, month, found), create=bool(found),
                                    summary=f'{len(found)} link{"" if len(found) == 1 else "s"} from #{mood.slug}, '
                                            "from magenta's record")
    if '' not in done and 'refused' not in done.values():
        cache.set(_THROUGH, until, None)
        if sweep:
            cache.set(_SWEPT, 1, SWEEP_EVERY)
    return done


def nudge():
    """Look, in the background, if it's been LOOK_EVERY seconds and the bot can write."""
    from . import todo_ticks
    if not todo_ticks.enabled() or not cache.add('wiki-ledger:lock', 1, LOOK_EVERY):
        return False

    def run():
        from django.db import connection
        try:
            look()
        except Exception:
            pass  # the wiki, or the network: the next look tries again
        finally:
            connection.close()
    threading.Thread(target=run, daemon=True).start()
    return True
