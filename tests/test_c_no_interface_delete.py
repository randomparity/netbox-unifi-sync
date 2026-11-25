from types import SimpleNamespace
from unittest.mock import Mock

from netbox_unifi_sync.services import sync_engine as engine


def test_interfaces_kept(monkeypatch):
    monkeypatch.setenv("SYNC_INTERFACE_DELETE", "false")
    stale = [Mock(cable=None) for _ in range(2)]
    stale[0].name, stale[1].name = "eth9", "Port ?"
    nb = Mock()
    nb.dcim.interfaces.filter.return_value = stale
    engine.sync_device_interfaces(nb, SimpleNamespace(id=1), {"name": "d"}, "legacy")
    assert not any(i.delete.called for i in stale)
