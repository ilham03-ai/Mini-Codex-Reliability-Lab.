"""
Opt-in tests that exercise the --isolated / Docker branch of sandbox/docker_runner.py.

Excluded from the default run via `addopts = -m "not docker"` in pytest.ini.
Run them explicitly on a machine with a working Docker daemon:

    python -m pytest -m docker -v

Every assertion in this file checks an *observable* effect — exit code, file-system
state, image-creation timestamp — not stdout/stderr substrings. The goal is that
"this passes" means the runner actually did the right thing, not that the right log
line happened to appear.
"""
import pathlib
import subprocess

import pytest

from agent.tools import run_tests
from sandbox.docker_runner import (
    DOCKER_RUNNER_IMAGE,
    _docker_available,
    _ensure_runner_image,
    run_in_sandbox,
)

pytestmark = pytest.mark.docker


@pytest.fixture
def require_docker():
    """Skip lazily — we never call _docker_available() at module-import time so the
    default `pytest -q` run (which deselects `docker`) pays nothing for these tests.
    """
    if not _docker_available():
        pytest.skip("Docker daemon not running")


# ---------------------------------------------------------------------------
# Test execution actually happens inside the container (not on the host)
# ---------------------------------------------------------------------------

def test_green_suite_runs_inside_container_and_returns_success(tmp_path, require_docker):
    """A passing pytest suite must report success AND leave proof on the bind-mount."""
    pathlib.Path(tmp_path, "tests").mkdir()
    # The test writes a marker file at /workspace/test_ran. Because the workspace is
    # bind-mounted into the container at /workspace, the file appears on the host at
    # tmp_path/test_ran — observable proof the test executed inside the sandbox.
    (tmp_path / "tests" / "test_marker.py").write_text(
        "import pathlib\n"
        "def test_writes_marker():\n"
        "    pathlib.Path('/workspace/test_ran').touch()\n"
    )
    result = run_tests("python -m pytest tests/ -v", str(tmp_path), force_docker=True)

    assert result["success"] is True
    assert result["returncode"] == 0
    assert result["sandbox"] == "docker"
    assert (tmp_path / "test_ran").exists(), "test did not actually execute in the container"


def test_red_suite_returns_failure(tmp_path, require_docker):
    """A failing pytest suite must report failure with a non-zero exit code."""
    pathlib.Path(tmp_path, "tests").mkdir()
    (tmp_path / "tests" / "test_red.py").write_text(
        "def test_intentional_failure():\n"
        "    assert False\n"
    )
    result = run_tests("python -m pytest tests/ -v", str(tmp_path), force_docker=True)

    assert result["success"] is False
    assert result["returncode"] != 0
    assert result["sandbox"] == "docker"


# ---------------------------------------------------------------------------
# Argv-based execution: shell metacharacters in tokens are inert
# ---------------------------------------------------------------------------

def test_argv_injection_attempt_creates_no_side_effect(tmp_path, require_docker):
    """If shell-interpreted, `; touch /workspace/pwned` would create a file on the host
    mount. With argv-based execution these tokens become literal pytest test-path args.
    """
    pathlib.Path(tmp_path, "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok(): assert True\n")

    injection = "pytest tests/; touch /workspace/pwned"
    result = run_tests(injection, str(tmp_path), force_docker=True)

    # Observable proof #1: the file the injection would create does NOT exist on the host
    assert not (tmp_path / "pwned").exists(), "injection executed — sandbox is broken"
    # Observable proof #2: pytest itself rejected the bad arguments
    assert result["success"] is False


# ---------------------------------------------------------------------------
# --network none isolation
# ---------------------------------------------------------------------------

def test_network_none_blocks_outbound_connections(tmp_path, require_docker):
    """A network attempt from inside the container must fail. We bypass
    _sanitize_command (which only allows pytest) by calling run_in_sandbox directly,
    so this test exercises the Docker isolation flags, not the command sanitizer.
    """
    argv = [
        "python", "-c",
        "import sys, urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('https://example.com', timeout=3)\n"
        "    sys.exit(0)\n"
        "except Exception:\n"
        "    sys.exit(42)\n",
    ]
    result = run_in_sandbox(str(tmp_path), argv, timeout=30, force_docker=True)

    # Observable proof: the script's own exit code (42) indicates the network call raised.
    # Exit 0 would mean the request succeeded — i.e. isolation was off.
    assert result["success"] is False
    assert result["returncode"] == 42


# ---------------------------------------------------------------------------
# Image build is idempotent
# ---------------------------------------------------------------------------

def _image_created_at(name: str) -> str:
    r = subprocess.run(
        ["docker", "image", "inspect", "-f", "{{.Created}}", name],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0, f"image {name} should exist after _ensure_runner_image"
    return r.stdout.strip()


def test_runner_image_build_is_idempotent(require_docker):
    """Removing the runner image, then calling _ensure_runner_image() twice, must:
      1. succeed both times,
      2. only build on the first call (image creation timestamp is stable on call #2).
    """
    # Clean slate: force the first call to actually build
    subprocess.run(
        ["docker", "rmi", "-f", DOCKER_RUNNER_IMAGE],
        capture_output=True, timeout=30,
    )

    ok_first, err_first = _ensure_runner_image()
    assert ok_first is True, f"first build failed: {err_first}"
    created_after_build = _image_created_at(DOCKER_RUNNER_IMAGE)

    ok_second, _ = _ensure_runner_image()
    assert ok_second is True

    # Observable proof: image creation timestamp unchanged → no rebuild happened
    assert _image_created_at(DOCKER_RUNNER_IMAGE) == created_after_build
