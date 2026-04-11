#!/usr/bin/env bash
#ddev-generated
# Stops the CLI Relay server on the host.
# Called automatically by DDEV's post-stop hook.

PIDFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}.pid"

if [ -f "$PIDFILE" ]; then
  PID=$(cat "$PIDFILE")
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    rm -f "$PIDFILE"
    echo "CLI Relay stopped (was PID $PID)"
  else
    rm -f "$PIDFILE"
    echo "CLI Relay was not running (stale PID file removed)"
  fi
else
  echo "CLI Relay is not running"
fi
