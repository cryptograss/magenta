"""Where an address is, roughly -- its city, region and country -- for the readers an SSH-key sign-in sees
(services/watchers.py; Justin, 24 Oct: "add location based on the IP address").

Looked up here, in DB-IP's free IP to City Lite database (CC BY 4.0, https://db-ip.com): no reader's address
is sent anywhere to be placed. The database (60 MB to download, 130 MB unpacked) is fetched in the background
the first time it's wanted, and again once it's a month old; until it's here, an address has no place. A city
from it is often right and sometimes the next one over: a phone's address is its carrier's, and may be a
state away.
"""

import datetime
import gzip
import ipaddress
import logging
import os
import shutil
import tempfile
import threading

import requests
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

URL = 'https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz'
CREDIT = 'IP Geolocation by DB-IP (db-ip.com)'
STALE_AFTER = 35 * 86400   # seconds: DB-IP publishes a new one each month
RETRY_AFTER = 3600         # seconds between tries, should a download fail (and the lock across workers)

_reader = None
_opened = None             # the file's mtime when it was opened: a newer one is opened afresh
_lock = threading.Lock()


def path():
    return (getattr(settings, 'GEOIP_PATH', '')
            or os.path.join(tempfile.gettempdir(), 'memory-lane-geoip', 'dbip-city-lite.mmdb'))


def _months(today=None):
    """This month's database, and last month's: early in a month, this one may not be out yet."""
    today = today or datetime.date.today()
    last = today.replace(day=1) - datetime.timedelta(days=1)
    return [today.strftime('%Y-%m'), last.strftime('%Y-%m')]


def fetch():
    """Download and unpack the newest database beside the old, check it opens, and put it in place."""
    import maxminddb
    target = path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    for month in _months():
        part = f'{target}.{os.getpid()}.part'
        try:
            with requests.get(URL.format(month=month), stream=True, timeout=60) as r:
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                r.raw.decode_content = False
                with gzip.GzipFile(fileobj=r.raw) as packed, open(part, 'wb') as out:
                    shutil.copyfileobj(packed, out, 1 << 20)
            maxminddb.open_database(part).close()  # a whole database, or none
            os.replace(part, target)
            log.info('geoip: DB-IP city lite %s in place', month)
            return True
        except Exception as e:  # noqa: BLE001 -- the next month back, or the next try
            log.warning('geoip: DB-IP city lite %s: %s', month, e)
        finally:
            if os.path.exists(part):
                os.remove(part)
    return False


def _fresh(now=None):
    """Fetch the database in the background if it's missing or old: one worker at a time, once an hour at most."""
    if not getattr(settings, 'GEOIP_FETCH', True):
        return
    try:
        age = (now or datetime.datetime.now().timestamp()) - os.path.getmtime(path())
    except OSError:
        age = None
    if (age is None or age > STALE_AFTER) and cache.add('geoip:fetching', 1, RETRY_AFTER):
        threading.Thread(target=fetch, name='geoip-fetch', daemon=True).start()


def _open():
    global _reader, _opened
    try:
        mtime = os.path.getmtime(path())
    except OSError:
        return None
    with _lock:
        if _reader is None or mtime != _opened:
            import maxminddb
            try:
                fresh = maxminddb.open_database(path())
            except Exception as e:  # noqa: BLE001
                log.warning('geoip: %s', e)
                return _reader
            _reader, _opened = fresh, mtime
    return _reader


def place(ip):
    """'Portland, Oregon, United States' for an address; '' for one that's private, unknown, or not looked up yet."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ''
    if not address.is_global:
        return ''
    _fresh()
    reader = _open()
    if reader is None:
        return ''
    try:
        found = reader.get(ip) or {}
    except Exception:  # noqa: BLE001
        return ''
    name = lambda part: ((part or {}).get('names') or {}).get('en', '')  # noqa: E731
    parts = [name(found.get('city')), name((found.get('subdivisions') or [None])[0]), name(found.get('country'))]
    return ', '.join(p for i, p in enumerate(parts) if p and p not in parts[:i])
