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
