"""Middleware for conversations app."""

import logging
import re
from .services import banner

logger = logging.getLogger(__name__)

BODY_OPEN_RE = re.compile(r'(<body[^>]*>)', re.IGNORECASE)


class BannerMiddleware:
    """Inject the global banner across the top of every HTML page response.

    Reads banner state from a file on disk (never the DB), so the banner
    displays even if the database is down or migrations are broken.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code >= 600 or getattr(response, 'streaming', False):
            return response
        content_type = response.get('Content-Type', '')
        if not content_type.startswith('text/html'):
            return response

        b = banner.get_banner()
        if not b:
            return response

        banner_html = banner.render_banner_html(b)
        try:
            content = response.content.decode('utf-8')
            if 'id="magenta-banner"' in content or 'class="banner"' in content:
                return response
            match = BODY_OPEN_RE.search(content)
            if match:
                idx = match.end()
                new_content = content[:idx] + banner_html + content[idx:]
                response.content = new_content.encode('utf-8')
                if response.has_header('Content-Length'):
                    response['Content-Length'] = len(response.content)
        except Exception as e:
            logger.warning(f"BannerMiddleware failed to inject banner: {e}")

        return response
