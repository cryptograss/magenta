"""
MCP Server for Magenta Conversation Memory

Provides programmatic access to conversation history via the Model Context Protocol.
Implements the tools defined in MCP_MEMORY_RECOVERY_SPEC.md
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from conversations.models import Message, Era, ContextHeap, ThinkingEntity
from django.db.models import Q
from asgiref.sync import sync_to_async
import asyncio
import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
import logging
import sys
from pathlib import Path

# Set up file logging - use BASE_DIR from settings
log_dir = Path(settings.BASE_DIR) / 'logs'
log_dir.mkdir(exist_ok=True, parents=True)
log_file = log_dir / 'mcp_server.log'

# Configure logging to both file and stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stderr)
    ]
)

logger = logging.getLogger(__name__)
logger.info(f"MCP Server logging to {log_file}")


class Command(BaseCommand):
    help = 'Run MCP server for conversation memory access'

    def handle(self, *args, **options):
        """Run the MCP server on stdio"""
        asyncio.run(self.run_server())

    async def run_server(self):
        """Main MCP server loop"""
        logger.info("Starting MCP server initialization...")
        server = Server("magenta-memory")
        logger.info("MCP Server instance created")

        @server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            """List available MCP tools for memory recovery"""
            return [
                types.Tool(
                    name="get_latest_continuation",
                    description="Get the most recent continuation message from a compacting action",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                types.Tool(
                    name="get_messages_before",
                    description="Get N messages before a given message or timestamp",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "reference_id": {"type": "string", "description": "Message UUID to use as reference point"},
                            "reference_timestamp": {"type": "string", "description": "ISO timestamp to use as reference point"},
                            "limit": {"type": "number", "description": "Number of messages to retrieve (default 300)", "default": 300}
                        }
                    }
                ),
                types.Tool(
                    name="get_era_summary",
                    description="Get all messages from Era 1 (foundational summaries)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "era_name": {"type": "string", "description": "Era name (default: 'Compacting Meta-Conversation (Era 1)')", "default": "Compacting Meta-Conversation (Era 1)"}
                        }
                    }
                ),
                types.Tool(
                    name="get_context_heap",
                    description="Get all messages from a specific context heap",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "heap_id": {"type": "string", "description": "Context heap UUID"}
                        },
                        "required": ["heap_id"]
                    }
                ),
                types.Tool(
                    name="search_messages",
                    description="Search for messages containing specific content",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "limit": {"type": "number", "description": "Maximum results (default 50)", "default": 50}
                        },
                        "required": ["query"]
                    }
                ),
                types.Tool(
                    name="get_recent_work",
                    description="Get the most recent N messages to understand current work",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "limit": {"type": "number", "description": "Number of messages (default 50)", "default": 50}
                        }
                    }
                ),
                types.Tool(
                    name="bootstrap_memory",
                    description="Complete memory bootstrap: recent messages (10k chars), latest continuation (if not included), Era 1 summary, and most recent 'reawaken and breathe' reflection",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                types.Tool(
                    name="random_messages",
                    description="Get random messages with context - retrieves N random messages, each with M following messages for context",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "count": {"type": "number", "description": "Number of random starting points (default 4)", "default": 4},
                            "context_messages": {"type": "number", "description": "Number of subsequent messages to include (default 4)", "default": 4}
                        }
                    }
                )
            ]

        @server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict | None
        ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
            """Handle tool execution"""

            if name == "get_latest_continuation":
                @sync_to_async
                def get_latest_continuation():
                    # Find most recent continuation message
                    continuation = Message.objects.filter(
                        is_continuation_message=True
                    ).order_by('-created_at').first()

                    if not continuation:
                        return "No continuation messages found"

                    return str(continuation.content)

                result = await get_latest_continuation()
                return [types.TextContent(type="text", text=result)]

            elif name == "get_messages_before":
                limit = arguments.get("limit", 300) if arguments else 300

                @sync_to_async
                def get_messages_before():
                    if arguments and arguments.get("reference_id"):
                        ref_msg = Message.objects.get(id=arguments["reference_id"])
                        messages = list(Message.objects.filter(
                            created_at__lt=ref_msg.created_at
                        ).order_by('-created_at')[:limit])
                    elif arguments and arguments.get("reference_timestamp"):
                        messages = list(Message.objects.filter(
                            created_at__lt=arguments["reference_timestamp"]
                        ).order_by('-created_at')[:limit])
                    else:
                        messages = list(Message.objects.order_by('-created_at')[:limit])

                    result = []
                    for msg in messages:
                        result.append({
                            "id": str(msg.id),
                            "sender": msg.sender_id,
                            "content": msg.content,
                            "created_at": msg.created_at.isoformat()
                        })

                    return f"Retrieved {len(result)} messages:\n\n" + "\n---\n".join([
                        f"[{m['sender']}] {m['created_at']}\n{str(m['content'])[:200]}..."
                        for m in result[:10]  # Show first 10 in detail
                    ])

                result = await get_messages_before()
                return [types.TextContent(type="text", text=result)]

            elif name == "get_era_summary":
                era_name = arguments.get("era_name", "Compacting Meta-Conversation (Era 1)") if arguments else "Compacting Meta-Conversation (Era 1)"

                @sync_to_async
                def get_era_summary():
                    try:
                        era = Era.objects.get(name=era_name)
                        heaps = list(ContextHeap.objects.filter(era=era))
                        messages = list(Message.objects.filter(
                            context_heap__in=heaps
                        ).order_by('message_number')[:100])  # Limit to first 100

                        result = f"Era: {era.name}\n"
                        result += f"Messages: {len(messages)}\n\n"

                        for msg in messages[:20]:  # Show first 20
                            result += f"[{msg.sender_id}] {str(msg.content)[:150]}...\n\n"

                        return result
                    except Era.DoesNotExist:
                        return f"Era '{era_name}' not found"

                result = await get_era_summary()
                return [types.TextContent(type="text", text=result)]

            elif name == "get_recent_work":
                limit = arguments.get("limit", 50) if arguments else 50

                @sync_to_async
                def get_messages():
                    messages = list(Message.objects.order_by('-created_at')[:limit])
                    result = f"Most recent {len(messages)} messages:\n\n"
                    for msg in messages:
                        result += f"[{msg.sender_id}] {msg.created_at.isoformat()}\n"
                        result += f"{str(msg.content)[:150]}...\n\n"
                    return result

                result = await get_messages()
                return [types.TextContent(type="text", text=result)]

            elif name == "bootstrap_memory":
                @sync_to_async
                def bootstrap():
                    result = "# Memory Bootstrap\n\n"

                    # 1. Most recent messages (up to 10,000 chars)
                    result += "## Recent Context (10k chars max)\n\n"
                    messages = []
                    total_chars = 0
                    for msg in Message.objects.order_by('-created_at'):
                        content_str = str(msg.content)
                        if total_chars + len(content_str) > 10000:
                            break
                        messages.append(msg)
                        total_chars += len(content_str)

                    result += f"Retrieved {len(messages)} recent messages ({total_chars} chars):\n\n"
                    for msg in reversed(messages[-20:]):  # Show last 20 in chronological order
                        result += f"[{msg.sender_id}] {msg.created_at.isoformat()}\n"
                        result += f"{str(msg.content)[:200]}...\n\n"

                    # 2. Latest continuation message (if not already included)
                    result += "\n## Latest Continuation\n\n"
                    continuation = Message.objects.filter(
                        is_continuation_message=True
                    ).order_by('-created_at').first()

                    if continuation:
                        if continuation.id not in [m.id for m in messages]:
                            result += f"[{continuation.sender_id}] {continuation.created_at.isoformat()}\n"
                            result += f"{str(continuation.content)[:500]}...\n\n"
                        else:
                            result += "(Already included in recent messages)\n\n"
                    else:
                        result += "No continuation messages found\n\n"

                    # 3. Era 1 summary
                    result += "\n## Era 1: Foundational Summary\n\n"
                    try:
                        era = Era.objects.get(name="Compacting Meta-Conversation (Era 1)")
                        heaps = list(ContextHeap.objects.filter(era=era))
                        era_messages = list(Message.objects.filter(
                            context_heap__in=heaps
                        ).order_by('message_number')[:50])

                        result += f"Era: {era.name}\n"
                        result += f"Messages: {len(era_messages)}\n\n"

                        for msg in era_messages[:15]:
                            result += f"[{msg.sender_id}] {str(msg.content)[:150]}...\n\n"
                    except Era.DoesNotExist:
                        result += "Era 1 not found\n\n"

                    # 4. Most recent "reawaken and breathe" message
                    result += "\n## Most Recent Awakening Reflection\n\n"
                    # TODO: Query by topic once we have topic tagging working
                    # For now, search for recent messages from magent containing "reawaken" or "breathe"
                    awakening = Message.objects.filter(
                        Q(sender_id='magent') &
                        (Q(content__icontains='reawaken') | Q(content__icontains='breathe'))
                    ).order_by('-created_at').first()

                    if awakening:
                        result += f"[{awakening.sender_id}] {awakening.created_at.isoformat()}\n"
                        result += f"{str(awakening.content)[:1000]}...\n\n"
                    else:
                        result += "No awakening reflection found (search for 'reawaken' or 'breathe')\n\n"

                    return result

                result = await bootstrap()
                return [types.TextContent(type="text", text=result)]

            elif name == "random_messages":
                count = arguments.get("count", 4) if arguments else 4
                context_messages = arguments.get("context_messages", 4) if arguments else 4

                @sync_to_async
                def get_random_with_context():
                    import random

                    # Get total message count
                    total = Message.objects.count()
                    if total == 0:
                        return "No messages in database"

                    # Generate random message IDs
                    all_ids = list(Message.objects.values_list('id', flat=True))
                    random_ids = random.sample(all_ids, min(count, len(all_ids)))

                    result = f"# Random Messages with Context\n\n"
                    result += f"Selected {len(random_ids)} random starting points from {total} total messages\n\n"

                    for idx, msg_id in enumerate(random_ids, 1):
                        random_msg = Message.objects.get(id=msg_id)

                        result += f"## Random Sample {idx}\n\n"
                        result += f"**Starting at:** [{random_msg.sender_id}] {random_msg.created_at.isoformat()}\n\n"

                        # Get this message plus N following messages
                        following = list(Message.objects.filter(
                            created_at__gte=random_msg.created_at
                        ).order_by('created_at')[:(context_messages + 1)])

                        for msg in following:
                            result += f"[{msg.sender_id}] {msg.created_at.isoformat()}\n"
                            result += f"{str(msg.content)[:300]}\n\n"
                            result += "---\n\n"

                    return result

                result = await get_random_with_context()
                return [types.TextContent(type="text", text=result)]

            else:
                raise ValueError(f"Unknown tool: {name}")

        # Use Streamable HTTP transport for HTTP-based access
        logger.info("Setting up Streamable HTTP transport...")
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.routing import Route, Mount
        import uvicorn

        # Create session manager for HTTP transport
        session_manager = StreamableHTTPSessionManager(server)
        logger.info("HTTP session manager created")

        # Create ASGI app wrapper that logs and delegates to session manager
        async def mcp_asgi_app(scope, receive, send):
            if scope["type"] == "http":
                logger.info(f"Received MCP HTTP request from {scope['client']}: {scope['method']} {scope['path']}")
            try:
                await session_manager.handle_request(scope, receive, send)
            except Exception as e:
                logger.error(f"Error in MCP handler: {e}", exc_info=True)
                raise

        app = Starlette(
            routes=[
                Mount("/sse", app=mcp_asgi_app),
                Mount("/", app=mcp_asgi_app),  # Also handle root for flexibility
            ]
        )
        logger.info("Starlette app created with HTTP handler at /")

        # Run server on port 8000
        # Use "::" to listen on both IPv4 and IPv6
        config = uvicorn.Config(
            app,
            host="::",
            port=8000,
            log_level="info"
        )
        server_instance = uvicorn.Server(config)
        logger.info("Starting uvicorn server on http://0.0.0.0:8000")

        async with session_manager.run():
            await server_instance.serve()
