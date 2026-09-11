"""Detected CMake commands build repository targets; the folder is not an alias."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from ticketbunker import checks


def fixture_environment():
    # This compiler-free target tests our suggested command, not Visual Studio
    # discovery or the host's preferred toolchain. Never change product defaults.
    env = dict(os.environ)
    for key in ("CMAKE_GENERATOR_PLATFORM", "CMAKE_GENERATOR_TOOLSET", "CMAKE_GENERATOR_INSTANCE", "CMAKE_TOOLCHAIN_FILE"):
        env.pop(key, None)
    env["CMAKE_GENERATOR"] = "Ninja"
    return env


def run_fixture(command, root):
    try:
        return subprocess.run(command, cwd=root, env=fixture_environment(), shell=True,
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as error:
        def text(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or "(no output)"
        raise AssertionError(
            f"CMake fixture exceeded 60 seconds (generator: Ninja).\n"
            f"Command: {command}\nstdout:\n{text(error.stdout)}\nstderr:\n{text(error.stderr)}"
        ) from error


class BuildDetectionTests(unittest.TestCase):
    def test_cmake_suggestion_builds_the_projects_actual_target(self):
        if not (shutil.which("cmake") and shutil.which("ninja")):
            if os.environ.get("CI"):
                self.fail("The CMake fixture requires cmake and ninja on the CI runner's PATH.")
            self.skipTest("CMake and Ninja required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CMakeLists.txt").write_text('cmake_minimum_required(VERSION 3.16)\nproject(Dummy NONE)\nadd_custom_target(project_output ALL COMMAND "${CMAKE_COMMAND}" -E touch "${CMAKE_BINARY_DIR}/project-built.txt")\n', encoding="utf-8")
            suggestion = checks.detect(root)[0]
            self.assertEqual(suggestion["tool"], "CMake")
            # Only our own deterministic test fixture's generated command runs.
            result = run_fixture(suggestion["build"], root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / ".tb-check-build" / "project-built.txt").is_file())

    def test_fixture_isolates_generator_without_changing_parent_environment(self):
        inherited = {"CMAKE_GENERATOR": "Visual Studio 18 2026", "CMAKE_GENERATOR_PLATFORM": "x64",
                     "CMAKE_GENERATOR_TOOLSET": "v145", "CMAKE_GENERATOR_INSTANCE": "host-install",
                     "CMAKE_TOOLCHAIN_FILE": "host-toolchain", "KEEP_FIXTURE_SETTING": "yes"}
        with mock.patch.dict(os.environ, inherited):
            env = fixture_environment()
            self.assertEqual(env["CMAKE_GENERATOR"], "Ninja")
            for key in inherited.keys() - {"CMAKE_GENERATOR", "KEEP_FIXTURE_SETTING"}:
                self.assertNotIn(key, env)
            self.assertEqual(env["KEEP_FIXTURE_SETTING"], "yes")
            self.assertEqual(os.environ["CMAKE_GENERATOR"], inherited["CMAKE_GENERATOR"])

    def test_timeout_includes_captured_output_and_keeps_deadline(self):
        error = subprocess.TimeoutExpired("build", 60, output=b"configure reached", stderr=b"build stalled")
        with mock.patch.object(subprocess, "run", side_effect=error) as run:
            with self.assertRaises(AssertionError) as caught:
                run_fixture("build", Path("fixture"))
        self.assertIn("configure reached", str(caught.exception))
        self.assertIn("build stalled", str(caught.exception))
        self.assertIn("generator: Ninja", str(caught.exception))
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        self.assertEqual(run.call_args.kwargs["env"]["CMAKE_GENERATOR"], "Ninja")

    def test_ci_cannot_silently_skip_a_missing_build_tool(self):
        with mock.patch.dict(os.environ, {"CI": "true"}), mock.patch.object(shutil, "which", return_value=None):
            with self.assertRaisesRegex(AssertionError, "requires cmake and ninja"):
                self.test_cmake_suggestion_builds_the_projects_actual_target()
