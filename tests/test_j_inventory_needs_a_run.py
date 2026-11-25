"""The inventory adapter refuses to read or write outside a run that holds its branch."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import branch_guard
from netbox_unifi_sync.services.branch_guard import BranchLostError, BranchRequiredError
from netbox_unifi_sync.services.sync.netbox_orm import _Endpoint, _OrmObject


@pytest.fixture
def real_inventory_guard():
    return branch_guard.require_held


class Model:
    __name__ = "Model"
    objects = Mock()
    DoesNotExist = LookupError


CALLS = {
    "get": lambda: _Endpoint(Model).get(name="x"),
    "get by key": lambda: _Endpoint(Model).get(1),
    "filter": lambda: _Endpoint(Model).filter(name="x"),
    "all": lambda: _Endpoint(Model).all(),
    "create": lambda: _Endpoint(Model).create({"name": "x"}),
    "save": lambda: _OrmObject(Mock()).save(),
    "delete": lambda: _OrmObject(Mock()).delete(),
    "set": lambda: setattr(_OrmObject(Mock()), "name", "x"),
}


@pytest.mark.parametrize("call", CALLS)
def test_refused_outside_a_run(real_inventory_guard, call):
    Model.objects.reset_mock()
    with pytest.raises(BranchRequiredError, match="no sync holds a branch"):
        CALLS[call]()
    assert not Model.objects.mock_calls


@pytest.mark.parametrize("call", CALLS)
def test_refused_once_the_branch_is_lost(real_inventory_guard, monkeypatch, call):
    hold = SimpleNamespace(lost="gone")
    token = branch_guard._held.set(hold)
    try:
        with pytest.raises(BranchLostError, match="gone"):
            CALLS[call]()
    finally:
        branch_guard._held.reset(token)
