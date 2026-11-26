from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import sync_engine as engine


def _sync_offline(monkeypatch, value, state="OFFLINE"):
    if value is None:
        monkeypatch.delenv("SYNC_OFFLINE_CABLE_DELETE", raising=False)
    else:
        monkeypatch.setenv("SYNC_OFFLINE_CABLE_DELETE", value)
    cable = Mock(a_terminations=[], b_terminations=[])
    nb = Mock()
    nb.dcim.interfaces.filter.return_value = [SimpleNamespace(name="Port 1", cable=SimpleNamespace(id=1))]
    nb.dcim.cables.get.return_value = cable
    engine.sync_uplink_cable(nb, SimpleNamespace(id=1), {"name": "sw", "state": state}, {})
    return cable


@pytest.mark.parametrize("state", ["OFFLINE", "DISCONNECTED", "0"])
def test_an_offline_devices_cables_are_kept(monkeypatch, state):
    _sync_offline(monkeypatch, "false", state).delete.assert_not_called()


@pytest.mark.parametrize("value", [None, "true"])
def test_by_default_an_offline_devices_cables_are_removed(monkeypatch, value):
    _sync_offline(monkeypatch, value).delete.assert_called_once_with()
