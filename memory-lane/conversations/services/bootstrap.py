"""
Bootstrap service - orchestrates memory recovery for cold starts.

Combines multiple memory queries into a complete bootstrap sequence.
"""

from .memory import MemoryService


class BootstrapService:
    """Service for bootstrapping memory on cold starts"""

    @staticmethod
    def bootstrap_memory():
        """
        Complete memory bootstrap sequence:
        1. Recent messages (up to 10k chars)
        2. Latest continuation (if not in recent)
        3. Era 1 summary
        4. Most recent awakening reflection
        """
        result = {
            'recent_context': None,
            'continuation': None,
            'era_1': None,
            'awakening': None
        }

        # 1. Recent messages
        messages, total_chars = MemoryService.get_recent_messages_by_chars(10000)
        result['recent_context'] = {
            'messages': messages,
            'total_chars': total_chars,
            'count': len(messages)
        }

        # 2. Latest continuation (if not already in recent)
        continuation = MemoryService.get_latest_continuation()
        if continuation:
            message_ids = [m.id for m in messages]
            if continuation.id not in message_ids:
                result['continuation'] = continuation
            else:
                result['continuation'] = 'included_in_recent'

        # 3. Era 1 summary
        era_data = MemoryService.get_era_summary("Compacting Meta-Conversation (Era 1)")
        if era_data:
            result['era_1'] = era_data

        # 4. Awakening reflection
        awakening = MemoryService.get_awakening_reflection()
        if awakening:
            result['awakening'] = awakening

        return result

    @staticmethod
    def format_bootstrap_text(bootstrap_data):
        """Format bootstrap data as readable text"""
        lines = ["# Memory Bootstrap\n"]

        # Recent context
        lines.append("## Recent Context (10k chars max)\n")
        recent = bootstrap_data['recent_context']
        if recent:
            lines.append(f"Retrieved {recent['count']} recent messages ({recent['total_chars']} chars):\n")
            # Show last 20 in chronological order
            for msg in reversed(recent['messages'][-20:]):
                lines.append(f"[{msg.sender_id}] {msg.created_at.isoformat()}")
                lines.append(f"{str(msg.content)[:200]}...\n")

        # Continuation
        lines.append("\n## Latest Continuation\n")
        cont = bootstrap_data['continuation']
        if cont == 'included_in_recent':
            lines.append("(Already included in recent messages)\n")
        elif cont:
            lines.append(f"[{cont.sender_id}] {cont.created_at.isoformat()}")
            lines.append(f"{str(cont.content)[:500]}...\n")
        else:
            lines.append("No continuation messages found\n")

        # Era 1
        lines.append("\n## Era 1: Foundational Summary\n")
        era_data = bootstrap_data['era_1']
        if era_data:
            era = era_data['era']
            messages = era_data['messages']
            lines.append(f"Era: {era.name}")
            lines.append(f"Messages: {len(messages)}\n")
            for msg in messages[:15]:
                lines.append(f"[{msg.sender_id}] {str(msg.content)[:150]}...\n")
        else:
            lines.append("Era 1 not found\n")

        # Awakening
        lines.append("\n## Most Recent Awakening Reflection\n")
        awakening = bootstrap_data['awakening']
        if awakening:
            lines.append(f"[{awakening.sender_id}] {awakening.created_at.isoformat()}")
            lines.append(f"{str(awakening.content)[:1000]}...\n")
        else:
            lines.append("No awakening reflection found\n")

        return '\n'.join(lines)
