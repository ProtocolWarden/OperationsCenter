#!/usr/bin/env bash
# Build the CI job image and publish it to this Forgejo's own container registry.
#
# Why this exists: `runner-config.yml` maps `runs-on: ubuntu-latest` to a job
# image BY NAME. That image was built locally and existed in no registry, so
# anything that empties the daemon's image store -- a Docker Desktop reinstall
# or upgrade, toggling the containerd image store, `docker system prune -a` --
# silently took CI down. Every job then died in about a second at
#
#     Error response from daemon: pull access denied for oc-ci-runner,
#     repository does not exist or may require 'docker login'
#
# which reads like an auth problem and is not. The runner sets forcePull=false,
# so it only reaches for a registry when the image is MISSING locally, and the
# pull then fails because no registry ever had it. That happened on 2026-08-20
# and cost 18 failed jobs across five runs before anyone looked at the image.
#
# Publishing to Forgejo closes it: the blobs land in the forgejo-data volume,
# which IS in the backup set (see "Moving to another machine" in README.md), so
# a wiped image store self-heals on the next pull instead of failing the queue.
set -euo pipefail

# Must match FORGEJO__server__ROOT_URL's host:port in docker-compose.yml. The
# registry is served by the same instance on the same port, so if you change one
# you change all three -- see "If the host name or port changes" in README.md.
REGISTRY="${OC_REGISTRY:-localhost:3000}"
OWNER="${OC_REGISTRY_OWNER:-operations_center_admin}"   # lowercase: Forgejo normalises owner names in registry paths
IMAGE="oc-ci-runner"
TAG="${OC_IMAGE_TAG:-latest}"

REMOTE="$REGISTRY/$OWNER/$IMAGE:$TAG"
LOCAL="$IMAGE:$TAG"

cd "$(dirname "$0")/../.."   # repo root: the build context below is repo-relative

echo "==> building $LOCAL"
# Tag both names from one build. The bare local tag is kept because the README's
# bootstrap and restore paths still reference it, and because a build here should
# not depend on the registry being up.
docker build -t "$LOCAL" -t "$REMOTE" deploy/forgejo/ci-runner/

echo "==> pushing $REMOTE"
if ! docker push "$REMOTE"; then
  cat >&2 <<MSG

Push failed. If that was an authentication error, log in first:

    docker login $REGISTRY -u $OWNER

Use a Forgejo API token as the password, not your account password -- mint one
at http://$REGISTRY/user/settings/applications with the 'write:package' scope.
Only pushing needs credentials; the runner pulls anonymously.
MSG
  exit 1
fi

echo "==> verifying the runner can resolve it anonymously"
# Prove the pull path the runner will actually take, using a throwaway config so
# this check cannot silently pass on THIS shell's cached credentials.
EMPTY_CFG="$(mktemp -d)"
trap 'rm -rf "$EMPTY_CFG"' EXIT
if docker --config "$EMPTY_CFG" manifest inspect --insecure "$REMOTE" >/dev/null 2>&1; then
  echo "    ok: $REMOTE resolves without credentials"
else
  echo "    WARNING: anonymous pull failed -- the runner will not self-heal." >&2
  echo "    Check that '$OWNER' is public, or the job image will need a pull secret." >&2
fi

echo
echo "done. $REMOTE is published."
echo "runner-config.yml maps ubuntu-latest to this reference; a missing local"
echo "image now re-pulls from Forgejo instead of failing the job."
