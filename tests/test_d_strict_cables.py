from types import SimpleNamespace
from unittest.mock import Mock

from netbox_unifi_sync.services import sync_engine as engine


def _run(uplink):
    ports = [SimpleNamespace(id=i, name=f"Port {i}", type="1000base-t", cable=None, description="") for i in (1, 2)]
    nb = Mock()
    nb.dcim.interfaces.filter.return_value = ports
    nb.dcim.interfaces.get.side_effect = lambda name, **k: next((p for p in ports if p.name == name), None)
    up = SimpleNamespace(id=2, name="up")
    engine.sync_uplink_cable(nb, SimpleNamespace(id=1), {"name": "d", "state": "ONLINE", "uplink": uplink},
                             {"020000000001": up})
    return nb.dcim.cables.create


def test_only_identified_ports(monkeypatch):
    monkeypatch.setenv("SYNC_CABLES_STRICT", "true")
    mac = {"mac": "02:00:00:00:00:01"}
    _run(mac).assert_not_called()
    _run({**mac, "port_name": "Port 1"}).assert_not_called()
    _run({**mac, "port_name": "Port 1", "uplink_remote_port": "Port 2"}).assert_called_once()
