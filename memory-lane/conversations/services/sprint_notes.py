"""A message, added to your notes for the current sprint (Justin, 24 Oct: "Add this message to my current
sprint notes ... sometimes a message is just the perfect little snapshot of what to note").

Settled with re (25 Oct), who keeps the wiki side and its documentation:

* **The current sprint** is whatever one page names: Magenta:Current sprint. A link there
  ([[Cryptograss/Jellyfish]]) names the sprint by its page's last part ("Jellyfish"); plain words name
  it as they are. Moving that pointer is a sprint's whole lifecycle; nothing here keeps dates.
* **Each person's notes for a sprint** are a page of their own: Magenta:Sprints/<sprint>/<person>, by
  their PickiPedia name. A sprint's page can show everyone's.
* **A note keeps the message's words and its link**: words so it reads on its own after the message is
  edited or gone, the link to go back to it; who said it, where and when. Written as a card,
  {{Magenta message}} with every word (services/wiki_embed.py), and when it was noted.

People only, each to their own notes. The server's wiki bot writes them (todo_ticks.Bot), naming in the
edit summary whose notes and from which message -- nobody's wiki password is held here. The Magenta:
namespace isn't gated for bot edits (PickiPediaVerification enforces markers in the main namespace), so
a note is plain, as the person chose it.
"""

import re

POINTER = 'Magenta:Current sprint'
NOTES = 'Magenta:Sprints/{sprint}/{person}'
_LINK = re.compile(r'\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]')


class Refused(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def sprint_from(text):
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
    """(name, page or None) of the sprint Magenta:Current sprint names now; Refused if it names none."""
    import requests
    from .todo import Unreadable, _raw
    try:
        text = _raw(POINTER, http or requests)
    except (Unreadable, OSError) as e:
        raise Refused(f"couldn't read {POINTER}: {e}", 503)
    name, page = sprint_from(text)
    if not name:
        raise Refused(f'no current sprint: {POINTER} names none yet', 409)
    return name, page


def notes_page(sprint, person):
    """Magenta:Sprints/<sprint>/<their PickiPedia name>."""
    from .wiki_auth import names
    who = names().get(person) or person[:1].upper() + person[1:]
    return NOTES.format(sprint=sprint.replace('[', '').replace(']', '').replace('|', ''), person=who)


def note(message, noted_at):
    """A message as a note: its card, every word of it (Template:Magenta message), and when it was noted."""
    from .wiki_embed import template_call
    return f"{template_call(message, chars='all')}\n<small>noted {noted_at.strftime('%-d %b %Y %H:%MZ')}</small>\n\n"


def header(sprint, sprint_page, person_page):
    named = f'[[{sprint_page}|{sprint}]]' if sprint_page else f"'''{sprint}'''"
    person = person_page.rsplit('/', 1)[-1]
    return (f'Notes for the {named} sprint, kept by [[User:{person}|{person}]]: messages from magenta they chose '
            f'to note, each with its words and its link. Added from a message\'s ⋯ menu, "Add to my sprint notes"; '
            f'see [[Magenta:Sprints]]. Edit freely.\n\n')


SOURCE = 'sprint-note'


def announce_note(message, person, sprint, page, url):
    """Leave an event line in the message's Mood, and also in #general (Justin, 30 Oct: "When someone adds
    something to their sprint notes, it needs to say that in the mood, and also in #general")."""
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
        'sprint': sprint,
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
    """Add `message` to `person`'s notes for the current sprint: {'sprint', 'page', 'url', 'already'}. Refused if
    there's no sprint named, no wiki bot here, or the wiki won't take it."""
    from django.utils import timezone
    from .mood_view import pickipedia_url
    from . import todo_ticks
    if bot is None and not todo_ticks.enabled():
        raise Refused('no wiki bot is set up here to write notes', 503)
    sprint, sprint_page = current(http)
    page = notes_page(sprint, person)
    url = f"{pickipedia_url()}/wiki/{page.replace(' ', '_')}"
    bot = bot or todo_ticks.Bot(purpose='sprint notes')
    if not bot.login():
        raise Refused("the wiki bot couldn't sign in", 503)
    entry = note(message, now or timezone.now())
    for _ in range(3):  # again, if someone edited it meanwhile
        revision = bot.read(page)
        text = revision['slots']['main']['content'] if revision else header(sprint, sprint_page, page)
        if f'|id={message.id}\n' in text:
            return {'sprint': sprint, 'page': page, 'url': url, 'already': True}
        done = bot.edit(page, text.rstrip('\n') + '\n' + entry, f'A note for {person}, from #{message.mood.slug} '
                        f'(#m-{message.id}), by magenta at their ask', revision)
        if (done.get('edit') or {}).get('result') == 'Success':
            announce_note(message, person, sprint, page, url)
            return {'sprint': sprint, 'page': page, 'url': url, 'already': False}
        if (done.get('error') or {}).get('code') not in ('editconflict', 'articleexists', 'missingtitle'):
            raise Refused(f"the wiki didn't take it: {(done.get('error') or {}).get('info') or done}", 502)
    raise Refused('the page kept changing; try again', 409)
