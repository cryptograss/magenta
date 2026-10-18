"""
Heap assignment logic extracted for reuse by both importer and watcher.
"""

from conversations.models import ContextHeap, ContextHeapType, Message


def assign_heap_to_message(message, era, current_heap=None):
    """
    Assign a message to the appropriate context heap.

    This implements the simplified heap assignment rules:
    1. Has parent with heap → use parent's heap
    2. Is continuation message → create POST_COMPACTING heap
    3. No parent → create FRESH heap
    4. Parent exists but has no heap → use current_heap or create fresh

    Args:
        message: Message instance (newly created, not yet assigned to heap)
        era: Era instance to create heaps in
        current_heap: Optional currently active heap (for edge cases)

    Returns:
        ContextHeap that the message was assigned to

    Raises:
        ValueError: If message already has a heap or other constraint violation
    """
    if message.context_heap:
        raise ValueError(f"Message {message.id} already has heap {message.context_heap.id}")

    # Rule 1: Has parent with heap → use parent's heap
    if message.parent and message.parent.context_heap:
        message.context_heap = message.parent.context_heap
        message.save()
        return message.context_heap

    # Rule 2: Is continuation message → create POST_COMPACTING heap
    elif hasattr(message, "is_continuation_message") and message.is_continuation_message:
        heap = ContextHeap.objects.create(era=era, type=ContextHeapType.POST_COMPACTING)
        heap.add_event(message)
        return heap

    # Rule 3: No parent → create FRESH heap
    elif message.parent is None:
        heap = ContextHeap.objects.create(era=era, type=ContextHeapType.FRESH)
        heap.add_event(message)
        return heap

    # Rule 4: Parent exists but has no heap (edge case from old imports)
    elif message.parent and not message.parent.context_heap:
        # Use current heap if available, otherwise create fresh
        if current_heap:
            current_heap.add_event(message)
            return current_heap
        else:
            heap = ContextHeap.objects.create(era=era, type=ContextHeapType.FRESH)
            heap.add_event(message)
            return heap

    else:
        raise ValueError(f"Unexpected heap assignment case for message {message.id}")
