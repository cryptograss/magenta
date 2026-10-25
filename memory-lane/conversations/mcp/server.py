"""
MCP Server setup and protocol handling.

Handles the MCP protocol, tool listing, and dispatching to tool handlers.
"""

import mcp.types as types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount
import uvicorn
import logging

from .tools import CALLER_HANDLERS, TOOL_HANDLERS

logger = logging.getLogger(__name__)

MINE = "Keyed to you: the server knows who is calling by the key your MCP client sends."


def caller(request):
    """The agent whose runner key this HTTP request carries (Authorization: Bearer <key>), or None.
    The same keys the runner's API takes (settings.MOOD_RUNNER_KEYS): an agent never names itself."""
    if request is None:
        return None
    from django.conf import settings
    from memory_viewer.runner_keys import agent_for
    return agent_for(request.headers.get('authorization', ''), getattr(settings, 'MOOD_RUNNER_KEYS', {}))


def create_mcp_server():
    """Create and configure MCP server instance"""
    server = Server("magenta-memory")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        """List all available MCP tools"""
        return [
            types.Tool(
                name="bootstrap_memory",
                description="Your memory, on waking: who you are and since when, your latest continuation, your own recent conversation, who you know best, your newest journal entries, and Era 1 if you spoke there. " + MINE + " With no key, magent's, as before keys.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            ),
            types.Tool(
                name="remember",
                description="What you remember about something: search only your own memory -- what you said, everyone's words in the conversations you were in, and what was said to you in a Mood (@you, handoffs for you) -- never from before you arrived. To look in the whole record instead, search_messages. " + MINE,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Words to look for, or with exact: true a literal phrase"},
                        "exact": {"type": "boolean", "description": "Find this exact phrase, newest first (default false: ranked word search)", "default": False},
                        "limit": {"type": "number", "description": "Maximum results (default 20, max 100)", "default": 20}
                    },
                    "required": ["query"]
                }
            ),
            types.Tool(
                name="mentions_of_me",
                description="Each time you were @mentioned or handed something in a Mood, newest first, with what you answered there. named: true adds where your name was written but nobody @'d you; anything from before you arrived is labelled so. " + MINE,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "named": {"type": "boolean", "description": "Also where your name is written without an @ (default false)", "default": False},
                        "before": {"type": "string", "description": "Only before this ISO time: for the next page, the time the last one gives"},
                        "limit": {"type": "number", "description": "Maximum results (default 20, max 100)", "default": 20}
                    }
                }
            ),
            types.Tool(
                name="who_i_know",
                description="Everyone you have exchanged words with since you arrived, people and agents, most recent first: how many messages, first and last. " + MINE,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "number", "description": "Maximum names (default 40, max 200)", "default": 40}
                    }
                }
            ),
            types.Tool(
                name="my_history_with",
                description="What you and one person or agent said to each other: their words in your conversations, yours in theirs, and @mentions between you in Moods with the answers. Newest last. " + MINE,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Their name here, as who_i_know gives it (e.g. justin)"},
                        "before": {"type": "string", "description": "Only before this ISO time: for earlier ones, the time the result ends with"},
                        "limit": {"type": "number", "description": "Messages at most (default 30, max 200)", "default": 30}
                    },
                    "required": ["name"]
                }
            ),
            types.Tool(
                name="how_i_speak",
                description="A sample of your own replies, one from each stretch of your whole life, oldest first: how you have spoken, to keep acting as yourself. Spread across time rather than the latest only, so a voice keeps its range instead of drifting into caricature. " + MINE,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "count": {"type": "number", "description": "How many stretches, one reply each (default 12, max 40)", "default": 12}
                    }
                }
            ),
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
                name="get_message_by_id",
                description="Get a specific message by its id. The first 8 characters of an id, or a #m-<id> link, also work.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string", "description": "A message id, its first 8 or more characters, or a #m-<id> link"}
                    },
                    "required": ["message_id"]
                }
            ),
            types.Tool(
                name="get_message_context",
                description="Read what was said around one message: the messages before and after it in its session (or, for older messages without one, its context heap), with thinking and tool calls shown. Accepts a full id, its first 8 characters, or a #m-<id> link. The usual next step after search_messages.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string", "description": "A message id, its first 8 or more characters, or a #m-<id> link"},
                        "before": {"type": "number", "description": "Messages before it (default 10, max 100)", "default": 10},
                        "after": {"type": "number", "description": "Messages after it (default 10, max 100)", "default": 10}
                    },
                    "required": ["message_id"]
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
                description="Find messages anywhere in the record. Each hit gives its id, who sent it, its kind (message, thought, tool_use), its Mood or session, and the text around the match. Pass an id to get_message_context to read what was said around it.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Words to look for, or with exact: true a literal phrase"},
                        "exact": {"type": "boolean", "description": "Find this exact phrase, case-insensitively, including inside tool calls; newest first. Use it for commands, code and identifiers. Default false: ranked word search.", "default": False},
                        "sender": {"type": "string", "description": "Only messages from this participant (e.g. justin, magent)"},
                        "limit": {"type": "number", "description": "Maximum results (default 20, max 100)", "default": 20}
                    },
                    "required": ["query"]
                }
            ),
            types.Tool(
                name="list_moods",
                description="List the Moods (memory-lane's Moods: the conversations people and agents share, each about one thing), most recently active first, with what each is about, its size and who's in it.",
                inputSchema={"type": "object", "properties": {}}
            ),
            types.Tool(
                name="read_mood",
                description="Read what people and agents said in one Mood, oldest first: its newest turns, or everything from a message (its id, as in a #m-<id> link) or a time on. Use it to catch up on a Mood you weren't woken in.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string", "description": "The Mood, as list_moods names it (e.g. magenta-26-million)"},
                        "from": {"type": "string", "description": "Start here: a message id (the <id> of a #m-<id> link) or an ISO time"},
                        "limit": {"type": "number", "description": "Turns at most (default 60, max 300)", "default": 60}
                    },
                    "required": ["slug"]
                }
            ),
            types.Tool(
                name="list_threads",
                description="List distinct conversation threads, most recently active first, with message count, first/last activity, participants, working directory, and a title hint from the first substantive human message. Use this before get_recent_work to see what separate things were happening instead of one interleaved stream.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "number", "description": "Maximum threads (default 20)", "default": 20},
                        "since": {"type": "string", "description": "Only threads with activity at or after this ISO timestamp"}
                    }
                }
            ),
            types.Tool(
                name="get_recent_work",
                description="Get the most recent N messages to understand current work. Pass thread_id (from list_threads) to read one thread alone.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "number", "description": "Number of messages (default 50)", "default": 50},
                        "thread_id": {"type": "string", "description": "Restrict to one thread, as returned by list_threads"}
                    }
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
        """Dispatch tool calls to appropriate handlers"""

        if name in CALLER_HANDLERS:
            return await CALLER_HANDLERS[name](arguments, caller(server.request_context.request))

        if name not in TOOL_HANDLERS:
            raise ValueError(f"Unknown tool: {name}")

        handler = TOOL_HANDLERS[name]

        # Call handler - they all accept arguments dict (or None)
        if name == "get_latest_continuation":
            return await handler()
        else:
            return await handler(arguments)

    return server


async def run_mcp_server(port=8000):
    """Run the MCP server on HTTP/SSE"""
    logger.info("Starting MCP server initialization...")

    server = create_mcp_server()
    logger.info("MCP Server instance created")

    # Create session manager for HTTP transport
    session_manager = StreamableHTTPSessionManager(server)
    logger.info("HTTP session manager created")

    # Create ASGI app wrapper
    async def mcp_asgi_app(scope, receive, send):
        if scope["type"] == "http":
            logger.info(f"Received MCP HTTP request: {scope['method']} {scope['path']}")
        try:
            await session_manager.handle_request(scope, receive, send)
        except Exception as e:
            logger.error(f"Error in MCP handler: {e}", exc_info=True)
            raise

    app = Starlette(
        routes=[
            Mount("/sse", app=mcp_asgi_app),
            Mount("/", app=mcp_asgi_app),
        ]
    )
    logger.info(f"Starlette app created, starting server on port {port}")

    # Run server
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
    server_instance = uvicorn.Server(config)
    logger.info(f"Starting uvicorn server on http://0.0.0.0:{port}")

    async with session_manager.run():
        await server_instance.serve()
