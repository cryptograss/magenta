"""Open tabs, as the Moods page makes them: a read-only load test for memory-lane.

Each virtual tab does what moods.html does with nobody touching it:
  every 4 s   /api/moods/<slug>/turns/?after=<last id>   (the Mood it's in)
  every 5 s   /api/moods/live/; a Mood whose `last` moved is fetched ?after= (preload)
  every 60 s  /api/servers/, the Mood's /todo/ and /humans/
  now and then, another Mood opened cold (/turns/ whole), as a person moving about

Tabs ramp in steps (--steps 10,25,50); each step runs --hold seconds. Every request's
time and its Server-Timing are kept; each step prints p50/p95/p99 per kind, and the
laps that grew. A step whose p95 passes --stop-p95 seconds, or whose errors pass 5%,
ends the run: production is shared, and the point is the knee, not the cliff.

Anonymous, so it reads only what a signed-out page reads; nothing is written.

    python3 loadtest/tabs.py --base https://memory-lane.maybelle.cryptograss.live --steps 10,25,50,100 --hold 90
"""

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import time
from collections import defaultdict

import aiohttp

LAP = re.compile(r'([\w-]+);(?:desc="[^"]*";)?dur=([\d.]+)')


class Record:
    def __init__(self):
        self.rows = []  # (t, kind, seconds, status, laps)

    def add(self, kind, seconds, status, timing):
        laps = {n: float(d) for n, d in LAP.findall(timing or '')}
        self.rows.append((time.monotonic(), kind, seconds, status, laps))

    def since(self, t0):
        return [r for r in self.rows if r[0] >= t0]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


async def get(session, rec, base, path, kind):
    start = time.monotonic()
    try:
        async with session.get(base + path, timeout=aiohttp.ClientTimeout(total=20)) as r:
            body = await r.read()
            rec.add(kind, time.monotonic() - start, r.status, r.headers.get('Server-Timing'))
            if r.status == 200 and r.content_type == 'application/json':
                return json.loads(body)
    except Exception as e:
        rec.add(kind, time.monotonic() - start, type(e).__name__, None)
    return None


def last_of(data):
    turns = (data or {}).get('turns') or []
    return turns[-1]['id'] if turns else None


async def tab(session, rec, base, slugs, last_ids, stop, n):
    await asyncio.sleep(random.uniform(0, 5))  # tabs aren't opened in step
    slug = random.choice(slugs[:8]) if random.random() < 0.8 else random.choice(slugs)
    seen_last = {}
    t = time.monotonic()
    next_turns, next_live, next_minute = t, t + random.uniform(0, 5), t + random.uniform(0, 60)
    next_move = t + random.uniform(60, 300)
    while not stop.is_set():
        now = time.monotonic()
        if now >= next_move:
            slug = random.choice(slugs)
            data = await get(session, rec, base, f'/api/moods/{slug}/turns/', 'open')
            last_ids[slug] = last_of(data) or last_ids.get(slug)
            next_move = now + random.uniform(120, 400)
        if now >= next_turns:
            after = last_ids.get(slug) or ''
            data = await get(session, rec, base, f'/api/moods/{slug}/turns/?after={after}', 'poll')
            last_ids[slug] = last_of(data) or last_ids.get(slug)
            next_turns = now + 4
        if now >= next_live:
            live = await get(session, rec, base, '/api/moods/live/', 'live')
            for s, m in ((live or {}).get('moods') or {}).items():
                if s != slug and s in seen_last and seen_last[s] != m.get('last') and s in last_ids:
                    data = await get(session, rec, base, f'/api/moods/{s}/turns/?after={last_ids[s] or ""}', 'preload')
                    last_ids[s] = last_of(data) or last_ids[s]
                seen_last[s] = m.get('last')
            next_live = now + 5
        if now >= next_minute:
            await asyncio.gather(get(session, rec, base, '/api/servers/', 'servers'),
                                 get(session, rec, base, f'/api/moods/{slug}/todo/', 'todo'),
                                 get(session, rec, base, f'/api/moods/{slug}/humans/', 'humans'))
            next_minute = now + 60
        await asyncio.sleep(max(0.05, min(next_turns, next_live, next_minute, next_move) - time.monotonic()))


def report(rows, tabs, held):
    kinds = defaultdict(list)
    errors = 0
    for _, kind, s, status, _laps in rows:
        kinds[kind].append(s)
        errors += status != 200
    allt = [r[2] for r in rows]
    print(f'\n== {tabs} tabs: {len(rows)} requests in {held:.0f}s ({len(rows) / held:.1f}/s), '
          f'errors {errors} ({100 * errors / max(1, len(rows)):.1f}%), '
          f'p50 {pct(allt, .5) * 1000:.0f} ms, p95 {pct(allt, .95) * 1000:.0f} ms, p99 {pct(allt, .99) * 1000:.0f} ms')
    for kind, xs in sorted(kinds.items()):
        print(f'   {kind:8} n={len(xs):5}  p50 {pct(xs, .5) * 1000:6.0f}  p95 {pct(xs, .95) * 1000:6.0f}  '
              f'max {max(xs) * 1000:6.0f} ms')
    # Where the slow ones spent their time: server total vs. what the wire added (queueing at gunicorn shows here).
    slow = sorted(rows, key=lambda r: r[2])[-max(1, len(rows) // 20):]
    server = [r[4].get('total', 0) / 1000 for r in slow if 'total' in r[4]]
    if server:
        wire = statistics.mean(r[2] for r in slow if 'total' in r[4]) - statistics.mean(server)
        laps = defaultdict(list)
        for r in slow:
            for n, d in r[4].items():
                if n != 'total':
                    laps[n].append(d)
        top = sorted(((statistics.mean(v), n) for n, v in laps.items()), reverse=True)[:4]
        print(f'   slowest 5%: server {statistics.mean(server) * 1000:.0f} ms, outside the server {wire * 1000:.0f} ms; '
              f'laps ' + ', '.join(f'{n} {d:.0f}' for d, n in top))
    return errors / max(1, len(rows)), pct(allt, .95)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--steps', default='10,25,50')
    ap.add_argument('--hold', type=float, default=90)
    ap.add_argument('--stop-p95', type=float, default=3.0)
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    rec = Record()
    async with aiohttp.ClientSession(headers={'User-Agent': 'memory-lane loadtest (tabs.py)'},
                                     connector=aiohttp.TCPConnector(limit=0)) as session:
        slugs = [m['slug'] for m in (await get(session, rec, a.base, '/api/moods/', 'moods'))['moods']]
        last_ids = {}
        gate = asyncio.Semaphore(4)

        async def newest(slug):  # each Mood's newest, once, as the tabs' kept copies would have it
            async with gate:
                last_ids[slug] = last_of(await get(session, rec, a.base, f'/api/moods/{slug}/turns/?limit=30', 'setup'))
        await asyncio.gather(*map(newest, slugs))
        print(f'{len(slugs)} Moods; setup took {sum(r[2] for r in rec.rows):.1f}s of requests', flush=True)
        stop, tasks, n = asyncio.Event(), [], 0
        for tabs in (int(x) for x in a.steps.split(',')):
            while n < tabs:
                tasks.append(asyncio.create_task(tab(session, rec, a.base, slugs, last_ids, stop, n)))
                n += 1
            t0 = time.monotonic()
            await asyncio.sleep(a.hold)
            err, p95 = report(rec.since(t0 + 10), tabs, a.hold - 10)  # the first 10 s are tabs opening
            sys.stdout.flush()
            if err > 0.05 or p95 > a.stop_p95:
                print(f'\nstopped at {tabs} tabs: p95 {p95:.2f}s, errors {100 * err:.1f}%')
                break
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    if a.out:
        with open(a.out, 'w') as f:
            json.dump([(t, k, s, st if isinstance(st, int) else str(st), laps) for t, k, s, st, laps in rec.rows], f)


if __name__ == '__main__':
    asyncio.run(main())
