#!/usr/bin/env bash
#ddev-generated
# Creates the relay's shared-secret token file if it does not exist yet.
# Runs on the host (pre-start hook and start.sh). The file is readable only by
# the current user and is git-ignored.

TOKEN_FILE="${DDEV_APPROOT}/.ddev/cli-relay/token"

# Keep an existing token only if it is readable and has real content.
if [ -r "$TOKEN_FILE" ] && grep -q '[^[:space:]]' "$TOKEN_FILE" 2>/dev/null; then
  chmod 600 "$TOKEN_FILE"
  exit 0
fi
if [ -e "$TOKEN_FILE" ]; then
  echo "CLI Relay token at .ddev/cli-relay/token is empty or unreadable; replacing it."
  rm -f "$TOKEN_FILE" || { echo "ERROR: cannot replace $TOKEN_FILE" >&2; exit 1; }
fi

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is not installed. CLI Relay requires Python 3." >&2
  exit 1
fi

umask 077
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "${TOKEN_FILE}.tmp" \
  && mv "${TOKEN_FILE}.tmp" "$TOKEN_FILE" \
  && chmod 600 "$TOKEN_FILE" \
  && echo "CLI Relay token created at .ddev/cli-relay/token"
