"""Cache for the Moods list API endpoint, invalidated on mood lifecycle events."""

from django.core.cache import cache

API_MOODS_CACHE_KEY = 'api:moods'
API_MOODS_CACHE_TTL = 30  # seconds: freshens message counts and last_at periodically


def bust_moods_cache():
    """Invalidate the cached /api/moods/ payload when a mood is created, renamed,
    archived, pinned, closed, reforked or deleted."""
    cache.delete(API_MOODS_CACHE_KEY)
