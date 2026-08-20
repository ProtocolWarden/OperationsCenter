# Serving the forge to other machines

`docker-compose.yml` stands this instance up for a fleet where everything —
board, runner, workers, submitters — shares one host. `ROOT_URL` is
`http://localhost:3000/` and that is correct for that case.

It stops being correct the moment anything that needs the board lives somewhere
else: a managed repo pinned to its own machine (a GPU box, a build host), an
operator submitting from a laptop, a second fleet member. This document covers
that boundary. For moving the instance itself — the volumes, the secrets, the
runner registration — see `README.md` in this directory; the two are meant to be
read together.

**Nothing else can substitute.** `board_backend` is `Literal["forgejo"]` and
Plane was removed at the 2026-08-18 cutover, so a board the submitter cannot
reach is a submitter that cannot submit. The `~/.console/queue/` drop-file is not
an alternative — it is a local directory watched with inotify and has no network
listener, so it never crosses a host boundary.

## What actually has to change

The compose file already publishes `3000:3000`, which binds `0.0.0.0`. The
container is not what is keeping you on loopback. Three other things are:

1. **`FORGEJO__server__ROOT_URL`** — Forgejo renders clone URLs and webhook
   targets from it. Left as `localhost` it hands every remote caller URLs that
   point that caller back at itself.
2. **The host's network boundary** — trivial on native Linux, emphatically not
   on WSL2.
3. **`DISABLE_REGISTRATION: "true"`** — remote submitters cannot self-register;
   an admin creates the account.

## 1. Pick an address

Prefer a name you control over a DHCP lease; a lease change otherwise breaks
every submitter at once, silently. A static reservation, a DNS entry, or a
`/etc/hosts` line on the submitting host all work.

```bash
hostname -I | awk '{print $1}'
```

Written below as `FORGE_HOST`.

## 2. Make the port reachable

### Native Linux

Just the firewall:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 3000 proto tcp
```

### WSL2 — the part that wastes an afternoon

**WSL2 sits behind a NAT with its own virtual IP. A service bound to `0.0.0.0`
inside WSL is not reachable from the LAN** — only from its Windows host. `ss
-tlnp` inside WSL shows it listening on all interfaces, docker reports the port
published, and remote clients still time out. Nothing in the container is wrong.

**Preferred — mirrored networking** (Windows 11 22H2+). WSL shares the Windows
host's interfaces, so LAN clients reach it directly and no forwarding rule has to
be maintained. In `C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

```powershell
wsl --shutdown          # then restart WSL and `docker compose up -d`
New-NetFirewallRule -DisplayName "Forgejo 3000 (LAN)" -Direction Inbound `
  -Protocol TCP -LocalPort 3000 -Action Allow -Profile Private
```

Keep `-Profile Private`. On a Public profile Windows treats the network as
untrusted, and the rule would open the forge more broadly than intended.

**Fallback — portproxy.** For older Windows or where mirrored mode is
unavailable. It works, but WSL's IP changes across most restarts, so this must be
re-applied or the board goes dark:

```powershell
$wslIp = (wsl.exe -d Ubuntu-24.04 -- hostname -I).Trim().Split()[0]
netsh interface portproxy delete v4tov4 listenport=3000 listenaddress=0.0.0.0 2>$null
netsh interface portproxy add v4tov4 listenport=3000 listenaddress=0.0.0.0 `
  connectport=3000 connectaddress=$wslIp
```

## 3. Repoint the instance

In `docker-compose.yml`:

```yaml
FORGEJO__server__ROOT_URL: http://FORGE_HOST:3000/
```

**Leave `runner-config.yml` at `container.network: host`.** That pairing is the
whole reason CI works: with host networking the instance URL means the same thing
inside a job container as outside it, so `FORGE_HOST:3000` keeps resolving for
`actions/checkout`. On a bridge network every job dies at `git exit 128`. See
"If the host name or port changes" in `README.md`.

```bash
docker compose -f deploy/forgejo/docker-compose.yml up -d
docker logs forgejo-runner 2>&1 | grep 'declared successfully'
```

Existing repositories need no edit — clone URLs are derived from `ROOT_URL` at
render time.

## 4. Repoint OC at its own board

Three places still say `localhost`, and they must move together:

```yaml
# config/operations_center.local.yaml
forgejo:
  base_url: http://FORGE_HOST:3000
repos:
  OperationsCenter:
    clone_url: http://<user>:<token>@FORGE_HOST:3000/<owner>/OperationsCenter.git
```

```bash
git remote set-url forgejo http://<user>:<token>@FORGE_HOST:3000/<owner>/OperationsCenter.git
```

One address that means the same thing from every host beats two that drift apart.

## 5. An account per remote submitter

Registration is disabled, so create it as admin — and **do not hand out the
admin token**. It can rewrite the forge, and it would come to rest in an env var
on a machine doing unrelated work.

1. Site Administration → Identity & Access → Users → Create.
2. As that user: Settings → Applications → Generate token.
3. Scope it to **issue write on the board repo**. It creates issues; it needs
   nothing else.

## 6. The labels a submission needs

A remote submitter creates an issue; `board_worker.claim_next()` claims it. It is
only claimable with all of these, and they must exist on the board repo first
(Issues → Labels) because Forgejo's create-issue API takes label **IDs**, not
names:

```
state: Ready for AI     exactly one `state:` label — the client raises on two
repo: <RepoKey>         must match a key in settings.repos
task-kind: goal         or test | test_campaign | improve | improve_campaign | spec-author
priority: normal        or low | high
```

The body needs a `## Goal` section — `extract_goal()` falls back to the title —
carrying at least 40 characters (`claim.py:_MIN_GOAL_TEXT_CHARS`). Under that the
task is claimed and immediately blocked, which reads from the submitting side as
the fleet ignoring you.

Vocabulary lives in `src/operations_center/entrypoints/board_worker/labels.py`
(`STATE_READY`, `ROLE_KINDS`); if it changes, this list is what goes stale.

## 7. Verify from the other machine

Verifying from the forge host proves nothing — loopback passes there.

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://FORGE_HOST:3000/api/v1/version
curl -fsS -H "Authorization: token $TOKEN" \
     http://FORGE_HOST:3000/api/v1/repos/<owner>/<repo>/labels | head -c 200
```

Then submit one real item and watch it go `state: Ready for AI` → `state:
Running`.

## When it doesn't work

| Symptom | Cause |
|---|---|
| Refused/timeout remotely, fine locally | WSL2 NAT (§2), or the firewall rule is on the wrong profile |
| Worked, then stopped after a reboot | portproxy stale after a WSL IP change — re-apply §2 |
| 401 on the labels call | token not scoped to issue write, or pasted with whitespace |
| Submits fine, never claimed | label names don't match exactly, or `repo:` is not a key in `settings.repos` |
| Claimed, then instantly blocked | goal text under 40 chars |
| Jobs die at `git exit 128` | `ROOT_URL` changed while `container.network` is no longer `host` |
| Clone URLs still say localhost | `ROOT_URL` not updated, or the container was not recreated |
