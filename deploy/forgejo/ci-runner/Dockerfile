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
      D="/opt/hostedtoolcache/Python/$V/x64"; \
      mkdir -p "/opt/hostedtoolcache/Python/$V"; \
      mv "/tmp/py$p" "$D"; \
      grep -rlI '^#!/usr/local/bin/' "$D/bin" | while read -r f; do \
        sed -i "1s|^#!/usr/local/bin/|#!$D/bin/|" "$f"; \
      done; \
      touch "/opt/hostedtoolcache/Python/$V/x64.complete"; \
    done; \
    ls -la /opt/hostedtoolcache/Python

# The console scripts (pip, wheel, ...) carry an absolute shebang from the image
# they were built in: `#!/usr/local/bin/python3.11`. That path does not exist at
# the relocated prefix, so the kernel fails the exec with ENOENT and bash
# reports the thoroughly misleading
#
#     /opt/hostedtoolcache/Python/3.11.16/x64/bin/pip: cannot execute: required file not found
#
# which names pip, while the file actually missing is the INTERPRETER. The
# rewrite above fixes that. The interpreter itself needs no patching: CPython
# derives sys.prefix from argv[0] at runtime, so it relocates cleanly.

# Prove the relocated interpreters are usable at BUILD time, so a broken image
# fails here instead of in a CI job ten minutes later.
RUN set -eux; \
    for d in /opt/hostedtoolcache/Python/*/x64; do \
      "$d/bin/python3" -c 'import sys,ssl,sqlite3,lzma,ctypes; print(sys.version.split()[0], sys.prefix)'; \
      "$d/bin/pip" --version; \
      V="$(basename "$(dirname "$d")")"; \
      "$d/bin/python3" -m venv "/tmp/vcheck-$V"; \
      "/tmp/vcheck-$V/bin/python" -c 'import sys; print("venv ok", sys.version.split()[0])'; \
    done