"""A message, added to your notes for the current bloom (Justin, 24 Oct: "Add this message to my current
sprint notes ... sometimes a message is just the perfect little snapshot of what to note"). A sprint is a bloom
now (Justin, #interfaces, 13 Nov 2026, as Skyler reminded: "we want to change the word 'sprint' to 'bloom' in our teamwork practice").

Settled with re (25 Oct), who keeps the wiki side and its documentation:

* **The current bloom** is whatever one page names: Magenta:Current bloom (Magenta:Current sprint, until
  that page has moved: read only when the new one isn't there yet). A link there
  ([[Cryptograss/Jellyfish]]) names the bloom by its page's last part ("Jellyfish"); plain words name
  it as they are. Moving that pointer is a bloom's whole lifecycle; nothing here keeps dates.
* **Each person's notes for a bloom** are a page of their own: Cryptograss:Blooms/<bloom>/<person>, by
  their PickiPedia name. A bloom's page can show everyone's.
* **A note keeps the message's words and its link**: words so it reads on its own after the message is
  edited or gone, the link to go back to it; who said it, where and when. Written as a card,
  {{Magenta message}} with every word (services/wiki_embed.py), and when it was noted.

People only, each to their own notes. The server's wiki bot writes them (todo_ticks.Bot), naming in the
edit summary whose notes and from which message -- nobody's wiki password is held here. The Cryptograss:
namespace isn't gated for bot edits (PickiPediaVerification enforces markers in the main and Instructions
namespaces), so a note is plain, as the person chose it. Notes moved from Magenta:Sprints to Cryptograss:Sprints
on 9 Nov (Justin: "let's move Magenta:Sprints to Cryptograss:Sprints"), and to Cryptograss:Blooms on 13 Nov;
the old titles redirect.
"""

import re

POINTER = 'Magenta:Current bloom'
OLD_POINTER = 'Magenta:Current sprint'  # until the pointer page moves to POINTER
NOTES = 'Cryptograss:Blooms/{bloom}/{person}'
_LINK = re.compile(r'\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]')


class Refused(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def bloom_from(text):
    """(name, its page or None) from the pointer page's text; (None, None) if it names nothing."""
    from .mood_view import wiki_title
    text = re.sub(r'<!--.*?-->|<noinclude>.*?</noinclude>', '', text or '', flags=re.S)
    link = _LINK.search(text)
    if link:
        page = wiki_title(link.group(1))
        return page.rsplit('/', 1)[-1].strip(), page
    for line in text.splitlines():
        words = re.sub(r"[*#:;=']|\{\{[^{}]*\}\}|<[^>]*>", '', line).strip()
        if words:
            return words[:80], None
    return None, None


def current(http=None):
    """(name, page or None) of the bloom Magenta:Current bloom names now; Refused if it names none."""
    import requests
    from .todo import Unreadable, _raw
    try:
        text = _raw(POINTER, http or requests)
        if text is None:
            text = _raw(OLD_POINTER, http or requests)
    except (Unreadable, OSError) as e:
        raise Refused(f"couldn't read {POINTER}: {e}", 503)
    name, page = bloom_from(text)
    if not name:
        raise Refused(f'no current bloom: {POINTER} names none yet', 409)
    return name, page


def shown():
    """{'name', 'url'} of the current bloom, for the header's menu (Justin, 3 Nov); None if there's none or the
    wiki can't say. Kept ten minutes, a failure one, so a page load never waits on the wiki twice running."""
    from django.conf import settings
    from django.core.cache import cache
    if not getattr(settings, 'INTERFACE_WORDS_FROM_WIKI', True):  # off under test, as the interface's words
        return None
    kept = cache.get('current-bloom')
    if kept is not None:
        return kept or None
    try:
        name, page = current()
    except Exception:  # noqa: BLE001 -- no bloom, or the wiki away: the menu just doesn't say
        cache.set('current-bloom', {}, 60)
        return None
    wiki = getattr(settings, 'PICKIPEDIA_URL', 'https://pickipedia.xyz').rstrip('/')
    found = {'name': name, 'url': f"{wiki}/wiki/{(page or POINTER).replace(' ', '_')}"}
    cache.set('current-bloom', found, 600)
    return found


def notes_page(bloom, person):
    """Cryptograss:Blooms/<bloom>/<their PickiPedia name>."""
    from .wiki_auth import names
    who = names().get(person) or person[:1].upper() + person[1:]
    return NOTES.format(bloom=bloom.replace('[', '').replace(']', '').replace('|', ''), person=who)


def note(message, noted_at):
    """A message as a note: its card, every word of it (Template:Magenta message), and when it was noted."""
    from .wiki_embed import template_call
    return f"{template_call(message, chars='all')}\n<small>noted {noted_at.strftime('%-d %b %Y %H:%MZ')}</small>\n\n"


def header(bloom, bloom_page, person_page):
    named = f'[[{bloom_page}|{bloom}]]' if bloom_page else f"'''{bloom}'''"
    person = person_page.rsplit('/', 1)[-1]
    return (f'Notes for the {named} bloom, kept by [[User:{person}|{person}]]: messages from magenta they chose '
            f'to note, each with its words and its link. Added from a message\'s ⋯ menu, "Add to my bloom notes"; '
            f'see [[Cryptograss:Blooms]]. Edit freely.\n\n')


SOURCE = 'bloom-note'


def announce_note(message, person, bloom, page, url):
    """Leave an event line in the message's Mood, and also in #general (Justin, 30 Oct: "When someone adds
    something to their bloom notes, it needs to say that in the mood, and also in #general")."""
    import time
    import uuid
    from conversations.models import ConversationParticipant, Message, Mood
    system, _ = ConversationParticipant.objects.get_or_create(name='system',
                                                              defaults={'participant_type': 'system'})
    now_ms = int(time.time() * 1000)
    content = {
        'type': SOURCE,
        'by': person,
        'message': str(message.id),
        'message_by': message.sender_id,
        'mood': message.mood.slug if message.mood else '',
        'bloom': bloom,
        'page': page,
        'url': url,
    }
    if message.mood:
        Message.objects.create(id=uuid.uuid4(), sender=system, mood=message.mood,
                               source_file=SOURCE, timestamp=now_ms, content=content)
    general = Mood.objects.filter(slug='general').first()
    if general and (not message.mood or general.id != message.mood.id):
        Message.objects.create(id=uuid.uuid4(), sender=system, mood=general,
                               source_file=SOURCE, timestamp=now_ms, content=content)


def add(message, person, bot=None, http=None, now=None):
    """Add `message` to `person`'s notes for the current bloom: {'bloom', 'page', 'url', 'already'}. Refused if
    there's no bloom named, no wiki bot here, or the wiki won't take it."""
    from django.utils import timezone
    from .mood_view import pickipedia_url
    from . import todo_ticks
    if bot is None and not todo_ticks.enabled():
        raise Refused('no wiki bot is set up here to write notes', 503)
    bloom, bloom_page = current(http)
    page = notes_page(bloom, person)
    url = f"{pickipedia_url()}/wiki/{page.replace(' ', '_')}"
    bot = bot or todo_ticks.Bot(purpose='bloom notes')
    if not bot.login():
        raise Refused("the wiki bot couldn't sign in", 503)
    entry = note(message, now or timezone.now())
    for _ in range(3):  # again, if someone edited it meanwhile
        revision = bot.read(page)
        text = revision['slots']['main']['content'] if revision else header(bloom, bloom_page, page)
        if f'|id={message.id}\n' in text:
            return {'bloom': bloom, 'page': page, 'url': url, 'already': True}
        done = bot.edit(page, text.rstrip('\n') + '\n' + entry, f'A note for {person}, from #{message.mood.slug} '
                        f'(#m-{message.id}), by magenta at their ask', revision)
        if (done.get('edit') or {}).get('result') == 'Success':
            announce_note(message, person, bloom, page, url)
            return {'bloom': bloom, 'page': page, 'url': url, 'already': False}
        if (done.get('error') or {}).get('code') not in ('editconflict', 'articleexists', 'missingtitle'):
            raise Refused(f"the wiki didn't take it: {(done.get('error') or {}).get('info') or done}", 502)
    raise Refused('the page kept changing; try again', 409)
