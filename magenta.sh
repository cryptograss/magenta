#!/bin/bash
# magenta.sh — connect to a Docker container and start (or attach) a Claude Code
# session inside tmux. Supports local containers, hunter VPS, named parallel
# sessions in one container, and joining another user's shared session.
#
# Session model:
#   * The tmux session provides SSH-disconnect-survival and multiplayer support.
#   * Inside tmux, we invoke `claude` in one of four modes (see MODES below).
#   * The Claude Code supervisor (`claude agents`) tracks conversations across
#     restarts by short-ID and name. `--session foo` looks up the Claude session
#     named `foo`, attaches to it, or creates it fresh if none exists.

set -e  # Exit on error

# ─── help text ─────────────────────────────────────────────────────────
show_help() {
    cat <<'EOF'
Usage: magenta.sh [TARGET] [OPTIONS]
       magenta.sh login [--key <path>] [--base <url>]
       magenta.sh kick <name> --all [--ban] | --device <id>   |   magenta.sh unban <name>
       magenta.sh AZ5 [TARGET]          |   magenta.sh AZ5 --lift
       magenta.sh recovery [--fresh] [--openrouter | --subscription]

Connect to a container and drop into a tmux + Claude Code session.

TARGETS:
  local                 Local Docker container (default). SSH to localhost:2222.
  hunter                Hunter VPS. SSH via sshrouter@hunter.cryptograss.live;
                        the route-ssh script routes to your container by SSH key.

MODES (mutually exclusive; last one on the line wins):
  (no mode flag)        DEFAULT. Drop into the Claude session picker
                        (`claude agents`). Arrow to a session and hit Enter
                        to attach. If your target session isn't in the default
                        list (idle sessions get hidden in some states), type
                        `/resume` in the dispatch input to see the full list.
  --session <name>      Attach to the Claude session named <name>. If no such
                        session exists, create it fresh with the reawaken prompt
                        under that name. The tmux session is also named <name>.
  --force-fresh         Kill the existing tmux session (if any) and start a
                        fresh Claude conversation with the reawaken prompt.
                        If combined with --session, the new session gets that
                        name; otherwise it lives under 'magenta'.
  --continue            Old behavior — `claude --continue` in the tmux session.
                        This picks the most-recently-modified conversation in
                        the cwd, which can be wrong when multiple sessions run
                        in parallel. Kept as an escape hatch for when the
                        supervisor is misbehaving.

SUBCOMMANDS:
  login [args...]       Get a one-time link to write into Moods (memory-lane's
                        public conversation view) from any device, vouched for
                        by your SSH key; the key says who you are. Runs
                        memory-lane's tools/mood_login.py (fetched fresh from
                        GitHub; set MOOD_LOGIN_SCRIPT to a local copy to use
                        that instead). Args pass straight through (e.g. --key
                        for a non-default key); `magenta.sh login --help` shows
                        them. Needs no TARGET and opens no SSH connection.
  renew <device>        Bring one of your timed-out devices back (a device
                        unused for 30 days times out), by the name you gave
                        it when it signed in. Signed with your SSH key, like
                        login; no new link needed.
  attest "<words>"      Post a statement into #general, signed with your SSH
                        key. Its proof (what was signed, the signature, your
                        key) is kept beside it, checkable with ssh-keygen
                        anywhere, memory-lane or not.
  kick <name> --all     Sign <name> out of Moods everywhere: every device
                        and every live login link of theirs. With --ban,
                        their key can't sign in again until `unban`. For a
                        leaked link or a stolen key.
  kick <name> --device <id>
                        Sign out just one of their devices -- a stolen phone,
                        say. <id> is the start of its id: in Moods, your name
                        → your devices → everyone, then click it to copy the
                        whole command. Either kick needs an admin's key
                        (memory-lane's MOOD_ADMINS); signed like login.
  unban <name>          Let <name> sign in again.
  AZ5 [TARGET]          The scram. Signs everyone out of Moods, locks them
                        (no posting, no signing in) and stills every agent
                        runner -- then drops you into the session picker on
                        TARGET (default: hunter), as `magenta.sh hunter` does.
                        Undo with `magenta.sh AZ5 --lift`.
  recovery [--fresh] [--openrouter | --subscription]
                        For when you can't reach magent at all: no Mood
                        answers, your container won't let you in, Docker
                        itself is down. Opens Claude Code -- magent, with its
                        memory -- on hunter's host, outside every container, in
                        a tmux session that outlives the connection. It can
                        read the journal, start containers and restart dockerd;
                        it looks first and asks before it touches.
                        Two ways to the model, each its own session, in case
                        one of them is part of the trouble: --openrouter
                        (Anthropic's models on OpenRouter's credits; the
                        default) or --subscription (the Claude subscription).
                        Opening one never closes the other. --fresh starts the
                        chosen one over. Only Justin's key opens it
                        (maybelle-config hunter/ansible/roles/recovery). Plain
                        ssh, not mosh; MAGENTA_RECOVERY_HOST overrides the host
                        (say, 5.78.83.4 when DNS is the trouble).

OTHER OPTIONS:
  --dangerously-skip-permissions
                        Pass through to `claude` — bypass permission prompts.
                        Only takes effect at process start; can't flip mid-session.
                        Note: attaching to an already-running session inherits
                        that session's original permission mode, so this flag
                        may not have any effect until the session is respawned.
  --join <user>         Join another user's container in multiplayer/pair-programming
                        mode. Only valid with TARGET=hunter. Combine with
                        --session <name> to attach to a specific tmux session
                        in their container.
  -h, --help            Show this help and exit.

EXAMPLES:
  magenta.sh                                  # local, session picker
  magenta.sh hunter                           # hunter, session picker
  magenta.sh hunter --session pickipedia      # attach (or create) the 'pickipedia' session
  magenta.sh hunter --session ticketstubs     # ...and separately 'ticketstubs'
  magenta.sh hunter --force-fresh             # fresh Claude, generic 'magenta' name
  magenta.sh hunter --session foo --force-fresh
                                              # kill+recreate: fresh 'foo' session
  magenta.sh hunter --continue                # legacy: --continue in tmux
  magenta.sh hunter --join skyler             # pair-program with skyler
  magenta.sh hunter --join skyler --session review
                                              # join skyler's 'review' tmux session
  magenta.sh login                            # link to write into Moods
  magenta.sh login --key ~/.ssh/other_key     # ...signed with a non-default key
  magenta.sh kick skyler --all                # sign skyler out of Moods everywhere
  magenta.sh kick skyler --all --ban          # ...and bar their key until unbanned
  magenta.sh kick skyler --device 21c74117    # sign out just that one device of theirs
  magenta.sh AZ5                              # everyone out, everything still, then the picker
  magenta.sh AZ5 --lift                       # back to normal
  magenta.sh recovery                         # nothing answers: magent on hunter's host
  magenta.sh recovery --subscription          # ...on the subscription, not OpenRouter

MENTAL MODEL:
  * tmux session name = what shows in `tmux list-sessions`
  * Claude session name = what shows in `claude agents`
  * When you use --session <name>, both get that name. Kept in sync on purpose:
    one name to think about, not two.
  * `claude agents` (the default when no mode flag) lets you pick any Claude
    session regardless of tmux state. Handy after a container restart.

ENVIRONMENT:
  Reads magenta/.env if present. GH_TOKEN and POSTGRES_PASSWORD are forwarded
  to the remote session.

LOG FILE:
  Setup-phase output (mode dispatch, session lookup, claude --bg output,
  the tmux command about to run) is appended to ~/magenta.log inside the
  container. Useful when mosh quits after a fast tmux exit and you want
  to see what actually happened. Also readable by any claude session
  running with $HOME as cwd.
EOF
}

# ─── Moods subcommands: login, kick, unban, AZ5 ──────────────────────
# Handled before anything else: no TARGET, no SSH (until AZ5's picker). The
# logic lives in one place, memory-lane's tools/; we fetch and run it rather
# than vendoring a copy that would drift.
MEMORY_LANE_TOOLS="https://raw.githubusercontent.com/jMyles/memory-lane/main/tools"

# run_mood_tool <tool.py> <env var naming a local copy> [args...]
run_mood_tool() {
    local tool="$1" override_var="$2"
    shift 2
    if ! command -v python3 &> /dev/null; then
        echo "Error: this needs python3"
        exit 1
    fi
    local script="${!override_var}"
    if [ -n "$script" ]; then
        if [ ! -r "$script" ]; then
            echo "Error: $override_var='$script' is not a readable file"
            exit 1
        fi
    else
        if ! command -v curl &> /dev/null; then
            echo "Error: this needs curl (or set $override_var to a local $tool)"
            exit 1
        fi
        # Global, not local: the EXIT trap fires after this function returns.
        MOOD_TOOL_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/mood_tool.XXXXXX")"
        trap 'rm -rf "$MOOD_TOOL_TMPDIR"' EXIT
        script="$MOOD_TOOL_TMPDIR/$tool"
        if ! curl -fsSL "$MEMORY_LANE_TOOLS/$tool" -o "$script"; then
            echo "Error: could not download $MEMORY_LANE_TOOLS/$tool"
            echo "       (set $override_var to a local memory-lane checkout's tools/$tool)"
            exit 1
        fi
    fi
    python3 "$script" "$@"
}

case "${1:-}" in
    login)
        shift
        run_mood_tool mood_login.py MOOD_LOGIN_SCRIPT "$@"
        exit $?
        ;;
    renew)
        shift
        run_mood_tool mood_login.py MOOD_LOGIN_SCRIPT renew "$@"
        exit $?
        ;;
    attest)
        shift
        run_mood_tool mood_login.py MOOD_LOGIN_SCRIPT attest "$@"
        exit $?
        ;;
    kick)
        shift
        run_mood_tool mood_admin.py MOOD_ADMIN_SCRIPT kick "$@"
        exit $?
        ;;
    unban)
        shift
        run_mood_tool mood_admin.py MOOD_ADMIN_SCRIPT unban "$@"
        exit $?
        ;;
    recovery)
        shift
        # Straight to hunter's host as `recovery`, whose key only opens the
        # recovery session. It takes plain words: "fresh" starts the session
        # over; "openrouter" or "subscription" picks the way to the model, each
        # its own session. Needs nothing else of this script.
        RECOVERY_WORDS=()
        RECOVERY_ROUTE=""
        for arg in "$@"; do
            case "$arg" in
                --fresh) RECOVERY_WORDS+=(fresh) ;;
                --openrouter|--subscription)
                    if [ -n "$RECOVERY_ROUTE" ] && [ "$RECOVERY_ROUTE" != "${arg#--}" ]; then
                        echo "Usage: magenta.sh recovery [--fresh] [--openrouter | --subscription]"; exit 1
                    fi
                    RECOVERY_ROUTE="${arg#--}" ;;
                *) echo "Usage: magenta.sh recovery [--fresh] [--openrouter | --subscription]"; exit 1 ;;
            esac
        done
        [ -n "$RECOVERY_ROUTE" ] && RECOVERY_WORDS+=("$RECOVERY_ROUTE")
        exec ssh -t "recovery@${MAGENTA_RECOVERY_HOST:-hunter.cryptograss.live}" "${RECOVERY_WORDS[@]}"
        ;;
    AZ5|az5)
        shift
        if [ "${1:-}" = "--lift" ]; then
            shift
            run_mood_tool mood_admin.py MOOD_ADMIN_SCRIPT lift "$@"
            exit $?
        fi
        run_mood_tool mood_admin.py MOOD_ADMIN_SCRIPT az5
        echo "Dropping you into the session picker…"
        # Then just as `magenta.sh hunter` (or the TARGET given): pick a session.
        exec "$0" "${@:-hunter}"
        ;;
esac

# ─── argument parsing ──────────────────────────────────────────────────
FORCE_FRESH=false
DANGEROUSLY_SKIP_PERMISSIONS=false
JOIN_USER=""
SESSION_NAME=""
SESSION_NAME_EXPLICIT=false
CONTINUE_MODE=false
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        --force-fresh)
            FORCE_FRESH=true
            shift
            ;;
        --continue)
            CONTINUE_MODE=true
            shift
            ;;
        --dangerously-skip-permissions)
            DANGEROUSLY_SKIP_PERMISSIONS=true
            shift
            ;;
        --join)
            JOIN_USER="$2"
            shift 2
            ;;
        --session)
            SESSION_NAME="$2"
            SESSION_NAME_EXPLICIT=true
            shift 2
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# Default target = local; default tmux name = magenta.
TARGET="${POSITIONAL_ARGS[0]:-local}"
SESSION_NAME="${SESSION_NAME:-magenta}"

# ─── mosh check (remote targets only) ──────────────────────────────────
USE_MOSH=false
if [ "$TARGET" != "local" ]; then
    if command -v mosh &> /dev/null; then
        USE_MOSH=true
    else
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "⚠️  Mosh not found (optional)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "Mosh provides persistent SSH connections that survive network changes."
        echo "Install it for better connection reliability:"
        echo ""
        echo "  macOS:   brew install mosh"
        echo "  Ubuntu:  sudo apt install mosh"
        echo "  Arch:    sudo pacman -S mosh"
        echo ""
        echo "Continuing with regular SSH..."
        echo ""
    fi
fi

# ─── target resolution ────────────────────────────────────────────────
case "$TARGET" in
    local)
        SSH_HOST="localhost"
        SSH_PORT="2222"
        SSH_USER="magent"
        HOST_KEY_ID="[localhost]:2222"
        ;;
    hunter)
        SSH_HOST="hunter.cryptograss.live"
        SSH_PORT="22"
        SSH_USER="sshrouter"
        HOST_KEY_ID="hunter.cryptograss.live"
        ;;
    *)
        echo "Error: unknown target '$TARGET'"
        echo ""
        show_help
        exit 1
        ;;
esac

# ─── mode determination (local side) ──────────────────────────────────
# Precedence: --continue > --force-fresh > --session > default (picker)
if [ "$CONTINUE_MODE" = "true" ]; then
    MODE="continue"
elif [ "$FORCE_FRESH" = "true" ]; then
    MODE="fresh"
elif [ "$SESSION_NAME_EXPLICIT" = "true" ]; then
    MODE="named"
else
    MODE="picker"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Connecting to: $TARGET ($SSH_USER@$SSH_HOST:$SSH_PORT)"
echo "   tmux session:  $SESSION_NAME"
echo "   claude mode:   $MODE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ─── host key check ────────────────────────────────────────────────────
if ssh-keygen -F "$HOST_KEY_ID" > /dev/null 2>&1; then
    TTY_FLAG=""
    if [ "$TARGET" = "hunter" ]; then
        TTY_FLAG="-t"
    fi

    if ! ssh $TTY_FLAG -o StrictHostKeyChecking=yes -o BatchMode=yes -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" exit 2>/dev/null; then
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "⚠️  SSH HOST KEY HAS CHANGED"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "This usually happens when the container/server has been rebuilt."
        echo ""
        read -p "Has the $TARGET been rebuilt? (y/n): " -n 1 -r
        echo ""

        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "Removing old host key and adding new one..."
            ssh-keygen -R "$HOST_KEY_ID" 2>/dev/null
            echo "Connecting to accept new host key..."
            ssh $TTY_FLAG -o StrictHostKeyChecking=accept-new -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" exit
            echo "✓ Host key updated successfully!"
            echo ""
        else
            echo "⚠️  Proceeding anyway with StrictHostKeyChecking=no"
            echo "   (Security warning: This bypasses host verification!)"
            echo ""
        fi
    fi
fi

# ─── env forwarding ────────────────────────────────────────────────────
ENV_FILE="$(dirname "$0")/.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

# ─── remote command ────────────────────────────────────────────────────
# Runs on the container side. The MODE dispatch happens here because the
# Claude session lookup (agents --json) has to run where claude lives.
REMOTE_COMMAND="
    export GH_TOKEN='$GH_TOKEN'
    export POSTGRES_PASSWORD='$POSTGRES_PASSWORD'
    FORCE_FRESH='$FORCE_FRESH'
    DANGEROUSLY_SKIP_PERMISSIONS='$DANGEROUSLY_SKIP_PERMISSIONS'
    SESSION_NAME='$SESSION_NAME'
    MODE='$MODE'

    # ─── logging ──────────────────────────────────────────────────────
    # Log setup-phase output to a persistent file so failures don't
    # disappear when mosh quits after tmux exits. Location is
    # ~/magenta.log inside the container — readable by both the user
    # and any claude session running in ~. Tee everything through log_line
    # so it appears on the terminal AND lands in the log.
    LOG_FILE=\"\$HOME/magenta.log\"
    log_line() {
        # Timestamp, then message, to both stdout and log.
        local ts stamp
        ts=\$(date -Iseconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)
        stamp=\"[\$ts]\"
        # Terminal: unstamped for readability. Log: stamped.
        echo \"\$@\"
        echo \"\$stamp \$*\" >> \"\$LOG_FILE\"
    }
    log_line \"=== magenta.sh session=\$SESSION_NAME mode=\$MODE fresh=\$FORCE_FRESH ===\"

    CLAUDE_FLAGS=''
    if [ \"\$DANGEROUSLY_SKIP_PERMISSIONS\" = 'true' ]; then
        CLAUDE_FLAGS='--dangerously-skip-permissions'
    fi

    # ─── tmux session: attach if exists (unless force-fresh) ──────────
    if tmux has-session -t \"\$SESSION_NAME\" 2>/dev/null; then
        if [ \"\$FORCE_FRESH\" = 'true' ]; then
            log_line \"⚠️  --force-fresh: Killing existing tmux session: \$SESSION_NAME\"
            tmux kill-session -t \"\$SESSION_NAME\"
        else
            log_line \"🔄 Attaching to existing tmux session: \$SESSION_NAME\"
            tmux attach-session -t \"\$SESSION_NAME\"
            exit 0
        fi
    fi

    # ─── mode-specific inner command ──────────────────────────────────
    # Build INNER (the command that will run inside the new tmux session)
    # based on MODE. Uses python3 (always present on the container) for
    # JSON parsing rather than jq (not guaranteed).
    lookup_claude_session_id() {
        local target_name=\"\$1\"
        claude agents --json --all 2>/dev/null | python3 -c \"
import sys, json
try:
    for s in json.load(sys.stdin):
        if s.get('name') == '\$target_name':
            print(s.get('id', ''))
            break
except Exception:
    pass
\" 2>/dev/null
    }

    case \"\$MODE\" in
        picker)
            INNER=\"cd ~ && claude agents \$CLAUDE_FLAGS\"
            ;;
        fresh)
            INNER=\"cd ~ && claude \$CLAUDE_FLAGS 'reawaken magent'\"
            ;;
        named)
            SESS_ID=\$(lookup_claude_session_id \"\$SESSION_NAME\")
            if [ -n \"\$SESS_ID\" ]; then
                log_line \"→ Found Claude session '\$SESSION_NAME' (id \$SESS_ID). Attaching.\"
                INNER=\"cd ~ && claude attach \$SESS_ID\"
            else
                log_line \"→ No Claude session named '\$SESSION_NAME' — creating one\"
                log_line \"─── claude --bg output ───────────────────────────────────\"
                # --bg registers a new session under the supervisor. Tee its
                # combined output so failures are visible on the terminal AND
                # preserved in the log for post-mortem inspection.
                claude \$CLAUDE_FLAGS --bg --name \"\$SESSION_NAME\" 'reawaken magent' 2>&1 \\
                    | tee -a \"\$LOG_FILE\"
                BG_STATUS=\${PIPESTATUS[0]}
                log_line \"─── (claude --bg exit \$BG_STATUS) ────────────────────────\"
                sleep 3
                SESS_ID=\$(lookup_claude_session_id \"\$SESSION_NAME\")
                if [ -n \"\$SESS_ID\" ]; then
                    log_line \"→ Created and attaching (id \$SESS_ID)\"
                    INNER=\"cd ~ && claude attach \$SESS_ID\"
                else
                    log_line \"\"
                    log_line \"⚠️  Session '\$SESSION_NAME' was not registered after --bg attempt.\"
                    log_line \"    Full log at \$LOG_FILE\"
                    log_line \"    Dropping into a shell so you can investigate.\"
                    log_line \"\"
                    # Don't tmux+claude here — that just eats the error. Give the
                    # user a shell in tmux so they can retry manually.
                    INNER=\"cd ~ && echo 'session create failed; see \$LOG_FILE' && bash\"
                fi
            fi
            ;;
        continue)
            # Legacy: --continue with reawaken fallback if no conversation
            # to continue. Kept as escape hatch when supervisor misbehaves.
            INNER=\"cd ~ && (
                if ! claude \$CLAUDE_FLAGS --continue 2>/tmp/claude_error.log; then
                    if grep -iq 'no conversation found to continue' /tmp/claude_error.log; then
                        echo 'No conversation found; starting fresh.'
                        claude \$CLAUDE_FLAGS 'reawaken magent'
                    else
                        echo 'Claude Code failed. See /tmp/claude_error.log'
                        read -p 'Press Enter to close...'
                    fi
                    rm -f /tmp/claude_error.log
                fi
            )\"
            ;;
    esac

    log_line \"✨ Creating new tmux session: \$SESSION_NAME\"
    log_line \"   INNER: \$INNER\"
    tmux new-session -s \"\$SESSION_NAME\" \"\$INNER\"
"

# ─── --join branch (hunter multiplayer) ────────────────────────────────
if [ -n "$JOIN_USER" ]; then
    if [ "$TARGET" != "hunter" ]; then
        echo "Error: --join is only supported with the hunter target"
        exit 1
    fi
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Joining ${JOIN_USER}'s container in multiplayer mode..."
    [ "$SESSION_NAME" != "magenta" ] && echo "(session: $SESSION_NAME)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    JOIN_CMD="$JOIN_USER"
    [ "$SESSION_NAME" != "magenta" ] && JOIN_CMD="$JOIN_USER $SESSION_NAME"
    ssh -o StrictHostKeyChecking=accept-new -t -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" "$JOIN_CMD"
    exit $?
fi

# ─── normal connection (mosh preferred) ────────────────────────────────
if [ "$USE_MOSH" = true ]; then
    mosh --predict=always --ssh="ssh -p $SSH_PORT" "$SSH_USER@$SSH_HOST" -- bash -c "$REMOTE_COMMAND"
else
    ssh -o StrictHostKeyChecking=accept-new -t -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" "$REMOTE_COMMAND"
fi
