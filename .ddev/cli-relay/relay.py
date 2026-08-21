#!/usr/bin/env python3
#ddev-generated
"""CLI Relay — forwards HTTP requests to local CLI tools via subprocess.

Accepts POST /execute requests with a JSON body:
  {
    "binary": "/path/to/binary",
    "args": [...],
    "prompt": "...",
    "timeout": 120,
    "files": [{ "base64": "...", "filename": "image.png" }]
  }

The prompt is passed to the process via stdin (never as a shell argument).
Any "files" are decoded to host temp files and appended to the args as
"-f <path>" pairs (for CLI tools that accept file attachments, e.g. opencode
vision input); the temp files are removed once the process completes.
Returns { "stdout": "...", "stderr": "...", "exit_code": 0 } as JSON.

GET /health returns { "status": "ok", "port": <PORT> }.

Listens on 0.0.0.0 so host.docker.internal can reach it from inside a container.
Port defaults to 8765; set AI_RELAY_PORT env var to override.
"""

import base64
import binascii
import http.server
import json
import os
import subprocess
import sys
import tempfile

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
        files   = params.get('files', [])

        if not binary:
            self.send_json(400, {'error': 'binary is required'})
            return

        run_args = args if isinstance(args, list) else []
        temp_paths = self.write_temp_files(files)
        for path in temp_paths:
            run_args = run_args + ['-f', path]

        try:
            result = subprocess.run(
                [binary] + run_args,
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
        finally:
            for path in temp_paths:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def write_temp_files(self, files):
        """Decodes base64 file payloads to host temp files, returns their paths."""
        paths = []
        if not isinstance(files, list):
            return paths
        for item in files:
            if not isinstance(item, dict):
                continue
            b64 = item.get('base64', '')
            if not b64:
                continue
            try:
                binary = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                continue
            filename = item.get('filename', '') or ''
            suffix = os.path.splitext(filename)[1] or '.png'
            fd, path = tempfile.mkstemp(prefix='ai_cli_img_', suffix=suffix)
            try:
                os.write(fd, binary)
            finally:
                os.close(fd)
            paths.append(path)
        return paths


if __name__ == '__main__':
    server = http.server.HTTPServer(('0.0.0.0', PORT), RelayHandler)
    print(f'CLI Relay listening on 0.0.0.0:{PORT}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
