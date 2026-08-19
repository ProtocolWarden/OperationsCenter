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
    --labels docker:docker://node:20-bookworm,ubuntu-latest:docker://node:20-bookworm

docker run -d --name forgejo-runner --restart unless-stopped \
  -v forgejo-runner-data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --group-add "$(getent group docker | cut -d: -f3)" \
  --network host \
  code.forgejo.org/forgejo/runner:6.3.1 forgejo-runner daemon
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
