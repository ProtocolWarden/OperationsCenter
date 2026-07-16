# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Regression suite that execs the *live* STEP 3 snippet from
``.console/haiku_collector_prompt.md`` against real `extraction-health` CLI
output, instead of a hand-copied reimplementation of its mapping logic.

#313 shipped with STEP 3 parsing the wrong CLI command's output (an
always-empty ``tests[]`` array), undetected because nothing executed the
actual markdown text against real output — only hand-reasoning kept them in
sync. `test_cli_extraction_health.py::test_step3_parser_maps_the_output`
still hand-reimplements STEP 3's mapping inline; it proves the mapping
*would* work if kept in sync, not that the file's actual snippet does. This
module extracts the real fenced python3 block from the prompt file at test
time and runs it as a subprocess, so a future incompatible edit to the
snippet fails these tests loudly instead of silently drifting.

Run from the OperationsCenter repo:

    pytest tests/unit/observer/test_step3_snippet_regression.py -v

``TestStep3SnippetExtraction`` validates the extraction mechanism itself
(fails loudly if the prompt file's heading/fence structure drifts).
``TestStep3SnippetAgainstRealOutput`` execs the extracted snippet against
real `extraction-health --format json` CLI output (produced via
`CliRunner`, not hand-built JSON) and asserts the mapped result against the
OUTPUT SCHEMA `extraction` contract.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from operations_center.observer.cli import app
from operations_center.observer.query_flaky import ExtractionHealth

runner = CliRunner()

PROMPT_PATH = Path(__file__).resolve().parents[3] / ".console" / "haiku_collector_prompt.md"

_STEP3_HEADING = "## STEP 3"
_NEXT_HEADING_RE = re.compile(r"\n## ")
_BASH_FENCE_RE = re.compile(r"```bash\n(.*?)\n```", re.DOTALL)
_PYTHON_DASH_C_RE = re.compile(r'python3 -c "\n(.*)\n"\s*\Z', re.DOTALL)


def _step3_section(markdown_text: str) -> str:
    """Return the STEP 3 section's text, up to (not including) the next `## ` heading."""
    start = markdown_text.find(_STEP3_HEADING)
    if start == -1:
        raise AssertionError(
            "STEP 3 snippet not found: no '## STEP 3' heading in haiku_collector_prompt.md "
            "(has the section been renamed or removed?)"
        )
    section = markdown_text[start:]
    next_heading = _NEXT_HEADING_RE.search(section, 1)
    return section[: next_heading.start()] if next_heading else section


def extract_step3_python_source(markdown_text: str) -> str:
    """Pull the literal python3 source out of STEP 3's second ```bash fence.

    STEP 3 has two fenced bash blocks: the CLI invocation, then a
    ``python3 -c "..."`` one-liner that maps the CLI's JSON into the
    collector's metric schema. This returns the exact python source text
    inside that second fence's ``-c "..."`` argument — no retyping.
    """
    section = _step3_section(markdown_text)
    fences = _BASH_FENCE_RE.findall(section)
    if len(fences) < 2:
        raise AssertionError(
            "STEP 3 snippet not found: expected 2 fenced ```bash blocks under "
            f"'## STEP 3' (CLI call + python3 mapper), found {len(fences)}. "
            "Has the snippet's structure changed?"
        )
    python_block = fences[1]
    match = _PYTHON_DASH_C_RE.match(python_block)
    if not match:
        raise AssertionError(
            "STEP 3 snippet not found: second bash fence is not a "
            f'`python3 -c "..."` block as expected:\n{python_block!r}'
        )
    return match.group(1)


def run_step3_snippet(cli_json_path: Path) -> dict:
    """Execute the live STEP 3 python3 mapper against a real CLI JSON file.

    Extracts the snippet fresh from the prompt file on every call (so edits
    to the file are picked up immediately) and runs it exactly as written,
    substituting only the hardcoded ``/tmp/oc_extraction_health.json`` path
    so parallel test runs don't collide on a shared file.
    """
    source = extract_step3_python_source(PROMPT_PATH.read_text())
    source = source.replace("/tmp/oc_extraction_health.json", str(cli_json_path))
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"STEP 3 snippet exited non-zero: {result.stderr}"
    return json.loads(result.stdout)


def _query_returning(health: ExtractionHealth) -> MagicMock:
    q = MagicMock()
    q.get_extraction_health.return_value = health
    return q


def _cli_json_for(health: ExtractionHealth, tmp_path: Path) -> Path:
    """Produce the real `extraction-health --format json` payload via CliRunner
    (the actual command STEP 3's bash line invokes) and write it to disk, the
    same way STEP 3's bash line redirects CLI stdout to a file."""
    with (
        patch("operations_center.observer.cli.TestSignalQuery") as mock_cls,
        patch("operations_center.observer.cli.ExtractionHistoryCollector"),
        patch("pathlib.Path.exists", return_value=True),
    ):
        mock_cls.return_value = _query_returning(health)
        result = runner.invoke(app, ["extraction-health", "--format", "json"])
    assert result.exit_code == 0
    cli_json_path = tmp_path / "oc_extraction_health.json"
    cli_json_path.write_text(result.stdout)
    return cli_json_path


class TestStep3SnippetExtraction:
    """The extraction mechanism itself: fail loudly if STEP 3 drifts out of
    a recognizable shape, rather than silently finding nothing."""

    def test_extracts_the_real_snippet_from_the_prompt_file(self) -> None:
        source = extract_step3_python_source(PROMPT_PATH.read_text())
        assert "json.load" in source
        assert "success_rate" in source

    def test_missing_step3_heading_fails_loudly(self) -> None:
        with pytest.raises(AssertionError, match="STEP 3 snippet not found"):
            extract_step3_python_source("# Some other document\n\nno STEP 3 here.\n")

    def test_step3_section_with_wrong_fence_count_fails_loudly(self) -> None:
        markdown = "## STEP 3 — EXTRACTION SIGNAL COLLECTION\n\n```bash\necho hi\n```\n"
        with pytest.raises(AssertionError, match="STEP 3 snippet not found"):
            extract_step3_python_source(markdown)

    def test_second_fence_not_python_dash_c_fails_loudly(self) -> None:
        markdown = (
            "## STEP 3 — EXTRACTION SIGNAL COLLECTION\n\n"
            "```bash\necho one\n```\n\n"
            "```bash\necho two\n```\n"
        )
        with pytest.raises(AssertionError, match="STEP 3 snippet not found"):
            extract_step3_python_source(markdown)


class TestStep3SnippetAgainstRealOutput:
    """Exec the live STEP 3 snippet against real `extraction-health` CLI JSON
    and validate the mapped result against the OUTPUT SCHEMA `extraction`
    contract (`haiku_collector_prompt.md` OUTPUT SCHEMA, lines ~312-320)."""

    _OUTPUT_SCHEMA_KEYS = frozenset(
        {
            "success_rate",
            "extracted_count",
            "total_count",
            "gap_count",
            "edge_case_count",
            "gaps",
            "edge_cases",
        }
    )

    def test_maps_typical_output_correctly(self, tmp_path: Path) -> None:
        health = ExtractionHealth(
            success_rate=75.0,
            complete_extraction=3,
            partial_extraction=0,
            no_extraction=1,
            edge_case_summary={"truncated_messages": 1, "special_chars": 0},
            gaps=["test_module::test_missing_both"],
            edge_cases=[{"test_id": "test_module::test_foo", "issue": "truncated_message"}],
        )
        cli_json_path = _cli_json_for(health, tmp_path)

        mapped = run_step3_snippet(cli_json_path)

        assert mapped == {
            "success_rate": 75.0,
            "extracted_count": 3,
            "total_count": 4,
            "gap_count": 1,
            "edge_case_count": 1,
            "gaps": ["test_module::test_missing_both"],
            "edge_cases": [{"test_id": "test_module::test_foo", "issue": "truncated_message"}],
        }

    def test_maps_all_defaults_zero_case(self, tmp_path: Path) -> None:
        cli_json_path = _cli_json_for(ExtractionHealth(), tmp_path)

        mapped = run_step3_snippet(cli_json_path)

        assert mapped == {
            "success_rate": 0.0,
            "extracted_count": 0,
            "total_count": 0,
            "gap_count": 0,
            "edge_case_count": 0,
            "gaps": [],
            "edge_cases": [],
        }

    def test_maps_multi_key_edge_case_summary(self, tmp_path: Path) -> None:
        health = ExtractionHealth(
            success_rate=40.0,
            complete_extraction=2,
            partial_extraction=1,
            no_extraction=2,
            edge_case_summary={
                "truncated_messages": 3,
                "special_chars": 2,
                "malformed_exceptions": 1,
            },
            edge_cases=[
                {"test_id": "test_module::test_a", "issue": "truncated_message"},
                {"test_id": "test_module::test_b", "issue": "special_chars"},
            ],
        )
        cli_json_path = _cli_json_for(health, tmp_path)

        mapped = run_step3_snippet(cli_json_path)

        assert mapped["edge_case_count"] == 6
        assert mapped["total_count"] == 5
        assert len(mapped["edge_cases"]) == 2

    def test_success_rate_rounded_to_one_decimal(self, tmp_path: Path) -> None:
        health = ExtractionHealth(success_rate=66.666, complete_extraction=2, no_extraction=1)
        cli_json_path = _cli_json_for(health, tmp_path)

        mapped = run_step3_snippet(cli_json_path)

        assert mapped["success_rate"] == 66.7

    def test_malformed_json_hits_parse_error_fallback(self, tmp_path: Path) -> None:
        cli_json_path = tmp_path / "oc_extraction_health.json"
        cli_json_path.write_text("{not valid json")

        mapped = run_step3_snippet(cli_json_path)

        assert mapped["success_rate"] is None
        assert mapped["extracted_count"] == 0
        assert mapped["total_count"] == 0
        assert mapped["gap_count"] == 0
        assert mapped["edge_case_count"] == 0
        assert mapped["gaps"] == []
        assert mapped["edge_cases"] == []
        assert "parse_error" in mapped

    def test_missing_file_hits_parse_error_fallback(self, tmp_path: Path) -> None:
        missing_path = tmp_path / "does_not_exist.json"

        mapped = run_step3_snippet(missing_path)

        assert mapped["success_rate"] is None
        assert "parse_error" in mapped

    def test_mapped_output_matches_output_schema_extraction_contract(self, tmp_path: Path) -> None:
        health = ExtractionHealth(
            success_rate=80.0,
            complete_extraction=4,
            no_extraction=1,
            gaps=["test_module::test_missing"],
            edge_cases=[{"test_id": "test_module::test_foo", "issue": "truncated_message"}],
        )
        cli_json_path = _cli_json_for(health, tmp_path)

        mapped = run_step3_snippet(cli_json_path)

        assert set(mapped.keys()) == self._OUTPUT_SCHEMA_KEYS
        assert isinstance(mapped["success_rate"], float)
        assert isinstance(mapped["extracted_count"], int)
        assert isinstance(mapped["total_count"], int)
        assert isinstance(mapped["gap_count"], int)
        assert isinstance(mapped["edge_case_count"], int)
        assert isinstance(mapped["gaps"], list)
        assert all(isinstance(g, str) for g in mapped["gaps"])
        assert isinstance(mapped["edge_cases"], list)
        for edge_case in mapped["edge_cases"]:
            assert set(edge_case.keys()) == {"test_id", "issue"}

    def test_gaps_pass_through_unmodified_from_cli_output(self, tmp_path: Path) -> None:
        health = ExtractionHealth(
            no_extraction=2,
            gaps=["test_module::test_a", "test_module::test_b"],
        )
        cli_json_path = _cli_json_for(health, tmp_path)

        mapped = run_step3_snippet(cli_json_path)

        assert mapped["gaps"] == ["test_module::test_a", "test_module::test_b"]
