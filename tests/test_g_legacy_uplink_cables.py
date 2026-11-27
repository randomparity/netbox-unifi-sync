"""Uplink cables from the legacy API's port numbers, landing on the interfaces the sync made."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import sync_engine as engine
from netbox_unifi_sync.services.sync import ipam

SWITCH, UPSTREAM = "02:00:00:00:00:0a", "02:00:00:00:00:0b"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    engine._clear_run_state()
    monkeypatch.setenv("SYNC_CABLES_STRICT", "true")
    yield
    engine._clear_run_state()


# --- the legacy device list --------------------------------------------------------------------

def _unifi(response=None, error=None):
    session = Mock()
    session.get.side_effect = error
    session.get.return_value = response
    return SimpleNamespace(base_url="https://console.example", session=session, verify_ssl=False,
                           request_timeout=5, integration_auth_headers={"X-API-KEY": "k"})


def test_the_legacy_device_list_is_read_with_the_integration_key():
    unifi = _unifi(SimpleNamespace(status_code=200, json=lambda: {"data": [{"mac": SWITCH}]}))
    assert ipam._fetch_legacy_devices(unifi, SimpleNamespace(internal_reference="default")) == [
        {"mac": SWITCH}]
    url = unifi.session.get.call_args.args[0]
    assert url == "https://console.example/proxy/network/api/s/default/stat/device"
    assert unifi.session.get.call_args.kwargs["headers"]["X-API-KEY"] == "k"


@pytest.mark.parametrize("response, error", [
    (SimpleNamespace(status_code=403, json=lambda: {"data": [{"mac": SWITCH}]}), None),
    (None, RuntimeError("refused")),
])
def test_an_unreadable_legacy_device_list_is_none(response, error):
    unifi = _unifi(response, error)
    assert ipam._fetch_legacy_devices(unifi, SimpleNamespace(internal_reference="default")) is None


def _legacy(mac, uplink):
    return {"mac": mac, "uplink": uplink}


WIRE = {"type": "wire", "port_idx": 8, "uplink_mac": UPSTREAM.upper(), "uplink_remote_port": 3}


@pytest.mark.parametrize("uplink, expected", [
    (WIRE, (8, "02000000000B", 3)),
    ({**WIRE, "uplink_mac": "02-00-00-00-00-0B"}, (8, "02000000000B", 3)),
    ({**WIRE, "type": "wireless"}, None),
    ({k: v for k, v in WIRE.items() if k != "port_idx"}, None),
    ({k: v for k, v in WIRE.items() if k != "uplink_remote_port"}, None),
    ({k: v for k, v in WIRE.items() if k != "uplink_mac"}, None),
])
def test_legacy_uplinks_need_both_port_numbers_on_a_wire(uplink, expected):
    found = engine._legacy_uplinks([_legacy(SWITCH, uplink)])
    assert found.get("02000000000A") == expected


def test_legacy_uplinks_are_matched_by_mac_whatever_its_case_or_separators():
    found = engine._legacy_uplinks([_legacy("02-00-00-00-00-0A", WIRE)])
    devices = [{"macAddress": "02:00:00:00:00:0A"}, {"mac": "020000000099"}]
    unifi = SimpleNamespace(api_style="integration")
    engine._attach_legacy_uplinks(unifi, SimpleNamespace(), devices, legacy=found)
    assert devices[0]["_legacy_uplink"] == (8, "02000000000B", 3)
    assert "_legacy_uplink" not in devices[1]


def test_the_legacy_list_is_read_only_for_the_integration_api(monkeypatch):
    fetch = Mock(return_value=[_legacy(SWITCH, WIRE)])
    monkeypatch.setattr(engine, "_fetch_legacy_devices", fetch)
    devices = [{"mac": SWITCH}]
    engine._attach_legacy_uplinks(SimpleNamespace(api_style="legacy"), SimpleNamespace(), devices)
    fetch.assert_not_called()
    engine._attach_legacy_uplinks(SimpleNamespace(api_style="integration"), SimpleNamespace(), devices)
    fetch.assert_called_once()
    assert devices[0]["_legacy_uplink"] == (8, "02000000000B", 3)


def test_an_unreadable_legacy_list_attaches_nothing_and_warns(monkeypatch, caplog):
    monkeypatch.setattr(engine, "_fetch_legacy_devices", Mock(return_value=None))
    devices = [{"mac": SWITCH}]
    engine._attach_legacy_uplinks(SimpleNamespace(api_style="integration"), SimpleNamespace(), devices)
    assert "_legacy_uplink" not in devices[0]
    assert "legacy device list" in caplog.text


def test_process_site_attaches_legacy_uplinks_before_cables():
    import inspect
    source = inspect.getsource(engine.process_site)
    assert source.index("_attach_legacy_uplinks(") < source.index("sync_uplink_cable(")


# --- port names: the ones the sync gave the interfaces -----------------------------------------

def _sync_ports(monkeypatch, mac, ports, api_style="integration"):
    made = []
    monkeypatch.setattr(engine, "_create_or_get_interface",
                        lambda nb, data: (made.append(data["name"]) or SimpleNamespace(id=len(made), **data), True))
    nb = Mock()
    nb.dcim.interfaces.filter.return_value = []
    device = {"name": "d", "mac": mac}
    if api_style == "integration":
        device["interfaces"] = {"ports": ports}
    else:
        device["port_table"] = ports
    engine.sync_device_interfaces(nb, SimpleNamespace(id=1), device, api_style)
    return set(made)


def test_the_names_the_sync_gives_ports_are_kept_by_index(monkeypatch):
    monkeypatch.setenv("SYNC_INTERFACE_DELETE", "false")
    made = _sync_ports(monkeypatch, SWITCH, [{"idx": 8, "name": "Uplink"}, {"idx": 1}])
    assert made == {"Uplink", "Port 1"}
    assert engine._port_names["02000000000A"] == {8: "Uplink", 1: "Port 1"}
    _sync_ports(monkeypatch, UPSTREAM.upper(), [{"port_idx": 3, "name": "Core 3"}], "legacy")
    assert engine._port_names["02000000000B"] == {3: "Core 3"}


CABLES = {}


# --- the cable --------------------------------------------------------------------------------

def _cable(monkeypatch, *, local=("Uplink", None), remote=("Port 3", None), integration=None,
           names=True):
    """Sync the switch's uplink; return the created cable's ends, or None."""
    if names:
        engine._port_names["02000000000A"] = {8: "Uplink"}
        engine._port_names["02000000000B"] = {3: "Port 3"}
    ports = {
        1: [SimpleNamespace(id=11, name=local[0], type="1000base-t", cable=local[1], description="")],
        2: [SimpleNamespace(id=23, name=remote[0], type="1000base-t", cable=remote[1], description="")],
    }
    nb = Mock()
    nb.dcim.interfaces.filter.side_effect = lambda device_id: ports[device_id]
    nb.dcim.interfaces.get.side_effect = lambda device_id, name: next(
        (p for p in ports[device_id] if p.name == name), None)
    nb.dcim.cables.get.side_effect = lambda pk: CABLES.setdefault(pk, SimpleNamespace(
        id=pk, a_terminations=[], b_terminations=[], delete=Mock()))
    device = {"name": "sw", "mac": SWITCH, "state": "ONLINE", "uplink": integration or {"deviceId": "u2"},
              "_legacy_uplink": (8, "02000000000B", 3)}
    upstream = SimpleNamespace(id=2, name="upstream")
    engine.sync_uplink_cable(nb, SimpleNamespace(id=1), device, {"02000000000B": upstream, "u2": upstream})
    if not nb.dcim.cables.create.called:
        return None
    payload = nb.dcim.cables.create.call_args.args[0]
    return payload["a_terminations"][0]["object_id"], payload["b_terminations"][0]["object_id"]


def test_a_cable_between_the_interfaces_the_sync_made(monkeypatch):
    assert _cable(monkeypatch) == (11, 23)


def test_a_legacy_port_table_name_does_not_redirect_the_cable(monkeypatch):
    # The legacy list names local port 8 "Port 8"; the sync named it "Uplink". The cable lands on
    # "Uplink", and with no interface of the engine's name there is no cable.
    assert _cable(monkeypatch) == (11, 23)
    assert _cable(monkeypatch, local=("Port 8", None)) is None


def test_no_cable_without_the_ports_the_sync_named(monkeypatch):
    assert _cable(monkeypatch, names=False) is None


def test_no_cable_when_an_interface_is_missing(monkeypatch):
    assert _cable(monkeypatch, remote=("Port 4", None)) is None


def test_the_integration_apis_port_names_win(monkeypatch):
    named = {"deviceId": "u2", "name": "Other", "uplink_remote_port": "Port 9"}
    assert _cable(monkeypatch, integration=named) is None


@pytest.mark.parametrize("end", ["local", "remote"])
def test_a_hand_made_cable_stays_and_none_is_added(monkeypatch, end):
    CABLES.clear()
    kwargs = {end: (("Uplink" if end == "local" else "Port 3"), SimpleNamespace(id=77))}
    assert _cable(monkeypatch, **kwargs) is None
    assert all(not cable.delete.called for cable in CABLES.values())


def test_an_offline_devices_hand_made_cable_stays(monkeypatch):
    monkeypatch.delenv("SYNC_OFFLINE_CABLE_DELETE", raising=False)
    hand = SimpleNamespace(id=77, a_terminations=[], b_terminations=[], delete=Mock())
    nb = Mock()
    nb.dcim.interfaces.filter.return_value = [SimpleNamespace(name="Uplink", cable=hand)]
    nb.dcim.cables.get.return_value = hand
    engine.sync_uplink_cable(nb, SimpleNamespace(id=1), {"name": "sw", "state": "OFFLINE",
                             "_legacy_uplink": (8, "02000000000B", 3)}, {})
    hand.delete.assert_not_called()
    nb.dcim.cables.create.assert_not_called()


@pytest.mark.parametrize("names", [{8: "Uplink"}, {}])
def test_no_names_unless_the_sync_named_both_ends(names):
    engine._port_names["02000000000A"] = names
    engine._port_names["02000000000B"] = {} if names else {3: "Port 3"}
    device = {"mac": SWITCH, "_legacy_uplink": (8, "02000000000B", 3)}
    assert engine._legacy_port_names(device) is None
