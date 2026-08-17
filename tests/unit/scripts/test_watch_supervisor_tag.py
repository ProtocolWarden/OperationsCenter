# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pin the watch-supervisor tag contract in scripts/operations-center.sh.

PR #481 tried to recover a drifted watcher pid by scanning `ps` for a
hand-maintained dict of per-role command-line fragments. That dict duplicated
knowledge that already lived in `start_watch_role`, and nothing kept the two in
sync. A quoting or argument change in the launcher would make matching silently
return nothing, every caller reads "not found" as "not running", and
`start_watch_role` then launches a DUPLICATE supervisor — the exact pid-drift
failure the change set out to fix. The council raised it on five heads running.

The replacement stamps one uniform tag into every supervisor's command line and
matches only that. These tests exist so the single-source property is enforced
rather than merely intended: if someone adds a sixth launch branch and forgets
the stamp, `test_every_launch_branch_is_stamped` fails here instead of silently
degrading in production.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "operations-center.sh"

pytestmark = pytest.mark.skipif(
    not SCRIPT.exists(), reason="launcher script not present in this checkout"
)


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


# ── the invariant that #481 lacked ───────────────────────────────────────────


def test_every_launch_branch_is_stamped():
    """Every `setsid /bin/bash -lc` supervisor must carry the tag.

    This is the drift guard. Discovery matches the tag and nothing else, so an
    unstamped branch is invisible to reconciliation — and an invisible watcher
    reads as "not running", which is what produces duplicate supervisors.
    """
    text = _script()
    launches = []
    for m in re.finditer(r"setsid /bin/bash -lc \"", text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        # Prose describing the launch pattern is not a launch. stop_watch_role's
        # comment quotes it verbatim, and matching that would be a false alarm.
        if text[line_start : m.start()].lstrip().startswith("#"):
            continue
        launches.append(m.start())
    assert launches, "no supervisor launches found — has the launcher been restructured?"

    unstamped = []
    for pos in launches:
        # The stamp is the first line inside the command string. The watch roles
        # interpolate ${role}; the watchdog is its own fixed role name.
        window = text[pos : pos + 200]
        if "oc-watch-supervisor=" not in window:
            line_no = text.count("\n", 0, pos) + 1
            unstamped.append(line_no)

    assert not unstamped, (
        f"{len(unstamped)} launch branch(es) at line(s) {unstamped} do not stamp "
        "oc-watch-supervisor=${role} as their first line. Discovery matches only "
        "that tag, so an unstamped supervisor cannot be reconciled and a duplicate "
        "will be started."
    )


def test_tag_has_exactly_one_definition():
    """One producer of the tag string. Two would be the drift #481 died of."""
    text = _script()
    assert text.count("watch_supervisor_tag() {") == 1
    # The literal may appear only in the helper and in the launch stamps; every
    # other consumer must go through watch_supervisor_tag().
    literal_uses = re.findall(r"oc-watch-supervisor=", text)
    stamps = re.findall(r"# oc-watch-supervisor=", text)
    assert len(literal_uses) == len(stamps) + 1, (
        f"oc-watch-supervisor= appears {len(literal_uses)}x but only "
        f"{len(stamps)} stamps + 1 helper are allowed; a second producer of the "
        "tag string is exactly the drift that sank #481"
    )


def test_no_per_role_command_line_patterns_reintroduced():
    """Guard against the #481 shape coming back.

    A dict keyed by role holding command-line fragments is the specific design
    the council rejected three-to-nil.
    """
    text = _script()
    assert "find_watch_supervisor_pid" not in text, (
        "find_watch_supervisor_pid is back — it matched per-role command-line "
        "fragments that duplicate start_watch_role and drift silently"
    )


def test_ambiguous_is_distinct_from_absent():
    """reconcile must not collapse 'many' into 'none'.

    Returning the same code for both is how a discovery miss becomes a duplicate
    supervisor: the caller starts one because it believes nothing is running.
    """
    text = _script()
    body = re.search(
        r"reconcile_watch_pid_file\(\) \{(.*?)\n\}", text, re.S
    )
    assert body, "reconcile_watch_pid_file not found"
    assert "return 2" in body.group(1), "no distinct exit code for the ambiguous case"
    assert "return 1" in body.group(1), "no distinct exit code for the absent case"


def test_start_refuses_to_start_when_ambiguous():
    """start_watch_role must bail on rc=2 rather than launch another."""
    text = _script()
    body = re.search(r"start_watch_role\(\) \{(.*?)\n  local log_file", text, re.S)
    assert body, "start_watch_role not found"
    assert 'rc}" -eq 2' in body.group(1).replace("${", "{"), (
        "start_watch_role does not handle the ambiguous exit code"
    )


# ── behaviour, exercised against real processes ──────────────────────────────

_FUNCS = ("watch_supervisor_tag", "watch_pid_is_supervisor", "reconcile_watch_pid_file")


def _bash_prelude(watch_dir: Path) -> str:
    """Extract the helpers so they can run without executing the script's dispatch."""
    text = _script()
    out = [f'WATCH_DIR="{watch_dir}"', 'watch_pid_file() { echo "${WATCH_DIR}/${1}.pid"; }']
    for name in _FUNCS:
        m = re.search(rf"^{name}\(\) \{{.*?^\}}", text, re.S | re.M)
        assert m, f"could not extract {name}"
        out.append(m.group(0))
    return "\n".join(out) + "\n"


def _run(prelude: str, snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", prelude + snippet],
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
@pytest.mark.skipif(shutil.which("pgrep") is None, reason="needs pgrep")
def test_live_tagged_process_is_recognised(tmp_path):
    """A process carrying the tag validates; the same pid without it does not."""
    prelude = _bash_prelude(tmp_path)
    tag = "oc-watch-supervisor=pytestrole"
    # The trailing `:` matters. With a single simple command, bash exec-replaces
    # itself with `sleep`, which discards the script text — and the tag with it —
    # from the process command line. A second command forces bash to stay alive
    # as the parent, which is also how the real supervisors behave.
    proc = subprocess.Popen(
        ["bash", "-c", f"# {tag}\nsleep 30\n:"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.3)
        r = _run(prelude, f'watch_pid_is_supervisor {proc.pid} pytestrole && echo YES || echo NO')
        assert r.stdout.strip() == "YES", r.stderr

        # Same live pid, different role — must NOT validate. This is the
        # pid-reuse hole: kill -0 alone would say "running".
        r2 = _run(prelude, f'watch_pid_is_supervisor {proc.pid} otherrole && echo YES || echo NO')
        assert r2.stdout.strip() == "NO", r2.stderr
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
def test_stale_pid_file_is_not_trusted(tmp_path):
    """A pid file naming a live but unrelated process must not read as running.

    This is the bug on main today: `kill -0` succeeds on a recycled pid, so the
    role silently never starts.
    """
    prelude = _bash_prelude(tmp_path)
    # Point the pid file at a process that is definitely alive and definitely
    # not a watcher: this test's own interpreter.
    (tmp_path / "ghost.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    r = _run(prelude, 'watch_pid_is_supervisor "$(cat "${WATCH_DIR}/ghost.pid")" ghost && echo YES || echo NO')
    assert r.stdout.strip() == "NO", r.stderr


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
def test_reconcile_reports_absent_with_code_1(tmp_path):
    prelude = _bash_prelude(tmp_path)
    r = _run(prelude, 'reconcile_watch_pid_file nosuchrole_xyz; echo "rc=$?"')
    assert "rc=1" in r.stdout, r.stdout + r.stderr


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
def test_untagged_live_pid_is_not_reported_as_absent(tmp_path):
    """The upgrade case: a live supervisor started before tagging existed.

    Reporting it as absent (rc=1) would make start_watch_role launch a SECOND
    supervisor for a role that is already running — the duplicate-supervisor
    failure this whole change exists to prevent, caused by the fix itself. It
    must be its own outcome so the launcher can refuse.
    """
    prelude = _bash_prelude(tmp_path)
    (tmp_path / "legacy.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    r = _run(prelude, 'reconcile_watch_pid_file legacy; echo "rc=$?"')
    assert "rc=3" in r.stdout, (
        "a live but untagged pid must not read as absent; got: "
        + r.stdout + r.stderr
    )


def test_start_refuses_on_untagged_live_pid():
    """start_watch_role must handle rc=3, not fall through to launching."""
    text = _script()
    body = re.search(r"start_watch_role\(\) \{(.*?)\n  local log_file", text, re.S)
    assert body, "start_watch_role not found"
    assert 'rc}" -eq 3' in body.group(1).replace("${", "{"), (
        "start_watch_role does not handle the untagged-live-pid case, so an "
        "upgrade would double-run every already-running watcher"
    )
