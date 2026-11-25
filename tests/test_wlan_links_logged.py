import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from netbox_unifi_sync.services import sync_engine as engine


def test_links_change_through_the_interface(monkeypatch):
    interfaces = {pk: Mock(pk=pk) for pk in (1, 2, 3)}
    objects = Mock()
    objects.filter.side_effect = lambda pk__in: [interfaces[pk] for pk in sorted(pk__in)]
    models = ModuleType("dcim.models")
    models.Interface = SimpleNamespace(objects=objects)
    monkeypatch.setitem(sys.modules, "dcim", ModuleType("dcim"))
    monkeypatch.setitem(sys.modules, "dcim.models", models)
    related = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    wlan = SimpleNamespace(id=9, interfaces=SimpleNamespace(all=lambda: related))

    engine._set_wlan_interfaces(wlan, [2, 3])

    interfaces[1].wireless_lans.remove.assert_called_once_with(9)
    interfaces[3].wireless_lans.add.assert_called_once_with(9)
    for pk in (1, 3):
        assert [call[0] for call in interfaces[pk].mock_calls][0] == "snapshot"
        interfaces[pk].save.assert_called_once_with()
    interfaces[2].save.assert_not_called()
