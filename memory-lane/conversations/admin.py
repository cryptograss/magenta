from django.contrib import admin
from .models import (
    ThinkingEntity,
    Era,
    ContextHeap,
    Message,
    Thought,
    ToolUse,
    ToolResult,
    CompactingAction,
    Summary,
    Topic,
    Note
)


@admin.register(ThinkingEntity)
class ThinkingEntityAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_biological_human')
    list_filter = ('is_biological_human',)
    search_fields = ('name',)


@admin.register(Era)
class EraAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_current', 'created_at')
    list_filter = ('is_current',)
    search_fields = ('name',)
    readonly_fields = ('id', 'created_at')
    actions = ['make_current']

    @admin.action(description='Set as current era')
    def make_current(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, 'Please select exactly one era to make current.', level='error')
            return
        era = queryset.first()
        era.is_current = True
        era.save()
        self.message_user(request, f'"{era.name}" is now the current era.')


@admin.register(ContextHeap)
class ContextHeapAdmin(admin.ModelAdmin):
    list_display = ('id', 'era', 'type', 'first_message', 'created_at')
    list_filter = ('era', 'type')
    search_fields = ('id',)
    readonly_fields = ('id', 'created_at')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'sender', 'get_recipients', 'timestamp', 'session_id', 'get_type')
    list_filter = ('sender',)
    search_fields = ('id', 'session_id')
    readonly_fields = ('id', 'created_at')

    def get_recipients(self, obj):
        return ', '.join(r.name for r in obj.recipients.all())
    get_recipients.short_description = 'Recipients'

    def get_type(self, obj):
        """Determine polymorphic type"""
        if hasattr(obj, 'thought'):
            return 'thought'
        elif hasattr(obj, 'tooluse'):
            return 'tool_use'
        elif hasattr(obj, 'toolresult'):
            return 'tool_result'
        return 'message'
    get_type.short_description = 'Type'


@admin.register(Thought)
class ThoughtAdmin(admin.ModelAdmin):
    list_display = ('id', 'sender', 'content_preview', 'timestamp')
    search_fields = ('id', 'content', 'signature')
    readonly_fields = ('id', 'created_at', 'signature')

    def content_preview(self, obj):
        content_str = str(obj.content)
        return content_str[:100] + '...' if len(content_str) > 100 else content_str
    content_preview.short_description = 'Thought'


@admin.register(ToolUse)
class ToolUseAdmin(admin.ModelAdmin):
    list_display = ('id', 'tool_name', 'tool_id', 'sender', 'timestamp')
    list_filter = ('tool_name',)
    search_fields = ('id', 'tool_name', 'tool_id')
    readonly_fields = ('id', 'created_at')


@admin.register(ToolResult)
class ToolResultAdmin(admin.ModelAdmin):
    list_display = ('id', 'tool_use_id', 'is_error', 'content_preview', 'timestamp')
    list_filter = ('is_error',)
    search_fields = ('id', 'tool_use_id', 'content')
    readonly_fields = ('id', 'created_at')

    def content_preview(self, obj):
        content_str = str(obj.content)
        return content_str[:100] + '...' if len(content_str) > 100 else content_str
    content_preview.short_description = 'Content'


@admin.register(CompactingAction)
class CompactingActionAdmin(admin.ModelAdmin):
    list_display = ('context_heap', 'ending_message', 'compact_trigger', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    list_display = ('id', 'summary_preview', 'leaf_message', 'looking_for_leaf_message', 'created_at')
    search_fields = ('summary_text',)
    readonly_fields = ('id', 'created_at')

    def summary_preview(self, obj):
        return obj.summary_text[:80] + '...' if len(obj.summary_text) > 80 else obj.summary_text
    summary_preview.short_description = 'Summary'


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'description')
    list_filter = ('category',)
    search_fields = ('name', 'description')


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'from_entity', 'about_type', 'content_preview', 'created_at')
    list_filter = ('from_entity', 'content_type')
    search_fields = ('content',)
    readonly_fields = ('id', 'created_at')

    def about_type(self, obj):
        return obj.content_type.model if obj.content_type else '?'
    about_type.short_description = 'About'

    def content_preview(self, obj):
        return obj.content[:100] + '...' if len(obj.content) > 100 else obj.content
    content_preview.short_description = 'Content'
