"""Run a sync only in a netbox-branching branch, and keep it there until it ends.

A check at the start is not enough: a branch deleted, archived or merged during the run would let
the rest of it write to main, because a branch connection falls back to main's tables once the
branch's schema is gone. So a run holds the branch:

- An advisory lock keyed by the branch, on a connection of its own, for the whole run. The
  plugin's receivers of netbox-branching's signals refuse to delete, archive, merge, revert, sync
  or migrate a branch whose lock is taken, and a second run of the branch is refused.
- Every statement on the branch's connection, in any thread, runs in a transaction that first
  locks a table of the branch's schema. If the schema is gone, the statement is not run; while
  the transaction holds that lock, the schema cannot be dropped. This holds even for a drop that
  bypasses the receivers, and if the advisory lock's connection is lost.

Anything that goes wrong after the start makes the run fail when it ends. A killed run's
connections close, and PostgreSQL releases its locks.
"""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from ._validation import SyncConfigurationError

# The first key of the plugin's advisory locks; the second is the branch's ID.
LOCK_CLASS = 0x554E4946
_TRANSACTION_CONTROL = re.compile(r"\s*(SAVEPOINT|RELEASE|ROLLBACK)\b", re.IGNORECASE)
_held: ContextVar["_Hold | None"] = ContextVar("netbox_unifi_sync_held_branch", default=None)
_wrapper_installed = threading.Lock()


class BranchRequiredError(SyncConfigurationError):
    """Raised when a sync would not run in a branch."""


class BranchLostError(RuntimeError):
    """Raised when the branch of a running sync stopped being safe to write to."""


def _refuse(reason: str):
    raise BranchRequiredError(
        f"Sync refused: {reason}. A sync only runs in an active netbox-branching branch; "
        "background jobs and schedules have none."
    )


def require_active_branch():
    """Return the active branch if it is ready, has its schema, and inventory goes to it."""
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
    return branch


def busy_message(branch) -> str:
    return (
        f"A UniFi sync is running in branch {branch.name!r}, or an action on it is; "
        "try again when it has ended."
    )


class _Hold:
    """The locks of one run."""

    def __init__(self, branch):
        self.branch = branch
        self.alias = branch.connection_name
        self.table = f'"{branch.schema_name}"."core_objectchange"'
        self.lost: str | None = None
        self._mutex = threading.Lock()
        self._connection = None
        self._pid = None

    def acquire(self) -> None:
        from django.db import DEFAULT_DB_ALIAS, connections

        self._connection = connections[DEFAULT_DB_ALIAS].copy()
        self._connection.inc_thread_sharing()
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s, %s), pg_backend_pid()",
                [LOCK_CLASS, self.branch.pk],
            )
            taken, self._pid = cursor.fetchone()
        if not taken:
            self.release()
            raise BranchRequiredError(
                f"Sync refused: branch {self.branch.name!r} is in use. {busy_message(self.branch)}"
            )

    def release(self) -> None:
        if self._connection is not None:
            # Closing the connection ends its session, and with it the lock.
            self._connection.close()
            self._connection = None

    def _check_lock(self) -> None:
        with self._mutex:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                if cursor.fetchone()[0] != self._pid:
                    raise RuntimeError("the lock's connection was replaced")

    def lock_schema(self, connection) -> None:
        """Lock a table of the branch in the connection's open transaction, or fail the run."""
        if self.lost:
            raise BranchLostError(f"Sync stopped: {self.lost}")
        try:
            self._check_lock()
            connection.connection.execute(f"LOCK TABLE {self.table} IN ACCESS SHARE MODE")
        except Exception as exc:
            self.lost = f"branch {self.branch.name!r} could not be locked ({exc})"
            raise BranchLostError(f"Sync stopped: {self.lost}") from exc

    def statement(self, execute, sql, params, many, context):
        from django.db import transaction

        connection = context["connection"]
        if _TRANSACTION_CONTROL.match(str(sql)):
            return execute(sql, params, many, context)
        if connection.in_atomic_block:
            if connection.needs_rollback or _failed_transaction(connection):
                # The statement fails anyway, and must not be taken for a lost branch.
                return execute(sql, params, many, context)
            self.lock_schema(connection)
            return execute(sql, params, many, context)
        with transaction.atomic(using=connection.alias):
            self.lock_schema(connection)
            return execute(sql, params, many, context)

    def verify(self) -> None:
        if self.lost:
            raise BranchLostError(f"Sync failed: {self.lost}")
        try:
            self._check_lock()
            require_active_branch()
        except Exception as exc:
            raise BranchLostError(f"Sync failed: the branch changed during the run ({exc})") from exc


def _failed_transaction(connection) -> bool:
    from psycopg.pq import TransactionStatus

    return connection.connection.info.transaction_status == TransactionStatus.INERROR


def _guard_statement(execute, sql, params, many, context):
    hold = _held.get()
    if hold is None or context["connection"].alias != hold.alias:
        return execute(sql, params, many, context)
    return hold.statement(execute, sql, params, many, context)


def _add_wrapper(connection) -> None:
    if _guard_statement not in connection.execute_wrappers:
        # First, so that a wrapper pushed and popped around it cannot remove it.
        connection.execute_wrappers.insert(0, _guard_statement)


def _on_connection_created(sender, connection, **kwargs):
    if connection.alias.startswith("schema_"):
        _add_wrapper(connection)


def _install_wrappers(alias: str) -> None:
    """Guard the branch's connection in this thread, and every one opened from now on."""
    from django.db import connections
    from django.db.backends.signals import connection_created

    with _wrapper_installed:
        connection_created.connect(
            _on_connection_created, dispatch_uid="netbox_unifi_sync.branch_guard"
        )
    _add_wrapper(connections[alias])


@contextmanager
def hold_branch():
    """Hold the active branch for the duration; a nested use joins the outer one."""
    outer = _held.get()
    branch = require_active_branch()
    if outer is not None and outer.branch.pk == branch.pk:
        yield outer
        return
    hold = _Hold(branch)
    hold.acquire()
    try:
        _install_wrappers(hold.alias)
        token = _held.set(hold)
        try:
            require_active_branch()
            yield hold
            hold.verify()
        finally:
            _held.reset(token)
    finally:
        hold.release()


def in_branch(function):
    """Run the function only while holding the active branch."""
    @wraps(function)
    def held(*args, **kwargs):
        with hold_branch():
            return function(*args, **kwargs)
    return held


def _refuse_action(sender, branch, **kwargs):
    """Receiver of netbox-branching's pre-action signals: wait for no run, and hold off new ones.

    The shared lock is the session's and lasts until the matching post signal, or the end of the
    session: a job's process, or a request's connection. A transaction-scoped one would end before
    a merge or sync has read the branch.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock_shared(%s, %s)", [LOCK_CLASS, branch.pk])
        taken = cursor.fetchone()[0]
    if not taken:
        _raise_busy(branch)


def _release_action(sender, branch, **kwargs):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock_shared(%s, %s)", [LOCK_CLASS, branch.pk])


def _refuse_deprovision(sender, branch, **kwargs):
    """The drop runs in the caller's transaction when it has one, so the lock lasts until it ends."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_xact_lock_shared(%s, %s)", [LOCK_CLASS, branch.pk]
        )
        taken = cursor.fetchone()[0]
    if not taken:
        _raise_busy(branch)


def _raise_busy(branch):
    try:
        from utilities.exceptions import AbortRequest
    except ImportError:  # pragma: no cover - outside NetBox
        AbortRequest = RuntimeError  # noqa: N806
    raise AbortRequest(busy_message(branch))


def connect_branch_actions() -> None:
    """Called from the plugin's ready()."""
    from netbox_branching import signals

    for action in ("merge", "revert", "sync", "migrate"):
        getattr(signals, f"pre_{action}").connect(
            _refuse_action, dispatch_uid=f"netbox_unifi_sync.pre_{action}"
        )
        getattr(signals, f"post_{action}").connect(
            _release_action, dispatch_uid=f"netbox_unifi_sync.post_{action}"
        )
    signals.pre_deprovision.connect(
        _refuse_deprovision, dispatch_uid="netbox_unifi_sync.pre_deprovision"
    )
