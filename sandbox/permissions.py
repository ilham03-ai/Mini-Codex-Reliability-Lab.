"""
Permission model for sandbox tool execution.
Defines which operations are allowed, require confirmation, or are denied.
"""
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


def check_permission(policy: PermissionPolicy, tool_name: str) -> Permission:
    mapping = {
        "read_file": policy.read_file,
        "search_code": policy.search_code,
        "edit_file": policy.edit_file,
        "run_tests": policy.run_tests,
        "inspect_error": policy.inspect_error,
    }
    return mapping.get(tool_name, Permission.DENY)
