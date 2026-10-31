"""What a look-only wake may touch, one place for both harnesses.

A look-only turn (no person signed in with a key behind the post that woke
it) may look but not touch: read and search the code, search its memory,
read PickiPedia. The separation from write tools is not by MCP server --
the wiki server carries both reads and writes -- but by tool name, in the
allowlist each harness gets. A tool not on the list is not merely denied:
it is not present (pi_harness builds Pi's `--tools` from here,
mood_poller.py's ALLOWED builds Claude Code's `--allowedTools`).

Deny beats allow: the list below is the whole of what a look-only turn may
call through MCP. Justin asked for this precisely (30 Oct): the wiki server
is shared, and the separation is by tool name in the harness's tool
selection -- reads enabled, writes absent, on both harnesses.
"""

import os

# The wiki server, as mcp.json names it, and its read tools as its MCP
# server names them (`pi mcp list`, 30 Oct: hyphens -- which is also the
# spelling Claude Code's allowlist already used). Its write tools --
# create-page, update-page, move-page, delete-page, undelete-page,
# protect-page, upload-*, update-file*, set-my-pronouns, oauth-logout,
# add-wiki -- are deliberately absent.
WIKI_MCP_SERVER = 'pickipedia'
WIKI_READ_TOOLS = (
    'get-page', 'get-pages', 'get-page-history', 'get-recent-changes',
    'get-revision', 'get-file', 'get-file-data', 'get-site-info',
    'get-category-members', 'get-links-here', 'search-page',
    'search-page-by-prefix', 'parse-wikitext', 'compare-pages', 'whoami',
    'oauth-status', 'smw-query', 'smw-list-properties',
)

# The record: loaded whole on a look-only turn. Its one write tool,
# `remember`, stays available: an agent may keep its own notes without
# being granted tools. Whether that is wanted is an open decision,
# recorded in Cryptograss:Agents/Wake modes (30 Oct).
MEMORY_MCP_SERVERS = ('magenta-memory-v2',)


def mcp_read_patterns():
    """The MCP tool entries a look-only turn may call, as each harness spells
    them: PickiPedia's reads by name; the memory server whole (Pi's `*`
    wildcard; Claude Code's bare server name is added by its own list)."""
    return [f'mcp__{WIKI_MCP_SERVER}__{name}' for name in WIKI_READ_TOOLS]


# Tool loop tripwire limits (shared by pi_harness and mood_poller):
# Attractor loops: identical tool call repeated 3 times, or 5 times in 10-call window.
# Turn call ceiling: 150 calls default (settable via TOOL_LOOP_MAX_CALLS env var).
TOOL_LOOP_KILL = 3
TOOL_LOOP_WINDOW = 10
TOOL_LOOP_WINDOW_KILL = 5
TOOL_LOOP_MAX_CALLS = int(os.environ.get('TOOL_LOOP_MAX_CALLS', '150'))

