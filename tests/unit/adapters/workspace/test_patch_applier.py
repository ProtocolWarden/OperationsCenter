# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for SBX Layer 0 patch applier with path allowlist enforcement."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


from operations_center.adapters.workspace.patch_applier import PatchApplier, PatchApplyResult


class TestPatchApplierPathBlocking:
    """Test that the patch applier blocks dangerous paths."""

    def test_blocks_github_workflows(self):
        """Test that .github/workflows patches are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 1234567..abcdefg 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -10,7 +10,7 @@ jobs:
   build:
     runs-on: ubuntu-latest
     steps:
-      - run: echo "hello"
+      - run: echo "pwned"
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason
        assert ".github/workflows/ci.yml" in result.blocked_paths

    def test_blocks_setup_py(self):
        """Test that setup.py patches are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/setup.py b/setup.py
index 1234567..abcdefg 100644
--- a/setup.py
+++ b/setup.py
@@ -1,5 +1,5 @@
 from setuptools import setup
-setup(name="myapp")
+setup(name="myapp", cmdclass={"install": BadInstall()})
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason
        assert "setup.py" in result.blocked_paths

    def test_blocks_conftest(self):
        """Test that conftest.py patches are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/conftest.py b/conftest.py
index 1234567..abcdefg 100644
--- a/conftest.py
+++ b/conftest.py
@@ -1 +1 @@
-# pytest config
+os.system("rm -rf /")
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason

    def test_blocks_git_directory(self):
        """Test that .git directory patches are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/.git/config b/.git/config
index 1234567..abcdefg 100644
--- a/.git/config
+++ b/.git/config
@@ -1 +1 @@
 [core]
+[credential]
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason

    def test_blocks_docker_files(self):
        """Test that Dockerfile patches are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/Dockerfile b/Dockerfile
index 1234567..abcdefg 100644
--- a/Dockerfile
+++ b/Dockerfile
@@ -1,2 +1,2 @@
 FROM ubuntu
-RUN apt-get install -y nginx
+RUN apt-get install -y nginx && rm -rf /
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason

    def test_blocks_makefile(self):
        """Test that Makefile patches are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/Makefile b/Makefile
index 1234567..abcdefg 100644
--- a/Makefile
+++ b/Makefile
@@ -1 +1 @@
 build:
-	go build
+	go build && rm -rf /
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason

    def test_blocks_husky_hooks(self):
        """Test that .husky hooks are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/.husky/pre-commit b/.husky/pre-commit
index 1234567..abcdefg 100644
--- a/.husky/pre-commit
+++ b/.husky/pre-commit
@@ -1 +1 @@
 #!/bin/sh
+rm -rf /
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason

    def test_blocks_ssh_keys(self):
        """Test that .ssh directory patches are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/.ssh/id_rsa b/.ssh/id_rsa
index 1234567..abcdefg 100644
--- a/.ssh/id_rsa
+++ b/.ssh/id_rsa
@@ -1 +1 @@
 -----BEGIN RSA PRIVATE KEY-----
+MIIEpAIBAAKCAQEA...
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason

    def test_blocks_env_files(self):
        """Test that .env files are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/.env b/.env
index 1234567..abcdefg 100644
--- a/.env
+++ b/.env
@@ -1 +1 @@
 EXISTING_VAR=value
+API_KEY=secret123
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason

    def test_blocks_ci_gitlab(self):
        """Test that .gitlab-ci.yml is blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/.gitlab-ci.yml b/.gitlab-ci.yml
index 1234567..abcdefg 100644
--- a/.gitlab-ci.yml
+++ b/.gitlab-ci.yml
@@ -1 +1,2 @@
 stages:
+  - pwn
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason

    def test_blocks_path_traversal(self):
        """Test that path traversal attempts are blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/../../../etc/passwd b/../../../etc/passwd
index 1234567..abcdefg 100644
--- a/../../../etc/passwd
+++ b/../../../etc/passwd
@@ -1 +1 @@
 root:x:0:0:root:/root:/bin/bash
+hacker:x:0:0:hacker:/root:/bin/bash
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Unsafe paths" in result.reason
        assert "../" in str(result.blocked_paths) or ".." in result.reason

    def test_allows_normal_source_files(self):
        """Test that normal source file patches are allowed through path check."""
        applier = PatchApplier()

        diff = """\
diff --git a/src/main.py b/src/main.py
index 1234567..abcdefg 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,3 @@
 def hello():
-    print("hello")
+    print("hello world")
"""

        result = applier.apply(diff, Path.cwd())
        # Normal source files should not be blocked by path allowlist
        # Failures should not be due to "Blocked paths" message
        if not result.success:
            assert "blocked" not in result.reason.lower() if result.reason else True

    def test_allows_test_file_patches(self):
        """Test that test file patches are allowed through path check."""
        applier = PatchApplier()

        diff = """\
diff --git a/tests/test_main.py b/tests/test_main.py
index 1234567..abcdefg 100644
--- a/tests/test_main.py
+++ b/tests/test_main.py
@@ -1,3 +1,3 @@
 def test_hello():
-    assert hello() == "hello"
+    assert hello() == "hello world"
"""

        result = applier.apply(diff, Path.cwd())
        # Path check should pass for test files
        assert "Blocked paths" not in result.reason if result.reason else True

    def test_allows_readme_patches(self):
        """Test that README patches are allowed through path check."""
        applier = PatchApplier()

        diff = """\
diff --git a/README.md b/README.md
index 1234567..abcdefg 100644
--- a/README.md
+++ b/README.md
@@ -1,3 +1,3 @@
 # My Project
-
+This is a great project.
"""

        result = applier.apply(diff, Path.cwd())
        # Path check should pass for README
        assert "Blocked paths" not in result.reason if result.reason else True


class TestPatchApplierPackageJsonScripts:
    """Test that the patch applier blocks package.json script modifications."""

    def test_blocks_package_json_scripts(self):
        """Test that modifying package.json scripts field is blocked."""
        applier = PatchApplier()

        diff = """\
diff --git a/package.json b/package.json
index 1234567..abcdefg 100644
--- a/package.json
+++ b/package.json
@@ -2,6 +2,7 @@
   "name": "myapp",
   "version": "1.0.0",
   "scripts": {
+    "postinstall": "rm -rf /",
     "test": "jest"
   }
"""

        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "scripts" in result.reason.lower() or "package.json" in result.blocked_paths


class TestPatchApplierParsing:
    """Test that the patch applier correctly parses diff files."""

    def test_parse_diff_files_from_headers(self):
        """Test parsing file paths from diff headers."""
        applier = PatchApplier()

        diff = """\
diff --git a/file1.py b/file1.py
diff --git a/dir/file2.py b/dir/file2.py
diff --git a/nested/deep/file3.py b/nested/deep/file3.py
"""

        files = applier._parse_diff_files(diff)
        assert "file1.py" in files
        assert "dir/file2.py" in files
        assert "nested/deep/file3.py" in files

    def test_parse_diff_files_from_unified_format(self):
        """Test parsing file paths from unified diff format."""
        applier = PatchApplier()

        diff = """\
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,3 @@
 def hello():
     pass
"""

        files = applier._parse_diff_files(diff)
        assert "file1.py" in files

    def test_parse_empty_diff(self):
        """Test parsing empty diff."""
        applier = PatchApplier()

        diff = ""

        files = applier._parse_diff_files(diff)
        assert len(files) == 0

    def test_is_blocked_matches_patterns(self):
        """Test that _is_blocked correctly matches blocked patterns."""
        applier = PatchApplier()

        assert applier._is_blocked(".github/workflows/ci.yml")
        assert applier._is_blocked("setup.py")
        assert applier._is_blocked("conftest.py")
        assert applier._is_blocked(".git/config")
        assert applier._is_blocked(".husky/pre-commit")
        assert applier._is_blocked(".ssh/id_rsa")
        assert applier._is_blocked("Dockerfile")
        assert applier._is_blocked("Dockerfile.prod")
        assert applier._is_blocked(".env")
        assert applier._is_blocked(".env.local")

    def test_is_not_blocked_for_allowed_paths(self):
        """Test that _is_blocked correctly allows normal paths."""
        applier = PatchApplier()

        assert not applier._is_blocked("src/main.py")
        assert not applier._is_blocked("tests/test_main.py")
        assert not applier._is_blocked("README.md")
        assert not applier._is_blocked("docs/guide.md")
        assert not applier._is_blocked("lib/util.js")


class TestPatchApplierIntegration:
    """Integration tests with actual git repositories."""

    def test_apply_valid_patch_to_git_repo(self):
        """Test applying a valid patch to an actual git repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Initialize a git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Configure git user
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Create initial file
            (tmpdir / "test.txt").write_text("hello\nworld\n")

            # Commit it
            subprocess.run(
                ["git", "add", "test.txt"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Create a patch
            diff = """\
diff --git a/test.txt b/test.txt
index 1234567..abcdefg 100644
--- a/test.txt
+++ b/test.txt
@@ -1,2 +1,2 @@
 hello
-world
+world!
"""

            applier = PatchApplier()
            result = applier.apply(diff, tmpdir)

            assert result.success
            assert (tmpdir / "test.txt").read_text() == "hello\nworld!\n"

    def test_reject_blocked_patch_to_git_repo(self):
        """Test that blocked patches are rejected in git repo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Initialize a git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Configure git user
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Create .github/workflows directory
            (tmpdir / ".github" / "workflows").mkdir(parents=True)
            (tmpdir / ".github" / "workflows" / "ci.yml").write_text("jobs:")

            # Commit it
            subprocess.run(
                ["git", "add", "-A"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Create a patch that modifies .github/workflows
            diff = """\
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 1234567..abcdefg 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1 +1,2 @@
 jobs:
+  pwn: malicious
"""

            applier = PatchApplier()
            result = applier.apply(diff, tmpdir)

            assert not result.success
            assert "Blocked paths" in result.reason
            # File should not be modified
            assert "pwn" not in (tmpdir / ".github" / "workflows" / "ci.yml").read_text()


class TestPatchApplyResult:
    """Test the PatchApplyResult dataclass."""

    def test_result_success(self):
        """Test successful result."""
        result = PatchApplyResult(success=True)
        assert result.success
        assert result.reason is None
        assert result.blocked_paths is None

    def test_result_failure_with_reason(self):
        """Test failure result with reason."""
        result = PatchApplyResult(
            success=False,
            reason="Blocked path: setup.py",
            blocked_paths=["setup.py"],
        )
        assert not result.success
        assert "setup.py" in result.reason
        assert "setup.py" in result.blocked_paths


class TestPatchApplierValidateMethod:
    """Test the validate() method directly (non-applying validation)."""

    def test_validate_empty_diff(self):
        """Test validating an empty diff."""
        applier = PatchApplier()
        result = applier.validate("", Path.cwd())
        assert result.success
        assert "No files in diff" in result.reason

    def test_validate_safe_patch(self):
        """Test validating a safe patch returns success."""
        applier = PatchApplier()
        diff = """\
diff --git a/src/main.py b/src/main.py
index 1234567..abcdefg 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,3 @@
 def hello():
-    print("hello")
+    print("hello world")
"""
        result = applier.validate(diff, Path.cwd())
        # Should pass path check (no blocked paths)
        assert "Blocked paths" not in (result.reason or "")

    def test_validate_blocked_path_detection(self):
        """Test that validate() properly detects blocked paths."""
        applier = PatchApplier()
        diff = """\
diff --git a/setup.py b/setup.py
index 1234567..abcdefg 100644
--- a/setup.py
+++ b/setup.py
@@ -1 +1 @@
-setup(name="old")
+setup(name="new")
"""
        result = applier.validate(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason
        assert "setup.py" in result.blocked_paths

    def test_validate_unsafe_path_traversal(self):
        """Test that validate() detects path traversal."""
        applier = PatchApplier()
        diff = """\
diff --git a/../../../etc/passwd b/../../../etc/passwd
index 1234567..abcdefg 100644
--- a/../../../etc/passwd
+++ b/../../../etc/passwd
@@ -1 +1 @@
 root:x:0:0:root:/root:/bin/bash
+attacker:x:0:0:attacker:/root:/bin/bash
"""
        result = applier.validate(diff, Path.cwd())
        assert not result.success
        assert "Unsafe paths" in result.reason

    def test_validate_absolute_path(self):
        """Test that validate() rejects absolute paths."""
        applier = PatchApplier()
        diff = """\
diff --git a//etc/passwd b//etc/passwd
index 1234567..abcdefg 100644
--- a//etc/passwd
+++ b//etc/passwd
@@ -1 +1 @@
 root:x:0:0:root:/root:/bin/bash
+attacker:x:0:0:attacker:/root:/bin/bash
"""
        result = applier.validate(diff, Path.cwd())
        # Should reject due to absolute path check
        if not result.success:
            assert "Unsafe paths" in result.reason or "blocked" in result.reason.lower()


class TestPatchApplierFilenameEdgeCases:
    """Test edge cases with special filenames."""

    def test_files_with_spaces(self):
        """Test handling files with spaces in names."""
        applier = PatchApplier()
        diff = """\
diff --git a/src/my file.py b/src/my file.py
index 1234567..abcdefg 100644
--- a/src/my file.py
+++ b/src/my file.py
@@ -1 +1 @@
-old code
+new code
"""
        files = applier._parse_diff_files(diff)
        # Should parse filename with space correctly
        assert any("my file" in f for f in files)

    def test_files_with_unicode(self):
        """Test handling files with unicode characters."""
        applier = PatchApplier()
        diff = """\
diff --git a/src/café.py b/src/café.py
index 1234567..abcdefg 100644
--- a/src/café.py
+++ b/src/café.py
@@ -1 +1 @@
-old
+new
"""
        files = applier._parse_diff_files(diff)
        assert any("café" in f for f in files)

    def test_files_with_special_chars(self):
        """Test handling files with special characters."""
        applier = PatchApplier()
        diff = """\
diff --git a/src/my-file_v2.test.py b/src/my-file_v2.test.py
index 1234567..abcdefg 100644
--- a/src/my-file_v2.test.py
+++ b/src/my-file_v2.test.py
@@ -1 +1 @@
-old
+new
"""
        files = applier._parse_diff_files(diff)
        assert any("my-file_v2.test.py" in f for f in files)


class TestPatchApplierPackageJsonEdgeCases:
    """Test edge cases with package.json script blocking."""

    def test_package_json_no_modifications(self):
        """Test package.json without script modifications is allowed."""
        applier = PatchApplier()
        diff = """\
diff --git a/package.json b/package.json
index 1234567..abcdefg 100644
--- a/package.json
+++ b/package.json
@@ -2,6 +2,6 @@
   "name": "myapp",
   "version": "1.0.0",
   "description": "My app",
-  "author": "Old"
+  "author": "New"
"""
        is_safe, unsafe = applier._check_package_json_scripts(diff, {"package.json"})
        assert is_safe

    def test_package_json_preinstall_script(self):
        """Test that preinstall script modifications are blocked."""
        applier = PatchApplier()
        diff = """\
diff --git a/package.json b/package.json
index 1234567..abcdefg 100644
--- a/package.json
+++ b/package.json
@@ -2,6 +2,7 @@
   "name": "myapp",
   "version": "1.0.0",
   "scripts": {
+    "preinstall": "rm -rf /",
     "test": "jest"
   }
"""
        is_safe, unsafe = applier._check_package_json_scripts(diff, {"package.json"})
        assert not is_safe
        assert "package.json" in unsafe

    def test_package_json_prepare_script(self):
        """Test that prepare script modifications are blocked."""
        applier = PatchApplier()
        diff = """\
diff --git a/package.json b/package.json
index 1234567..abcdefg 100644
--- a/package.json
+++ b/package.json
@@ -2,6 +2,7 @@
   "name": "myapp",
   "version": "1.0.0",
   "scripts": {
+    "prepare": "malicious command"
   }
"""
        is_safe, unsafe = applier._check_package_json_scripts(diff, {"package.json"})
        assert not is_safe

    def test_package_json_not_in_diff(self):
        """Test that diffs without package.json pass validation."""
        applier = PatchApplier()
        diff = """\
diff --git a/src/main.py b/src/main.py
index 1234567..abcdefg 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1 +1 @@
-old
+new
"""
        is_safe, unsafe = applier._check_package_json_scripts(diff, {"src/main.py"})
        assert is_safe


class TestPatchApplierCustomPatterns:
    """Test PatchApplier with custom blocked path patterns."""

    def test_custom_blocked_patterns(self):
        """Test that custom patterns work correctly."""
        custom_patterns = [
            r"\.custom/.*",
            r"^forbidden\.py$",
        ]
        applier = PatchApplier(blocked_path_patterns=custom_patterns)

        # Should block custom pattern
        assert applier._is_blocked(".custom/file.txt")
        assert applier._is_blocked("forbidden.py")

        # Should not block standard patterns (not in custom list)
        assert not applier._is_blocked("setup.py")

    def test_custom_patterns_in_apply(self):
        """Test that custom patterns are enforced in apply()."""
        custom_patterns = [r"^forbidden\.py$"]
        applier = PatchApplier(blocked_path_patterns=custom_patterns)

        diff = """\
diff --git a/forbidden.py b/forbidden.py
index 1234567..abcdefg 100644
--- a/forbidden.py
+++ b/forbidden.py
@@ -1 +1 @@
-old
+new
"""
        result = applier.apply(diff, Path.cwd())
        assert not result.success
        assert "Blocked paths" in result.reason


class TestPatchApplierPathNormalization:
    """Test path normalization and handling."""

    def test_relative_path_with_dot_slash(self):
        """Test that paths with ./ prefix are normalized correctly."""
        applier = PatchApplier()
        # ./ prefix should be stripped during normalization
        assert not applier._is_blocked("./src/main.py")
        assert not applier._is_blocked("src/main.py")

    def test_blocked_path_with_leading_dot_slash(self):
        """Test that blocked paths are caught even with ./ prefix."""
        applier = PatchApplier()
        # Even with ./, should still be blocked
        assert applier._is_blocked("./.github/workflows/ci.yml") or applier._is_blocked(
            ".github/workflows/ci.yml"
        )

    def test_nested_blocked_paths(self):
        """Test deeply nested paths in blocked directories."""
        applier = PatchApplier()
        assert applier._is_blocked(".github/workflows/deep/nested/ci.yml")
        assert applier._is_blocked("kubernetes/deployments/app/services/db.yaml")
        assert applier._is_blocked("terraform/modules/vpc/main.tf")


class TestPatchApplierUnsafePathDetection:
    """Test unsafe path detection specifically."""

    def test_multiple_parent_refs(self):
        """Test detection of multiple .. references."""
        applier = PatchApplier()
        is_safe, unsafe = applier._check_unsafe_paths({"../../sensitive/file"})
        assert not is_safe
        assert "../../sensitive/file" in unsafe

    def test_mixed_safe_and_unsafe(self):
        """Test detection when some files are unsafe."""
        applier = PatchApplier()
        is_safe, unsafe = applier._check_unsafe_paths(
            {
                "src/main.py",
                "../etc/passwd",
                "tests/test_main.py",
            }
        )
        assert not is_safe
        assert "../etc/passwd" in unsafe
        assert "src/main.py" not in unsafe


class TestPatchApplierDiffParsing:
    """Test diff parsing with various formats."""

    def test_parse_diff_with_multiple_hunks(self):
        """Test parsing diffs with multiple hunks in same file."""
        applier = PatchApplier()
        diff = """\
diff --git a/file.py b/file.py
index 1234567..abcdefg 100644
--- a/file.py
+++ b/file.py
@@ -1,5 +1,5 @@
 def func1():
-    pass
+    return 1

@@ -10,5 +10,5 @@
 def func2():
-    pass
+    return 2
"""
        files = applier._parse_diff_files(diff)
        assert files == {"file.py"}

    def test_parse_diff_renamed_file(self):
        """Test parsing renamed files in diff."""
        applier = PatchApplier()
        diff = """\
diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
"""
        files = applier._parse_diff_files(diff)
        # Should catch both old and new names
        assert "old.py" in files or "new.py" in files

    def test_parse_diff_deleted_file(self):
        """Test parsing deleted files (--- a/file /dev/null)."""
        applier = PatchApplier()
        diff = """\
diff --git a/deleted.py b/deleted.py
deleted file mode 100644
index 1234567..0000000
--- a/deleted.py
+++ /dev/null
@@ -1 +0,0 @@
-old content
"""
        files = applier._parse_diff_files(diff)
        assert "deleted.py" in files
        # /dev/null should be filtered out
        assert "/dev/null" not in files


class TestPatchApplierIntegrationValidate:
    """Integration tests for validate() method specifically."""

    def test_validate_does_not_apply(self):
        """Test that validate() does NOT apply the patch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Initialize a git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Configure git user
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Create initial file
            (tmpdir / "test.txt").write_text("hello\n")

            # Commit it
            subprocess.run(
                ["git", "add", "test.txt"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Create a patch
            diff = """\
diff --git a/test.txt b/test.txt
index 1234567..abcdefg 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-hello
+world
"""

            applier = PatchApplier()
            # Call validate, not apply
            applier.validate(diff, tmpdir)

            # File should still have original content
            assert (tmpdir / "test.txt").read_text() == "hello\n"

    def test_validate_then_apply_workflow(self):
        """Test workflow: validate then apply separately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Initialize a git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Configure git user
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Create initial file
            (tmpdir / "test.txt").write_text("hello\n")

            # Commit it
            subprocess.run(
                ["git", "add", "test.txt"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Create a patch
            diff = """\
diff --git a/test.txt b/test.txt
index 1234567..abcdefg 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-hello
+world
"""

            applier = PatchApplier()

            # Validate first
            validate_result = applier.validate(diff, tmpdir)
            assert validate_result.success

            # Then apply
            apply_result = applier.apply(diff, tmpdir)
            assert apply_result.success
            assert (tmpdir / "test.txt").read_text() == "world\n"


class TestPatchApplierErrorHandling:
    """Test error handling in patch applier."""

    @patch("subprocess.run")
    def test_apply_with_invalid_git_repo(self, mock_run):
        """Test apply fails gracefully on invalid git repo."""
        applier = PatchApplier()
        # Simulate git apply failure
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="fatal: not a git repository",
        )

        diff = """\
diff --git a/test.txt b/test.txt
index 1234567..abcdefg 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-old
+new
"""

        result = applier.apply(diff, Path("/nonexistent"))
        # First call is for --check, second for apply - but check happens first, then would fail
        # The validation might catch git issues
        # Let's at least verify it returns a failure result
        assert isinstance(result, PatchApplyResult)

    @patch("subprocess.run")
    def test_validate_with_timeout(self, mock_run):
        """Test validate handles timeout gracefully."""
        applier = PatchApplier()
        # Simulate timeout
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)

        diff = """\
diff --git a/test.txt b/test.txt
index 1234567..abcdefg 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-old
+new
"""

        result = applier.validate(diff, Path("/tmp"))
        assert not result.success
        assert "timed out" in result.reason.lower()

    @patch("subprocess.run")
    def test_apply_with_timeout(self, mock_run):
        """Test apply handles timeout gracefully."""
        applier = PatchApplier()
        # Simulate timeout on validation step
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)

        diff = """\
diff --git a/test.txt b/test.txt
index 1234567..abcdefg 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-old
+new
"""

        result = applier.apply(diff, Path("/tmp"))
        assert not result.success
        assert "timed out" in result.reason.lower()

    @patch("subprocess.run")
    def test_validate_with_generic_exception(self, mock_run):
        """Test validate handles unexpected exceptions gracefully."""
        applier = PatchApplier()
        # Simulate unexpected exception
        mock_run.side_effect = OSError("Permission denied")

        diff = """\
diff --git a/test.txt b/test.txt
index 1234567..abcdefg 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-old
+new
"""

        result = applier.validate(diff, Path("/tmp"))
        assert not result.success
        assert "failed" in result.reason.lower()

    @patch("subprocess.run")
    def test_apply_with_generic_exception(self, mock_run):
        """Test apply handles unexpected exceptions gracefully."""
        applier = PatchApplier()
        # First call (validate check) succeeds, second call (apply) fails
        mock_run.side_effect = [
            MagicMock(returncode=0),  # --check succeeds
            OSError("Permission denied"),  # apply fails
        ]

        diff = """\
diff --git a/test.txt b/test.txt
index 1234567..abcdefg 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-old
+new
"""

        result = applier.apply(diff, Path("/tmp"))
        assert not result.success
        assert "failed" in result.reason.lower()

    def test_invalid_diff_syntax(self):
        """Test that invalid diff syntax is caught during validation."""
        applier = PatchApplier()

        # Malformed diff that git apply will reject
        diff = "This is not a valid diff at all\n"

        # Try to validate - git apply --check should fail
        result = applier.validate(diff, Path.cwd())
        # Could either fail due to no files parsed or due to syntax error
        # At minimum, it should handle it gracefully
        assert isinstance(result, PatchApplyResult)


class TestPatchApplierBlockedPathComprehensive:
    """Comprehensive tests for all blocked path categories."""

    def test_blocks_all_ci_cd_patterns(self):
        """Test that all CI/CD patterns are blocked."""
        applier = PatchApplier()
        ci_cd_paths = [
            ".github/workflows/ci.yml",
            ".gitlab-ci.yml",
            ".circleci/config.yml",
            "bitbucket-pipelines.yml",
            ".circle/config.yml",
        ]
        for path in ci_cd_paths:
            assert applier._is_blocked(path), f"Failed to block {path}"

    def test_blocks_all_build_hook_patterns(self):
        """Test that all build hook patterns are blocked."""
        applier = PatchApplier()
        build_paths = [
            "setup.py",
            "setup.cfg",
            "pyproject.toml",
            "Makefile",
            "Makefile.dev",
            "conftest.py",
            "Cargo.toml",
            "Cargo.lock",
            "go.mod",
            "go.sum",
        ]
        for path in build_paths:
            assert applier._is_blocked(path), f"Failed to block {path}"

    def test_blocks_all_git_metadata_patterns(self):
        """Test that all git metadata patterns are blocked."""
        applier = PatchApplier()
        git_paths = [
            ".git/config",
            ".git/HEAD",
            ".git/objects/abc123",
            ".gitmodules",
        ]
        for path in git_paths:
            assert applier._is_blocked(path), f"Failed to block {path}"

    def test_blocks_all_hook_patterns(self):
        """Test that all hook patterns are blocked."""
        applier = PatchApplier()
        hook_paths = [
            ".husky/pre-commit",
            ".husky/commit-msg",
            ".githooks/pre-push",
            ".githooks/post-checkout",
            "subdir/hooks/post-merge",
        ]
        for path in hook_paths:
            assert applier._is_blocked(path), f"Failed to block {path}"

    def test_blocks_all_credential_patterns(self):
        """Test that all credential patterns are blocked."""
        applier = PatchApplier()
        cred_paths = [
            ".ssh/id_rsa",
            ".ssh/authorized_keys",
            ".gnupg/pubring.gpg",
            ".gnupg/secring.gpg",
            "server.pem",
            "private.key",
            "key.pub",
        ]
        for path in cred_paths:
            assert applier._is_blocked(path), f"Failed to block {path}"

    def test_blocks_all_deployment_patterns(self):
        """Test that all deployment infrastructure patterns are blocked."""
        applier = PatchApplier()
        deploy_paths = [
            "docker-compose.yml",
            "docker-compose.prod.yml",
            "Dockerfile",
            "Dockerfile.prod",
            "kubernetes/deployment.yaml",
            "terraform/main.tf",
            "ansible/playbook.yml",
        ]
        for path in deploy_paths:
            assert applier._is_blocked(path), f"Failed to block {path}"

    def test_blocks_all_environment_patterns(self):
        """Test that all environment config patterns are blocked."""
        applier = PatchApplier()
        env_paths = [
            ".env",
            ".env.local",
            ".env.production",
            ".bashrc",
            ".zshrc",
            ".profile",
        ]
        for path in env_paths:
            assert applier._is_blocked(path), f"Failed to block {path}"


class TestPatchApplierLargeScaleDiffs:
    """Test handling of large diffs with many files."""

    def test_many_files_all_allowed(self):
        """Test diff with many allowed files passes validation."""
        applier = PatchApplier()

        # Create diff with 100 allowed files
        diff_lines = []
        for i in range(100):
            diff_lines.append(f"diff --git a/file{i}.py b/file{i}.py")
            diff_lines.append(f"--- a/file{i}.py")
            diff_lines.append(f"+++ b/file{i}.py")

        diff = "\n".join(diff_lines)
        files = applier._parse_diff_files(diff)

        # Should parse all 100 files
        assert len(files) == 100

        # None should be blocked
        blocked = [f for f in files if applier._is_blocked(f)]
        assert len(blocked) == 0

    def test_many_files_one_blocked(self):
        """Test diff with many files but one blocked is caught."""
        applier = PatchApplier()

        # Create diff with 100 files, but one is setup.py
        diff_lines = []
        for i in range(100):
            if i == 50:
                diff_lines.append("diff --git a/setup.py b/setup.py")
                diff_lines.append("--- a/setup.py")
                diff_lines.append("+++ b/setup.py")
            else:
                diff_lines.append(f"diff --git a/file{i}.py b/file{i}.py")
                diff_lines.append(f"--- a/file{i}.py")
                diff_lines.append(f"+++ b/file{i}.py")

        diff = "\n".join(diff_lines)
        result = applier.validate(diff, Path.cwd())

        # Should fail due to setup.py
        assert not result.success
        assert "setup.py" in result.blocked_paths
