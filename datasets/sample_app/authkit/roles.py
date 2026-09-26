"""Roles and the actions each may perform."""

PERMISSIONS: dict[str, frozenset[str]] = {
    "user": frozenset({"read_profile", "edit_profile"}),
    "admin": frozenset({"read_profile", "edit_profile", "delete_user", "view_audit_log"}),
}


def has_permission(role: str, action: str) -> bool:
    return action in PERMISSIONS.get(role, frozenset())
