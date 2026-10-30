#!/usr/bin/env python3
"""Moderate Moods with your SSH key: kick someone out, or stop everything.

    python3 mood_admin.py kick skyler --all              # sign out every device of theirs
    python3 mood_admin.py kick skyler --device 1a2b3c4d  # ...or just one (its id, from the devices dialog)
    python3 mood_admin.py kick skyler --all --ban        # every device, and bar their key from signing in
    python3 mood_admin.py unban skyler
    python3 mood_admin.py az5                  # the scram: everyone out, Moods locked, runners still
    python3 mood_admin.py lift                 # the scram off
    python3 mood_admin.py mention-only         # turn all emergents to mention-only in every Mood
    python3 mood_admin.py mention-only --lift  # turn all emergents back to listening: on
    python3 mood_admin.py banner "message" <bg> <fg>  # emergency banner across every page (no DB)
    python3 mood_admin.py banner --clear       # clear the banner

Your key must be an admin's (memory-lane's MOOD_ADMINS). The signature
covers the action, its name and any device, so it can't be replayed as
anything else (conversations/views_admin.py). A kick says which: --all, or
--device; a ban bars the key, so it's always --all. Standard library only, like mood_login.py.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = 'https://magenta.cryptograss.live'
NAMESPACE = 'magenta-moods'  # fixed here: a server must not choose what your key signs for


def signed_message(challenge, base, purpose):
    """mood_auth.signed_message: the challenge, the server, and what this signature is for."""
    parts = urllib.parse.urlsplit(base)
    origin = f'{parts.scheme}://{parts.netloc}'.lower().rstrip('/')
    return f'{NAMESPACE} {purpose}\n{origin}\n{challenge}'


def call(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json',
                                                              'User-Agent': 'mood-admin'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get('error', '')
        except ValueError:
            detail = ''
        sys.exit(f'memory-lane said {e.code}: {detail}')


def default_key():
    for name in ('id_ed25519', 'id_ecdsa', 'id_rsa'):
        path = os.path.expanduser(f'~/.ssh/{name}')
        if os.path.exists(path):
            return path
    return None


def sign(text, key):
    with tempfile.TemporaryDirectory() as tmp:
        message = os.path.join(tmp, 'challenge')
        with open(message, 'w') as f:
            f.write(text)
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', key, '-n', NAMESPACE, message], check=True,
                       stdout=subprocess.DEVNULL)
        with open(message + '.sig') as f:
            return f.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('action', choices=['kick', 'unban', 'az5', 'lift', 'mention-only', 'banner'])
    parser.add_argument('name', nargs='?', help='Whose devices (kick, unban), or message for banner')
    parser.add_argument('extra', nargs='*', help='Extra arguments (e.g. bg and fg for banner)')
    parser.add_argument('--all', action='store_true', help='With kick: every device of theirs')
    parser.add_argument('--device', help="With kick: just this device -- the start of its id, from the devices dialog")
    parser.add_argument('--ban', action='store_true', help='With kick --all: bar their key from signing in again')
    parser.add_argument('--on', action='store_true', help='With mention-only: turn listening back on')
    parser.add_argument('--lift', action='store_true', help='With mention-only: turn listening back on')
    parser.add_argument('--clear', action='store_true', help='With banner: clear the banner')
    parser.add_argument('--key', default=default_key(), help='Your SSH private key (default: ~/.ssh/id_ed25519, …)')
    parser.add_argument('--base', default=os.environ.get('MEMORY_LANE_URL', DEFAULT_BASE))
    args = parser.parse_args()
    if args.action in ('kick', 'unban') and not args.name:
        parser.error(f'{args.action} needs a name')
    if args.action in ('az5', 'lift') and args.name:
        parser.error(f'{args.action} takes no name')
    if args.action == 'kick' and args.all == bool(args.device):
        parser.error('kick: say which -- --all (every device of theirs) or --device <id> (one, from the devices dialog)')
    if args.ban and not (args.action == 'kick' and args.all):
        parser.error("--ban bars their key, so it's for every device: kick <name> --all --ban")
    if args.action != 'kick' and (args.all or args.device):
        parser.error(f'{args.action} takes no --all or --device')
    if not args.key:
        sys.exit('No SSH key found; pass --key.')
    if not shutil.which('ssh-keygen'):
        sys.exit('ssh-keygen not found; install OpenSSH.')

    action = 'ban' if args.action == 'kick' and args.ban else args.action
    device = (args.device or '').lower().replace('-', '')
    if args.action == 'kick' and device:
        action = 'kick-device'
    target = None if action == 'banner' else (args.name or '').lower()
    base = args.base.rstrip('/')

    message_arg = ''
    bg_arg = ''
    fg_arg = ''
    if action == 'banner':
        if args.clear or not args.name or args.name in ('--clear', 'clear', 'off', 'none', ''):
            message_arg = ''
            bg_arg = ''
            fg_arg = ''
        else:
            message_arg = args.name
            bg_arg = args.extra[0] if len(args.extra) > 0 else '#b71c1c'
            fg_arg = args.extra[1] if len(args.extra) > 1 else '#ffffff'
        purpose = f'admin banner {message_arg} {bg_arg} {fg_arg}'.strip()
    elif action == 'mention-only':
        target = 'lift' if args.lift or args.on or args.name in ('on', 'lift') else 'mentions'
        purpose = f'admin mention-only {target}'.strip()
    else:
        purpose = f'admin {action} {target} {device}'.strip()

    challenge = call(f'{base}/api/auth/challenge/')['challenge']
    try:
        signature = sign(signed_message(challenge, base, purpose), args.key)
    except subprocess.CalledProcessError:
        sys.exit('ssh-keygen could not sign with that key.')

    payload = {'challenge': challenge, 'signature': signature,
               'action': action, 'target': target or None, 'device': device or None}
    if action == 'banner':
        payload['message'] = message_arg
        payload['bg'] = bg_arg
        payload['fg'] = fg_arg

    result = call(f'{base}/api/auth/admin/', payload)

    if action == 'kick-device':
        print(f"{target}: signed out of {result.get('device', 'that device')}.")
    elif action in ('kick', 'ban'):
        print(f"{target}: {result.get('devices_signed_out', 0)} device(s) signed out, "
              f"{result.get('links_spent', 0)} login link(s) spent" + ('; barred from signing in' if action == 'ban' else '') + '.')
    elif action == 'unban':
        print(f'{target} may sign in again.')
    elif action == 'az5':
        print(f"AZ5: {result.get('devices_signed_out', 0)} device(s) signed out, {result.get('links_spent', 0)} "
              f"login link(s) spent. Moods are locked and every runner is still until `lift`.")
    elif action == 'lift':
        print('Lifted: Moods are open again; people sign in as usual.')
    elif action == 'mention-only':
        mode = result.get('mode', 'mentions')
        emergents = result.get('emergents', [])
        if mode == 'mentions':
            print(f"All emergents ({', '.join(emergents)}) set to mention-only in every Mood.")
        else:
            print(f"Emergents ({', '.join(emergents)}) turned back to listening in every Mood.")
    elif action == 'banner':
        b = result.get('banner')
        if b and b.get('message'):
            print(f"Banner set across every page: \"{b['message']}\" (bg: {b['bg']}, fg: {b['fg']})")
        else:
            print("Banner cleared across every page.")


if __name__ == '__main__':
    main()
