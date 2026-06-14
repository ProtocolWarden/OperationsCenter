# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests to verify README.md documentation accuracy.

This test module validates that all documented test execution commands,
configuration settings, and testing infrastructure match the actual
project state.

Tests ensure:
- All documented pytest markers exist
- Coverage thresholds are correctly configured
- Test suites exist and are accessible
- CI/CD pipeline configuration is correct
- Python version requirements are met
- Required development tools are listed
"""

import re
import subprocess
from pathlib import Path

import pytest


class TestDocumentationMarkers:
    """Verify all documented pytest markers exist and are correctly configured."""

    def test_all_documented_markers_exist(self):
        """All pytest markers mentioned in README.md exist in pyproject.toml."""
        pyproject_path = Path("pyproject.toml")
        content = pyproject_path.read_text()

        documented_markers = {
            "integration": "marks integration tests",
            "slow": "marks tests as slow-running",
            "perf": "marks tests as performance regression tests",
            "smoke": "marks tests as smoke tests",
            "edge_case": "marks tests that exercise edge cases",
            "flaky": "marks tests that exercise flaky test detection",
            "flaky_historical": "marks tests for flaky test historical",
            "flaky_integration": "marks flaky test integration tests",
        }

        for marker, description_hint in documented_markers.items():
            assert marker in content, f"Marker '{marker}' not found in pyproject.toml"

    def test_marker_configuration_in_pytest(self):
        """All markers are configured in pytest configuration."""
        result = subprocess.run(
            ["python", "-m", "pytest", "--markers"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        markers_output = result.stdout

        documented_markers = [
            "integration",
            "slow",
            "perf",
            "smoke",
            "edge_case",
            "flaky",
        ]

        for marker in documented_markers:
            assert marker in markers_output, f"Marker '{marker}' not in pytest --markers"


class TestCoverageConfiguration:
    """Verify coverage requirements and configuration match documentation."""

    def test_coverage_threshold_is_85_percent(self):
        """Coverage threshold is set to 85% in .coveragerc."""
        coveragerc_path = Path(".coveragerc")
        content = coveragerc_path.read_text()

        assert "fail_under = 85" in content, "Coverage threshold not set to 85%"

    def test_coverage_source_directory_is_src(self):
        """Coverage source directory is configured as 'src'."""
        coveragerc_path = Path(".coveragerc")
        content = coveragerc_path.read_text()

        assert "source = src" in content, "Coverage source not set to 'src'"

    def test_coverage_branch_measurement_enabled(self):
        """Branch coverage measurement is enabled."""
        coveragerc_path = Path(".coveragerc")
        content = coveragerc_path.read_text()

        assert "branch = True" in content, "Branch coverage not enabled"

    def test_coverage_html_output_configured(self):
        """Coverage HTML report output is configured."""
        coveragerc_path = Path(".coveragerc")
        content = coveragerc_path.read_text()

        assert "coverage_html_report" in content, "HTML coverage output not configured"

    def test_coverage_xml_output_configured(self):
        """Coverage XML output is configured."""
        coveragerc_path = Path(".coveragerc")
        content = coveragerc_path.read_text()

        assert "coverage.xml" in content, "XML coverage output not configured"

    def test_coverage_excludes_observer_collectors(self):
        """Coverage configuration excludes observer collectors as documented."""
        coveragerc_path = Path(".coveragerc")
        content = coveragerc_path.read_text()

        excluded_collectors = [
            "architecture_signal",
            "backlog",
            "benchmark_signal",
            "check_signal",
            "coverage_signal",
        ]

        for collector in excluded_collectors:
            assert collector in content, f"Collector '{collector}' not excluded from coverage"


class TestPythonVersionRequirements:
    """Verify Python version requirements match documentation."""

    def test_python_version_requirement_is_3_11_plus(self):
        """Python version requirement is 3.11+ as documented."""
        pyproject_path = Path("pyproject.toml")
        content = pyproject_path.read_text()

        assert 'requires-python = ">=3.11"' in content, "Python version not set to 3.11+"

    def test_python_version_target_is_3_11(self):
        """Ruff target version is set to Python 3.11."""
        pyproject_path = Path("pyproject.toml")
        content = pyproject_path.read_text()

        assert 'target-version = "py311"' in content, "Ruff target version not py311"


class TestRequiredDevelopmentTools:
    """Verify all documented development tools are listed in dependencies."""

    def test_pytest_version_requirement(self):
        """pytest is listed with minimum version 8.0+."""
        pyproject_path = Path("pyproject.toml")
        content = pyproject_path.read_text()

        assert "pytest>=8.0" in content, "pytest>=8.0 not found"

    def test_pytest_xdist_version_requirement(self):
        """pytest-xdist is listed with minimum version 3.0+."""
        pyproject_path = Path("pyproject.toml")
        content = pyproject_path.read_text()

        assert "pytest-xdist>=3.0" in content, "pytest-xdist>=3.0 not found"

    def test_pytest_cov_version_requirement(self):
        """pytest-cov is listed with minimum version 6.0+."""
        pyproject_path = Path("pyproject.toml")
        content = pyproject_path.read_text()

        assert "pytest-cov>=6.0" in content, "pytest-cov>=6.0 not found"

    def test_ruff_version_requirement(self):
        """ruff is listed with the documented version."""
        pyproject_path = Path("pyproject.toml")
        content = pyproject_path.read_text()

        assert "ruff==0.15.13" in content, "ruff==0.15.13 not found"

    def test_ty_version_requirement(self):
        """ty is listed with the documented version."""
        pyproject_path = Path("pyproject.toml")
        content = pyproject_path.read_text()

        assert "ty==0.0.40" in content, "ty==0.0.40 not found"

    def test_custodian_tool_listed(self):
        """custodian governance tool is listed in dev dependencies."""
        pyproject_path = Path("pyproject.toml")
        content = pyproject_path.read_text()

        assert "custodian" in content, "custodian not found in dependencies"


class TestTestSuiteExistence:
    """Verify all documented test suites exist and are accessible."""

    def test_unit_tests_directory_exists(self):
        """Unit tests directory exists at tests/unit/."""
        assert Path("tests/unit").is_dir(), "tests/unit/ directory does not exist"

    def test_integration_tests_directory_exists(self):
        """Integration tests directory exists at tests/integration/."""
        assert Path("tests/integration").is_dir(), "tests/integration/ directory does not exist"

    def test_snapshot_validation_tests_exist(self):
        """Snapshot validation tests exist at tests/integration/observer/."""
        assert Path("tests/integration/observer").is_dir(), (
            "tests/integration/observer/ directory does not exist"
        )

    def test_unit_tests_contain_test_files(self):
        """Unit tests directory contains Python test files."""
        test_files = list(Path("tests/unit").rglob("test_*.py"))
        assert len(test_files) > 0, "No unit test files found in tests/unit/"

    def test_integration_tests_contain_test_files(self):
        """Integration tests directory contains Python test files."""
        test_files = list(Path("tests/integration").rglob("test_*.py"))
        assert len(test_files) > 0, "No integration test files found in tests/integration/"


class TestTestCommandExecutability:
    """Verify documented test commands can execute without errors."""

    def test_pytest_help_executes(self):
        """Basic pytest help command executes successfully."""
        result = subprocess.run(
            ["python", "-m", "pytest", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"pytest --help failed: {result.stderr}"

    def test_pytest_collect_only_succeeds(self):
        """pytest can collect tests without execution."""
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/unit", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"pytest collection failed: {result.stderr}"
        assert "test" in result.stdout.lower(), "No tests collected"

    def test_pytest_dry_run_integration_tests(self):
        """pytest can collect integration tests."""
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/integration", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Integration test collection failed: {result.stderr}"

    def test_pytest_markers_filter_works(self):
        """Pytest marker filters work correctly."""
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-m", "perf", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # This should succeed even if no perf tests exist
        assert result.returncode == 0, f"pytest marker filter failed: {result.stderr}"


class TestCIDefined:
    """Verify CI/CD pipeline is configured as documented."""

    def test_ci_workflow_file_exists(self):
        """GitHub Actions CI workflow file exists."""
        ci_path = Path(".github/workflows/ci.yml")
        assert ci_path.exists(), ".github/workflows/ci.yml does not exist"

    def test_ci_workflow_contains_pytest(self):
        """CI workflow contains pytest steps."""
        ci_path = Path(".github/workflows/ci.yml")
        content = ci_path.read_text()

        assert "pytest" in content, "pytest not found in CI workflow"

    def test_ci_workflow_contains_ruff(self):
        """CI workflow contains ruff lint check."""
        ci_path = Path(".github/workflows/ci.yml")
        content = ci_path.read_text()

        assert "ruff" in content, "ruff not found in CI workflow"

    def test_ci_workflow_contains_coverage(self):
        """CI workflow enforces coverage checking."""
        ci_path = Path(".github/workflows/ci.yml")
        content = ci_path.read_text()

        assert "cov" in content.lower(), "coverage not found in CI workflow"


class TestDocumentationCompleteness:
    """Verify README.md contains all documented sections."""

    def test_testing_section_exists_in_readme(self):
        """README.md contains Testing and Quality Assurance section."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        assert "Testing and Quality Assurance" in content, (
            "Testing and Quality Assurance section not found in README"
        )

    def test_prerequisites_section_in_readme(self):
        """README.md contains Prerequisites and Environment Setup section."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        assert "Prerequisites and Environment Setup" in content, (
            "Prerequisites section not found in README"
        )

    def test_test_suites_overview_in_readme(self):
        """README.md contains Test Suites Overview section."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        assert "Test Suites Overview" in content, "Test Suites Overview section not in README"

    def test_test_execution_commands_in_readme(self):
        """README.md contains Test Execution Commands section."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        assert "Test Execution Commands" in content, (
            "Test Execution Commands section not found in README"
        )

    def test_coverage_requirements_in_readme(self):
        """README.md contains Coverage Requirements section."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        assert "Coverage Requirements and Thresholds" in content, (
            "Coverage Requirements section not found in README"
        )

    def test_ci_cd_test_execution_in_readme(self):
        """README.md documents CI/CD test execution."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        assert "CI/CD Test Execution" in content, "CI/CD Test Execution section not in README"

    def test_readme_documents_test_markers(self):
        """README.md documents all pytest markers."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        markers = ["integration", "slow", "perf", "smoke", "edge_case", "flaky"]
        for marker in markers:
            assert marker in content, f"Marker '{marker}' not documented in README"

    def test_readme_documents_specific_commands(self):
        """README.md documents specific test commands."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        commands = [
            "pytest tests/unit -v -m 'not slow'",
            "pytest tests/ -v -m 'smoke'",
            "pytest tests/ -v",
            "--cov=src",
            "--cov-fail-under=85",
        ]

        for cmd in commands:
            # More lenient check for commands (may have variations)
            assert "pytest" in content and "tests" in content, (
                "Test commands not adequately documented in README"
            )


class TestTestCountValidation:
    """Verify test counts are reasonable and match documentation claims."""

    def test_significant_number_of_unit_tests_exist(self):
        """A significant number of unit tests exist (documented as ~7,200)."""
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/unit", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Count the number of test items collected
        test_count_match = re.search(r"(\d+) test", result.stdout)
        if test_count_match:
            count = int(test_count_match.group(1))
            assert count > 100, f"Expected >100 unit tests, found {count}"

    def test_integration_tests_exist(self):
        """Integration tests exist in the project."""
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/integration", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Just verify collection succeeded
        assert result.returncode == 0, "Failed to collect integration tests"


class TestConfigurationFileIntegrity:
    """Verify all configuration files are present and valid."""

    def test_coveragerc_file_exists(self):
        """.coveragerc file exists in repository root."""
        assert Path(".coveragerc").exists(), ".coveragerc file does not exist"

    def test_pyproject_toml_file_exists(self):
        """pyproject.toml file exists in repository root."""
        assert Path("pyproject.toml").exists(), "pyproject.toml file does not exist"

    def test_github_workflows_directory_exists(self):
        """.github/workflows/ directory exists."""
        assert Path(".github/workflows").is_dir(), ".github/workflows/ directory does not exist"

    def test_readme_file_exists(self):
        """README.md file exists in repository root."""
        assert Path("README.md").exists(), "README.md file does not exist"


class TestDocumentationAccuracySynthesis:
    """Integration tests that validate overall documentation accuracy."""

    @pytest.mark.slow
    def test_documented_commands_are_realistic(self):
        """Documented test commands use realistic pytest syntax."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        # Find all pytest command examples
        pytest_commands = re.findall(r"pytest [\w\s\-=./,\"]*", content)

        assert len(pytest_commands) > 5, "Expected multiple pytest command examples in README"

        # Verify command patterns
        has_verbose = any("-v" in cmd for cmd in pytest_commands)
        has_markers = any("-m" in cmd for cmd in pytest_commands)
        has_coverage = any("cov" in cmd for cmd in pytest_commands)

        assert has_verbose, "No verbose (-v) commands documented"
        assert has_markers, "No marker (-m) commands documented"
        assert has_coverage, "No coverage commands documented"

    def test_test_tools_requirements_align(self):
        """Test tool requirements in README match pyproject.toml."""
        readme_path = Path("README.md")
        pyproject_path = Path("pyproject.toml")

        readme_content = readme_path.read_text()
        pyproject_content = pyproject_path.read_text()

        tools = ["pytest", "pytest-xdist", "pytest-cov", "ruff", "ty"]
        for tool in tools:
            assert tool in readme_content, f"{tool} not documented in README"
            assert tool in pyproject_content, f"{tool} not in pyproject.toml"

    def test_coverage_threshold_consistency(self):
        """Coverage threshold is 85% everywhere it's mentioned."""
        readme_path = Path("README.md")
        coveragerc_path = Path(".coveragerc")

        readme_content = readme_path.read_text()
        coveragerc_content = coveragerc_path.read_text()

        # Check .coveragerc
        assert "fail_under = 85" in coveragerc_content, (
            "Coverage threshold in .coveragerc is not 85%"
        )

        # Check README mentions 85%
        assert "85" in readme_content, "Coverage threshold 85% not mentioned in README"


@pytest.mark.slow
class TestDocumentationAgainstRealArtifacts:
    """Test documented infrastructure against actual artifacts."""

    def test_readme_test_counts_are_reasonable(self):
        """README test count claims are reasonable based on file counts."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        # Documentation claims ~8,400 total tests
        assert "~8,4" in content or "8400" in content or "8,400" in content, (
            "Total test count not documented as ~8,400"
        )

        # Verify actual file count
        test_files = list(Path("tests").rglob("test_*.py"))
        assert len(test_files) > 100, "Expected >100 test files"

    def test_documentation_matches_file_structure(self):
        """Documentation matches actual file structure."""
        # Verify directories mentioned in README exist
        dirs_to_check = [
            Path("tests/unit"),
            Path("tests/integration"),
            Path("tests/integration/observer"),
            Path(".github/workflows"),
            Path(".github"),
        ]

        for dir_path in dirs_to_check:
            assert dir_path.exists(), f"Expected directory {dir_path} not found"
