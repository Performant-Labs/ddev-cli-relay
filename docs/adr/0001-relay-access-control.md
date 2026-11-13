---
status: accepted
date: 2026-11-13
---

# Relay access control: loopback bind, per-project token, binary allowlist

The relay runs programs on the developer's machine as the developer, so only
the project's own containers may use it, and only to run known AI CLI tools.
Releases up to this one listened on `0.0.0.0` with no authentication and ran
any binary named in the request. Anyone on the same network, and possibly any
web page the developer had open, could run arbitrary commands on the host.
We closed this with four independent checks, each covering a different
attacker:

| Check | Stops |
|---|---|
| **Bind** to `127.0.0.1` on macOS. Elsewhere, to the address `host.docker.internal` resolves to inside the web container, falling back to the `ddev_default` gateway, and only if it is a local address. Override with `AI_RELAY_BIND` | Other machines on the LAN |
| **Bearer token**: one random token per project, created by a `pre-start` hook at `.ddev/cli-relay/token` (mode `600`, git-ignored), sent as `Authorization: Bearer …` | Local processes and pages that cannot read the project's files |
| **Browser rejection**: refuse any request with an `Origin` header, or without `Content-Type: application/json` | Web pages, including DNS-rebinding attacks, even if they somehow obtain the token |
| **Binary allowlist**: `claude codex gemini gemini-cli opencode ollama lms llm`, extended through `AI_RELAY_ALLOWED_BINARIES` or `.ddev/cli-relay/allowed-binaries.txt`. Resolved once at startup (absolute `PATH` entries only); the relay runs those paths and never one the caller supplies | Mistakes, and casual misuse by a token holder; **not** a determined one (see Consequences) |

The relay refuses to start without a token: there is no unauthenticated mode.
To rotate the token, delete the file and run `ddev restart`.

## Considered options

- **Token only, keep `0.0.0.0`.** Rejected: one leaked token would expose the
  host to the whole network. Binding narrowly costs nothing on macOS, where
  every Docker provider tested (Docker Desktop, OrbStack, Colima) forwards
  `host.docker.internal` to loopback.
- **Token passed as a container environment variable** (`web_environment`, or
  an env file). Rejected: DDEV reads these when it creates the container, so a
  token created on first start would need a second restart before Drupal could
  see it, and it would appear in `ddev describe` and `docker inspect`. DDEV
  already mounts the project directory into the container, so a file under
  `.ddev/` is visible there as soon as it is written. Drupal reads it as
  `/var/www/html/.ddev/cli-relay/token`.
- **Unix socket instead of TCP.** Rejected: bind-mounting a host socket into
  the container does not work reliably on macOS Docker providers.
- **Validating arguments** (for example rejecting `--dangerously-skip-permissions`).
  Rejected as a security boundary: any denylist is incomplete, and it would
  suggest protection the relay does not give. The README says plainly that the
  token holder controls arguments and prompt.

## Consequences

- **The token is a shell on the host.** Allowed tools accept arguments that
  run arbitrary commands, for example Claude Code's `--settings` hooks and
  `--mcp-config`, or Codex's sandbox bypass. Container code can also edit the
  `.ddev/` scripts the host runs, because DDEV syncs that directory both ways.
  The relay therefore keeps out the network and browsers; it does not fence
  the containers off from the host. We document this rather than add
  per-tool argument filters, which would be incomplete.
- **One port per running project.** Every project defaults to port 8765.
  `start.sh` checks that `/health` reports this project's `token_id` (the
  first 12 hex characters of the token's SHA-256), and fails with instructions
  if another project holds the port. The Drupal module runs the same check, so
  it can say "another project's relay" instead of "token rejected".
- **Upgrading is a breaking change.** Clients must send the token, and older
  `ai_provider_cli` releases will get `401` until they are updated. Custom
  tools must be added to the allowlist on the relay side as well as in Drupal.
- **Linux and WSL2 are untested.** On Docker Desktop for WSL2 the address
  containers use may not belong to the WSL distro, in which case start-up
  stops with an error asking for `AI_RELAY_BIND` rather than falling back to
  `0.0.0.0`.
- **The add-on layout was also fixed.** Files moved from `.ddev/…` to the
  repository root, the standard layout for DDEV add-ons. Before this,
  `ddev add-on get` installed into `.ddev/.ddev/`, where DDEV never reads
  them, so the published install command never worked.
