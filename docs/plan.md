# ddev-cli-relay — Implementation Plan

## Overview

`ddev-cli-relay` is a DDEV addon that starts a lightweight HTTP relay server on the **host machine** via DDEV lifecycle hooks. It allows code running inside a DDEV container to execute local CLI tools on the host by sending HTTP requests.

### Why this exists

CLI tools like `claude`, `codex`, and `gemini-cli` use browser-based OAuth authentication — they must run on the host where authentication already happened. PHP running inside a DDEV container cannot directly execute host binaries. DDEV's `.ddev/commands/host/` scripts are user-terminal-only (invoked via `ddev <command>`), and the `ddev` CLI itself is not available inside the web container.

The relay solves this with a simple HTTP server that:
1. Starts automatically when `ddev start` fires (via `exec-host` hook)
2. Accepts POST requests from inside the container at `http://host.docker.internal:8765/execute`
3. Executes the requested binary on the host with the prompt sent via stdin
4. Returns stdout, stderr, and exit code as JSON

### Why Python 3

The relay uses Python 3 with its standard library only — no `pip install`, no dependencies. Python 3 is pre-installed on macOS and virtually all Linux distributions, making it universally available without any setup.

---

## File Structure

Files live under `.ddev/` in the repo. `install.yaml` lists them with `.ddev/`-prefixed paths, and DDEV copies them to the project's `.ddev/` preserving directory structure. `config.cli-relay.yaml` is auto-discovered by DDEV and its hooks merged with the project's other config files.

```
~/Projects/ddev-cli-relay/          <- GitHub repo root
├── install.yaml                    <- DDEV addon manifest
├── docs/
│   └── plan.md                     <- this file
├── .ddev/
│   ├── config.cli-relay.yaml       <- lifecycle hooks (auto-discovered by DDEV)
│   ├── cli-relay/
│   │   ├── relay.py                <- Python 3 HTTP relay server (stdlib only)
│   │   ├── start.sh                <- start relay in background
│   │   └── stop.sh                 <- stop relay by PID
│   └── commands/host/
│       ├── cli-relay-start         <- ddev cli-relay:start
│       └── cli-relay-stop          <- ddev cli-relay:stop
└── README.md
```

---

## HTTP API

### `GET /health`

Liveness check. Used by consumers (e.g. the `ai_provider_cli` Drupal module) to verify the relay is running before attempting execution.

**Response:**
```json
{ "status": "ok", "port": 8765 }
```

### `POST /execute`

Execute a binary on the host. The prompt is passed to the process via **stdin** (never as a command-line argument).

**Request body:**
```json
{
  "binary":  "/usr/local/bin/claude",
  "args":    ["--print"],
  "prompt":  "User: Hello\n\nAssistant: Hi there",
  "timeout": 120
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `binary` | string | yes | Absolute path to the executable on the host |
| `args` | array | no | Additional CLI flags (e.g. `["--print"]`) |
| `prompt` | string | no | Text sent to the process via stdin |
| `timeout` | integer | no | Max seconds to wait (default: 120) |

**Response:**
```json
{
  "stdout":    "Hello! How can I help?",
  "stderr":    "",
  "exit_code": 0
}
```

**Error exit codes:**
- `124` — process timed out
- `127` — binary not found
- Other non-zero — process returned an error (check `stderr`)

---

## Components

### `install.yaml`

The DDEV addon manifest. Lists the files to copy into the project's `.ddev/` on `ddev add-on get`, and runs `chmod +x` on the scripts via `post_install_actions`.

Requires DDEV >= v1.24.0.

### `.ddev/config.cli-relay.yaml`

Defines two `exec-host` lifecycle hooks:
- `post-start` → runs `start.sh` after `ddev start`
- `post-stop` → runs `stop.sh` after `ddev stop`

`DDEV_APPROOT` is available in `exec-host` hooks and points to the project root on the host.

### `.ddev/cli-relay/relay.py`

The relay server. Single-threaded Python 3 HTTP server (stdlib `http.server`). Handles one request at a time. Uses `subprocess.run()` with `capture_output=True` — prompt is sent via `input=` (stdin), never passed as a shell argument.

Listens on `0.0.0.0` so `host.docker.internal` (the Docker-to-host routing address) can reach it from inside a container. Port defaults to `8765`, configurable via `AI_RELAY_PORT` env var.

### `.ddev/cli-relay/start.sh` / `stop.sh`

Start/stop scripts for the relay. Use `nohup` to run the relay in the background; track the PID in `/tmp/ddev-cli-relay-{DDEV_SITENAME}.pid` so multiple DDEV projects don't collide.

### `.ddev/commands/host/cli-relay-start` / `cli-relay-stop`

Manual control commands. Allow developers to run `ddev cli-relay:start` and `ddev cli-relay:stop` without restarting the full DDEV environment.

---

## Configuration

| Env var | Default | Description |
|---|---|---|
| `AI_RELAY_PORT` | `8765` | Port the relay listens on |
| `AI_RELAY_HOST` | `0.0.0.0` | Interface the relay binds to |

---

## Installation

```bash
# From inside a DDEV project:
ddev add-on get Performant-Labs/ddev-cli-relay
ddev restart
# The relay starts automatically as part of ddev restart.
```

---

## Testing Checklist

All of these must pass before the relay is considered ready for use by `ai_provider_cli`.

```bash
# 1. Relay starts on ddev restart
ddev restart
# Expect: "CLI Relay started on port 8765 (PID ...)" in output

# 2. Health check from the host
curl http://localhost:8765/health
# Expect: {"status":"ok","port":8765}

# 3. Health check from inside the container
ddev exec curl http://host.docker.internal:8765/health
# Expect: {"status":"ok","port":8765}

# 4. Stdin passthrough test (using /bin/cat to echo stdin back)
ddev exec curl -s -X POST http://host.docker.internal:8765/execute \
  -H 'Content-Type: application/json' \
  -d '{"binary":"/bin/cat","args":[],"prompt":"hello from stdin","timeout":5}'
# Expect: {"stdout":"hello from stdin","stderr":"","exit_code":0}

# 5. Real AI CLI tool (must be authenticated on host)
ddev exec curl -s -X POST http://host.docker.internal:8765/execute \
  -H 'Content-Type: application/json' \
  -d '{"binary":"/usr/local/bin/claude","args":["--print"],"prompt":"User: Say hello in one sentence","timeout":30}'
# Expect: {"stdout":"Hello! ...","stderr":"","exit_code":0}

# 6. Manual stop/start
ddev cli-relay:stop
curl http://localhost:8765/health        # should fail (connection refused)
ddev cli-relay:start
curl http://localhost:8765/health        # should succeed
```

---

## Edge Cases

| Scenario | Behaviour |
|---|---|
| Port already in use | Start fails with a clear error; configure `AI_RELAY_PORT` env var |
| `python3` not installed | `start.sh` prints an error and exits non-zero |
| Binary not on PATH | Return `exit_code: 127`, `stderr: "Binary not found: ..."` |
| Process times out | Return `exit_code: 124`, `stderr: "Timed out"` |
| Multiple DDEV projects | PID file is per-site (`ddev-cli-relay-{sitename}.pid`), avoid collisions |
| Linux `host.docker.internal` | DDEV sets this automatically; plain Docker requires `--add-host=host.docker.internal:host-gateway` |

---

## Security Notes

- The relay listens on `0.0.0.0` — required for `host.docker.internal` routing. **For development use only.** Do not expose the relay port to public networks.
- The binary path and args come from the POST body. This is admin/developer-controlled config, not user input — but the relay executes exactly what is requested. Treat access to port 8765 as equivalent to host shell access.
- The prompt is passed via stdin (`subprocess.run(..., input=...)`), never interpolated into a shell command or passed as a command-line argument.

---

## Related Projects

- [`ai_provider_cli`](https://github.com/Performant-Labs/pl-dcms-ai_provider) — Drupal AI provider module that uses this relay to pipe prompts to CLI tools from within a DDEV container.
