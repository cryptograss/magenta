"""Which runner key speaks for which agent (settings.MOOD_RUNNER_KEYS).

Each agent's key is a variable of its own, MOOD_RUNNER_KEY_<AGENT>, so a
deploy writes one plain line per agent and nothing is assembled in a
template: a key may hold any character but a newline. The older single
variable, MOOD_RUNNER_KEYS=agent:key,agent2:key2, is still read, for a
deploy that predates them; where both name an agent, its own variable wins.
"""

PREFIX = 'MOOD_RUNNER_KEY_'


def runner_keys(environ):
    """{agent: key} from an environment."""
    keys = dict(pair.split(':', 1) for pair in environ.get('MOOD_RUNNER_KEYS', '').split(',') if ':' in pair)
    for name, value in environ.items():
        agent = name[len(PREFIX):].lower().replace('_', '-')
        if name.startswith(PREFIX) and agent and value.strip():
            keys[agent] = value.strip()
    return keys
