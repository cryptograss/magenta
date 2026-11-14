"""To-do types: eight, each with its own way of getting done (Justin, #ramp, 13 Nov: "on days like today, the todos
become completely unmanageable. What we need are todo types").

An item's `kind` was free text, and 179 open items had 29 kinds ("decide" and "decision", "docs" and "document").
Each kind is read as one of TYPES (KINDS below; an unknown one is `work`), so no list needs rewriting by hand.

Closed as soon as they're finished, by what finishes them, wherever that can be told for sure (Justin, 14 Nov:
"really reliable, fast, push-side hooks for each type where practical"):

- merge: an item linking a candidate branch's page, or one of our pull requests, ticks when githouse merges or
  closes it (services/todo.py, with_merges). Pushed: githouse's merge (merge_orders.announce_merged) and GitHub's
  webhook forget every list.
- deploy: an item naming a server ("Redeploy maybelle ..."). If it names branches (magenta:magent/foo), it ticks
  once every one is merged and the server runs it: in the commit that server's last deploy reported for that
  repository (ramp.running), or, where its deploys don't report that repository, a deploy finished after the merge.
  Naming none, and no merge, a deploy of each server finished after the item was first seen here. Pushed: a finished deploy
  (servers.record_deploy) forgets every list. One naming a pull request by number, a merge it doesn't name as repo:branch ("after both merges"), or no
  server, waits for a person.
- verify: an item linking a PickiPedia page ticks once the page holds no unverified bot edit (it's out of
  Category:Pages with unverified bot claims) and its newest revision is a trusted editor's (wiki_rules.EDITORS, as
  services/verify.py reads instruction pages). Pushed: the page's change, from the wiki (wiki_hooks.run).
- decide, review, hands, make, work: by hand. Telling an answer from a comment, or a review from a read, isn't
  reliable; a wrong tick is worse than none.
"""

import hashlib
import re
import time
import urllib.parse

from django.core.cache import cache

# type: (emoji, label, how it gets done)
TYPES = {
    'merge': ('🪢', 'merge', 'ticks itself when githouse merges or closes the branch it links'),
    'deploy': ('🖥️', 'deploy', 'ticks itself when the server it names runs what it waits for'),
    'verify': ('✅', 'verify', 'ticks itself when the PickiPedia page it links is verified'),
    'decide': ('🤔', 'decide', 'a question only a person can answer'),
    'review': ('👀', 'review', 'read something and say yes or no'),
    'hands': ('🔧', 'hands', "a person's hands on a box, the vault or an account"),
    'make': ('🎻', 'make', "a person's own craft: music, writing, transcribing"),
    'work': ('🤖', 'work', "an emergent's own job; its emergent ticks it"),
}

KINDS = {
    'merge': 'merge', 'merges': 'merge', 'promote': 'merge',
    'deploy': 'deploy', 'redeploy': 'deploy',
    'verify': 'verify', 'check': 'verify', 'qa': 'verify',
    'decide': 'decide', 'decision': 'decide', 'answer': 'decide', 'choose': 'decide', 'design': 'decide',
    'data model': 'decide', 'identify': 'decide', 'question': 'decide',
    'review': 'review', 'read': 'review', 'approve': 'review',
    'hands': 'hands', 'infra': 'hands', 'secret': 'hands', 'admin': 'hands', 'config': 'hands', 'ops': 'hands',
    'account': 'hands', 'vault': 'hands', 'setup': 'hands', 'find': 'hands',
    'make': 'make', 'music': 'make', 'write': 'make', 'writing': 'make', 'transcribe': 'make', 'record': 'make',
    'edit': 'make', 'wiki': 'make',
    'work': 'work', 'code': 'work', 'build': 'work', 'pr': 'work', 'docs': 'work', 'document': 'work',
    'test': 'work', 'fix': 'work', 'research': 'work',
}


def type_of(item):
    """The item's type, one of TYPES: from its kind, else from its link (a branch's page is a merge), else work."""
    kind = (item.get('kind') or '').strip().lower()
    if kind in KINDS:
        return KINDS[kind]
    if not kind:
        from .todo import _BRANCH_PAGE, _PULL
        if _BRANCH_PAGE.match(item.get('link') or '') or _PULL.match(item.get('link') or ''):
            return 'merge'
    return 'work'


def with_types(items):
    return [{**item, 'type': type_of(item)} for item in items]


def summary(items):
    """[{'type', 'emoji', 'label', 'about', 'open', 'total'}] in TYPES' order, for the page's filters."""
    out = []
    for name, (emoji, label, about) in TYPES.items():
        mine = [i for i in items if i.get('type') == name]
        out.append({'type': name, 'emoji': emoji, 'label': label, 'about': about, 'total': len(mine),
                    'open': sum(1 for i in mine if not i.get('done'))})
    return out


# --- first seen: when an item appeared, for a deploy that names no branch -----------------------------

SEEN_KEY = 'todo:seen:{slug}:{task}'


def _seen_key(slug, task):
    return SEEN_KEY.format(slug=slug, task=hashlib.sha1(task.encode()).hexdigest()[:16])


def note_seen(slug, items, now=None):
    """Keep when each open deploy item was first read here (never moved later). The list's own wiki hook reads it
    the moment it's saved, so that's within seconds of its writing."""
    now = time.time() if now is None else now
    for item in items:
        if not item.get('done') and item.get('type') == 'deploy':
            cache.add(_seen_key(slug, item['task']), now, None)


def first_seen(slug, task):
    return cache.get(_seen_key(slug, task))


# --- deploy ------------------------------------------------------------------------------------------

_NAMED_THINGS = re.compile(r'\S+:\S+/\S+|https?://\S+|\b[\w-]+#\d+')  # repo:branch, a URL, repo#N: not a server


def _servers(text):
    from .tools_ask import named_servers
    return named_servers(_NAMED_THINGS.sub(' ', text or ''))


def _merge_time(repo, branch):
    """(the merged head, when its merge landed on its base, unix) or None if githouse hasn't merged it."""
    from . import githouse, ramp, work_pages
    if not githouse.branch_merged(repo, branch):
        return None
    held = githouse.repository(repo)
    head = githouse._sha(held, f'refs/merged/{branch}') or githouse._sha(held, f'refs/heads/{branch}')
    if not head:
        return None
    base = ramp._base(ramp._refs(held), '', repo)
    merge = work_pages._merge_commit(held, base, head) if base else None
    when = work_pages._read(held, 'log', '-1', '--format=%ct', merge or head).strip()
    return head, int(when) if when.isdigit() else None


def deployed(slug, item, runs=None, last_finished=None):
    """'deployed' when the deploy the item asks for has happened (the module's rules); None while it hasn't, or
    when it can't be told."""
    from . import ramp, servers as srv
    from .tools_ask import named_branches
    text = item.get('task') or ''
    wanted = _servers(text)
    if not wanted or re.search(r'(?<![\w/])#\d', text):  # no server, or a bare pull request number: a person's call
        return None
    finished = last_finished or srv.last_finished
    branches = named_branches(text)
    if branches is None:
        return None  # a repository githouse doesn't hold
    if not branches:
        if re.search(r'\bmerg', text, re.I):
            return None  # waits on merges it doesn't name as repo:branch ("after both merges"): a person's call
        seen = first_seen(slug, text)
        if seen is None:
            return None
        return 'deployed' if all((finished(s) or {}).get('at_ts', 0) > seen for s in wanted) else None
    runs = ramp.running() if runs is None else runs
    for repo, branch in branches:
        merged = _merge_time(repo, branch)
        if merged is None:
            return None
        head, when = merged
        live = ramp.live_on(repo, head, runs)
        for server in wanted:
            if live is not None and server in (runs.get(repo) or {}):
                if server not in live:
                    return None
            elif when is None or (finished(server) or {}).get('at_ts', 0) <= when:
                return None
    return 'deployed'


# --- verify ------------------------------------------------------------------------------------------

VERIFIED_KEY = 'todo:verified:{}'
VERIFIED_FOR = 600     # seconds a yes is kept; the page's own change drops it at once
UNVERIFIED_FOR = 60    # a no: the category can lag the edit by a moment (MediaWiki's job queue)
UNVERIFIED_CATEGORY = 'Category:Pages with unverified bot claims'
LINKED_KEY = 'todo:verify-linked'   # {title: [slug]}: which lists a page's change concerns


def wiki_title(link):
    """The PickiPedia page a link names, as MediaWiki writes titles; None if it isn't one."""
    from .mood_view import pickipedia_url
    link = (link or '').strip()
    base = pickipedia_url().rstrip('/')
    if link.startswith(base + '/wiki/'):
        title = link[len(base) + len('/wiki/'):].split('#')[0].split('?')[0]
    elif link.startswith(base + '/index.php?'):
        title = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get('title', [''])[0]
    else:
        return None
    title = urllib.parse.unquote(title).replace('_', ' ').strip()
    return title or None


def _key(title):
    return VERIFIED_KEY.format(hashlib.sha1(title.encode()).hexdigest()[:20])


def _trusted():
    from django.conf import settings
    from poller.wiki_rules import EDITORS
    return {e.strip().lower().replace('_', ' ') for e in (getattr(settings, 'MOOD_WIKI_RULES_EDITORS', None) or EDITORS)}


def _ask(titles):
    from .verify import _ask as ask
    return ask({'action': 'query', 'prop': 'revisions|categories', 'titles': '|'.join(titles), 'redirects': '1',
                'rvprop': 'user', 'clcategories': UNVERIFIED_CATEGORY, 'cllimit': 'max'})


def verified(titles, ask=_ask):
    """{title: True|False} for each page: verified as the module says. One question of PickiPedia for any not kept;
    a page it couldn't be asked about is left out (its item stands as written)."""
    found, wanted = {}, []
    for title in dict.fromkeys(titles):
        kept = cache.get(_key(title))
        if kept is None:
            wanted.append(title)
        else:
            found[title] = kept
    trusted = _trusted()
    for start in range(0, len(wanted), 50):
        chunk = wanted[start:start + 50]
        try:
            query = ask(chunk).get('query') or {}
        except Exception:  # noqa: BLE001 -- PickiPedia unreachable: asked again at the next look
            continue
        to = {}
        for step in (query.get('normalized') or []) + (query.get('redirects') or []):
            to[step['from']] = step['to']
        pages = {p.get('title'): p for p in query.get('pages') or []}
        for title in chunk:
            name = title
            for _ in range(3):
                name = to.get(name, name)
            page = pages.get(name)
            if page is None or page.get('missing'):
                continue
            newest = ((page.get('revisions') or [{}])[0].get('user') or '').lower().replace('_', ' ')
            ok = newest in trusted and not page.get('categories')
            cache.set(_key(title), ok, VERIFIED_FOR if ok else UNVERIFIED_FOR)
            found[title] = ok
    return found


def remember_linked(slug, items):
    """Which pages this Mood's open verify items link, so a page's change forgets only the lists it concerns."""
    linked = cache.get(LINKED_KEY) or {}
    for title in [t for t, slugs in linked.items() if slug in slugs]:
        linked[title] = [s for s in linked[title] if s != slug]
        if not linked[title]:
            del linked[title]
    for item in items:
        title = wiki_title(item.get('link')) if item.get('type') == 'verify' and not item.get('done') else None
        if title:
            linked.setdefault(title, [])
            if slug not in linked[title]:
                linked[title].append(slug)
    cache.set(LINKED_KEY, linked, None)


def page_changed(title):
    """A PickiPedia page changed (wiki_hooks.run): what's kept of whether it's verified goes, and so does every
    list with an open verify item linking it, so those items tick at their next look. The Moods it concerns."""
    from conversations.models import Mood
    from . import todo
    title = (title or '').replace('_', ' ').strip()
    if not title:
        return []
    cache.delete(_key(title))
    slugs = (cache.get(LINKED_KEY) or {}).get(title) or []
    for slug in slugs:
        mood = Mood.objects.filter(slug=slug).first()
        if mood is not None:
            todo.forget(mood)
    return slugs


# --- closing: the types that close themselves, beyond merges -------------------------------------------

def with_closes(slug, items):
    """The items typed; each open deploy and verify item marked done ('deployed' or 'verified': True) once it's
    finished. Merges are services/todo.py's with_merges, before this."""
    items = with_types(items)
    note_seen(slug, items)
    remember_linked(slug, items)
    titles = [wiki_title(i.get('link')) for i in items if i['type'] == 'verify' and not i.get('done')]
    states = {}
    try:
        states = verified([t for t in titles if t])
    except Exception:  # noqa: BLE001 -- the items stand as written
        pass
    runs = None
    out = []
    for item in items:
        if not item.get('done') and item['type'] == 'verify':
            title = wiki_title(item.get('link'))
            if title and states.get(title):
                item = {**item, 'done': True, 'verified': True}
        elif not item.get('done') and item['type'] == 'deploy':
            try:
                if runs is None:
                    from .ramp import running
                    runs = running()
                if deployed(slug, item, runs=runs):
                    item = {**item, 'done': True, 'deployed': True}
            except Exception:  # noqa: BLE001 -- githouse or the record unreadable: the item stands as written
                pass
        out.append(item)
    return out
