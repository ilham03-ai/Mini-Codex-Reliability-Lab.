import re
import shlex
import difflib
from pathlib import Path

from sandbox.docker_runner import run_in_sandbox

# Matches python, python3, python3.9, python3.11.2 — but not pythonX, ipython, python-foo, etc.
_PYTHON_RE = re.compile(r"^python\d*(\.\d+)*$")


def _safe_resolve(path: str, workspace: str) -> Path:
    ws = Path(workspace).resolve()
    candidate = (ws / path).resolve()
    try:
        candidate.relative_to(ws)
    except ValueError:
        raise PermissionError(f"Path traversal blocked: {path!r} escapes workspace")
    return candidate


def _sanitize_command(command: str) -> list:
    """Validate and tokenize a pytest command. Only allows pytest or <python> -m pytest."""
    if any(c in command for c in ("\n", "\r", "\x00")):
        raise ValueError("Control characters are not permitted in test commands")

    try:
        parts = shlex.split(command)
    except ValueError as e:
        raise ValueError(f"Unparseable command: {e}")
    if not parts:
        raise ValueError("Empty command")

    executable = Path(parts[0]).name
    if executable == "pytest":
        return parts
    if _PYTHON_RE.match(executable):
        # Only allow: <python> -m pytest [args...]  — blocks python -c, python script.py, etc.
        if len(parts) < 3 or parts[1] != "-m" or parts[2] != "pytest":
            raise ValueError(
                f"{executable!r} is only allowed as '{executable} -m pytest [args...]'"
            )
        return parts
    raise ValueError(
        f"Command '{executable}' not allowed — only 'pytest' or '<python> -m pytest' are permitted"
    )


def read_file(path: str, workspace: str = ".") -> dict:
    try:
        full = _safe_resolve(path, workspace)
        content = full.read_text(encoding="utf-8")
        return {
            "success": True,
            "path": path,
            "content": content,
            "lines": len(content.splitlines()),
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def search_code(query: str, workspace: str = ".", file_pattern: str = "*.py") -> dict:
    workspace_path = Path(workspace).resolve()
    results = []
    try:
        for fp in workspace_path.rglob(file_pattern):
            # skip files that somehow escape the workspace (e.g. symlinks)
            try:
                fp.resolve().relative_to(workspace_path)
            except ValueError:
                continue
            if any(p in fp.parts for p in (".git", "__pycache__", ".pytest_cache")):
                continue
            try:
                lines = fp.read_text(encoding="utf-8").splitlines()
                for i, line in enumerate(lines, 1):
                    if query.lower() in line.lower():
                        results.append({
                            "file": str(fp.relative_to(workspace_path)),
                            "line": i,
                            "content": line.strip(),
                        })
            except Exception:
                pass
        return {"success": True, "query": query, "matches": results[:50], "total": len(results)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def edit_file(path: str, old_content: str, new_content: str, workspace: str = ".") -> dict:
    try:
        full = _safe_resolve(path, workspace)
        current = full.read_text(encoding="utf-8")
        if old_content not in current:
            # Try normalizing line endings
            normalized = current.replace("\r\n", "\n")
            old_normalized = old_content.replace("\r\n", "\n")
            if old_normalized not in normalized:
                return {
                    "success": False,
                    "error": "old_content not found in file — check exact whitespace and content",
                }
            current = normalized
            old_content = old_normalized

        updated = current.replace(old_content, new_content, 1)
        full.write_text(updated, encoding="utf-8")

        diff = "".join(difflib.unified_diff(
            current.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        ))
        return {"success": True, "path": path, "diff": diff[:2000]}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_tests(command: str, workspace: str = ".", timeout: int = 60, force_docker: bool = False) -> dict:
    try:
        argv = _sanitize_command(command)
    except ValueError as e:
        return {"success": False, "error": str(e), "command": command}

    result = run_in_sandbox(workspace, argv, timeout=timeout, force_docker=force_docker)
    result["command"] = command
    return result


def inspect_error(log: str) -> dict:
    patterns = {
        "syntax_error": r"SyntaxError: (.+)",
        "assertion_error": r"AssertionError: (.+)",
        "name_error": r"NameError: (.+)",
        "type_error": r"TypeError: (.+)",
        "import_error": r"(?:ImportError|ModuleNotFoundError): (.+)",
        "attribute_error": r"AttributeError: (.+)",
        "value_error": r"ValueError: (.+)",
        "zero_division": r"ZeroDivisionError: (.+)",
        "index_error": r"IndexError: (.+)",
        "key_error": r"KeyError: (.+)",
        "runtime_error": r"RuntimeError: (.+)",
    }

    errors = []
    for error_type, pattern in patterns.items():
        for m in re.findall(pattern, log):
            errors.append({"type": error_type, "message": m})

    tb_match = re.search(
        r"(Traceback \(most recent call last\):.*?)(?=\n[A-Z]|\Z)", log, re.DOTALL
    )
    traceback = tb_match.group(0)[:2000] if tb_match else ""

    failed_tests = re.findall(r"FAILED (.+?) -", log)
    passed = re.findall(r"(\d+) passed", log)
    failed_count = re.findall(r"(\d+) failed", log)
    errors_count = re.findall(r"(\d+) error", log)

    suggestions = []
    for err in errors:
        t = err["type"]
        if t == "syntax_error":
            suggestions.append("Check for missing colons, parentheses, or wrong indentation")
        elif t == "name_error":
            suggestions.append(f"Undefined variable or function: {err['message']}")
        elif t == "type_error":
            suggestions.append("Check argument types and return values")
        elif t == "assertion_error":
            suggestions.append("Test assertion failed — check expected vs actual value")
        elif t == "zero_division":
            suggestions.append("Add a guard for empty input or zero denominator")
        elif t == "index_error":
            suggestions.append("Check list bounds and off-by-one conditions")

    return {
        "errors": errors,
        "traceback": traceback,
        "failed_tests": failed_tests,
        "passed": int(passed[0]) if passed else 0,
        "failed": int(failed_count[0]) if failed_count else 0,
        "errors_count": int(errors_count[0]) if errors_count else 0,
        "suggestions": suggestions,
    }
