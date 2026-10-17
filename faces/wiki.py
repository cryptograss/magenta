"""The reference photos, from PickiPedia: Category:Face reference.

A reference is a file page carrying {{Face reference|Person}}, where Person is the
title of that person's PickiPedia page. The template puts the file in the category
(and says, on the page, whose face it is); the person is read from the template.
A file in the category without a person named is left out, and said so.
"""

import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger('faces')

API = 'https://pickipedia.xyz/api.php'
CATEGORY = 'Category:Face reference'
USER_AGENT = 'magenta-faces/1 (https://github.com/cryptograss/memory-lane; faces/)'
# {{Face reference|Justin Holmes}} or {{Face reference|person=Justin Holmes}}
TEMPLATE = re.compile(r'\{\{\s*[Ff]ace[ _]reference\s*\|\s*(?:person\s*=\s*)?([^|}]+?)\s*(?:\|[^}]*)?\}\}')


def _get(params, api=API):
    url = api + '?' + urllib.parse.urlencode({**params, 'format': 'json', 'formatversion': '2'})
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def person_in(wikitext):
    """Whose face a reference's page says it is; None if it doesn't."""
    m = TEMPLATE.search(wikitext or '')
    return m.group(1).replace('_', ' ').strip() if m else None


def references(api=API):
    """Every file in the category: [{'file', 'person', 'sha1', 'url'}] (person None if unnamed)."""
    out, cont = [], {}
    while True:
        data = _get({'action': 'query', 'generator': 'categorymembers', 'gcmtitle': CATEGORY, 'gcmtype': 'file',
                     'gcmlimit': '50', 'prop': 'imageinfo|revisions', 'iiprop': 'url|sha1',
                     'rvprop': 'content', 'rvslots': 'main', **cont}, api)
        for page in (data.get('query') or {}).get('pages', []):
            info = (page.get('imageinfo') or [{}])[0]
            rev = (page.get('revisions') or [{}])[0]
            text = ((rev.get('slots') or {}).get('main') or {}).get('content', '')
            if not info.get('url'):
                continue
            out.append({'file': page['title'], 'person': person_in(text), 'sha1': info.get('sha1', ''), 'url': info['url']})
        if 'continue' not in data:
            return out
        cont = data['continue']


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def sync(store, engine, api=API, listed=None, get=fetch):
    """The store made to match the category: new and changed photos fingerprinted, relabelled ones
    relabelled, removed ones forgotten. A reference photo should hold one face; where it holds more,
    the largest is taken (the one it's of). Returns what it did, for the log and the caller."""
    listed = references(api) if listed is None else listed
    known = store.known()
    done = {'added': [], 'relabelled': [], 'unnamed': [], 'no_face': [], 'failed': [], 'removed': []}
    current = {}
    for ref in listed:
        if not ref['person']:
            done['unnamed'].append(ref['file'])
            continue
        current[ref['file']] = ref['person']
        if known.get(ref['file']) == ref['sha1']:
            continue
        try:
            seen = engine.look(get(ref['url']))
        except Exception as e:  # one photo that won't come doesn't stop the rest
            log.warning('reference %s: %s', ref['file'], e)
            done['failed'].append(ref['file'])
            continue
        if not seen or not seen[2]:
            done['no_face'].append(ref['file'])
            current.pop(ref['file'])
            continue
        store.put(ref['file'], ref['person'], ref['sha1'], seen[2][0]['fingerprint'])
        done['added'].append(ref['file'])
    for file, person, _, _ in list(store.references()):
        if file in current and current[file] != person:
            store.relabel(file, current[file])
            done['relabelled'].append(file)
    done['removed'] = store.keep_only(current)
    return done
