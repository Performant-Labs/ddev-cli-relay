#!/usr/bin/env bash
#ddev-generated
# Starts the CLI Relay server in the background on the host, and checks that
# it actually came up. Called automatically by DDEV's post-start hook.

RELAY_DIR="${DDEV_APPROOT}/.ddev/cli-relay"
PORT="${AI_RELAY_PORT:-8765}"
PIDFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}.pid"
LOGFILE="/tmp/ddev-cli-relay-${DDEV_SITENAME}.log"
TOKEN_FILE="${RELAY_DIR}/token"
ALLOWLIST_FILE="${RELAY_DIR}/allowed-binaries.txt"

# Don't start if already running.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "CLI Relay already running (PID $(cat "$PIDFILE")) on port $PORT"
  exit 0
fi

# Require Python 3.10 or newer. macOS's /usr/bin/python3 is 3.9, so Mac users
# need a newer python3 (e.g. Homebrew's) first on PATH.
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is not installed. CLI Relay requires Python 3.10 or newer." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "ERROR: CLI Relay requires Python 3.10 or newer; python3 on PATH is $(python3 --version 2>&1). On macOS, install one with: brew install python" >&2
  exit 1
fi

# Shared secret. Drupal reads the same file from inside the container
# (/var/www/html/.ddev/cli-relay/token). Created once, kept across restarts.
"${RELAY_DIR}/ensure-token.sh" || exit 1

# Returns success if the given IPv4 address belongs to this machine.
is_local_address() {
  python3 - "$1" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
try:
    s.bind((sys.argv[1], 0))
except OSError:
    sys.exit(1)
PY
}

# Bind address. Never listen on every interface by default: that exposes the
# relay to anyone on the same network.
#  - macOS (Docker Desktop, OrbStack, Colima): host.docker.internal is
#    forwarded to the host's loopback, so 127.0.0.1 is enough.
#  - Elsewhere: bind to whatever host.docker.internal resolves to inside the
#    web container, if that address belongs to this machine; otherwise the
#    DDEV network gateway.
# Set AI_RELAY_BIND to override.
BIND="${AI_RELAY_BIND:-}"
if [ -z "$BIND" ]; then
  if [ "$(uname -s)" = "Darwin" ]; then
    BIND="127.0.0.1"
  else
    for CANDIDATE in \
      "$(docker exec "ddev-${DDEV_SITENAME}-web" getent ahostsv4 host.docker.internal 2>/dev/null | awk '{print $1; exit}')" \
      "$(docker network inspect ddev_default -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null | head -n1)"; do
      if [ -n "$CANDIDATE" ] && is_local_address "$CANDIDATE"; then
        BIND="$CANDIDATE"
        break
      fi
    done
    if [ -z "$BIND" ]; then
      echo "ERROR: could not find a local address that containers use to reach this host." >&2
      echo "       Set AI_RELAY_BIND to it (not 0.0.0.0) and run 'ddev cli-relay:start'." >&2
      exit 1
    fi
  fi
fi

AI_RELAY_PORT="$PORT" \
AI_RELAY_BIND="$BIND" \
AI_RELAY_TOKEN_FILE="$TOKEN_FILE" \
AI_RELAY_ALLOWLIST_FILE="$ALLOWLIST_FILE" \
  nohup python3 "${RELAY_DIR}/relay.py" > "$LOGFILE" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"

# Confirm that this project's relay is the one answering on the port. A port
# already used by another project's relay would otherwise look like success.
# http.client is used rather than urllib so system proxy settings are ignored.
if python3 - "$BIND" "$PORT" "$TOKEN_FILE" "$PID" <<'PY'
import hashlib, http.client, json, os, sys, time
host, port, token_file, pid = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
with open(token_file) as fh:
    expected = hashlib.sha256(fh.read().strip().encode()).hexdigest()[:12]
deadline = time.time() + 10
while time.time() < deadline:
    try:
        os.kill(pid, 0)
    except OSError:
        sys.exit(1)
    try:
        conn = http.client.HTTPConnection(host, port, timeout=1)
        conn.request('GET', '/health')
        body = json.loads(conn.getresponse().read() or b'{}')
        conn.close()
        if body.get('token_id') == expected:
            sys.exit(0)
    except (OSError, ValueError):
        pass
    time.sleep(0.1)
sys.exit(1)
PY
then
  echo "CLI Relay started on ${BIND}:${PORT} (PID $PID, log: $LOGFILE)"
  exit 0
fi

kill "$PID" 2>/dev/null
rm -f "$PIDFILE"
echo "ERROR: CLI Relay failed to start on ${BIND}:${PORT}." >&2
if grep -q -i "address already in use" "$LOGFILE" 2>/dev/null; then
  echo "       Port $PORT is already used, probably by another DDEV project's relay." >&2
  echo "       Give this project its own port: export AI_RELAY_PORT=<port> before 'ddev start'," >&2
  echo "       and set the relay URL in Drupal's CLI Provider settings to match." >&2
fi
echo "       Last lines of $LOGFILE:" >&2
tail -n 5 "$LOGFILE" >&2
exit 1
