# ddev-cli-relay

[![tests](https://github.com/Performant-Labs/ddev-cli-relay/actions/workflows/tests.yml/badge.svg)](https://github.com/Performant-Labs/ddev-cli-relay/actions/workflows/tests.yml)
[![DDEV add-on](https://img.shields.io/badge/DDEV-add--on-blue)](https://addons.ddev.com)

A DDEV add-on that starts a lightweight HTTP relay server on the **host machine**, allowing code running inside a DDEV container to execute local CLI tools on the host.

## Usage

Once installed, the relay is available inside the container at `http://host.docker.internal:8765`.

**Check the relay is running:**
```bash
# From inside the container
curl http://host.docker.internal:8765/health
# {"status":"ok","port":8765}
```

**Execute a CLI tool from inside the container:**
```bash
curl -s -X POST http://host.docker.internal:8765/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "binary":  "/usr/local/bin/claude",
    "args":    ["--print"],
    "prompt":  "User: Hello",
    "timeout": 30
  }'
# {"stdout":"Hello! How can I help?","stderr":"","exit_code":0}
```

The prompt is passed to the process via **stdin** — never as a shell argument.

**Start and stop the relay manually** (without a full `ddev restart`):
```bash
ddev cli-relay:start
ddev cli-relay:stop
```

The relay starts automatically on `ddev start` and stops on `ddev stop`.

## Why

CLI tools like `claude`, `codex`, and `gemini-cli` use browser-based OAuth authentication — they must run on the host where auth already happened. PHP running inside a DDEV container has no direct way to exec host binaries. DDEV's `.ddev/commands/host/` scripts are triggered by the developer in a terminal, not programmatically from application code. This add-on bridges the gap with a minimal HTTP server that runs on the host and proxies execution requests from the container.

## Requirements

- DDEV >= v1.24.0
- Python 3 (pre-installed on macOS and most Linux distributions)

## Installation

```bash
ddev add-on get Performant-Labs/ddev-cli-relay
ddev restart
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `AI_RELAY_PORT` | `8765` | Port the relay listens on |

Export `AI_RELAY_PORT` in your shell profile before running `ddev start` to use a different port.

## API Reference

### `GET /health`

Returns `200` when the relay is running.

```json
{ "status": "ok", "port": 8765 }
```

### `POST /execute`

Executes a binary on the host with the given arguments. The `prompt` value is written to the process's stdin.

**Request:**
```json
{
  "binary":  "/absolute/path/to/binary",
  "args":    ["--flag", "--other-flag"],
  "prompt":  "text sent to stdin",
  "timeout": 120
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `binary` | string | yes | Absolute path to the executable on the host |
| `args` | array | no | CLI flags passed to the binary |
| `prompt` | string | no | Text written to stdin |
| `timeout` | integer | no | Seconds before the process is killed (default: `120`) |

**Response:**
```json
{
  "stdout":    "output from the process",
  "stderr":    "",
  "exit_code": 0
}
```

| Exit code | Meaning |
|---|---|
| `0` | Success |
| `124` | Process timed out |
| `127` | Binary not found |
| other | Process returned an error — check `stderr` |

## Security

The relay listens on `0.0.0.0` so that Docker's `host.docker.internal` routing can reach it. **This add-on is intended for local development only.** Do not expose port 8765 to public networks.

## Contributing

Pull requests are welcome. For significant changes please open an issue first.

## License

MIT

## Related

- [ai_provider_cli](https://github.com/Performant-Labs/pl-dcms-ai_provider) — Drupal AI provider that uses this relay to pipe prompts to local CLI tools from within a DDEV container.
