#!/usr/bin/env bash
#ddev-generated
# Starts the CLI Relay server in the background on the host.
# Called automatically by DDEV's post-start hook.
#
# Launches two instances:
#   1. TCP on 0.0.0.0:$PORT — for host-side testing (curl localhost:8765/health)
#   2. Unix socket at .ddev/cli-relay/relay.sock — for container access via
#      the socat sidecar (required on Linux where host.docker.internal is
#      unreachable from custom Docker networks)

PORT="${AI_RELAY_PORT:-8765}"
SOCKET_PATH="${DDEV_APPROOT}/.ddev/cli-relay/relay.sock"
TCP_PIDFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}-tcp.pid"
UNIX_PIDFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}-unix.pid"
TCP_LOGFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}-tcp.log"
UNIX_LOGFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}-unix.log"

start_instance() {
  local pidfile="$1"
  local logfile="$2"
  shift 2

  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "CLI Relay already running (PID $(cat "$pidfile"), log: $logfile)"
    return
  fi

  nohup "$@" > "$logfile" 2>&1 &
  echo $! > "$pidfile"
  echo "CLI Relay started (PID $!, log: $logfile)"
}

# Require python3.
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is not installed. CLI Relay requires Python 3." >&2
  exit 1
fi

rm -f "$SOCKET_PATH"

start_instance \
  "$TCP_PIDFILE" \
  "$TCP_LOGFILE" \
  env AI_RELAY_PORT="$PORT" python3 "${DDEV_APPROOT}/.ddev/cli-relay/relay.py"

start_instance \
  "$UNIX_PIDFILE" \
  "$UNIX_LOGFILE" \
  env AI_RELAY_SOCKET="$SOCKET_PATH" python3 "${DDEV_APPROOT}/.ddev/cli-relay/relay.py"
