"""Every way to start a sync refuses without a ready, active branch that writes go to."""
import sys
from contextlib import nullcontext
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
    })
    with pytest.raises(BranchRequiredError):
        run(dry_run=dry_run, cleanup_requested=False)
    settings.assert_not_called()


@pytest.mark.parametrize("dry_run", [True, False])
def test_job_function_refuses_before_it_makes_a_run_row(refused, dry_run):
    from typing import Any

    runs = Mock()
    job = _load_function("jobs.py", "_run_sync_job", {
        "Any": Any, "branch_guard": branch_guard, "SyncRun": runs,
    })
    with pytest.raises(BranchRequiredError):
        job(dry_run=dry_run, cleanup_requested=False, requested_by_id=None, trigger="t", job_id="")
    assert not runs.mock_calls


def test_enqueue_refuses(refused):
    cls = Mock()
    enqueue = _load_function("jobs.py", "enqueue_sync", {
        "branch_guard": branch_guard, "can_queue_sync": lambda user: True,
        "can_run_cleanup": lambda user: True, "PermissionDenied": PermissionError,
    }, class_name="UnifiSyncJob")
    with pytest.raises(BranchRequiredError):
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
