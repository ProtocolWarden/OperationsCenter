# The two volumes, and why you cannot copy someone else's

`docker-compose.yml` declares both volumes `external: true`, which is a promise
that they already exist and that compose will not create or destroy them. That
is deliberate: everything the forge knows lives in them, and nothing in this
repo can rebuild it.

| Volume | Holds | Size as of 2026-08-21 |
|---|---|---|
| `forgejo-data` | the instance: users, repos, issues, PRs, CI history, **and the container registry** | 618 MB |
| `forgejo-runner-data` | the runner's registration and its `config.yml` | 150 MB |

`forgejo-data` was 78 MB until the CI job image was published into the forge's
own registry on 2026-08-21. The image is ~2 GB uncompressed and lands as about
540 MB of blobs. Anyone sizing a backup off an older number will be wrong by an
order of magnitude.

## Volumes are per-instance state, not artifacts to share

This is the thing to get straight before building any sync mechanism.

A second person does **not** copy these volumes. They stand up their own. The
contents are bound to one instance in ways that do not survive a copy:

* **The runner's registration.** `forgejo-runner-data` holds a `.runner` file
  with a token minted by *one* forge, plus the address it registered against.
  Hand it to someone else and their runner authenticates against an instance
  that is not theirs, or fails to authenticate at all.
* **Users, and therefore ownership.** Every repo, package, and issue in
  `forgejo-data` is owned by a numeric user id. Copying the volume copies the
  accounts, including the admin — which means copying credentials-adjacent
  state, not just data.
* **The registry path is the account name.** The job image is addressed as
  `localhost:3000/operations_center_admin/oc-ci-runner:latest`. That path
  embeds the owner. A different person with a different account has a different
  path, and `runner-config.yml` has to say so.

What travels between people is the **code and the procedure** — this repo. What
each person builds locally is the **state**. Conflating the two is what makes
"sync the volumes" sound simpler than it is.

## What a second person actually creates

Follow "Standing one up from scratch" in README.md. In volume terms it comes to:

```bash
docker volume create forgejo-data
docker volume create forgejo-runner-data
docker compose -f deploy/forgejo/docker-compose.yml up -d
```

Then, in this order, because each step depends on the last:

1. **Create the admin account** through the web UI. This is the owner every
   later path is written against.
2. **Register the runner** against *their* forge — see "Runner" in README.md.
   The token is minted by their instance and is not transferable.
3. **Publish the job image** with `push-ci-runner.sh`. It defaults to
   `operations_center_admin`; override with `OC_REGISTRY_OWNER` to match their
   account, and set the matching path in `runner-config.yml`.
4. **Apply branch protection** — `apply-branch-protection.sh`. It is in no
   volume and no backup, and a fresh instance starts unprotected.

## What is NOT in the volumes, and not in git either

Three things have to be moved by hand, and forgetting them is the usual way a
migration looks finished while being broken:

| Thing | Where it lives | Why it is not in git |
|---|---|---|
| `FORGEJO_API_TOKEN`, `GITHUB_TOKEN` | `.env.operations-center.local` | secrets |
| a `clone_url` with the token inline | `config/operations_center.local.yaml` | secrets |
| the git remote's embedded token | `.git/config` of the working clone | secrets |

The token appears in **three** places, not one. Rotating it means changing all
three, and the failure mode when you miss the remote is that the API keeps
working while `git push` starts failing — which reads as a network problem.

## Backing up your own

```bash
for v in forgejo-data forgejo-runner-data; do
  docker run --rm -v "$v":/from -v "$PWD":/to alpine tar czf "/to/$v.tgz" -C /from .
done
```

Restore is the inverse — see "Moving to another machine" in README.md. Two
things worth knowing before you trust an archive:

* **Stop the containers first**, or you are archiving a live SQLite database
  mid-write. The restore will look fine and fail later.
* **The archive now includes the registry.** That is a feature: the job image
  travels with the forge instead of having to be rebuilt. It is also why the
  archive is ~600 MB rather than ~80 MB.

## Open question this does not answer

Keeping *one* person's volumes in step across *their own* machines is a
different problem from letting two people run parallel instances, and it wants
a different mechanism. Nothing here solves the first; it only establishes what
is per-instance so a later sync design does not try to move state that cannot
be moved.
