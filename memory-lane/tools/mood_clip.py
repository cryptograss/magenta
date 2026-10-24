#!/usr/bin/env python3
"""Make a clip from a file on your computer, and save it under a name, as /clips save does for a Yarn link.

    magenta.sh clips save <file> <start> <end> "<subtitle>"
    magenta.sh clips save talk.mp4 1:02.5 1:06 "That's the whole point."
    magenta.sh clips save talk.mp4 62.5 66 "That's the whole point." --name whole-point --mood jams
    magenta.sh clips save talk.mp4 62.5 66 "That's the whole point." --out /tmp/clip.mp4   # cut only, to look first

Times are seconds, or m:ss, or h:mm:ss, with decimals if you like. The clip is cut here, by ffmpeg, at
up to 480 lines, with the subtitle burned in along the bottom as Yarn's are; a still from its middle
is its card's picture. Then it's signed with your SSH key (who you are, as `magenta.sh login`) and
sent to memory-lane, which keeps it and posts "/clips save <name> <its link>" as you, in #general
unless --mood says otherwise. From then on /clips <name> plays it, for people and agents alike.

Its name, unless --name gives one: the subtitle's first words ("thats-the-whole-point").

Needs ffmpeg and OpenSSH; otherwise only Python's standard library.
"""

import argparse
import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mood_login import DEFAULT_BASE, call, default_key, sign, signed_message  # noqa: E402

MAX_SECONDS = 60
MAX_BYTES = 12 * 1024 * 1024    # what memory-lane takes (views_auth.CLIP_MAX_BYTES)
NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,34}$')
RESERVED = {'save', 'forget', 'list'}


def seconds(text):
    """'62.5', '1:02.5' or '0:01:02.5' as seconds."""
    parts = text.strip().split(':')
    if not 1 <= len(parts) <= 3:
        raise ValueError(text)
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    if total < 0:
        raise ValueError(text)
    return total


def name_from(caption, path):
    """A clip's name from its subtitle's first words, else from the file's."""
    for source in (caption, os.path.splitext(os.path.basename(path))[0]):
        words = re.findall(r'[a-z0-9]+', source.lower().replace("'", ''))
        name = '-'.join(words[:5])[:35].strip('-')
        if name and NAME.match(name) and name not in RESERVED:
            return name
    return 'clip'


def stamp(t):
    """Seconds as SubRip's 00:00:00,000."""
    ms = int(round(t * 1000))
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'


def has_filter(name):
    found = subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True, text=True)
    return re.search(rf'^\s*\S+\s+{name}\s', found.stdout, re.M) is not None


def cut(path, start, length, caption, out, tmp, height=480, crf=27):
    """The clip, cut and encoded to `out`: H.264 and AAC in an MP4 that starts playing at once. The subtitle burned
    in with libass if ffmpeg has it, else drawn with drawtext, else (said so) left off -- it's still the clip's caption."""
    scale = f"scale=-2:'2*trunc(min({height},ih)/2)'"
    burns = []
    if caption and has_filter('subtitles'):
        srt = os.path.join(tmp, 'caption.srt')
        with open(srt, 'w', encoding='utf-8') as f:
            f.write(f'1\n{stamp(0)} --> {stamp(length + 1)}\n{caption}\n')
        burns.append(f"subtitles={srt}:force_style='FontSize=22,Outline=2,Shadow=0,MarginV=16'")
    if caption and has_filter('drawtext'):
        text = os.path.join(tmp, 'caption.txt')
        with open(text, 'w', encoding='utf-8') as f:
            f.write(caption)
        burns.append(f'drawtext=textfile={text}:fontcolor=white:fontsize=h/16:borderw=2:bordercolor=black'
                     ':x=(w-text_w)/2:y=h-text_h-h/18')
    burns.append(None)
    for burn in burns:
        vf = scale + (f',{burn}' if burn else '')
        done = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{start:.3f}', '-i', path,
                               '-t', f'{length:.3f}', '-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', str(crf),
                               '-pix_fmt', 'yuv420p', '-profile:v', 'main', '-c:a', 'aac', '-b:a', '96k', '-ac', '2',
                               '-movflags', '+faststart', out], capture_output=True, text=True)
        if done.returncode == 0:
            if caption and burn is None:
                print('(ffmpeg here can draw no text: the subtitle is kept as the caption, not on the picture)')
            return
        last = done.stderr.strip().splitlines()[-1:] or ['']
    sys.exit(f'ffmpeg could not cut it: {last[0]}')


def still_of(video, at, out):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{at:.3f}', '-i', video,
                    '-frames:v', '1', '-q:v', '4', out], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('action', choices=('save',), help='save: cut a clip from a local file and save it under a name')
    parser.add_argument('file')
    parser.add_argument('start', help='seconds, m:ss or h:mm:ss')
    parser.add_argument('end', help='seconds, m:ss or h:mm:ss')
    parser.add_argument('subtitle', nargs='+', help='the words, burned in along the bottom; quote them')
    parser.add_argument('--name', help="what /clips calls it (default: the subtitle's first words)")
    parser.add_argument('--mood', default='general', help='the Mood the save is posted in (default: general)')
    parser.add_argument('--out', help='only cut it, to this file, to look at first: nothing is sent')
    parser.add_argument('--key', default=default_key(), help='SSH private key (default: ~/.ssh/id_ed25519, …)')
    parser.add_argument('--base', default=os.environ.get('MEMORY_LANE_URL', DEFAULT_BASE))
    args = parser.parse_args()

    for tool in ('ffmpeg', 'ssh-keygen'):
        if not shutil.which(tool):
            sys.exit(f'{tool} not found; install {"ffmpeg" if tool == "ffmpeg" else "OpenSSH"}.')
    if not os.path.isfile(args.file):
        sys.exit(f'No such file: {args.file}')
    try:
        start, end = seconds(args.start), seconds(args.end)
    except ValueError:
        sys.exit('start and end: seconds, m:ss or h:mm:ss (1:02.5)')
    if end <= start:
        sys.exit('end must come after start')
    if end - start > MAX_SECONDS:
        sys.exit(f'a clip of up to {MAX_SECONDS} seconds')
    caption = ' '.join(' '.join(args.subtitle).split())
    name = (args.name or name_from(caption, args.file)).lower()
    if not NAME.match(name) or name in RESERVED:
        sys.exit('--name: letters, digits, - and _, up to 35, and not save, forget or list')

    with tempfile.TemporaryDirectory() as tmp:
        video = args.out or os.path.join(tmp, 'clip.mp4')
        cut(args.file, start, end - start, caption, video, tmp)
        if os.path.getsize(video) > MAX_BYTES:  # a big picture: smaller, once
            cut(args.file, start, end - start, caption, video, tmp, height=360, crf=32)
        size = os.path.getsize(video)
        if args.out:
            print(f'Cut {end - start:.1f}s to {video} ({size / 1024:.0f} KB); would be saved as "{name}". Nothing sent.')
            return
        if size > MAX_BYTES:
            sys.exit(f'Still {size / 1024 / 1024:.1f} MB: up to {MAX_BYTES // 1024 // 1024} MB; cut it shorter.')
        still = os.path.join(tmp, 'still.jpg')
        try:
            still_of(video, (end - start) / 2, still)
            with open(still, 'rb') as f:
                picture = f.read()
        except (subprocess.CalledProcessError, OSError):
            picture = None
        with open(video, 'rb') as f:
            data = f.read()

    if not args.key:
        sys.exit('No SSH key found; pass --key.')
    base = args.base.rstrip('/')
    challenge = call(f'{base}/api/auth/challenge/')['challenge']
    sha = hashlib.sha256(data).hexdigest()
    try:
        signature = sign(signed_message(challenge, base, purpose=f'clip {name}') + f'\n{sha}\n{caption}', args.key)
    except subprocess.CalledProcessError:
        sys.exit('ssh-keygen could not sign with that key.')
    result = call(f'{base}/api/clips/make/', {
        'challenge': challenge, 'signature': signature, 'name': name, 'caption': caption, 'mood': args.mood,
        'video': base64.b64encode(data).decode(), **({'still': base64.b64encode(picture).decode()} if picture else {})})
    print(f'\nSaved "{result["name"]}" ({len(data) / 1024:.0f} KB) in #{result["mood"]}: /clips {result["name"]} plays it.'
          f'\n{result["url"]}\n')


if __name__ == '__main__':
    main()
