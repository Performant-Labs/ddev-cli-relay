#!/usr/bin/env bash
#ddev-generated
# Stops the CLI Relay server on the host.
# Called automatically by DDEV's post-stop hook.

SOCKET_PATH="${DDEV_APPROOT}/.ddev/cli-relay/relay.sock"
TCP_PIDFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}-tcp.pid"
UNIX_PIDFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}-unix.pid"

stop_instance() {
  local pidfile="$1"
  local label="$2"

  if [ -f "$pidfile" ]; then
    PID=$(cat "$pidfile")
    if kill -0 "$PID" 2>/dev/null; then
      kill "$PID"
      echo "CLI Relay $label stopped (was PID $PID)"
    else
      echo "CLI Relay $label was not running (stale PID file removed)"
    fi
    rm -f "$pidfile"
  fi
}

stop_instance "$TCP_PIDFILE" "tcp"
stop_instance "$UNIX_PIDFILE" "unix"
rm -f "$SOCKET_PATH"
