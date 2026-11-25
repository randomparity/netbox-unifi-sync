from types import SimpleNamespace
from unittest.mock import Mock

from netbox_unifi_sync.services import sync_engine as engine


def test_offline_cable_kept(monkeypatch):
    monkeypatch.setenv("SYNC_OFFLINE_CABLE_DELETE", "false")
    cable = Mock(a_terminations=[], b_terminations=[])
    nb = Mock()
    nb.dcim.interfaces.filter.return_value = [SimpleNamespace(name="p", cable=SimpleNamespace(id=1))]
    nb.dcim.cables.get.return_value = cable
    engine.sync_uplink_cable(nb, SimpleNamespace(id=1), {"name": "d", "state": "OFFLINE"}, {})
    cable.delete.assert_not_called()
