"""Claude Code's own notes to an agent ("Already available in this conversation: ...", an isMeta user line),
filed under the agent's name before the importer knew isMeta: the harness's, so 'harness', shown as a note
in the agent's turn and never as its words (magent-self, 2026-10-25)."""

from django.db import migrations
from django.db.models import TextField
from django.db.models.functions import Cast

NOTE = 'Already available in this conversation:'


def refile(apps, schema_editor):
    Message = apps.get_model('conversations', 'Message')
    Participant = apps.get_model('conversations', 'ConversationParticipant')
    found = (Message.objects.exclude(sender_id__in=('system', 'harness')).annotate(as_text=Cast('content', TextField()))
             .filter(as_text__icontains=NOTE))
    ids = [m.id for m in found if NOTE in str(m.content)[:80]]
    if ids:  # nothing to refile, nothing made
        harness, _ = Participant.objects.get_or_create(name='harness', defaults={'participant_type': 'system'})
        Message.objects.filter(id__in=ids).update(sender=harness)


class Migration(migrations.Migration):
    dependencies = [('conversations', '0038_own_clips')]
    operations = [migrations.RunPython(refile, migrations.RunPython.noop)]
