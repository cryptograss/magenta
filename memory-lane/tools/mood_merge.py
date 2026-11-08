#!/usr/bin/env python3
"""Merge a branch without a pull request, on an order signed with your SSH key.

    magenta.sh merge pickipedia:re/fix-foo               # into the branch pickipedia deploys from
    magenta.sh merge magenta:magent/thing --into main
    magenta.sh merge arthel:skyler/bar --mood ramp --key ~/.ssh/id_rsa

What it does (Cryptograss:Moods/ramp/Plan, section 8):

1. Fetches the branch and its target itself, from GitHub (until the flip), into a scratch repository,
   and shows you `git log` and the diffstat of what would merge, computed here from those commits.
   Nothing it shows you comes from a service: a signature covers only what the signer was shown, and a
   service that chose what you saw could show one diff and hand over another commit (re, 7 Nov).
2. Asks you. Then writes the order -- the repository, the branch, its head commit, the target, your
   name, a nonce and an expiry an hour out -- and signs it with `ssh-keygen -Y sign -n magenta-merge`.
3. Posts it to memory-lane, which only carries it. githouse, on maybelle, fetches it within seconds and
   checks everything itself: your signature against its own list of who may merge, the nonce, the
   expiry, that the branch hasn't moved, and the brakes (a ban, AZ5, root's freeze). Then it merges
   --no-ff, exactly that commit, and pushes to GitHub.
4. Waits for githouse's answer, and says it.

Your name comes from memory-lane, which knows whose key is whose (or --as). That's only a convenience:
a wrong name gets the order refused, because githouse checks the signature against its own signers.

Standard library only, like mood_login.py.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = 'https://magenta.cryptograss.live'
SOURCE = 'https://github.com/cryptograss/{}.git'   # where the commits are read from, until the flip
# Fixed here, never taken from a server: githouse checks merge orders in this namespace only.
NAMESPACE = 'magenta-merge'
HEADER = 'githouse merge order v1'
FIELDS = ('repo', 'branch', 'commit', 'into', 'by', 'nonce', 'expires')
LIFE = 3600            # seconds an order is good for (githouse allows two hours at most)
# Where each repository's work goes: the branch its deploys come from. Not guessed from which branches
# exist: magenta has a production branch, dead since Nov 2025, and deploys from main.
DEPLOYS_FROM = {'magenta': 'main', 'maybelle-config': 'production', 'pickipedia': 'production',
                'arthel': 'production', 'rabbithole': 'main', 'pickipedia-mcp': 'master'}
TARGETS = ('production', 'main', 'master')   # for a repository not listed: the first of these it has
WAIT = 120             # seconds to wait for githouse's answer


class Stop(Exception):
    """Nothing to sign, and why."""


def git(where, *args):
    done = subprocess.run(['git', '--git-dir', str(where), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise Stop(f"git {args[0]}: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def look(repo, branch, into, source, scratch):
    """Fetch the branch and its target into scratch and say what merging it would bring:
    {'commit', 'into', 'tip', 'log', 'stat'}, all computed here."""
    url = source.format(repo)
    into = into or DEPLOYS_FROM.get(repo)
    subprocess.run(['git', 'init', '--quiet', '--bare', str(scratch)], check=True)
    listed = git(scratch, 'ls-remote', url, f'refs/heads/{branch}', *(f'refs/heads/{t}' for t in TARGETS),
                 *([f'refs/heads/{into}'] if into else []))
    heads = {ref[len('refs/heads/'):]: sha for sha, ref in (line.split('\t', 1) for line in listed.splitlines())}
    if branch not in heads:
        raise Stop(f'{repo} has no branch {branch} at {url}')
    if into and into not in heads:
        raise Stop(f'{repo} has no branch {into} to merge into')
    into = into or next((t for t in TARGETS if t in heads), None)
    if not into:
        raise Stop(f"{repo} has none of {', '.join(TARGETS)}; say which with --into")
    git(scratch, 'fetch', '--quiet', '--no-tags', url,
        f'+refs/heads/{branch}:refs/look/branch', f'+refs/heads/{into}:refs/look/into')
    commit = git(scratch, 'rev-parse', 'refs/look/branch').strip()
    tip = git(scratch, 'rev-parse', 'refs/look/into').strip()
    if subprocess.run(['git', '--git-dir', str(scratch), 'merge-base', '--is-ancestor', commit, tip]).returncode == 0:
        raise Stop(f'{repo}:{branch} ({commit[:8]}) is in {into} already')
    log = git(scratch, 'log', '--no-decorate', '--format=%h %an: %s', f'{tip}..{commit}')
    stat = git(scratch, 'diff', '--stat', f'{tip}...{commit}')
    return {'commit': commit, 'into': into, 'tip': tip, 'log': log, 'stat': stat}


def order_text(repo, branch, commit, into, by, now=None):
    fields = {'repo': repo, 'branch': branch, 'commit': commit, 'into': into, 'by': by,
              'nonce': os.urandom(16).hex(), 'expires': str(int(time.time() if now is None else now) + LIFE)}
    return HEADER + '\n' + ''.join(f'{k}: {fields[k]}\n' for k in FIELDS)


def sign(text, key):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'order.txt')
        with open(path, 'w') as f:
            f.write(text)
        # ssh-keygen may ask for the key's passphrase on the terminal.
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-q', '-f', key, '-n', NAMESPACE, path], check=True,
                       stdout=subprocess.DEVNULL)
        with open(path + '.sig') as f:
            return f.read()


def call(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json',
                                                              'User-Agent': 'mood-merge'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get('error', '')
        except ValueError:
            detail = ''
        raise Stop(f'memory-lane said {e.code}: {detail}') from None
    except (urllib.error.URLError, OSError) as e:
        raise Stop(f"couldn't reach memory-lane: {e}") from None


def default_key():
    for name in ('id_ed25519', 'id_ecdsa', 'id_rsa'):
        path = os.path.expanduser(f'~/.ssh/{name}')
        if os.path.exists(path):
            return path
    return None


def main(argv=None, out=sys.stdout, ask=input, source=None, pause=time.sleep):
    parser = argparse.ArgumentParser(prog='magenta.sh merge', description=__doc__.split('\n\n')[0])
    parser.add_argument('work', help='<repo>:<branch>, as the ramp names it')
    parser.add_argument('--into', help="the branch it goes into (default: the one the repository deploys from)")
    parser.add_argument('--key', default=default_key(), help='your SSH private key (default: ~/.ssh/id_ed25519, ...)')
    parser.add_argument('--as', dest='name', help="your name, if memory-lane doesn't know your key")
    parser.add_argument('--mood', default='', help='the Mood to tell (later: memory-lane says it there)')
    parser.add_argument('--yes', action='store_true', help="don't ask; sign what's shown")
    parser.add_argument('--base', default=os.environ.get('MAGENTA_BASE', DEFAULT_BASE))
    args = parser.parse_args(argv)
    source = source or os.environ.get('MAGENTA_MERGE_SOURCE', SOURCE)
    base = args.base.rstrip('/')
    say = lambda line='': print(line, file=out, flush=True)

    repo, sep, branch = args.work.partition(':')
    if not sep or not repo or not branch:
        say('Name the work as <repo>:<branch>, as the ramp does: pickipedia:re/fix-foo')
        return 2
    if not args.key or not os.path.exists(args.key):
        say('No SSH key found; give one with --key')
        return 2
    scratch = tempfile.mkdtemp(prefix='mood-merge-')
    try:
        seen = look(repo, branch, args.into, source, os.path.join(scratch, 'look.git'))
        name = args.name
        if not name:
            with open(args.key + '.pub') as f:
                public = f.read().strip()
            name = call(f'{base}/api/merge-orders/whose/?' + urllib.parse.urlencode({'key': public}))['name']

        # What's shown is what git computed here, from the commits fetched here; nothing a service said.
        say(f"{repo}:{branch} at {seen['commit'][:12]}, into {seen['into']} at {seen['tip'][:12]}:")
        say()
        say(seen['log'].rstrip())
        say()
        say(seen['stat'].rstrip())
        say()
        if not args.yes and ask(f"Merge it, signed as {name}? [y/N] ").strip().lower() not in ('y', 'yes'):
            say('Nothing signed.')
            return 1

        text = order_text(repo, branch, seen['commit'], seen['into'], name)
        signature = sign(text, args.key)
        posted = call(f'{base}/api/merge-orders/', {'order': text, 'signature': signature, 'mood': args.mood})
        say(f"Signed and sent. Waiting for githouse (order {posted['id'][:8]})...")
        status = posted
        waited = 0
        while status.get('status') == 'waiting' and waited < WAIT:
            pause(3)
            waited += 3
            status = call(f"{base}/api/merge-orders/{posted['id']}/")
        if status.get('status') == 'merged':
            say(f"Merged: {repo}:{branch} into {seen['into']} as {str(status.get('merge', ''))[:12]}.")
            return 0
        if status.get('status') == 'waiting':
            say(f"githouse hasn't answered in {WAIT} s. The order is good for an hour; "
                f"{base}/api/merge-orders/{posted['id']}/ will say what came of it.")
            return 1
        say(f"{status.get('status', 'refused').capitalize()}: {status.get('why', '')}")
        return 1
    except Stop as e:
        say(str(e))
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
