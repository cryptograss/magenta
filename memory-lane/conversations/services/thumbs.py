"""The 👍 on the ramp (Cryptograss:Moods/ramp/Plan, section 8): "I like this", from any person's signed-in
device. One press, kept with who, when and which commit; pressed again, taken back. It is a signal only:
githouse never requires one, because a 👍 is only memory-lane's say-so. Merging is the merge button's job.

A 👍 is on a commit, not a name: once the branch moves, the ramp shows it as on an older commit, so nobody
reads an old 👍 as a nod to code they never saw.
"""


class Invalid(ValueError):
    pass


def head_of(repo, branch, number=None):
    """The commit githouse has at the head of this work: the branch, else its pull request's head. Invalid if
    githouse holds no such repository or work."""
    from .githouse import _sha, repository
    from .merge_orders import _BRANCH
    held = repository(repo)
    if held is None:
        raise Invalid(f'githouse holds no repository {repo}')
    if not _BRANCH.fullmatch(branch or ''):
        raise Invalid('which branch?')
    commit = _sha(held, f'refs/heads/{branch}')
    if not commit and number:  # a branch pushed only to GitHub: its pull request's head, if the card says it's this one
        from . import ramp
        card = next((i for i in ramp.board(stale_ok=True).get('open', []) if i.get('repo') == repo and i.get('number') == int(number)),
                    None)
        if card is not None and card.get('branch') == branch:
            commit = _sha(held, f'refs/pull/{int(number)}/head')
    if not commit:
        raise Invalid(f'githouse has no {repo}:{branch}')
    return commit


def toggle(person, repo, branch, commit):
    """Give this commit a 👍 from person, or take theirs back. True if it's on now."""
    from conversations.models import Thumb
    mine = Thumb.objects.filter(repo=repo, branch=branch, commit=commit, by=person)
    if mine.exists():
        mine.delete()
        return False
    Thumb.objects.create(repo=repo, branch=branch, commit=commit, by=person)
    return True


def _newest(rows, head):
    """Each person's newest 👍 of these rows (oldest first): [{'by', 'at', 'commit', 'current'}], 'current' if
    on head. One who liked two commits shows once, on the later."""
    newest = {}
    for t in sorted(rows, key=lambda t: t.at):
        newest[t.by] = {'by': t.by, 'at': t.at, 'commit': t.commit, 'current': t.commit == head}
    return sorted(newest.values(), key=lambda t: t['at'])


def of(repo, branch, head):
    """The 👍s on this work (_newest)."""
    from conversations.models import Thumb
    return _newest(Thumb.objects.filter(repo=repo, branch=branch), head)


def with_thumbs(items):
    """The items, each with 'thumbs' (of above) and 'liked': the names whose 👍 is on its head now."""
    from conversations.models import Thumb
    keys = {(i.get('repo'), i.get('branch')) for i in items if i.get('branch')}
    found = {}
    if keys:
        for t in Thumb.objects.filter(repo__in={r for r, _ in keys}, branch__in={b for _, b in keys}):
            found.setdefault((t.repo, t.branch), []).append(t)
    for item in items:
        item['thumbs'] = _newest(found.get((item.get('repo'), item.get('branch')), []), item.get('head'))
        item['liked'] = [t['by'] for t in item['thumbs'] if t['current']]
    return items
