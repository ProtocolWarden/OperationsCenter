# PlatformDeployment compose profile smoke runbook

> Operator runbook. Closes the "PlatformDeployment compose profile smoke per
> profile" Verification Gaps backlog item. Verification-only — surfaces
> what works, names what doesn't, does not fix. Findings worth fixing
> are filed back to backlog.
>
> Smoke run executed 2026-05-08 against current main of PlatformDeployment +
> sibling repos. Repeat with the same commands against new images to
> re-verify.

PlatformDeployment ships four compose profiles. This runbook brings each up,
probes its health, lists expected containers + ports, and documents
known caveats. It does **not** validate deep features — that's each
service's own concern.

## Prerequisites

- Sibling repos cloned at expected paths under `~/Documents/GitHub/`:
  `PlatformDeployment`, `SwitchBoard`, `Archon`. (Compose contexts resolve
  relative to these locations.)
- Service images built or pullable. SwitchBoard + Archon images build
  from source; rebuild when source changes.

## Per-profile runbook

### `core` — SwitchBoard only

Minimal baseline. SwitchBoard runs alone.

```bash
cd ~/Documents/GitHub/PlatformDeployment
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  up -d
```

| Container | Healthy? | Port | Health endpoint |
|-----------|----------|------|-----------------|
| `platformdeployment-switchboard` | yes | `${PORT_SWITCHBOARD:-20401}` | `/health` |

Verify:

```bash
curl -fsS http://localhost:20401/health | jq .status
# → "ok"
```

**Smoke status (2026-05-08): ✅ healthy**

See `docs/operator/switchboard_live_verification.md` for the full
SwitchBoard runbook (rebuild path if you see a CxRP envelope mismatch).

### `archon` — Archon workflow harness (removed)

> **Note:** Archon has been removed. This section is kept for historical reference only.
> The `archon.yml` compose profile and `platformdeployment-archon` container are no longer used.

Layers on top of `core`. Archon joined as a long-running HTTP service.

```bash
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/archon.yml \
  up -d
```

| Container | Healthy? | Port | Health endpoint |
|-----------|----------|------|-----------------|
| `platformdeployment-switchboard` | yes | `:20401` | `/health` |
| `platformdeployment-archon` | yes | `${PORT_ARCHON:-3000}` | `/api/health` |

Verify:

```bash
curl -fsS http://localhost:3000/api/health | jq '{status, version}'
# → {"status": "ok", "version": "0.3.10", ...}
```

**Smoke status (2026-05-08): ✅ healthy**

See `docs/operator/archon_workflow_registration.md` for the historical
codebase registration runbook (Archon has been removed).

### `dev` — adds developer tooling on top of core

Adds Mailpit (local SMTP catcher) and bumps SwitchBoard to debug log
level. `mitmproxy` is commented out in the profile — uncomment if you
need traffic inspection.

```bash
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/dev.yml \
  up -d
```

| Container | Healthy? | Port(s) | Health endpoint |
|-----------|----------|---------|-----------------|
| `platformdeployment-switchboard` | yes | `:20401` | `/health` |
| `platformdeployment-mailpit` | yes | `:1025` (SMTP), `:8025` (web UI) | `/api/v1/info` |

Verify:

```bash
curl -fsS http://localhost:8025/api/v1/info | jq '.Name'
# → "Mailpit"
```

Mailpit's web UI is at `http://localhost:8025/`.

**Smoke status (2026-05-08): ✅ healthy**

### `observability` — Prometheus + Grafana

Layers on top of `core`. Adds Prometheus (metrics) and Grafana
(dashboards).

```bash
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/observability.yml \
  up -d
```

**Smoke status: ✅ clean (post PlatformDeployment #16)**

| Container | Healthy? | Port | Health endpoint |
|-----------|----------|------|-----------------|
| `platformdeployment-switchboard` | yes | `:20401` | `/health` |
| `platformdeployment-prometheus` | yes | `:9090` | `/-/healthy` |
| `platformdeployment-grafana` | yes | `:3000` | `/api/health` |

Verify:

```bash
curl -fsS http://localhost:9090/-/healthy
# → "Prometheus Server is Healthy."
curl -fsS http://localhost:3000/api/health | jq .database
# → "ok"
```

#### Historical note

Earlier versions of this profile mounted from
`../../config/observability/` — a sibling-of-PlatformDeployment path
that no clean clone has authored. Docker auto-created the missing
host files as root-owned directories on first start, which then
permanently broke subsequent starts with `failed to mount: not a
directory`. PlatformDeployment #16 shipped a skeleton inside the repo at
`config/observability/` and updated the compose mount paths, so a
fresh clone now boots the observability profile without manual
setup.

If you have an older machine where the original sibling-config dir
still has root-owned stub directories from the old layout, clean
them up once with:

```bash
sudo rm -rf /home/dev/Documents/GitHub/config/observability
```

(Pre-#16 workaround scripts have been retired from this repo.)

When the `observability` and `archon` profiles are both desired
together, **port 3000 collides** — both Grafana and Archon default to
`:3000`. Override one in your local `.env` (`PORT_ARCHON=3001` or
`GRAFANA_PORT=3001`).

## Tear down

```bash
# Stop services in any active profile (the override list is forgiving):
cd ~/Documents/GitHub/PlatformDeployment
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/archon.yml \
  -f compose/profiles/dev.yml \
  -f compose/profiles/observability.yml \
  stop

# Or remove containers entirely:
docker compose [...same flags...] down
```

Stopped containers retain state and volumes; bringing them back is
fast. `down` removes containers but keeps named volumes
(`prometheus_data`, `grafana_data`).

## Findings filed back to backlog

| Profile | Finding |
|---------|---------|
| `core` | None — clean. |
| `archon` | None — clean. The codebase-registration step lives in its own playbook. |
| `dev` | None — clean. The commented-out `mitmproxy` block could be removed or wired in; not urgent. |
| `observability` | Now clean (was broken on first-run prior to PlatformDeployment #16). The compose previously mounted from a sibling-of-PlatformDeployment path that no clean clone authored, which Docker silently turned into root-owned empty directories. PlatformDeployment #16 ships an in-repo skeleton at `config/observability/` and updates the mount paths; a fresh clone now boots without manual setup. |
