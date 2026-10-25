#!/usr/bin/env python3
"""Every emoji Unicode has, for the Moods page's picker and :name completion (Justin, 25 Oct: "the full
unicode gamut"), from the Unicode Consortium's own list:

    curl -sfLO https://unicode.org/Public/emoji/latest/emoji-test.txt
    curl -sfLO https://raw.githubusercontent.com/unicode-org/cldr/main/common/annotations/en.xml
    python3 tools/emoji_data.py emoji-test.txt en.xml > conversations/data/emoji.json

Fully qualified emoji only, in Unicode's order and groups. One with skin tones is listed once, untoned,
with its five single-tone forms beside it (light to dark); the mixed-tone pairs are left out. Each has the
words CLDR finds it by, as well as its name (👋: bye, hello, wave ...), so a search finds it as people put it.
Output: {"version", "groups": [[group, [[emoji, name, [five tones] or 0, "other words"], ...]], ...]}.
"""

import json
import re
import sys
import xml.etree.ElementTree as ET

TONES = '\U0001F3FB\U0001F3FC\U0001F3FD\U0001F3FE\U0001F3FF'
LINE = re.compile(r'^[0-9A-F ]+;\s*fully-qualified\s*#\s*(\S+)\s+E[\d.]+\s+(.+)$')


def keywords(path):
    """{emoji, without its variation selectors: 'its CLDR keywords'} from CLDR's annotations (en.xml)."""
    out = {}
    for a in ET.parse(path).getroot().iter('annotation'):
        if a.get('type') != 'tts' and a.text:
            out[a.get('cp', '').replace('\uFE0F', '')] = [w.strip().lower() for w in a.text.split('|') if w.strip()]
    return out


def main(path, annotations=None):
    words = keywords(annotations) if annotations else {}
    version, groups, by_base = '', [], {}
    for raw in open(path, encoding='utf-8'):
        line = raw.rstrip('\n')
        if line.startswith('# Version:'):
            version = line.split(':', 1)[1].strip()
        elif line.startswith('# group:'):
            groups.append([line.split(':', 1)[1].strip(), []])
        match = LINE.match(line)
        if not match or not groups:
            continue
        emoji, name = match.groups()
        toned = [c for c in emoji if c in TONES]
        if not toned:
            entry = [emoji, name]
            groups[-1][1].append(entry)
            by_base[emoji] = entry
            by_base[emoji.replace('️', '')] = entry
            continue
        if len(set(toned)) != 1 or len(toned) != 1:
            continue  # mixed tones, or a tone on each of two people: left out
        base = by_base.get(emoji.replace(toned[0], '')) or by_base.get(emoji.replace(toned[0], '').replace('️', ''))
        if base is not None:
            (base.append([]) if len(base) == 2 else None)
            base[2].append(emoji)
    def entry(e):
        tones = e[2] if len(e) == 3 and len(e[2]) == 5 else 0
        named = set(re.findall(r'[a-z0-9]+', e[1].lower()))
        more = [w for w in words.get(e[0].replace('\uFE0F', ''), []) if not set(re.findall(r'[a-z0-9]+', w)) <= named]
        return [e[0], e[1], tones, ' '.join(dict.fromkeys(more))]
    groups = [[g, [entry(e) for e in items]] for g, items in groups if g != 'Component']
    json.dump({'version': version, 'groups': groups}, sys.stdout, ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    main(*sys.argv[1:3])
