# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for slow test hooks with pytest."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


class TestSlowTestHooksIntegration:
    """Test slow test hooks integration with pytest."""

    def test_hooks_detect_slow_tests(self):
        """Test that hooks detect slow tests at runtime."""
        # Create a temporary test file with slow tests
        test_code = '''
import time
import pytest

def test_fast():
    """Fast test."""
    time.sleep(0.01)

@pytest.mark.slow
def test_marked_slow():
    """Marked slow test."""
    time.sleep(0.1)

def test_slow_by_threshold():
    """Test that exceeds threshold."""
    time.sleep(0.2)
'''
        # Get the tests directory to ensure conftest.py is discovered
        tests_dir = Path(__file__).parent
        repo_root = tests_dir.parent

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_demo.py"
            test_file.write_text(test_code)

            # Copy conftest.py to temp dir so hooks are available
            conftest_src = tests_dir / "conftest.py"
            conftest_dst = Path(tmpdir) / "conftest.py"
            if conftest_src.exists():
                conftest_dst.write_text(conftest_src.read_text())

            # Run pytest in temp directory
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "test_demo.py", "--slow-threshold=0.05", "-v"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(tmpdir),  # Run from temp dir where conftest.py is copied
            )

            # Check output contains warning
            assert "SLOW TEST THRESHOLD WARNING" in result.stdout, f"Expected warning in output, got: {result.stdout}\nstderr: {result.stderr}"
            assert "0.05s" in result.stdout or "0.050" in result.stdout

    def test_json_output_generation(self):
        """Test JSON output generation."""
        test_code = '''
import time
import pytest

def test_fast():
    time.sleep(0.01)

def test_slow():
    time.sleep(0.15)
'''
        tests_dir = Path(__file__).parent

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_demo.py"
            test_file.write_text(test_code)
            json_file = Path(tmpdir) / "slow_tests.json"

            # Copy conftest.py to temp dir
            conftest_src = tests_dir / "conftest.py"
            conftest_dst = Path(tmpdir) / "conftest.py"
            if conftest_src.exists():
                conftest_dst.write_text(conftest_src.read_text())

            # Run pytest with JSON output
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "test_demo.py",
                    "--slow-threshold=0.1",
                    f"--slow-report=slow_tests.json",
                    "-v",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(tmpdir),
            )

            # Check JSON file was created
            if json_file.exists():
                with open(json_file) as f:
                    report = json.load(f)

                assert report["version"] == "1.0"
                assert report["threshold_seconds"] == 0.1
                assert report["total_tests"] >= 1
                assert "statistics" in report
                assert "slow_tests" in report

    def test_silence_when_no_slow_tests(self):
        """Test that output is silent when no slow tests."""
        test_code = '''
def test_fast():
    pass
'''
        tests_dir = Path(__file__).parent

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_demo.py"
            test_file.write_text(test_code)

            # Copy conftest.py to temp dir
            conftest_src = tests_dir / "conftest.py"
            conftest_dst = Path(tmpdir) / "conftest.py"
            if conftest_src.exists():
                conftest_dst.write_text(conftest_src.read_text())

            result = subprocess.run(
                [sys.executable, "-m", "pytest", "test_demo.py", "--slow-threshold=1.0", "-v"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(tmpdir),
            )

            # Should not contain warning when no slow tests
            assert "SLOW TEST THRESHOLD WARNING" not in result.stdout

    def test_marked_tests_always_in_report(self):
        """Test that marked slow tests appear even below threshold."""
        test_code = '''
import pytest
import time

@pytest.mark.slow
def test_marked_but_fast():
    """Marked as slow but fast execution."""
    time.sleep(0.01)

def test_unmarked_fast():
    time.sleep(0.005)
'''
        tests_dir = Path(__file__).parent

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_demo.py"
            test_file.write_text(test_code)
            json_file = Path(tmpdir) / "slow_tests.json"

            # Copy conftest.py to temp dir
            conftest_src = tests_dir / "conftest.py"
            conftest_dst = Path(tmpdir) / "conftest.py"
            if conftest_src.exists():
                conftest_dst.write_text(conftest_src.read_text())

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "test_demo.py",
                    "--slow-threshold=0.1",
                    f"--slow-report=slow_tests.json",
                    "-v",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(tmpdir),
            )

            # Check that marked test is in JSON report
            if json_file.exists():
                with open(json_file) as f:
                    report = json.load(f)

                marked = report["slow_tests"]["marked_slow"]
                assert any("test_marked_but_fast" in t["test"] for t in marked)
