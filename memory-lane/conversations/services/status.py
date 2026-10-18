"""The magenta status page (/moods/status/ and /api/status/): how the system stands now.

Six things on one page:
  1. Every agent's settings bar in every Mood (and everywhere), plus every vendor
     each agent's harness knows and whether it has a key.
  2. Our four servers (hunter, maybelle, delivery-kid, pickipedia): whether each
     answers, its OS uptime where reachable, its newest redeploy time, and who
     ran it.
  3. The most recent magenta database backup on maybelle: bi-hourly and daily
     dumps in /mnt/persist/magenta/backups/, where each is, how big, and when
     it was made.
  4. The last-seen time of the ten most recently active biological humans,
     across Mood messages, read marks, and signed-in devices.
  5. The version of magenta (memory-lane) running on maybelle: commit, page
     digest, newest migration, and last maybelle deploy.
  6. The version of pickipedia-mcp in use and how cryptograss/pickipedia-mcp
     diverges from upstream (ProfessionalWiki/MediaWiki-MCP-Server).
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db.models import Max, Q

from conversations.models import Device, HarnessModels, Message, Mood, ReadMark, Setting, ThinkingEntity
from conversations.services import servers
from conversations.services import settings as knobs
from conversations.services.mood_view import context_in

DEFAULT_BACKUP_DIR = '/mnt/persist/magenta/backups'
_DUMP_BLOCK = re.compile(r'magenta_memory_(\d+)\.(?:dump|sql\.gz)$')
FORK_REPO = 'cryptograss/pickipedia-mcp'
UPSTREAM_REPO = 'ProfessionalWiki/MediaWiki-MCP-Server'
MCP_CACHE_SECONDS = 600


def local_uptime_seconds(proc_path='/proc/uptime'):
    """This machine's OS uptime in seconds, read from /proc/uptime, or None."""
    try:
        return round(float(Path(proc_path).read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def _remote_health_uptime(url, cache_key):
    """uptime_seconds from a service's JSON /health endpoint, cached for 60s."""
    if not url:
        return None
    cached = cache.get(cache_key)
    if cached is not None:
        return cached if cached >= 0 else None
    uptime = None
    try:
        import requests
        r = requests.get(url, timeout=3)
        if r.ok:
            val = r.json().get('uptime_seconds')
            if isinstance(val, (int, float)) and val >= 0:
                uptime = round(float(val))
    except Exception:
        uptime = None
    cache.set(cache_key, uptime if uptime is not None else -1, 60)
    return uptime


def servers_with_uptime():
    """servers.status(), augmented with uptime_seconds where known."""
    out = servers.status()
    faces_url = getattr(settings, 'FACES_URL', '').rstrip('/')
    for entry in out:
        name = entry['name']
        uptime = (entry.get('deploy') or {}).get('uptime_seconds')
        if name == 'maybelle' and uptime is None:
            uptime = local_uptime_seconds()
        elif name == 'hunter' and uptime is None and faces_url:
            uptime = _remote_health_uptime(f'{faces_url}/health', 'status:uptime:hunter')
        elif name == 'delivery-kid' and uptime is None:
            dk_url = getattr(settings, 'DELIVERY_KID_URL', 'https://delivery-kid.cryptograss.live').rstrip('/')
            uptime = _remote_health_uptime(f'{dk_url}/health', 'status:uptime:delivery-kid')
        entry['uptime_seconds'] = uptime
    return out


def _describe_dump(path):
    """Metadata for a single .dump or .sql.gz backup file."""
    stat = path.stat()
    m = _DUMP_BLOCK.search(path.name)
    return {
        'path': str(path),
        'filename': path.name,
        'size_bytes': stat.st_size,
        'created_at': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        'block': int(m.group(1)) if m else None,
    }


def _newest_dump_in(directory):
    """The newest backup file in `directory`, or (None, 0)."""
    if not directory.is_dir():
        return None, 0
    files = [
        p for p in directory.iterdir()
        if p.is_file() and (p.name.endswith('.dump') or p.name.endswith('.sql.gz'))
    ]
    if not files:
        return None, 0
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return _describe_dump(newest), len(files)


def backup_status(backup_dir=None):
    """The most recent bi-hourly and daily magenta DB backups on maybelle."""
    raw_dir = backup_dir or getattr(settings, 'MAGENTA_BACKUP_DIR', None) or os.environ.get('MAGENTA_BACKUP_DIR') or DEFAULT_BACKUP_DIR
    root = Path(raw_dir)
    if not root.exists():
        return {
            'mounted': False,
            'dir': str(root),
            'latest': None,
            'latest_count': 0,
            'daily': None,
            'daily_count': 0,
            'offsite': None,
        }
    latest, latest_count = _newest_dump_in(root)
    daily, daily_count = _newest_dump_in(root / 'daily')
    offsite = None
    offsite_file = root / 'daily' / '.offsite-status.json'
    if offsite_file.is_file():
        try:
            offsite = json.loads(offsite_file.read_text())
        except (OSError, ValueError):
            offsite = None
    return {
        'mounted': True,
        'dir': str(root),
        'latest': latest,
        'latest_count': latest_count,
        'daily': daily,
        'daily_count': daily_count,
        'offsite': offsite,
    }


def last_seen_humans(limit=10):
    """The `limit` biological humans most recently seen across messages, read marks, and devices."""
    humans = list(ThinkingEntity.objects.filter(is_biological_human=True).values_list('name', flat=True))
    if not humans:
        return []

    # Latest Mood message per human
    msg_by_person = {}
    msg_dates = dict(
        Message.objects.filter(sender_id__in=humans, mood__isnull=False, is_sidechain=False)
        .values('sender_id').annotate(last=Max('created_at')).values_list('sender_id', 'last')
    )
    if msg_dates:
        for row in (Message.objects.filter(sender_id__in=msg_dates.keys(), mood__isnull=False, is_sidechain=False)
                    .select_related('mood').order_by('-created_at')[:200]):
            if row.sender_id not in msg_by_person:
                msg_by_person[row.sender_id] = (row.created_at, f"spoke in #{row.mood.slug}", row.mood.slug)

    # Latest ReadMark per human
    read_by_person = {}
    for mark in ReadMark.objects.filter(entity_id__in=humans).select_related('mood').order_by('-seen_at'):
        if mark.entity_id not in read_by_person:
            read_by_person[mark.entity_id] = (mark.seen_at, f"read #{mark.mood.slug}", mark.mood.slug)

    # Latest Device activity per human
    dev_by_person = {}
    for dev in Device.objects.filter(entity_id__in=humans).order_by('-last_used_at', '-created_at'):
        at = dev.last_used_at or dev.created_at
        if at and dev.entity_id not in dev_by_person:
            dev_by_person[dev.entity_id] = (at, f"device ({dev.label or dev.tier})", None)

    from conversations.services import wiki_auth
    wiki_names = wiki_auth.names()

    out = []
    for name in humans:
        candidates = [c for c in (msg_by_person.get(name), read_by_person.get(name), dev_by_person.get(name)) if c]
        if not candidates:
            continue
        best_at, how, mood_slug = max(candidates, key=lambda c: c[0])
        out.append({
            'name': name,
            'wiki_name': wiki_names.get(name, ''),
            'last_seen_at': best_at.isoformat(),
            'how': how,
            'mood': mood_slug,
            '_ts': best_at.timestamp(),
        })
    out.sort(key=lambda item: item['_ts'], reverse=True)
    for item in out:
        item.pop('_ts', None)
    return out[:limit]


def magenta_version():
    """The version of magenta (memory-lane) running on maybelle."""
    from conversations.views_moods import page_version
    base = getattr(settings, 'BASE_DIR', Path(__file__).resolve().parent.parent.parent)
    commit = os.environ.get('MAGENTA_COMMIT', '').strip() or None
    committed_at = None
    subject = None
    if ( Path(base) / '.git' ).exists():
        try:
            proc = subprocess.run(
                ['git', '-C', str(base), 'log', '-1', '--format=%h%n%cI%n%s'],
                capture_output=True, text=True, timeout=3, check=True,
            )
            lines = proc.stdout.strip().splitlines()
            if lines:
                commit = commit or lines[0]
            if len(lines) >= 2:
                committed_at = lines[1]
            if len(lines) >= 3:
                subject = lines[2]
        except Exception:
            pass

    migration = None
    try:
        from django.db.migrations.recorder import MigrationRecorder
        last_mig = MigrationRecorder.Migration.objects.filter(app='conversations').order_by('-id').first()
        if last_mig:
            migration = {'name': last_mig.name, 'applied_at': last_mig.applied.isoformat() if last_mig.applied else None}
    except Exception:
        migration = None

    deploy = servers.last_deploy('maybelle')
    if deploy:
        deploy = {k: v for k, v in deploy.items() if k != 'at_ts'}

    return {
        'commit': commit,
        'committed_at': committed_at,
        'subject': subject,
        'page_version': page_version(),
        'migration': migration,
        'maybelle_deploy': deploy,
    }


def _fetch_pickipedia_mcp_forge():
    """Query GitHub for cryptograss/pickipedia-mcp vs ProfessionalWiki/MediaWiki-MCP-Server."""
    import requests
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'memory-lane'}
    token = getattr(settings, 'GITHUB_TOKEN', '')
    if token:
        headers['Authorization'] = f'Bearer {token}'

    head_r = requests.get(f'https://api.github.com/repos/{FORK_REPO}/commits/main', headers=headers, timeout=5)
    head_r.raise_for_status()
    head = head_r.json()
    head_sha = (head.get('sha') or '')[:7]
    head_at = ((head.get('commit') or {}).get('committer') or {}).get('date')
    head_msg = (((head.get('commit') or {}).get('message') or '').splitlines() or [''])[0]

    rel_r = requests.get(f'https://api.github.com/repos/{UPSTREAM_REPO}/releases/latest', headers=headers, timeout=5)
    rel_tag = rel_r.json().get('tag_name', 'v0.19.0') if rel_r.ok else 'v0.19.0'

    # Compare fork main against upstream release tag (or merge-base commit 0b5e84d for v0.19.0)
    cmp_tag = requests.get(
        f'https://api.github.com/repos/{FORK_REPO}/compare/{rel_tag}...main', headers=headers, timeout=5
    )
    if not cmp_tag.ok:
        cmp_tag = requests.get(
            f'https://api.github.com/repos/{FORK_REPO}/compare/0b5e84d...main', headers=headers, timeout=5
        )
    tag_data = cmp_tag.json() if cmp_tag.ok else {}

    return {
        'fork_repo': FORK_REPO,
        'upstream_repo': UPSTREAM_REPO,
        'head_sha': head_sha,
        'head_at': head_at,
        'head_message': head_msg,
        'upstream_tag': rel_tag,
        'ahead_of_tag': tag_data.get('ahead_by'),
        'behind_tag': tag_data.get('behind_by'),
        'changed_files': len(tag_data.get('files') or []),
    }


def pickipedia_mcp_status():
    """What pickipedia-mcp version we are running and how our fork diverges from upstream."""
    cached = cache.get('status:pickipedia-mcp')
    if cached is None:
        try:
            cached = _fetch_pickipedia_mcp_forge()
        except Exception as e:
            cached = {
                'fork_repo': FORK_REPO,
                'upstream_repo': UPSTREAM_REPO,
                'head_sha': None,
                'head_at': None,
                'head_message': None,
                'upstream_tag': 'v0.19.0',
                'ahead_of_tag': None,
                'behind_tag': None,
                'changed_files': None,
                'error': f'could not query GitHub: {type(e).__name__}',
            }
        cache.set('status:pickipedia-mcp', cached, MCP_CACHE_SECONDS if cached.get('head_sha') else 60)
    return cached


def agents_across_moods():
    """Every Mood's agent settings row (for the settings bar), plus `everywhere` and harness vendors."""
    agents = list(ThinkingEntity.objects.filter(is_biological_human=False).order_by('name').values_list('name', flat=True))
    shown = ('listening', 'model', 'mention_effort', 'ultracode', 'catch_up_tokens', 'discretion', 'verbosity')
    rows = list(Setting.objects.filter(key__in=shown))
    archived = knobs.archived_slugs()
    pinned = knobs.mood_flagged('pinned')

    harnesses = []
    catalogs = {}
    for hm in HarnessModels.objects.order_by('agent_id'):
        catalogs[hm.agent_id] = hm.harness
        harnesses.append({
            'agent': hm.agent_id,
            'harness': hm.harness,
            'reported_at': hm.reported_at.isoformat() if hm.reported_at else None,
            'vendors': [
                {'name': v.get('name', ''), 'usable': bool(v.get('usable')), 'model_count': len(v.get('models') or [])}
                for v in (hm.vendors or [])
            ],
        })

    moods_out = []
    for mood in Mood.objects.order_by('slug'):
        mood_agents = {}
        for name in agents:
            resolved = knobs.resolve(mood.slug, name, rows=rows)
            mood_agents[name] = {
                'listening': resolved['listening'],
                'model': resolved['model'],
                'effort': resolved['mention_effort'],
                'ultracode': bool(resolved['ultracode']),
                'reads': resolved['catch_up_tokens'],
                'discretion': resolved['discretion'],
                'verbosity': resolved['verbosity'],
                'set_by': knobs.set_by(mood.slug, name, shown, rows=rows),
                'context': context_in(mood, name),
                'catalog': catalogs.get(name),
            }
        moods_out.append({
            'slug': mood.slug,
            'title': mood.title or mood.slug,
            'archived': mood.slug in archived,
            'pinned': mood.slug in pinned,
            'agents': mood_agents,
        })
    moods_out.sort(key=lambda m: (m['archived'], not m['pinned'], m['slug']))

    everywhere = {}
    for name in agents:
        resolved = knobs.resolve(None, name, rows=rows)
        everywhere[name] = {
            'listening': resolved['listening'],
            'model': resolved['model'],
            'effort': resolved['mention_effort'],
            'ultracode': bool(resolved['ultracode']),
            'reads': resolved['catch_up_tokens'],
            'discretion': resolved['discretion'],
            'verbosity': resolved['verbosity'],
            'set_by': knobs.set_by(None, name, shown, rows=rows),
            'context': None,
            'catalog': catalogs.get(name),
        }

    return {
        'agents': agents,
        'moods': moods_out,
        'everywhere': everywhere,
        'harnesses': harnesses,
        'scram': knobs.scram(),
    }


def full_status():
    """The complete payload for /api/status/."""
    return {
        **agents_across_moods(),
        'servers': servers_with_uptime(),
        'backup': backup_status(),
        'humans': last_seen_humans(limit=10),
        'magenta': magenta_version(),
        'pickipedia_mcp': pickipedia_mcp_status(),
    }
