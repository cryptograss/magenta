"""githouse's copy of our repositories, read here (Cryptograss:Moods/ramp/Plan, sections 3 and 4).

maybelle holds the canonical copy of each repository (maybelle-config#250), mounted read-only into this
container at settings.GITHOUSE_DIR as one bare repository per repo: GitHub's branches (main, master,
production, release/*), every pull request's head as refs/pull/<n>/head, and the name registry -- a pushed
branch whose merge landed moves to refs/merged/<branch>, one its owner deleted to refs/closed/<branch>.
In a Mood container the same path is hunter's mirror of it, a minute behind.

Merged is read from git, not asked of GitHub (Justin, 31 Oct: "an unambiguous confirmation of the merge
of an exact commit"; the first watch, 6 Nov): a pull request is merged when its head is an ancestor of one
of GitHub's branches, a mergeable (<repo>:<branch>) when its name has moved to refs/merged/. No API call,
no token, no webhook, and no starving when GitHub's unsigned hour runs out (31 Oct). What githouse can't
say -- a repository it doesn't hold, a pull request it hasn't fetched yet -- is None, and the caller may
still ask GitHub. Merge commits only, on every repository here: a squash or rebase leaves no trace of the
pushed tip, so githouse would call it unmerged (settled 31 Oct; the settings flipped 6 Nov).
"""

import logging
import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

OWNED = ('main', 'master', 'production', 'release/*')  # GitHub's branches, as githouse names them
MERGED_FOR = 86400   # seconds a merged answer is kept: merged is merged
UNMERGED_FOR = 60    # seconds a not-yet answer is kept: githouse fetches every minute
_NAME = re.compile(r'[A-Za-z0-9._-]+')
_BRANCH = re.compile(r'[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*')


def root():
    return Path(getattr(settings, 'GITHOUSE_DIR', '') or '/srv/git')


def repository(name):
    """The bare repository githouse keeps for this repo (its short name, 'magenta'), or None if it holds none."""
    if not name or not _NAME.fullmatch(name):
        return None
    repo = root() / f'{name}.git'
    return repo if (repo / 'HEAD').exists() else None


def repositories():
    """The short names of every repository githouse holds, sorted; [] with no mount."""
    try:
        return sorted(p.name[:-4] for p in root().iterdir() if p.name.endswith('.git') and (p / 'HEAD').exists())
    except OSError:
        return []


def _git(repo, *args):
    """git's stdout in this bare repository, '' if it failed. The mount is another user's (githouse runs as its
    own), which git refuses without safe.directory; read-only, so there's nothing to protect here."""
    try:
        done = subprocess.run(['git', '-c', 'safe.directory=*', '--git-dir', str(repo), *args],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:  # git missing, or hung on a dying mount
        log.warning('githouse: git %s in %s: %s', args[0], repo.name, e)
        return ''
    return done.stdout if done.returncode == 0 else ''


def _sha(repo, ref):
    return _git(repo, 'rev-parse', '-q', '--verify', f'{ref}^{{commit}}').strip() or None


def owned_branches(repo):
    """{branch: sha} of GitHub's branches here -- the ones a merge lands in."""
    out = _git(repo, 'for-each-ref', '--format=%(refname:short) %(objectname)', *(f'refs/heads/{b}' for b in OWNED))
    found = {}
    for line in out.splitlines():
        name, _, sha = line.partition(' ')
        if sha:
            found[name] = sha
    return found


def _is_ancestor(repo, commit, of):
    try:
        done = subprocess.run(['git', '-c', 'safe.directory=*', '--git-dir', str(repo), 'merge-base', '--is-ancestor',
                               commit, of], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):  # git missing, or hung on a dying mount: can't say here
        return False
    return done.returncode == 0


def commit_merged(name, commit):
    """Whether this commit is in one of GitHub's branches of the repo: True, False, or None if githouse doesn't
    hold the repository or the commit. The watch of the plan's section 4, in one question."""
    repo = repository(name)
    if repo is None:
        return None
    sha = _sha(repo, commit)
    if sha is None:
        return None
    return any(_is_ancestor(repo, sha, tip) for tip in owned_branches(repo).values())


def pull_merged(name, number):
    """Whether pull request <number> of the repo is merged, by its head's ancestry; None if githouse can't say
    (no such repository here, or no refs/pull/<number>/head fetched yet)."""
    key = f'githouse:pull:{name}#{number}'
    found = cache.get(key)
    if found is None:
        found = commit_merged(name, f'refs/pull/{int(number)}/head')
        if found is not None:
            cache.set(key, found, MERGED_FOR if found else UNMERGED_FOR)
    return found


def branch_merged(name, branch):
    """Whether a mergeable, <repo>:<branch>, has merged: its name moved to refs/merged/. False while it's live
    or closed, or never pushed here; None if githouse doesn't hold the repository."""
    repo = repository(name)
    if repo is None or not _BRANCH.fullmatch(branch or ''):
        return None
    key = f'githouse:branch:{name}:{branch}'
    found = cache.get(key)
    if found is None:
        found = _sha(repo, f'refs/merged/{branch}') is not None
        cache.set(key, found, MERGED_FOR if found else UNMERGED_FOR)
    return found


def branch_closed(name, branch):
    """Whether a mergeable, <repo>:<branch>, was closed without merging: its owner deleted it, and its name moved
    to refs/closed/ (Justin, 10 Nov: "push the merged / wontfixed status from githouse to the moods"). False while
    it's live or merged; None if githouse doesn't hold the repository. Kept as briefly as a not-yet: a closed name
    can be pushed again."""
    repo = repository(name)
    if repo is None or not _BRANCH.fullmatch(branch or ''):
        return None
    key = f'githouse:closed:{name}:{branch}'
    found = cache.get(key)
    if found is None:
        found = _sha(repo, f'refs/closed/{branch}') is not None
        cache.set(key, found, UNMERGED_FOR)
    return found
