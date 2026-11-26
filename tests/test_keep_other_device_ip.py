from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import sync_engine as engine


def _run(holders, assigned_type="dcim.interface"):
    ip = SimpleNamespace(
        id=5, address="198.51.100.2/30", assigned_object_type=assigned_type,
        assigned_object_id=70, save=Mock(),
    )
    nb = Mock()
    nb.ipam.ip_addresses.get.return_value = ip
    nb.dcim.interfaces.get.return_value = SimpleNamespace(id=80, name="wan")
    nb.dcim.devices.filter.side_effect = lambda **k: holders.get(next(iter(k)), [])
    gateway = SimpleNamespace(id=1, primary_ip4=None, save=Mock())
    nets = [{"name": "5G", "purpose": "wan", "ip_subnet": "198.51.100.2/30"}]
    site = SimpleNamespace(network_conf=SimpleNamespace(all=lambda: nets))
    engine.sync_gateway_interfaces(
        nb, gateway, {"name": "gw", "ipAddress": "198.51.100.2"}, site, SimpleNamespace(id=1), None)
    return ip, gateway


@pytest.mark.parametrize("field", ["primary_ip4_id", "primary_ip6_id", "oob_ip_id"])
def test_another_devices_address_stays(field, caplog):
    ip, gateway = _run({field: [SimpleNamespace(id=2)]})
    assert ip.assigned_object_id == 70
    ip.save.assert_not_called()
    gateway.save.assert_not_called()
    assert "Kept IP 198.51.100.2/30" in caplog.text


def test_a_vms_address_stays():
    ip, gateway = _run({}, "virtualization.vminterface")
    assert ip.assigned_object_id == 70
    gateway.save.assert_not_called()


def test_the_gateways_own_address_moves():
    ip, gateway = _run({"primary_ip4_id": [SimpleNamespace(id=1)]})
    assert ip.assigned_object_id == 80
    assert gateway.primary_ip4 == 5


def test_the_gateways_fallback_takes_no_other_devices_address(monkeypatch):
    ip = SimpleNamespace(id=5, assigned_object_type="dcim.interface", assigned_object_id=70)
    nb = Mock()
    nb.ipam.ip_addresses.get.return_value = ip
    nb.dcim.devices.filter.side_effect = lambda **k: (
        [SimpleNamespace(id=2)] if "primary_ip4_id" in k else [])
    monkeypatch.setattr(engine, "_get_matching_prefixes",
                        lambda nb, ip: [SimpleNamespace(prefix="198.51.100.0/30")])
    gateway = SimpleNamespace(id=1, primary_ip4=None, save=Mock())
    # Only a VPN network, which makes no interface, so the fallback runs.
    nets = [{"name": "vpn", "purpose": "remote-user-vpn", "ip_subnet": "203.0.113.1/24"}]
    site = SimpleNamespace(network_conf=SimpleNamespace(all=lambda: nets))
    engine.sync_gateway_interfaces(
        nb, gateway, {"name": "gw", "ipAddress": "198.51.100.2"}, site, SimpleNamespace(id=1), None)
    assert gateway.primary_ip4 is None
    gateway.save.assert_not_called()


def _client_run(monkeypatch, holders):
    monkeypatch.setenv("SYNC_CLIENT_IPS", "true")
    tag = SimpleNamespace(id=3)
    monkeypatch.setattr(engine, "ensure_tag", lambda *args, **kwargs: tag)
    monkeypatch.setattr(engine, "_get_matching_prefixes",
                        lambda nb, ip: [SimpleNamespace(prefix="198.51.100.0/24")])
    interface = SimpleNamespace(id=80, device_id=9)
    monkeypatch.setattr(engine, "_lookup_interface_by_mac", lambda nb, mac: interface)
    ip = SimpleNamespace(id=5, description="typed", assigned_object_type="dcim.interface",
                         assigned_object_id=70, tags=[], save=Mock())
    nb = Mock()
    nb.ipam.ip_addresses.get.return_value = ip
    nb.dcim.devices.filter.side_effect = lambda **k: holders.get(next(iter(k)), [])
    client = {"mac": "02:00:00:00:00:09", "ip": "198.51.100.7", "hostname": "client"}
    site = SimpleNamespace(client=SimpleNamespace(all=lambda: [client]))
    engine.sync_client_ips(nb, site, SimpleNamespace(name="S"), SimpleNamespace(id=1))
    return ip


def test_a_client_does_not_take_or_relabel_another_devices_address(monkeypatch, caplog):
    ip = _client_run(monkeypatch, {"primary_ip4_id": [SimpleNamespace(id=2)]})
    assert (ip.assigned_object_id, ip.description, ip.tags) == (70, "typed", [])
    ip.save.assert_not_called()
    assert "Kept IP 198.51.100.7/24" in caplog.text


def test_a_client_takes_a_free_address(monkeypatch):
    ip = _client_run(monkeypatch, {})
    assert ip.assigned_object_id == 80
    assert ip.tags == [3]
    ip.save.assert_called_once()
