from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import sync_engine as engine


def _sync_vlan(monkeypatch, keep, name="IoT", status="active", enabled=True):
    if keep is None:
        monkeypatch.delenv("SYNC_KEEP_EXISTING", raising=False)
    else:
        monkeypatch.setenv("SYNC_KEEP_EXISTING", keep)
    engine._vlan_cache.clear()
    vlan = SimpleNamespace(name=name, status=status, save=Mock())
    nb = SimpleNamespace(ipam=SimpleNamespace(vlans=Mock(), vlan_groups=Mock()))
    nb.ipam.vlans.get.return_value = vlan
    site = SimpleNamespace(network_conf=SimpleNamespace(
        all=lambda: [{"name": "IoT", "vlanId": 20, "enabled": enabled}]))
    engine.sync_site_vlans(nb, site, SimpleNamespace(id=1, name="S"), SimpleNamespace(id=1))
    engine._vlan_cache.clear()
    return vlan


def test_keeps_a_vlans_name(monkeypatch):
    vlan = _sync_vlan(monkeypatch, "true", name="Typed")
    assert vlan.name == "Typed"
    vlan.save.assert_not_called()


@pytest.mark.parametrize("status, enabled", [
    ("reserved", True), ("deprecated", True), ("deprecated", False), ("active", False),
    (SimpleNamespace(value="reserved"), True),
])
def test_keeps_a_vlans_status(monkeypatch, status, enabled):
    vlan = _sync_vlan(monkeypatch, "true", status=status, enabled=enabled)
    assert vlan.status is status
    vlan.save.assert_not_called()


@pytest.mark.parametrize("keep", [None, "false"])
def test_by_default_a_vlan_takes_unifis_name_and_status(monkeypatch, keep):
    vlan = _sync_vlan(monkeypatch, keep, name="Typed", status="reserved")
    assert (vlan.name, vlan.status) == ("IoT", "active")
    vlan.save.assert_called_once()


def _sync_device(monkeypatch, keep, **device_fields):
    if keep is None:
        monkeypatch.delenv("SYNC_KEEP_EXISTING", raising=False)
    else:
        monkeypatch.setenv("SYNC_KEEP_EXISTING", keep)
    for option in ("SYNC_DEVICE_CUSTOM_FIELDS", "SYNC_PRIMARY_IPS", "SYNC_INTERFACES"):
        monkeypatch.setenv(option, "false")
    unifi_type, unifi_role = SimpleNamespace(id=9, name="t"), SimpleNamespace(id=8, name="r")
    monkeypatch.setattr(engine, "select_netbox_role_for_device", lambda d: (unifi_role, "LAN"))
    monkeypatch.setattr(engine, "get_vrf_for_site", lambda *a: (None, "none"))
    monkeypatch.setattr(engine, "find_device_type", lambda *a: (unifi_type, True))
    monkeypatch.setattr(engine, "ensure_tag", lambda *a: None)
    fields = {"name": "UniFi", "device_type": unifi_type, "role": unifi_role, **device_fields}
    device = SimpleNamespace(id=5, role_id=fields["role"].id, save=Mock(), **fields)
    nb = Mock()
    nb.dcim.devices.get.return_value = device
    engine.process_device(Mock(), nb, SimpleNamespace(id=1, name="S"), {
        "name": "UniFi", "model": "X", "mac": "02:00:00:00:00:01", "ip": "192.0.2.2",
    }, SimpleNamespace(id=1), SimpleNamespace(id=1))
    return device


TYPED = SimpleNamespace(id=1, name="typed")


@pytest.mark.parametrize("field, typed", [
    ("name", "typed"), ("device_type", TYPED), ("role", TYPED),
])
def test_keeps_a_devices_field(monkeypatch, field, typed):
    device = _sync_device(monkeypatch, "true", **{field: typed})
    assert getattr(device, field) is typed
    assert device.role_id == device.role.id
    device.save.assert_not_called()


@pytest.mark.parametrize("keep", [None, "false"])
def test_by_default_a_device_takes_unifis_name_type_and_role(monkeypatch, keep):
    device = _sync_device(monkeypatch, keep, name="typed", device_type=TYPED, role=TYPED)
    assert (device.name, device.device_type, device.role_id) == ("UniFi", 9, 8)
    assert device.save.called
