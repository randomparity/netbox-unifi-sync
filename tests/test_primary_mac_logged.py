import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from netbox_unifi_sync.services import sync_engine as engine


def _stub(monkeypatch, name, **members):
    """Stand in for a module that only exists inside NetBox."""
    parts = name.split(".")
    for end in range(1, len(parts)):
        package = ".".join(parts[:end])
        if package not in sys.modules:
            monkeypatch.setitem(sys.modules, package, ModuleType(package))
    module = ModuleType(name)
    for key, value in members.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


def test_primary_mac_is_saved_so_that_it_is_change_logged(monkeypatch):
    fresh = Mock()
    order = Mock()
    order.attach_mock(fresh.snapshot, "snapshot")
    order.attach_mock(fresh.save, "save")
    interfaces = Mock()
    interfaces.get.return_value = fresh
    mac = object()
    macs = Mock()
    macs.get_or_create.return_value = (mac, True)
    _stub(monkeypatch, "dcim.models",
          Interface=SimpleNamespace(objects=interfaces), MACAddress=SimpleNamespace(objects=macs))
    _stub(monkeypatch, "django.contrib.contenttypes.models", ContentType=Mock())
    interface = SimpleNamespace(pk=7, name="Port 1", primary_mac_address=None)

    engine._set_interface_mac(interface, "02:00:00:00:00:01")

    interfaces.get.assert_called_once_with(pk=7)
    assert [call[0] for call in order.mock_calls] == ["snapshot", "save"]
    assert fresh.primary_mac_address is mac
    assert interface.primary_mac_address is mac
    interfaces.filter.assert_not_called()


def test_the_same_mac_is_not_saved_again(monkeypatch):
    interfaces = Mock()
    _stub(monkeypatch, "dcim.models",
          Interface=SimpleNamespace(objects=interfaces), MACAddress=Mock())
    _stub(monkeypatch, "django.contrib.contenttypes.models", ContentType=Mock())
    current = SimpleNamespace(mac_address="02:00:00:00:00:01")
    engine._set_interface_mac(
        SimpleNamespace(pk=7, name="Port 1", primary_mac_address=current), "02-00-00-00-00-01")
    interfaces.get.assert_not_called()
