"""What happens when something lands in the record."""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import FaceLook, FaceName, Media, Message, Setting

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Message)
def handoffs_carried(sender, instance, created, **kwargs):
    """An agent's ```handoff block, taken to its Mood (services/handoff.py). Never in the way of the save."""
    if not created or not instance.mood_id:
        return
    try:
        from .services import handoff
        handoff.from_message(instance)
    except Exception:  # noqa: BLE001
        logger.exception('a handoff in %s could not be made', instance.id)
    try:
        # An agent's ```settings block, at a person's ask; and, at an agent's first word on settings other
        # than its last turn's, a line above it saying so (services/agent_settings.py).
        from .services import agent_settings
        agent_settings.from_message(instance)
        agent_settings.noticed(instance)
    except Exception:  # noqa: BLE001
        logger.exception('settings in %s could not be read', instance.id)


@receiver(post_save, sender=Media)
@receiver(post_save, sender=FaceLook)
@receiver(post_save, sender=FaceName)
@receiver(post_delete, sender=FaceName)
def pictures_marked(sender, **kwargs):
    """A picture stored, a face looked at or named: Tidings' pictures are read afresh (services/tidings.py)."""
    from .services.tidings import pictures_changed
    pictures_changed()


@receiver(post_save, sender=Message)
def pictures_posted(sender, instance, created, **kwargs):
    """A post showing a picture, or a picture's upload to PickiPedia: Tidings' pictures are read afresh."""
    if created and instance.mood_id and (instance.source_file == 'wiki-upload' or '/media/' in str(instance.content)):
        from .services.tidings import pictures_changed
        pictures_changed()


@receiver(post_save, sender=Setting)
@receiver(post_delete, sender=Setting)
def mood_flags_changed(sender, instance, **kwargs):
    """Archiving or pinning a Mood invalidates the cached Moods list."""
    if getattr(instance, 'key', None) in ('archived', 'pinned'):
        from .services.mood_cache import bust_moods_cache
        bust_moods_cache()


@receiver(post_save, sender=Message)
def compaction_occurred(sender, instance, created, **kwargs):
    """A compaction in a Mood marks its threads as compacted; invalidate the Moods list."""
    if created and getattr(instance, 'is_continuation_message', False):
        from .services.mood_cache import bust_moods_cache
        bust_moods_cache()
