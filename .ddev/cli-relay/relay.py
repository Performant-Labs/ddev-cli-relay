#!/usr/bin/env python3
#ddev-generated
"""CLI Relay — forwards HTTP requests to local CLI tools via subprocess.

Accepts POST /execute requests with a JSON body:
  { "binary": "/path/to/binary", "args": [...], "prompt": "...", "timeout": 120 }

The prompt is passed to the process via stdin (never as a shell argument).
Returns { "stdout": "...", "stderr": "...", "exit_code": 0 } as JSON.

GET /health returns { "status": "ok", "port": <PORT> }.

Listens on 0.0.0.0 so host.docker.internal can reach it from inside a container.
Port defaults to 8765; set AI_RELAY_PORT env var to override.
"""

import http.server
import json
import os
import subprocess
import sys

PORT = int(os.environ.get('AI_RELAY_PORT', 8765))


class RelayHandler(http.server.BaseHTTPRequestHandler):
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
            self.send_json(200, {'status': 'ok', 'port': PORT})
        else:
            self.send_json(404, {'error': 'Not found'})

    def do_POST(self):  # noqa: N802
        if self.path != '/execute':
            self.send_json(404, {'error': 'Not found'})
            return

        length = int(self.headers.get('Content-Length', 0))
        try:
            params = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'Invalid JSON'})
            return

        binary  = params.get('binary', '').strip()
        args    = params.get('args', [])
        prompt  = params.get('prompt', '')
        timeout = int(params.get('timeout', 120))

        if not binary:
            self.send_json(400, {'error': 'binary is required'})
            return

        try:
            result = subprocess.run(
                [binary] + (args if isinstance(args, list) else []),
                input=prompt.encode('utf-8'),
                capture_output=True,
                timeout=timeout,
            )
            self.send_json(200, {
                'stdout':    result.stdout.decode('utf-8', errors='replace'),
                'stderr':    result.stderr.decode('utf-8', errors='replace'),
                'exit_code': result.returncode,
            })
        except subprocess.TimeoutExpired:
            self.send_json(200, {'stdout': '', 'stderr': 'Timed out', 'exit_code': 124})
        except FileNotFoundError:
            self.send_json(200, {'stdout': '', 'stderr': f'Binary not found: {binary}', 'exit_code': 127})
        except Exception as exc:  # noqa: BLE001
            self.send_json(200, {'stdout': '', 'stderr': str(exc), 'exit_code': 1})


if __name__ == '__main__':
    server = http.server.HTTPServer(('0.0.0.0', PORT), RelayHandler)
    print(f'CLI Relay listening on 0.0.0.0:{PORT}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
