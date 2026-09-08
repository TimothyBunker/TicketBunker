"""Detected CMake commands build repository targets; the folder is not an alias."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from ticketbunker import checks


class BuildDetectionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cmake") and (shutil.which("make") or shutil.which("ninja")), "CMake and a build tool required")
    def test_cmake_suggestion_builds_the_projects_actual_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CMakeLists.txt").write_text('cmake_minimum_required(VERSION 3.16)\nproject(Dummy NONE)\nadd_custom_target(project_output ALL COMMAND "${CMAKE_COMMAND}" -E touch "${CMAKE_BINARY_DIR}/project-built.txt")\n', encoding="utf-8")
            suggestion = checks.detect(root)[0]
            self.assertEqual(suggestion["tool"], "CMake")
            # Only our own deterministic test fixture's generated command runs.
            result = subprocess.run(suggestion["build"], cwd=root, shell=True, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / ".tb-check-build" / "project-built.txt").is_file())
