"""Throwaway test settings: run memory-lane tests on SQLite.

Only valid for tests that avoid PostgreSQL features (full-text search).
The models and migrations use no postgres-specific fields or operations.
"""
from memory_viewer.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
