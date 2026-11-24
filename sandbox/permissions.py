"""
Permission model for sandbox tool execution.
Defines which operations are allowed, require confirmation, or are denied.
"""
import warnings
from enum import Enum
from dataclasses import dataclass


class Permission(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


@dataclass
class PermissionPolicy:
    read_file: Permission = Permission.ALLOW
    search_code: Permission = Permission.ALLOW
    edit_file: Permission = Permission.CONFIRM
    run_tests: Permission = Permission.ALLOW
    inspect_error: Permission = Permission.ALLOW
    network_access: Permission = Permission.DENY
    shell_arbitrary: Permission = Permission.DENY


STRICT_POLICY = PermissionPolicy(
    edit_file=Permission.CONFIRM,
    network_access=Permission.DENY,
    shell_arbitrary=Permission.DENY,
)

PERMISSIVE_POLICY = PermissionPolicy(
    edit_file=Permission.ALLOW,
    network_access=Permission.DENY,
    shell_arbitrary=Permission.DENY,
)

EVAL_POLICY = PermissionPolicy(
    edit_file=Permission.ALLOW,  # fully automated for evaluation
    network_access=Permission.DENY,
    shell_arbitrary=Permission.DENY,
)


_KNOWN_TOOLS = ("read_file", "search_code", "edit_file", "run_tests", "inspect_error")


def check_permission(policy: PermissionPolicy, tool_name: str) -> Permission:
    mapping = {
        "read_file": policy.read_file,
        "search_code": policy.search_code,
        "edit_file": policy.edit_file,
        "run_tests": policy.run_tests,
        "inspect_error": policy.inspect_error,
    }
    if tool_name not in mapping:
        # Loud default: a tool that reaches here without a permission entry
        # is almost always a missed wiring step on a newly-added tool. A
        # silent DENY looks like "model misbehaves" to downstream readers;
        # the warning names the real cause.
        warnings.warn(
            f"check_permission: unknown tool {tool_name!r} (known: {_KNOWN_TOOLS!r}) — "
            f"denying by default. If this tool is intentional, add it to "
            f"PermissionPolicy and to check_permission's mapping.",
            RuntimeWarning,
            stacklevel=2,
        )
        return Permission.DENY
    return mapping[tool_name]
