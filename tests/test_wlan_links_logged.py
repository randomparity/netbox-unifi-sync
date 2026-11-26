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


def test_an_existing_wireless_lan_is_relinked_through_its_interfaces(monkeypatch):
    from tests.test_wlan_relationships import RelatedObjects, _client, _site

    linked = []
    monkeypatch.setattr(engine, "_set_wlan_interfaces", lambda wlan, ids: linked.append(ids))
    existing = SimpleNamespace(
        status="active", auth_type="wpa-personal", vlan=SimpleNamespace(id=1000),
        interfaces=RelatedObjects(503), save=Mock(),
    )
    wlan = {
        "name": "Corporate", "enabled": True,
        "securityConfiguration": {"type": "WPA2_PERSONAL"},
        "network": {"vlanId": 100}, "broadcastingDeviceIds": ["ap-1"],
    }
    engine.sync_site_wlans(
        _client(existing_wlans=[existing]), _site(wlan), SimpleNamespace(id=1, name="HQ"),
        SimpleNamespace(id=2), devices=[{"id": "ap-1", "macAddress": "02:00:00:00:00:02"}],
    )
    assert [sorted(ids) for ids in linked] == [[501, 502]]
    # Not assigned on the wireless LAN's side, which NetBox does not change-log.
    assert isinstance(existing.interfaces, RelatedObjects)
