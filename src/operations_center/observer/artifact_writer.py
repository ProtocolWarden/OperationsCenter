# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path

from operations_center.observer.models import RepoStateSnapshot


class ObserverArtifactWriter:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path("tools/report/operations_center/observer")

    def write(self, snapshot: RepoStateSnapshot) -> list[str]:
        run_dir = self.root / snapshot.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        json_path = run_dir / "repo_state_snapshot.json"
        json_path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")

        md_path = run_dir / "repo_state_snapshot.md"
        md_lines = [
            "# Repo State Snapshot",
            f"- run_id: {snapshot.run_id}",
            f"- observed_at: {snapshot.observed_at.isoformat()}",
            f"- repo_name: {snapshot.repo.name}",
            f"- repo_path: {snapshot.repo.path}",
            f"- current_branch: {snapshot.repo.current_branch}",
            f"- base_branch: {snapshot.repo.base_branch or 'unknown'}",
            f"- is_dirty: {snapshot.repo.is_dirty}",
            "",
            "## Recent Commits",
        ]
        commit_lines = [
            f"- {c.sha_short} {c.author} {c.timestamp.isoformat()} {c.subject}"
            for c in snapshot.signals.recent_commits
        ]
        md_lines.extend(commit_lines or ["- none"])
        md_lines.extend(["", "## File Hotspots"])
        md_lines.extend(
            [
                f"- {hotspot.path}: {hotspot.touch_count}"
                for hotspot in snapshot.signals.file_hotspots
            ]
            or ["- none"]
        )
        test_signal = snapshot.signals.test_signal
        test_observed = (
            test_signal.observed_at.isoformat()
            if test_signal.observed_at
            else "none"
        )
        dependency_drift = snapshot.signals.dependency_drift
        drift_observed = (
            dependency_drift.observed_at.isoformat()
            if dependency_drift.observed_at
            else "none"
        )
        md_lines.extend(
            [
                "",
                "## Test Signal",
                f"- status: {test_signal.status}",
                f"- source: {test_signal.source or 'none'}",
                f"- observed_at: {test_observed}",
                f"- summary: {test_signal.summary or 'none'}",
                "",
                "## Dependency Drift",
                f"- status: {dependency_drift.status}",
                f"- source: {dependency_drift.source or 'none'}",
                f"- observed_at: {drift_observed}",
                f"- summary: {dependency_drift.summary or 'none'}",
                "",
                "## TODO Signal",
                f"- todo_count: {snapshot.signals.todo_signal.todo_count}",
                f"- fixme_count: {snapshot.signals.todo_signal.fixme_count}",
            ]
        )
        md_lines.extend(
            [f"- {item.path}: {item.count}" for item in snapshot.signals.todo_signal.top_files]
            or ["- none"]
        )
        if snapshot.collector_errors:
            md_lines.extend(["", "## Collector Errors"])
            md_lines.extend(
                [f"- {name}: {error}" for name, error in snapshot.collector_errors.items()]
            )
        md_path.write_text("\n".join(md_lines), encoding="utf-8")
        return [str(json_path), str(md_path)]
