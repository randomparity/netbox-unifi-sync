from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import sync_engine as engine


def _sync(monkeypatch, value, name):
    if value is None:
        monkeypatch.delenv("SYNC_INTERFACE_DELETE", raising=False)
    else:
        monkeypatch.setenv("SYNC_INTERFACE_DELETE", value)
    interface = Mock(cable=None)
    interface.name = name
    nb = Mock()
    nb.dcim.interfaces.filter.return_value = [interface]
    engine.sync_device_interfaces(nb, SimpleNamespace(id=1), {"name": "sw"}, "legacy")
    return interface


# One name for each cleanup: an incomplete name, and stale port names UniFi no longer lists.
NAMES = ["Port ?", "eth9", "SFP 1", "Port 3 (PoE+)"]


@pytest.mark.parametrize("name", NAMES)
def test_no_interface_is_deleted(monkeypatch, name):
    _sync(monkeypatch, "false", name).delete.assert_not_called()


@pytest.mark.parametrize("value", [None, "true"])
@pytest.mark.parametrize("name", NAMES)
def test_by_default_unlisted_interfaces_are_deleted(monkeypatch, value, name):
    _sync(monkeypatch, value, name).delete.assert_called_once_with()
