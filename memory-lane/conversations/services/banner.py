"""Global banner displayed across every page in Magenta.

Stored in a JSON file on disk (default /tmp/magenta-banner.json), never the database,
so it can be set and displayed even when the database is completely down or migrations
are broken (Justin, 30 Oct).
"""

from datetime import datetime, timezone
import html
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

BANNER_PATH = os.environ.get('MAGENTA_BANNER_PATH', '/tmp/magenta-banner.json')
CSS_COLOR = re.compile(r'^[a-zA-Z0-9#_.-]{1,30}$')


def get_banner():
    """Return the active banner dict or None. Reads from disk, never the database."""
    path = os.environ.get('MAGENTA_BANNER_PATH', BANNER_PATH)
    try:
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict) and data.get('message'):
                return data
    except Exception as e:
        logger.warning(f"Could not read banner file {path}: {e}")
        return None
    return None


def set_banner(message, bg='', fg='', by=None):
    """Set the banner in a file on disk (never the DB). If message is empty/clear/off, removes it."""
    path = os.environ.get('MAGENTA_BANNER_PATH', BANNER_PATH)
    msg = str(message or '').strip()
    if not msg or msg.lower() in ('off', 'clear', 'none'):
        clear_banner()
        return None

    bg = str(bg or '').strip() or '#b71c1c'
    fg = str(fg or '').strip() or '#ffffff'
    if not CSS_COLOR.match(bg):
        bg = '#b71c1c'
    if not CSS_COLOR.match(fg):
        fg = '#ffffff'

    data = {
        'message': msg,
        'bg': bg,
        'fg': fg,
        'by': by or 'admin',
        'at': datetime.now(timezone.utc).isoformat(),
    }
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        dir_name = os.path.dirname(os.path.abspath(path))
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.error(f"Failed to write banner file {path}: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise
    return data


def clear_banner():
    """Remove the banner file from disk."""
    path = os.environ.get('MAGENTA_BANNER_PATH', BANNER_PATH)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning(f"Could not remove banner file {path}: {e}")


def render_banner_html(banner):
    """Render the banner HTML tag with inline styles."""
    if not banner or not banner.get('message'):
        return ''
    bg = html.escape(banner.get('bg') or '#b71c1c')
    fg = html.escape(banner.get('fg') or '#ffffff')
    msg = html.escape(banner.get('message') or '')
    style = (
        f"background: {bg}; color: {fg}; width: 100%; box-sizing: border-box; "
        f"padding: 10px 16px; font-weight: 600; font-size: 14px; line-height: 1.4; "
        f"text-align: center; position: relative; z-index: 999999; flex: none; "
        f"border-bottom: 1px solid rgba(0,0,0,0.2); "
        f"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;"
    )
    return f'<div id="magenta-banner" class="banner" role="alert" style="{style}">{msg}</div>'
