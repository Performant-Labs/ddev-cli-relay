#!/usr/bin/env python3
#ddev-generated
"""CLI Relay — lets code in a DDEV container run allowlisted CLI tools on the host.

POST /execute with a JSON body:
  { "binary": "claude", "args": [...], "prompt": "...", "timeout": 120 }

The prompt is passed to the process via stdin (never as a shell argument).
Returns { "stdout": "...", "stderr": "...", "exit_code": 0 } as JSON.

GET /health returns { "status": "ok", "port": <PORT>, "auth": "token",
"token_id": <first 12 hex chars of sha256(token)> }, so a client can tell
whether it reached its own project's relay.

Every /execute request must:
  - carry "Authorization: Bearer <token>", where the token is read from the
    file named by AI_RELAY_TOKEN_FILE (start.sh creates it);
  - use "Content-Type: application/json";
  - carry no Origin header, so a web page open in a browser cannot use it;
  - name a binary on the allowlist (see AI_RELAY_ALLOWED_BINARIES). Allowed
    names are resolved to absolute paths once, at startup, using only the
    absolute entries of PATH; the relay executes those resolved paths and
    never a path supplied by the caller.

Whoever holds the token chooses the arguments for the allowed tools, and
several of them accept flags that run arbitrary commands. Treat the token as
equivalent to a shell on the host.
The relay binds to AI_RELAY_BIND (start.sh picks a non-public address).
It refuses to start without a token.
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import shutil
import signal
import subprocess
import sys

DEFAULT_ALLOWED = (
    'claude', 'codex', 'gemini', 'gemini-cli', 'opencode', 'ollama', 'lms', 'llm',
)
MAX_BODY_BYTES = 10 * 1024 * 1024
MAX_TIMEOUT = 3600
# Seconds a client may take to send its request (not how long a tool may run).
SOCKET_TIMEOUT = 30


def parse_allowlist(env_value: str | None, file_path: str | None) -> list[str]:
    """Returns the allowed binary names/paths: defaults + env + file entries."""
    entries = list(DEFAULT_ALLOWED)
    if env_value:
        entries += [e.strip() for e in env_value.split(',')]
    if file_path and os.path.isfile(file_path):
        with open(file_path, encoding='utf-8') as fh:
            for line in fh:
                line = line.split('#', 1)[0].strip()
                if line:
                    entries.append(line)
    seen = []
    for entry in entries:
        if entry and entry not in seen:
            seen.append(entry)
    return seen


def safe_path(path_value: str | None) -> str:
    """Returns PATH with relative entries (".", "vendor/bin", "") removed."""
    entries = (path_value or '').split(os.pathsep)
    return os.pathsep.join(e for e in entries if os.path.isabs(e))


def resolve_allowlist(allowed: list[str], path_value: str | None) -> dict[str, str]:
    """Resolves allowlist entries to absolute paths, once.

    Returns a map from each accepted request value to the absolute path the
    relay will execute: an allowed bare name maps to where it is found on the
    (absolute-only) PATH; an explicit absolute entry maps to itself. Each
    executable's real path is also accepted as a request value, so a client
    may send the absolute path it resolved itself. Entries that are not found
    are skipped.
    """
    search = safe_path(path_value)
    resolved: dict[str, str] = {}
    for entry in allowed:
        if os.path.isabs(entry):
            target = entry if os.path.isfile(entry) and os.access(entry, os.X_OK) else None
        elif '/' in entry:
            target = None
        else:
            target = shutil.which(entry, path=search)
        if not target:
            continue
        target = os.path.abspath(target)
        resolved.setdefault(entry, target)
        resolved.setdefault(target, target)
        resolved.setdefault(os.path.realpath(target), target)
    return resolved


def resolve_binary(requested: str, resolved: dict[str, str]) -> str | None:
    """Returns the absolute path to execute for a request, or None if refused.

    The caller's string is only ever used as a lookup key: the returned path
    was fixed at startup, so swapping a symlink after the check cannot change
    what runs. Relative paths are always refused.
    """
    if not requested or (not os.path.isabs(requested) and '/' in requested):
        return None
    return resolved.get(requested)


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()[:12]


def read_token(path: str | None) -> str:
    if not path or not os.path.isfile(path):
        return ''
    with open(path, encoding='utf-8') as fh:
        return fh.read().strip()


def run_tool(binary: str, args: list[str], prompt: str, timeout: int) -> dict:
    """Runs one tool in its own process group and returns the relay response.

    On timeout the whole group is killed, so helpers the tool started (node
    workers, MCP servers) do not outlive the request.
    """
    env = dict(os.environ, PATH=safe_path(os.environ.get('PATH')))
    try:
        proc = subprocess.Popen(
            [binary] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.expanduser('~'),
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        return {'stdout': '', 'stderr': f'Binary not found: {binary}', 'exit_code': 127}
    except OSError as exc:
        return {'stdout': '', 'stderr': str(exc), 'exit_code': 1}

    try:
        out, err = proc.communicate(input=prompt.encode('utf-8'), timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return {'stdout': '', 'stderr': 'Timed out', 'exit_code': 124}

    return {
        'stdout':    out.decode('utf-8', errors='replace'),
        'stderr':    err.decode('utf-8', errors='replace'),
        'exit_code': proc.returncode,
    }


def make_handler(token: str, resolved: dict[str, str], port: int):
    expected = f'Bearer {token}'.encode('utf-8')
    health = {'status': 'ok', 'port': port, 'auth': 'token', 'token_id': token_id(token)}

    class RelayHandler(http.server.BaseHTTPRequestHandler):
        timeout = SOCKET_TIMEOUT

        def log_message(self, format, *args):  # noqa: A002
            print(f"[cli-relay] {format % args}", file=sys.stderr, flush=True)

        def send_json(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == '/health':
                self.send_json(200, health)
            else:
                self.send_json(404, {'error': 'Not found'})

        def do_POST(self):  # noqa: N802
            if self.path != '/execute':
                self.send_json(404, {'error': 'Not found'})
                return

            # Browsers always send Origin on cross-origin POSTs; server-side
            # callers (PHP, curl) do not. Refusing it blocks drive-by requests
            # from web pages even before the token check.
            if self.headers.get('Origin') is not None:
                self.send_json(403, {'error': 'Browser requests are not accepted'})
                return

            supplied = (self.headers.get('Authorization') or '').encode('utf-8')
            if not hmac.compare_digest(supplied, expected):
                self.send_json(401, {'error': 'Missing or invalid relay token'})
                return

            content_type = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if content_type != 'application/json':
                self.send_json(415, {'error': 'Content-Type must be application/json'})
                return

            try:
                length = int(self.headers.get('Content-Length', 0))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                self.send_json(413, {'error': 'Request body too large or invalid length'})
                return

            try:
                params = json.loads(self.rfile.read(length) or b'null')
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {'error': 'Invalid JSON'})
                return
            if not isinstance(params, dict):
                self.send_json(400, {'error': 'Body must be a JSON object'})
                return

            requested = params.get('binary', '')
            args = params.get('args', [])
            prompt = params.get('prompt', '')
            timeout = params.get('timeout', 120)

            if not isinstance(requested, str) or not requested.strip():
                self.send_json(400, {'error': 'binary is required'})
                return
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                self.send_json(400, {'error': 'args must be a list of strings'})
                return
            if not isinstance(prompt, str):
                self.send_json(400, {'error': 'prompt must be a string'})
                return
            if isinstance(timeout, bool) or not isinstance(timeout, int):
                self.send_json(400, {'error': 'timeout must be an integer'})
                return
            timeout = max(1, min(timeout, MAX_TIMEOUT))

            requested = requested.strip()
            binary = resolve_binary(requested, resolved)
            if binary is None:
                self.send_json(403, {
                    'error': f'Binary not allowed or not installed: {requested}. Add it to '
                             'AI_RELAY_ALLOWED_BINARIES or .ddev/cli-relay/allowed-binaries.txt '
                             'and restart the relay (ddev cli-relay:stop && ddev cli-relay:start).',
                })
                return

            self.send_json(200, run_tool(binary, args, prompt, timeout))

    return RelayHandler


def make_server(bind: str, port: int, token: str, allowed: list[str],
                path_value: str | None = None):
    """Builds the server. Allowed binaries are resolved here, once."""
    if not token:
        raise ValueError('A relay token is required')
    if path_value is None:
        path_value = os.environ.get('PATH')
    resolved = resolve_allowlist(allowed, path_value)
    server = http.server.ThreadingHTTPServer((bind, port), make_handler(token, resolved, port))
    server.daemon_threads = True
    server.resolved = resolved
    return server


def main() -> int:
    port = int(os.environ.get('AI_RELAY_PORT', 8765))
    bind = os.environ.get('AI_RELAY_BIND', '127.0.0.1')
    token = read_token(os.environ.get('AI_RELAY_TOKEN_FILE'))
    if not token:
        print('ERROR: no relay token. Set AI_RELAY_TOKEN_FILE to a file containing '
              'the token (start.sh does this).', file=sys.stderr, flush=True)
        return 1
    allowed = parse_allowlist(
        os.environ.get('AI_RELAY_ALLOWED_BINARIES'),
        os.environ.get('AI_RELAY_ALLOWLIST_FILE'),
    )
    server = make_server(bind, port, token, allowed)
    found = sorted({v for v in server.resolved.values()})
    print(f'CLI Relay listening on {bind}:{port}', flush=True)
    print(f'Allowed binaries found: {", ".join(found) or "(none)"}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
