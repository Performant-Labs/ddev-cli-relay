#!/usr/bin/env bash
#ddev-generated
# Starts the CLI Relay server in the background on the host.
# Called automatically by DDEV's post-start hook.

PORT="${AI_RELAY_PORT:-8765}"
PIDFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}.pid"
LOGFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}.log"

# Don't start if already running.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "CLI Relay already running (PID $(cat "$PIDFILE")) on port $PORT"
  exit 0
fi

# Require python3.
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is not installed. CLI Relay requires Python 3." >&2
  exit 1
fi

AI_RELAY_PORT="$PORT" nohup python3 "${DDEV_APPROOT}/.ddev/cli-relay/relay.py" \
  > "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
echo "CLI Relay started on port $PORT (PID $!, log: $LOGFILE)"
