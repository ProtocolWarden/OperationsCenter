# Forgejo Actions runner

The `audit` status the fleet's branch protection requires is produced by a
workflow, and a workflow needs a runner. This is how the local one is set up.

## Runner

```bash
TOKEN=$(docker exec -u git forgejo forgejo forgejo-cli actions generate-runner-token)

docker volume create forgejo-runner-data
docker run --rm -v forgejo-runner-data:/data --network host \
  code.forgejo.org/forgejo/runner:6.3.1 \
  forgejo-runner register --no-interactive \
    --instance http://localhost:3000 \
    --token "$TOKEN" \
    --name oc-local-runner \
    --labels ubuntu-latest:docker://oc-ci-runner:latest

# The label mapping and the job network live in config.yml, so install it
# BEFORE starting the daemon and pass --config. A daemon started without it
# silently uses defaults: bridge networking (every checkout dies at
# `git exit 128`) and no image mapping.
docker cp deploy/forgejo/runner-config.yml forgejo-runner-data-helper:/data/config.yml \
  2>/dev/null || docker run --rm -v forgejo-runner-data:/data -v "$PWD/deploy/forgejo":/src \
  alpine cp /src/runner-config.yml /data/config.yml

docker build -t oc-ci-runner:latest deploy/forgejo/ci-runner/

docker run -d --name forgejo-runner --restart unless-stopped \
  -v forgejo-runner-data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --group-add "$(getent group docker | cut -d: -f3)" \
  code.forgejo.org/forgejo/runner:6.3.1 \
  forgejo-runner daemon --config /data/config.yml
```

Two things that were wrong in an earlier version of this section and cost real
debugging time:

* The label mapped `ubuntu-latest` to `node:20-bookworm`. That image has no
  Python tool cache, so `actions/setup-python` fails every job with
  `The version '3.11' with architecture 'x64' was not found` — see
  `ci-runner/Dockerfile` for why. Map it to `oc-ci-runner:latest`.
* The daemon was started without `--config`, so `runner-config.yml` — which is
  what sets `container.network: host` — was never read.

Confirm the mapping took effect before trusting anything:

```bash
docker logs forgejo-runner 2>&1 | grep 'declared successfully'
```

`--group-add` rather than `--user 0:0`: the runner needs the docker socket to
spawn job containers, and joining the socket's group is narrower than running
the daemon as root. Note that either way this grants control of the Docker
daemon, which is root-equivalent on the host — run it only where that is
acceptable.

## The status context is NOT the job name

Forgejo composes it as `<workflow name:> / <run-name> (<event>)`, and with no
`run-name:` the middle segment is **the commit message** — so the context
changes on every push and can never satisfy a required status check.

Always set `run-name:`, and trigger on `pull_request` only (a `push` trigger
produces a second, separate context on the same head):

```yaml
name: custodian-audit
run-name: audit
on:
  pull_request:
    branches: [main]
jobs:
  audit:
    runs-on: ubuntu-latest
```

That yields the stable context `custodian-audit / audit (pull_request)`, which
is what branch protection's `status_check_contexts` must list. It is Forgejo's
format, not GitHub's `audit` — a checklist that says "use the same name" is
wrong.

See `docs/specs/forgejo-pr-adapter.md` for the evidence behind both points.

---

## Standing one up from scratch

The section below moves an EXISTING instance. This is the other case: a new
machine with no forge to restore. Order matters — each step produces something
the next one needs.

```bash
# 1. the instance
docker volume create forgejo-data
docker compose -f deploy/forgejo/docker-compose.yml up -d forgejo
```

2. Create the admin user and the repo through the web UI at
   `http://localhost:3000`. Registration is disabled by default
   (`DISABLE_REGISTRATION=true`), so the FIRST account created through the
   install screen is the admin — there is no second chance without a CLI
   password reset.

3. Mint an API token at `<instance>/user/settings/applications` with
   `read:user`, `write:repository`, `write:issue`. Put it in
   `.env.operations-center.local` as `FORGEJO_API_TOKEN`, as a **literal
   value** — see that file's example for why command substitution hangs the
   daemons.

4. Register the runner and start it — the "Runner" section above. Build
   `oc-ci-runner:latest` before starting the daemon, or every job fails at
   image pull.

5. Apply branch protection. It lives in the forge's database, so a FRESH instance
   like this one starts **unprotected** — the failure mode that looks completely
   fine. (Restoring a volume backup is different: protection comes back with the
   database. See "Moving to another machine" below, where you verify rather than
   apply.)

```bash
./deploy/forgejo/apply-branch-protection.sh
```

6. Point OC at it — `board_backend: forgejo`, `pr_backend: forgejo`, the
   `forgejo:` block, and a `clone_url` carrying the token — see
   `config/operations_center.example.yaml` and `docs/operator/setup.md`.

7. Open a throwaway PR and confirm three things before trusting the fleet: the
   `custodian-audit / audit (pull_request)` context appears, the reviewer posts
   `reviewer-verdict`, and the merge is refused while either is pending.

## Moving to another machine

Everything in this directory is reproducible. The two things that matter most
are **not**, and neither lives in git:

| What | Where it lives | Why it cannot be committed |
|---|---|---|
| The forge itself — repos, PRs, users, **branch protection**, runner registration | docker volume `forgejo-data` | A live SQLite DB plus git repos |
| Runner registration token + config | docker volume `forgejo-runner-data` | Contains a credential bound to the instance |
| `config/operations_center.local.yaml` | OC repo root, gitignored | Clone URL with an embedded API token |
| `.env.operations-center.local` | OC repo root, gitignored | `FORGEJO_API_TOKEN`, `GITHUB_TOKEN` |

`docker inspect` on the old box is not a migration plan. If that machine is
gone, so is the answer. That is why `docker-compose.yml` exists at all: both
containers were originally created with raw `docker run`, and nothing recorded
the ports, volumes, or `FORGEJO__*` settings.

### 1. On the old machine — take the state with you

```bash
docker stop forgejo forgejo-runner
for v in forgejo-data forgejo-runner-data; do
  docker run --rm -v "$v":/from -v "$PWD":/to alpine tar czf "/to/$v.tgz" -C /from .
done
docker start forgejo forgejo-runner
```

Copy both tarballs, plus `config/operations_center.local.yaml` and
`.env.operations-center.local`, over a channel you would send a password
through — they contain live tokens.

**On keeping the secret files in a private repo.** Tempting, and fine *if they
are encrypted*. Plaintext secrets in git are effectively permanent: every
clone, reflog and backup keeps them, so "rotate the token" becomes "rewrite
history everywhere it was ever pushed", and private repos get forked, mirrored
and cloned to laptops like any other. Encrypt them instead — `sops` with an
`age` key, or `git-crypt` — so the repo holds ciphertext and only the key
travels out of band:

```bash
age-keygen -o ~/.config/sops/age/keys.txt          # once, per operator
sops --encrypt --age <public-key> .env.operations-center.local > env.enc.yaml
```

Then the private repo carries `env.enc.yaml`, and the machine move needs one
age key moved by hand rather than two plaintext files. If that is more
machinery than this deployment warrants, a password manager entry is a
perfectly good answer — the thing to avoid specifically is plaintext in git.

### 2. On the new machine — restore

```bash
for v in forgejo-data forgejo-runner-data; do
  docker volume create "$v"
  docker run --rm -v "$v":/to -v "$PWD":/from alpine tar xzf "/from/$v.tgz" -C /to
done
docker compose -f deploy/forgejo/docker-compose.yml up -d
docker build -t oc-ci-runner:latest deploy/forgejo/ci-runner/
```

The `oc-ci-runner` image must be **built**, not restored — it is in no volume,
and `runner-config.yml` maps `runs-on: ubuntu-latest` to it by name. If it is
missing, every job fails at image pull.

### 3. Verify before trusting it

```bash
docker logs forgejo-runner 2>&1 | grep 'declared successfully'
```

```bash
./deploy/forgejo/apply-branch-protection.sh --check
```

### If the host name or port changes

`FORGEJO__server__ROOT_URL` is load-bearing for CI, not cosmetic. The runner
hands that URL to job containers for `actions/checkout`, so it must resolve
*inside a job container*. That is the entire reason `runner-config.yml` sets
`container.network: host` — on the default bridge network `localhost:3000` is
the container's own localhost, and every job dies at `git exit 128`.

Change one and you must change the other. They are a pair.

### Serving the forge to other machines

Everything above assumes one host. When something that needs the board lives
elsewhere — a managed repo pinned to a GPU box, an operator submitting from a
laptop — `ROOT_URL` on `localhost` is no longer merely cosmetic: it hands remote
callers URLs pointing back at themselves, and on WSL2 the port is not reachable
from the LAN at all regardless of what it is bound to.

See **[LAN-ACCESS.md](LAN-ACCESS.md)** — addressing, the WSL2 NAT trap, firewall
rules, scoped submitter accounts, the labels a remote submission needs to be
claimable, and a symptom-to-cause table.

### Known operational wrinkles

* **`capacity: 1` is deliberate.** Jobs share the host network namespace, so
  concurrent jobs collide on any fixed port a test binds. Raising it trades
  correctness for wall-clock.
* **Per-job volumes accumulate.** Every run leaves `FORGEJO-ACTIONS-TASK-<n>_...`
  volumes behind; reclaim them by prefix periodically.
* **The runner's `docker.sock` mount is root-equivalent on the host.** Anything
  that can reach it can start a privileged container. Acceptable only because
  this runner executes code from a repo whose PRs are already trusted enough to
  merge — do not point it at an untrusted fork.
* **`actions/setup-python` cannot resolve interpreters here.** It reads
  `GITHUB_API_URL`, which on this runner is the local instance, and asks it for
  an `actions/python-versions` repo that does not exist. The tool cache baked
  into `ci-runner/Dockerfile` is what makes it a no-op. Do not "simplify" that
  image away.
