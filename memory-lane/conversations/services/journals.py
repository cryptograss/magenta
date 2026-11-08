"""The emergents' journals, for the journals section (/moods/journals/; Justin, 8 Nov: "add journals to the
chevron menu"). Each entry is a PickiPedia page, Emergent:<Name>/Journal/<block>, written by that emergent
(recall.journal_prefix): listed newest first by block across every emergent, each with its heading and its
first lines. Read from the wiki at most every KEEP seconds; a new or changed entry forgets it at once
(wiki_hooks.yml: journals)."""

import re

from django.core.cache import cache

KEY = 'journals:board'
KEEP = 600      # seconds the list is kept
SHOWN = 40      # the newest entries shown, across every emergent
SNIPPET = 320   # characters of an entry's opening shown

HEADING = re.compile(r'^\s*=+\s*(.*?)\s*=+\s*$', re.M)


def _plain(wikitext):
    """An entry's opening as plain words: no headings, templates, refs or link brackets."""
    text = HEADING.sub('', wikitext or '')
    text = re.sub(r'\{\{[^{}]*\}\}', '', text)
    text = re.sub(r'<ref[^>]*>.*?</ref>|<[^>]+>', '', text, flags=re.S)
    text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', text)
    text = re.sub(r'\[https?://\S+\s+([^\]]*)\]', r'\1', text)
    text = re.sub(r"'{2,}", '', text)
    text = re.sub(r'^[*#:;]+\s*', '', text, flags=re.M)
    text = ' '.join(text.split())
    return text if len(text) <= SNIPPET else text[:SNIPPET].rsplit(' ', 1)[0] + '…'


def entry(title, wikitext, agent):
    block = title.rsplit('/', 1)[1].replace(',', '')
    heading = HEADING.search(wikitext or '')
    return {'title': title, 'agent': agent, 'block': int(block), 'heading': heading.group(1) if heading else '',
            'opening': _plain(wikitext), 'url': 'https://pickipedia.xyz/wiki/' + title.replace(' ', '_')}


def board(http=None):
    """{'entries': the SHOWN newest, 'counts': [(emergent, entries, newest block)], 'error'}."""
    found = cache.get(KEY)
    if found is not None:
        return found
    import requests

    from conversations.models import ThinkingEntity
    from .mood_view import pickipedia_url
    from .recall import EMERGENT_NAMESPACE, journal_prefix
    http = http or requests
    api, headers = f'{pickipedia_url()}/api.php', {'User-Agent': 'memory-lane (magenta)'}
    listed, counts, error = [], [], ''
    try:
        for agent in sorted(ThinkingEntity.objects.filter(is_biological_human=False).values_list('name', flat=True)):
            prefix = journal_prefix(agent).split(':', 1)[1]
            pages = http.get(api, timeout=8, headers=headers, params={
                'action': 'query', 'list': 'allpages', 'apnamespace': EMERGENT_NAMESPACE, 'apprefix': prefix,
                'aplimit': 500, 'format': 'json', 'formatversion': '2'}).json()['query']['allpages']
            mine = [(int(p['title'].rsplit('/', 1)[1].replace(',', '')), p['title'], agent) for p in pages
                    if re.fullmatch(r'[\d,]+', p['title'].rsplit('/', 1)[1])]
            if mine:
                counts.append((agent, len(mine), max(b for b, _, _ in mine)))
            listed += mine
        newest = sorted(listed, reverse=True)[:SHOWN]
        text = {}
        for i in range(0, len(newest), 50):
            got = http.get(api, timeout=8, headers=headers, params={
                'action': 'query', 'prop': 'revisions', 'rvprop': 'content', 'rvslots': 'main',
                'titles': '|'.join(t for _, t, _ in newest[i:i + 50]), 'format': 'json', 'formatversion': '2'}).json()
            text.update({p['title']: p['revisions'][0]['slots']['main']['content']
                         for p in got['query']['pages'] if p.get('revisions')})
        entries = [entry(t, text.get(t, ''), agent) for _, t, agent in newest]
    except Exception as e:  # noqa: BLE001 -- the wiki down: said on the page, and asked again soon
        return {'entries': [], 'counts': [], 'error': f"PickiPedia couldn't be read just now ({type(e).__name__})"}
    found = {'entries': entries, 'counts': sorted(counts, key=lambda c: -c[2]), 'error': error}
    cache.set(KEY, found, KEEP)
    return found


def forget():
    cache.delete(KEY)
