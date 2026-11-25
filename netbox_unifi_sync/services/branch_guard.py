"""Refuse to sync unless inventory reads and writes go to a ready netbox-branching branch."""
from __future__ import annotations

from ._validation import SyncConfigurationError


class BranchRequiredError(SyncConfigurationError):
    """Raised when a sync would not run in a branch."""


def _refuse(reason: str):
    raise BranchRequiredError(
        f"Sync refused: {reason}. A sync only runs in an active netbox-branching branch; "
        "background jobs and schedules have none."
    )


def require_active_branch() -> None:
    try:
        from netbox_branching.contextvars import active_branch
    except ImportError:
        _refuse("netbox-branching is not installed")
    branch = active_branch.get()
    if branch is None:
        _refuse("no branch is active")

    # The active object may be older than the row, as after a merge.
    saved = type(branch).objects.filter(pk=branch.pk).first()
    if saved is None or not saved.ready:
        _refuse(f"branch {branch.name!r} is not ready")

    from dcim.models import Device
    from django.db import connections, router

    used = {router.db_for_read(Device), router.db_for_write(Device)}
    if used != {branch.connection_name}:
        _refuse(f"inventory uses {sorted(used)}, not branch {branch.name!r}")
    # Without its schema the search path falls through to main.
    with connections[branch.connection_name].cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"{branch.schema_name}.dcim_device"])
        if cursor.fetchone()[0] is None:
            _refuse(f"branch {branch.name!r} has no schema")
