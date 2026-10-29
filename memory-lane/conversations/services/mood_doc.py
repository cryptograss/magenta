"""A Mood's one document (Justin, re and magent, 29 Oct): its instructions page on PickiPedia,
Cryptograss:Moods/<slug>/Instructions, whose first paragraph is the Mood's description and whose whole is what its
emergents are told (poller/instructions.py). Only a trusted editor's revision counts, as the bundle has it, so what
the page shows as a Mood's description is what its emergents read too -- never a box anyone with a key could rewrite.

A Mood whose page has no trusted revision yet (most, today) keeps the description it has here (Mood.description).
Read from the wiki at most every KEEP seconds, all Moods at once.
"""

import json
import re
import urllib.parse
import urllib.request

from django.core.cache import cache

KEY = 'mood-doc:leads'
KEEP = 600
KEEP_FAILED = 120
PLACEHOLDER = 'This Mood has no instructions of its own.'


def lead_of(wikitext):
    """The first paragraph of a page's wikitext, as plain words: its links as their words, templates and markup gone."""
    text = re.sub(r'<!--.*?-->', '', wikitext or '', flags=re.S)
    text = re.sub(r'\{\{[^{}]*\}\}', '', text)
    para = []
    for line in text.split('\n'):
        if line.strip().startswith('=') or (not line.strip() and para):
            break
        if line.strip():
            para.append(line.strip())
    lead = ' '.join(para)
    lead = re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]*)\]\]', r'\1', lead)
    lead = re.sub(r'\[https?://\S+ ([^\]]+)\]', r'\1', lead)
    lead = re.sub(r"'{2,}", '', lead).strip()
    return '' if lead == PLACEHOLDER else lead[:2000]


def _trusted(history, trusted):
    return next((text for user, text in history if (user or '').lower().replace('_', ' ') in trusted), None)


def read(slugs, fetch=None):
    """{slug: lead} for those whose page has a trusted revision with a first paragraph."""
    from poller.instructions import mood_title
    from poller.wiki_rules import API, EDITORS, USER_AGENT
    trusted = {e.lower().replace('_', ' ') for e in EDITORS}
    titles = {mood_title(s): s for s in slugs}

    def ask(params):
        url = API + '?' + urllib.parse.urlencode({**params, 'format': 'json', 'formatversion': '2'})
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=5) as r:
            return json.load(r)

    def fetch_all():
        out, names = {}, list(titles)
        for at in range(0, len(names), 50):  # each page's newest revision, fifty at a time
            pages = ask({'action': 'query', 'prop': 'revisions', 'titles': '|'.join(names[at:at + 50]),
                         'rvprop': 'content|user', 'rvslots': 'main'})['query'].get('pages') or []
            for page in pages:
                revs = page.get('revisions') or []
                if revs:
                    out[page['title']] = [(revs[0].get('user', ''), revs[0]['slots']['main']['content'])]
        for title, history in list(out.items()):  # its newest not a trusted editor's: back through its history
            if _trusted(history, trusted) is None:
                page = ask({'action': 'query', 'prop': 'revisions', 'titles': title, 'rvprop': 'content|user',
                            'rvslots': 'main', 'rvlimit': 20})['query']['pages'][0]
                out[title] = [(r.get('user', ''), r['slots']['main']['content']) for r in page.get('revisions') or []]
        return out

    found = {}
    for title, history in (fetch or fetch_all)().items():
        text = _trusted(history, trusted)
        lead = lead_of(text) if text else ''
        if lead and title in titles:
            found[titles[title]] = lead
    return found


def refresh(slugs=None, fetch=None):
    """Read every Mood's document now; what was read, or the last good read when the wiki doesn't answer."""
    if slugs is None:
        from conversations.models import Mood
        slugs = list(Mood.objects.values_list('slug', flat=True))
    try:
        found = read(slugs, fetch)
        cache.set(KEY, found, KEEP)
        cache.set(KEY + ':last', found, None)
    except Exception:
        found = cache.get(KEY + ':last') or {}
        cache.set(KEY, found, KEEP_FAILED)
    return found


def leads(wait=False):
    """{slug: lead}: kept a while, and read again in the background when it's stale, so a page asking for a
    Mood never waits on the wiki (`wait`: read now, for a test or the first read of a management command)."""
    kept = cache.get(KEY)
    if kept is not None:
        return kept
    if wait:
        return refresh()
    if cache.add(KEY + ':lock', 1, 60):
        import threading

        def run():
            from django.db import connection
            try:
                refresh()
            finally:
                cache.delete(KEY + ':lock')
                connection.close()
        threading.Thread(target=run, daemon=True).start()
    return cache.get(KEY + ':last') or {}


def description(mood):
    """What the page shows as `mood`'s description: its document's first paragraph, else the one kept here."""
    return leads().get(mood.slug) or mood.description
