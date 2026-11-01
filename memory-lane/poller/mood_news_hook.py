"""Claude Code's PostToolUse hook for a Mood turn: hands the model what was said in its Mood while it works.

The runner (MoodPoller.news_check, ClaudeCodeWaker.watch) writes a nudge to the file named on this hook's
command line; after each tool call Claude Code runs this, which takes the file (so each nudge is told once)
and returns it as additionalContext, read with the tool's result. `-p` has no stdin to steer into, as Pi's
RPC mode has; a hook's output is the one way in mid-turn. No file, nothing said, and the turn goes on.
"""
import json
import os
import sys


def main(path):
    taken = f'{path}.taken'
    try:
        os.replace(path, taken)  # atomic: a nudge written meanwhile waits for the next tool call
    except OSError:
        return 0
    try:
        with open(taken, encoding='utf-8') as f:
            news = f.read().strip()
    finally:
        try:
            os.unlink(taken)
        except OSError:
            pass
    if news:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': news}}))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]) if len(sys.argv) > 1 else 0)
