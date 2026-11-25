from types import SimpleNamespace
from unittest.mock import Mock

from netbox_unifi_sync.services import sync_engine as engine


def test_keeps_vlan_name(monkeypatch):
    monkeypatch.setenv("SYNC_KEEP_EXISTING", "true")
    engine._vlan_cache.clear()
    vlan = SimpleNamespace(name="My IoT", status="active", save=Mock())
    nb = SimpleNamespace(ipam=SimpleNamespace(vlans=Mock(), vlan_groups=Mock()))
    nb.ipam.vlans.get.return_value = vlan
    site = SimpleNamespace(network_conf=SimpleNamespace(all=lambda: [{"name": "IoT", "vlanId": 20}]))
    engine.sync_site_vlans(nb, site, SimpleNamespace(id=1, name="S"), SimpleNamespace(id=1))
    engine._vlan_cache.clear()
    assert vlan.name == "My IoT"
    vlan.save.assert_not_called()


def test_keeps_device_fields(monkeypatch):
    monkeypatch.setenv("SYNC_KEEP_EXISTING", "true")
    for option in ("SYNC_DEVICE_CUSTOM_FIELDS", "SYNC_PRIMARY_IPS", "SYNC_INTERFACES"):
        monkeypatch.setenv(option, "false")
    new = SimpleNamespace(id=9, name="x")
    monkeypatch.setattr(engine, "select_netbox_role_for_device", lambda d: (new, "LAN"))
    monkeypatch.setattr(engine, "get_vrf_for_site", lambda *a: (None, "none"))
    monkeypatch.setattr(engine, "find_device_type", lambda *a: (new, True))
    monkeypatch.setattr(engine, "ensure_tag", lambda *a: None)
    old = SimpleNamespace(id=1)
    device = SimpleNamespace(id=5, name="typed", device_type=old, role=old, save=Mock())
    nb = Mock()
    nb.dcim.devices.get.return_value = device
    engine.process_device(Mock(), nb, SimpleNamespace(id=1, name="S"), {"name": "UniFi", "model": "X",
                          "mac": "02:00:00:00:00:01", "ip": "10.0.0.2"}, SimpleNamespace(id=1), SimpleNamespace(id=1))
    assert (device.name, device.device_type, device.role) == ("typed", old, old)
    device.save.assert_not_called()
