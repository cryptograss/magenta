"""The text Claude Code feeds an agent when it loads a skill ("Base directory for this skill: ..."), filed
under the agent's name when a turn's stream didn't say isMeta: the harness's, so 'harness', shown as a note
in the agent's turn and never as its words (blu in #connectome, 11 Nov; magent in #interfaces, 5 Nov)."""

from django.db import migrations
from django.db.models import TextField
from django.db.models.functions import Cast

NOTE = 'Base directory for this skill:'


def refile(apps, schema_editor):
    Message = apps.get_model('conversations', 'Message')
    Participant = apps.get_model('conversations', 'ConversationParticipant')
    found = (Message.objects.exclude(sender_id__in=('system', 'harness', 'tool-result'))
             .annotate(as_text=Cast('content', TextField())).filter(as_text__icontains=NOTE))
    ids = [m.id for m in found if NOTE in str(m.content)[:80]]
    if ids:  # nothing to refile, nothing made
        harness, _ = Participant.objects.get_or_create(name='harness', defaults={'participant_type': 'system'})
        Message.objects.filter(id__in=ids).update(sender=harness)


class Migration(migrations.Migration):
    dependencies = [('conversations', '0051_ramp_work_tidings')]
    operations = [migrations.RunPython(refile, migrations.RunPython.noop)]
