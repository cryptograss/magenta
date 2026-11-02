"""Both names for an emergent at the edges, for the emergent rename's window (PickiPedia: User:Magent/Emergent rename).

memory-lane (on maybelle) and the pollers (on hunter) are deployed at different moments, so for one deploy each
side takes either name: a request may name an emergent as `emergent` or, as before the rename, `agent`. The
rename's last step drops `agent` here and in the poller's `either`.
"""


def named(source, default=None):
    """The emergent a request names, from its query string or JSON body: its `emergent`, else its `agent`."""
    source = source or {}
    return source.get('emergent') or source.get('agent') or default
