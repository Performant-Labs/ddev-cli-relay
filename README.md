# ddev-cli-relay

[![tests](https://github.com/Performant-Labs/ddev-cli-relay/actions/workflows/tests.yml/badge.svg)](https://github.com/Performant-Labs/ddev-cli-relay/actions/workflows/tests.yml)
[![DDEV add-on](https://img.shields.io/badge/DDEV-add--on-blue)](https://addons.ddev.com)

A DDEV add-on that starts a lightweight HTTP relay server on the **host machine**, allowing code running inside a DDEV container to execute local CLI tools on the host.

## Usage

Once installed, the relay is available inside the container:

- **macOS:** `http://host.docker.internal:8765` (Docker Desktop routes this automatically)
- **Linux:** `http://cli-relay-proxy:8765` (via the socat sidecar — see [Linux support](#linux-support) below)

**Check the relay is running:**
```bash
# From inside the container (Linux)
curl http://cli-relay-proxy:8765/health

# From inside the container (macOS)
curl http://host.docker.internal:8765/health

# From the host
curl http://localhost:8765/health

# All return: {"status":"ok","port":8765}
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

## Linux support

On Linux, Docker runs natively (no VM), and `host.docker.internal` often cannot reach host-bound ports due to iptables filtering on Docker's bridge networks. The addon works around this automatically:

1. The relay starts **two** listeners on the host: TCP on `0.0.0.0:8765` (for host-side testing) and a Unix socket at `.ddev/cli-relay/relay.sock`
2. A lightweight `alpine/socat` sidecar container bridges `TCP:8765` to the Unix socket via the shared `.ddev/` volume mount
3. Containers reach the relay at `http://cli-relay-proxy:8765` — pure container-to-container, no host networking required

For `ai_provider_cli`, set the relay URL to the sidecar:
```bash
drush config:set ai_provider_cli.settings bridge.relay_url 'http://cli-relay-proxy:8765' -y
```

macOS users do not need to change anything — `host.docker.internal` continues to work as before.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `AI_RELAY_PORT` | `8765` | Port the relay listens on (host TCP and sidecar) |

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
