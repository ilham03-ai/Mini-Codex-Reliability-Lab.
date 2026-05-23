"""
Sandboxed test execution via Docker.
Falls back to local subprocess when Docker is unavailable.
"""
import os
import subprocess
import tempfile
import shutil
from pathlib import Path


DOCKER_BASE_IMAGE = "python:3.11-slim"
DOCKER_RUNNER_IMAGE = "mini-codex-lab-runner:py3.11"
DOCKER_TIMEOUT = 120

# Built lazily on first use. The base image has no pytest, and we cannot `pip install`
# inside a --network none container, so we bake pytest into a derived image once.
_RUNNER_DOCKERFILE = f"""\
FROM {DOCKER_BASE_IMAGE}
RUN pip install --no-cache-dir pytest
"""


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _ensure_runner_image() -> tuple:
    """Build the pytest-equipped runner image on demand. Returns (ok, error_message)."""
    inspect = subprocess.run(
        ["docker", "image", "inspect", DOCKER_RUNNER_IMAGE],
        capture_output=True, text=True, timeout=10,
    )
    if inspect.returncode == 0:
        return True, ""
    build = subprocess.run(
        ["docker", "build", "-t", DOCKER_RUNNER_IMAGE, "-"],
        input=_RUNNER_DOCKERFILE,
        capture_output=True, text=True, timeout=300,
    )
    if build.returncode != 0:
        return False, (build.stderr or build.stdout)[-1000:]
    return True, ""


def run_in_sandbox(
    workspace: str,
    argv: list,
    timeout: int = DOCKER_TIMEOUT,
    force_docker: bool = False,
) -> dict:
    """Run a tokenized argv inside a Docker container with the workspace mounted.

    `argv` must be a list of strings — never a single command string. Execution is
    argv-based end to end (no shell=True), which is the only real guarantee against
    command injection. When force_docker=True the local fallback is disabled.
    """
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return {"success": False, "error": "argv must be a list[str]", "sandbox": "argv"}

    if not _docker_available():
        if force_docker:
            return {
                "success": False,
                "error": "Docker is required for isolated runs (--isolated) but is not available",
                "sandbox": "docker",
            }
        return _run_local(workspace, argv, timeout)

    ok, err = _ensure_runner_image()
    if not ok:
        return {
            "success": False,
            "error": f"Failed to build runner image {DOCKER_RUNNER_IMAGE}: {err}",
            "sandbox": "docker",
        }

    abs_workspace = str(Path(workspace).resolve())

    # No shell wrapper: argv is passed directly to `docker run`. Docker forwards it to
    # the container's entrypoint (none) and runs argv[0] as the executable. Shell
    # metacharacters in tokens cannot be interpreted because there is no shell.
    docker_cmd = [
        "docker", "run", "--rm",
        "--network", "none",           # no network access
        "--memory", "512m",            # memory cap
        "--cpus", "1",                 # CPU cap
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{abs_workspace}:/workspace:rw",
        "-w", "/workspace",
        DOCKER_RUNNER_IMAGE,
    ] + argv

    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-2000:],
            "sandbox": "docker",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Docker timeout", "sandbox": "docker"}
    except Exception as e:
        return {"success": False, "error": str(e), "sandbox": "docker"}


def _run_local(workspace: str, argv: list, timeout: int) -> dict:
    """Fallback: run argv directly via subprocess (no shell, no isolation)."""
    try:
        result = subprocess.run(
            argv,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-2000:],
            "sandbox": "local",
        }
    except FileNotFoundError as e:
        return {"success": False, "error": f"Executable not found: {e}", "sandbox": "local"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Local timeout", "sandbox": "local"}
    except Exception as e:
        return {"success": False, "error": str(e), "sandbox": "local"}


def prepare_isolated_workspace(source: str) -> str:
    """Copy a workspace into a fresh temp directory for isolated execution."""
    tmp = tempfile.mkdtemp(prefix="codex_lab_")
    shutil.copytree(source, tmp, dirs_exist_ok=True)
    return tmp


def cleanup_workspace(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
