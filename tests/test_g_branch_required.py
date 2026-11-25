"""Every way to start a sync refuses without a ready, active branch that writes go to."""
import sys
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import branch_guard, sync_engine, sync_service
from netbox_unifi_sync.services.branch_guard import BranchRequiredError
from tests.test_review_regressions import _load_function


@pytest.fixture
def branching(monkeypatch):
    """Stub netbox-branching: an active branch that is ready, has a schema and is routed to."""
    class Branch:
        pk = 7
        name = "b"
        connection_name = "schema_branch_x"
        schema_name = "branch_x"

    state = SimpleNamespace(branch=Branch(), ready=True, alias="schema_branch_x", schema=True)

    def saved(pk):
        row = SimpleNamespace(ready=state.ready, status="ready" if state.ready else "new")
        return SimpleNamespace(first=lambda: row if state.ready is not None else None)

    Branch.objects = SimpleNamespace(filter=saved)
    cursor = SimpleNamespace(
        execute=Mock(), fetchone=lambda: ("branch_x.dcim_device" if state.schema else None,)
    )
    modules = {
        "netbox_branching": SimpleNamespace(),
        "netbox_branching.contextvars": SimpleNamespace(
            active_branch=SimpleNamespace(get=lambda: state.branch)
        ),
        "dcim": SimpleNamespace(),
        "dcim.models": SimpleNamespace(Device=object),
        "django": SimpleNamespace(),
        "django.db": SimpleNamespace(
            router=SimpleNamespace(
                db_for_read=lambda model: state.alias, db_for_write=lambda model: state.alias
            ),
            connections={
                "schema_branch_x": SimpleNamespace(cursor=lambda: nullcontext(cursor)),
                "default": SimpleNamespace(cursor=lambda: nullcontext(cursor)),
            },
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return state


def test_accepts_a_ready_active_branch(branching):
    branch_guard.require_active_branch()


@pytest.mark.parametrize("change, message", [
    ({"branch": None}, "no branch is active"),
    ({"ready": False}, "not ready"),
    ({"ready": None}, "not ready"),
    ({"alias": "default"}, "inventory uses"),
    ({"schema": False}, "no schema"),
])
def test_refuses(branching, change, message):
    for name, value in change.items():
        setattr(branching, name, value)
    with pytest.raises(BranchRequiredError, match=message):
        branch_guard.require_active_branch()


def test_refuses_without_netbox_branching(monkeypatch):
    monkeypatch.setitem(sys.modules, "netbox_branching.contextvars", None)
    with pytest.raises(BranchRequiredError, match="netbox-branching is not installed"):
        branch_guard.require_active_branch()


@pytest.fixture
def refused(monkeypatch):
    def refuse():
        raise BranchRequiredError("refused")
    monkeypatch.setattr(branch_guard, "require_active_branch", refuse)


def test_engine_refuses_before_it_reads_configuration(refused, monkeypatch):
    load = Mock()
    monkeypatch.setattr(sync_engine, "_load_runtime_or_exit", load)
    with pytest.raises(BranchRequiredError):
        sync_engine.run_sync_once()
    load.assert_not_called()


@pytest.mark.parametrize("dry_run", [True, False])
def test_service_refuses(refused, monkeypatch, dry_run):
    for name in ("run_sync_once", "_run_preflight"):
        monkeypatch.setattr(sync_service, name, Mock(side_effect=AssertionError))
    with pytest.raises(BranchRequiredError):
        sync_service.execute_sync(dry_run=dry_run)


@pytest.mark.parametrize("dry_run", [True, False])
def test_orchestrator_refuses_before_it_touches_settings(refused, dry_run):
    from typing import Any, Iterable

    settings = Mock()
    run = _load_function("services/orchestrator.py", "run_sync", {
        "Any": Any, "Iterable": Iterable, "branch_guard": branch_guard,
        "get_or_create_global_settings": settings,
    }, keep_decorators=True)
    with pytest.raises(BranchRequiredError):
        run(dry_run=dry_run, cleanup_requested=False)
    settings.assert_not_called()


@pytest.mark.parametrize("dry_run", [True, False])
def test_job_function_refuses_before_it_makes_a_run_row(refused, dry_run):
    from typing import Any

    runs = Mock()
    job = _load_function("jobs.py", "_run_sync_job", {
        "Any": Any, "branch_guard": branch_guard, "SyncRun": runs,
    }, keep_decorators=True)
    with pytest.raises(BranchRequiredError):
        job(dry_run=dry_run, cleanup_requested=False, requested_by_id=None, trigger="t", job_id="")
    assert not runs.mock_calls


@pytest.mark.parametrize("active", [False, True])
def test_enqueue_refuses(refused, monkeypatch, active):
    if active:
        monkeypatch.setattr(branch_guard, "require_active_branch", Mock())
    cls = Mock()
    enqueue = _load_function("jobs.py", "enqueue_sync", {
        "branch_guard": branch_guard, "can_queue_sync": lambda user: True,
        "can_run_cleanup": lambda user: True, "PermissionDenied": PermissionError,
    }, class_name="UnifiSyncJob")
    with pytest.raises(BranchRequiredError, match="background job"):
        enqueue(cls)
    cls.enqueue.assert_not_called()


def test_schedule_refuses_before_it_marks_the_tick(refused):
    tick = Mock()
    run = _load_function("jobs.py", "run", {
        "branch_guard": branch_guard, "get_or_create_global_settings": Mock(),
        "scheduler_due": lambda settings: True, "mark_scheduler_tick": tick,
        "_run_sync_job": Mock(side_effect=AssertionError),
    }, class_name="UnifiSyncSchedulerJob")
    with pytest.raises(BranchRequiredError):
        run(SimpleNamespace(job=None))
    tick.assert_not_called()


def test_job_logs_why_it_was_refused():
    class JobFailed(Exception):
        pass

    run = _load_function("jobs.py", "run", {
        "_run_sync_job": Mock(side_effect=BranchRequiredError("refused")),
        "SyncConfigurationError": BranchRequiredError, "JobFailed": JobFailed,
    }, class_name="UnifiSyncJob")
    job = SimpleNamespace(logger=Mock(), job=None)
    with pytest.raises(JobFailed):
        run(job)
    job.logger.error.assert_called_once_with("Configuration error: refused")


# --- holding the branch for the whole run -------------------------------------------------------

class FakeHolder:
    """The connection of the advisory lock."""

    def __init__(self, taken=True, pid=11):
        self.taken, self.pid, self.closed, self.sql = taken, pid, False, []
        self.tables = ["core_objectchange", "dcim_device", "dcim_interface"]

    def copy(self):
        return self

    def inc_thread_sharing(self):
        pass

    def cursor(self):
        holder = self

        class Cursor:
            def execute(self, sql, params=None):
                holder.sql.append(sql)
                self.row = (holder.taken, 11) if "advisory" in sql else (holder.pid,)

            def fetchone(self):
                return self.row

            def fetchall(self):
                return [(table,) for table in holder.tables]
        return nullcontext(Cursor())

    def close(self):
        self.closed = True


class FakeBranchConnection:
    def __init__(self, alias="schema_branch_x"):
        self.alias, self.execute_wrappers = alias, []
        self.in_atomic_block = self.needs_rollback = False
        self.sql = []
        self.lock_error = None
        status = SimpleNamespace(transaction_status="IDLE")
        self.connection = SimpleNamespace(info=status, execute=self._raw)

    def _raw(self, sql):
        if self.lock_error:
            raise self.lock_error
        self.sql.append(sql)


@pytest.fixture
def held(branching, monkeypatch):
    """A branch with fake connections; the statement wrapper is called by hand."""
    holder, branch_connection = FakeHolder(), FakeBranchConnection()

    @contextmanager
    def atomic(using):
        branch_connection.sql.append(f"BEGIN {using}")
        branch_connection.in_atomic_block = True
        try:
            yield
        finally:
            branch_connection.in_atomic_block = False
            branch_connection.sql.append("END")

    created = SimpleNamespace(connect=Mock())
    django_db = sys.modules["django.db"]
    branch_connection.cursor = django_db.connections["schema_branch_x"].cursor
    django_db.DEFAULT_DB_ALIAS = "default"
    django_db.transaction = SimpleNamespace(atomic=atomic)
    django_db.connections = {**django_db.connections, "default": holder,
                             "schema_branch_x": branch_connection}
    for name, module in {
        "django.db.backends": SimpleNamespace(),
        "django.db.backends.signals": SimpleNamespace(connection_created=created),
        "psycopg": SimpleNamespace(),
        "psycopg.pq": SimpleNamespace(TransactionStatus=SimpleNamespace(INERROR="INERROR")),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return SimpleNamespace(holder=holder, connection=branch_connection, created=created,
                           state=branching)


def run_statement(connection, sql="SELECT 1"):
    executed = []

    def execute(sql, params, many, context):
        executed.append(sql)
        connection.sql.append(sql)
        return "result"

    wrapper = connection.execute_wrappers[0]
    return wrapper(execute, sql, None, False, {"connection": connection}), executed


def test_a_run_holds_the_branch_and_locks_it_before_every_statement(held):
    with branch_guard.hold_branch():
        assert held.holder.sql[0].startswith("SELECT pg_try_advisory_lock(")
        assert held.connection.execute_wrappers == [branch_guard._guard_statement]
        held.connection.sql.clear()
        assert run_statement(held.connection)[0] == "result"
        assert held.connection.sql == [
            "BEGIN schema_branch_x",
            'LOCK TABLE "branch_x"."core_objectchange" IN ACCESS SHARE MODE',
            "SELECT 1", "END",
        ]
        held.connection.in_atomic_block = True
        held.connection.sql.clear()
        run_statement(held.connection)
        assert held.connection.sql[0].startswith("LOCK TABLE")
        held.connection.in_atomic_block = False
    assert held.holder.closed


def test_new_branch_connections_get_the_wrapper(held):
    with branch_guard.hold_branch():
        receiver = held.created.connect.call_args.args[0]
    for alias, wrapped in (("schema_branch_y", True), ("default", False)):
        connection = FakeBranchConnection(alias)
        connection.execute_wrappers.append("other")
        receiver(sender=None, connection=connection)
        assert (connection.execute_wrappers[0] is branch_guard._guard_statement) is wrapped


@pytest.mark.parametrize("sql", ["SAVEPOINT s1", "ROLLBACK TO SAVEPOINT s1", "RELEASE SAVEPOINT s1"])
def test_transaction_control_is_not_locked(held, sql):
    with branch_guard.hold_branch():
        held.connection.in_atomic_block = True
        held.connection.sql.clear()
        run_statement(held.connection, sql)
        assert held.connection.sql == [sql]
        held.connection.in_atomic_block = False


def test_a_failed_transaction_is_left_to_fail(held):
    with branch_guard.hold_branch():
        held.connection.in_atomic_block = True
        held.connection.connection.info.transaction_status = "INERROR"
        held.connection.sql.clear()
        run_statement(held.connection)
        assert held.connection.sql == ["SELECT 1"]
        held.connection.connection.info.transaction_status = "IDLE"
        held.connection.in_atomic_block = False


def test_a_lost_schema_stops_every_statement_and_fails_the_run(held, real_inventory_guard):
    with pytest.raises(branch_guard.BranchLostError, match="Sync failed"):
        with branch_guard.hold_branch():
            held.connection.lock_error = RuntimeError('relation "core_objectchange" does not exist')
            with pytest.raises(branch_guard.BranchLostError):
                run_statement(held.connection)
            held.connection.lock_error = None
            with pytest.raises(branch_guard.BranchLostError):
                run_statement(held.connection)
            assert "SELECT 1" not in held.connection.sql
            with pytest.raises(branch_guard.BranchLostError):
                branch_guard.require_held()
    assert held.holder.closed


def test_a_lost_lock_stops_the_statement(held):
    with pytest.raises(branch_guard.BranchLostError):
        with branch_guard.hold_branch():
            held.holder.pid = 12
            with pytest.raises(branch_guard.BranchLostError, match="replaced"):
                run_statement(held.connection)
            assert "SELECT 1" not in held.connection.sql


@pytest.mark.parametrize("change", [{"ready": False}, {"schema": False}, {"alias": "default"}])
def test_a_branch_changed_during_the_run_fails_it(held, change):
    with pytest.raises(branch_guard.BranchLostError, match="changed during the run"):
        with branch_guard.hold_branch():
            for name, value in change.items():
                setattr(held.state, name, value)


def test_a_busy_branch_is_refused(held):
    held.holder.taken = False
    with pytest.raises(BranchRequiredError, match="is in use"):
        with branch_guard.hold_branch():
            pytest.fail("ran")
    assert held.holder.closed


def test_a_nested_entry_point_joins_the_run(held):
    with branch_guard.hold_branch() as outer:
        with branch_guard.hold_branch() as inner:
            assert inner is outer
        assert len([s for s in held.holder.sql if "try_advisory" in s]) == 1


def test_statements_of_other_connections_and_outside_a_run_pass(held):
    other = FakeBranchConnection("default")
    other.execute_wrappers.append(branch_guard._guard_statement)
    assert run_statement(other)[1] == ["SELECT 1"]
    held.connection.execute_wrappers.append(branch_guard._guard_statement)
    assert run_statement(held.connection)[1] == ["SELECT 1"]
    assert held.connection.sql == ["SELECT 1"]


def test_every_entry_point_holds_the_branch():
    import ast
    from pathlib import Path

    root = Path(branch_guard.__file__).resolve().parents[1]
    for path, name in (("jobs.py", "_run_sync_job"), ("services/orchestrator.py", "run_sync"),
                       ("services/sync_service.py", "execute_sync"),
                       ("services/sync_engine.py", "run_sync_once")):
        tree = ast.parse((root / path).read_text())
        function = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name == name)
        assert [ast.unparse(d) for d in function.decorator_list] == ["branch_guard.in_branch"]


# --- netbox-branching's actions on a held branch ------------------------------------------------

@pytest.fixture
def actions(monkeypatch):
    connection = FakeHolder()
    monkeypatch.setitem(sys.modules, "django.db", SimpleNamespace(connection=connection))
    monkeypatch.setitem(sys.modules, "utilities.exceptions",
                        SimpleNamespace(AbortRequest=PermissionError))
    signals = SimpleNamespace(**{
        f"{when}_{action}": SimpleNamespace(connect=Mock())
        for when in ("pre", "post")
        for action in ("merge", "revert", "sync", "migrate", "deprovision")
    })
    monkeypatch.setitem(sys.modules, "netbox_branching", SimpleNamespace(signals=signals))
    monkeypatch.setitem(sys.modules, "netbox_branching.signals", signals)
    return SimpleNamespace(connection=connection, signals=signals)


BRANCH = SimpleNamespace(pk=7, name="b")


@pytest.mark.parametrize("receiver, lock", [
    ("_refuse_action", "pg_try_advisory_lock_shared"),
    ("_refuse_deprovision", "pg_try_advisory_xact_lock_shared"),
])
def test_an_action_on_a_held_branch_is_refused(actions, receiver, lock):
    getattr(branch_guard, receiver)(sender=None, branch=BRANCH)
    assert lock in actions.connection.sql[-1]
    actions.connection.taken = False
    with pytest.raises(PermissionError, match="A UniFi sync is running in branch 'b'"):
        getattr(branch_guard, receiver)(sender=None, branch=BRANCH)


def test_every_action_is_connected(actions):
    branch_guard.connect_branch_actions()
    for action in ("merge", "revert", "sync", "migrate"):
        pre = getattr(actions.signals, f"pre_{action}").connect.call_args.args[0]
        post = getattr(actions.signals, f"post_{action}").connect.call_args.args[0]
        assert (pre, post) == (branch_guard._refuse_action, branch_guard._release_action)
    assert actions.signals.pre_deprovision.connect.call_args.args[0] == (
        branch_guard._refuse_deprovision)


def test_the_plugin_connects_them_when_branching_is_installed():
    from pathlib import Path

    text = (Path(branch_guard.__file__).resolve().parents[1] / "__init__.py").read_text()
    assert 'if apps.is_installed("netbox_branching"):' in text
    assert "connect_branch_actions()" in text


@pytest.fixture
def real_inventory_guard():
    return branch_guard.require_held


def test_a_branch_table_dropped_during_the_run_fails_it(held):
    """A table dropped alone resolves to main's: the run must not end clean."""
    with pytest.raises(branch_guard.BranchLostError, match=r"no longer has \['dcim_interface'\]"):
        with branch_guard.hold_branch():
            held.holder.tables.remove("dcim_interface")
    assert any("pg_tables" in sql for sql in held.holder.sql)
