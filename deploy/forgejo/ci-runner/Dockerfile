# Job image for the self-hosted Forgejo runner.
#
# Why this exists: `actions/setup-python` resolves interpreter downloads from
# the FORGE's own `actions/python-versions` repo (it reads GITHUB_API_URL, which
# on a Forgejo runner points at the local instance). That repo does not exist
# here, so every job that used setup-python failed with
#
#     ::error::The version '3.11' with architecture 'x64' was not found ...
#
# setup-python checks $RUNNER_TOOL_CACHE first, so pre-seeding the tool cache
# makes it a no-op and the workflows stay byte-identical to GitHub's.
#
# Baked into the IMAGE rather than bind-mounted from the host on purpose: jobs
# run `pip install -e ".[dev]"`, which writes into the interpreter's
# site-packages. A host mount would persist those writes across every
# subsequent job; an image layer gives each job a fresh, writable copy.
FROM python:3.11-bookworm AS py311
FROM python:3.12-bookworm AS py312

FROM node:20-bookworm

COPY --from=py311 /usr/local /tmp/py311
COPY --from=py312 /usr/local /tmp/py312

RUN set -eux; \
    for p in 311 312; do \
      V="$(/tmp/py$p/bin/python3 -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')"; \
      mkdir -p "/opt/hostedtoolcache/Python/$V"; \
      mv "/tmp/py$p" "/opt/hostedtoolcache/Python/$V/x64"; \
      touch "/opt/hostedtoolcache/Python/$V/x64.complete"; \
    done; \
    ls -la /opt/hostedtoolcache/Python
