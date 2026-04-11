# ddev-cli-relay

A DDEV addon that starts a lightweight HTTP relay server on the **host machine**, allowing code running inside a DDEV container to execute local CLI tools on the host.

## Why

CLI tools like `claude`, `codex`, and `gemini-cli` authenticate via browser OAuth — they must run on the host where auth already happened. PHP inside a DDEV container cannot exec host binaries directly. This addon bridges the gap with a simple HTTP server.

## Installation

```bash
ddev add-on get Performant-Labs/ddev-cli-relay
ddev restart
```

The relay starts automatically on `ddev start` and stops on `ddev stop`.

## Usage

**Health check** (from inside the container):
```bash
curl http://host.docker.internal:8765/health
# {"status":"ok","port":8765}
```

**Execute a CLI tool** (from inside the container):
```bash
curl -s -X POST http://host.docker.internal:8765/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "binary": "/usr/local/bin/claude",
    "args": ["--print"],
    "prompt": "User: Hello",
    "timeout": 30
  }'
# {"stdout":"Hello! How can I help?","stderr":"","exit_code":0}
```

The prompt is passed to the process via **stdin** — never as a shell argument.

## Manual control

```bash
ddev cli-relay-start   # start without restarting DDEV
ddev cli-relay-stop    # stop the relay
```

## Configuration

| Env var | Default | Description |
|---|---|---|
| `AI_RELAY_PORT` | `8765` | Port the relay listens on |

Set in your shell before `ddev start`, or export it in your shell profile.

## API

### `GET /health`
```json
{ "status": "ok", "port": 8765 }
```

### `POST /execute`
Request:
```json
{
  "binary":  "/absolute/path/to/binary",
  "args":    ["--flag"],
  "prompt":  "text sent to stdin",
  "timeout": 120
}
```
Response:
```json
{
  "stdout":    "...",
  "stderr":    "...",
  "exit_code": 0
}
```

Exit code `124` = timeout, `127` = binary not found.

## Security

The relay listens on `0.0.0.0` for Docker networking compatibility. **Development use only** — do not expose port 8765 to public networks.

## Related

- [ai_provider_cli](https://github.com/Performant-Labs/pl-dcms-ai_provider) — Drupal AI provider that uses this relay to pipe prompts to CLI tools.
