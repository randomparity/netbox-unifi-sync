from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import sync_engine as engine

MAC = {"mac": "02:00:00:00:00:01"}


def _sync(monkeypatch, strict, uplink):
    """Cable a switch (device 1) to its upstream (device 2); return the cable's two port names."""
    if strict is None:
        monkeypatch.delenv("SYNC_CABLES_STRICT", raising=False)
    else:
        monkeypatch.setenv("SYNC_CABLES_STRICT", strict)
    ports = {
        device: [SimpleNamespace(id=10 * device + n, name=f"Port {n}", type="1000base-t",
                                 cable=None, description="", device=device) for n in (1, 2)]
        for device in (1, 2)
    }
    nb = Mock()
    nb.dcim.interfaces.filter.side_effect = lambda device_id: ports[device_id]
    nb.dcim.interfaces.get.side_effect = lambda device_id, name: next(
        (p for p in ports[device_id] if p.name == name), None)
    upstream = SimpleNamespace(id=2, name="upstream")
    engine.sync_uplink_cable(nb, SimpleNamespace(id=1), {"name": "sw", "state": "ONLINE", "uplink": uplink},
                             {"020000000001": upstream})
    if not nb.dcim.cables.create.called:
        return None
    payload = nb.dcim.cables.create.call_args.args[0]
    names = {p.id: p.name for device in ports.values() for p in device}
    return tuple(names[end[0]["object_id"]] for end in (payload["a_terminations"], payload["b_terminations"]))


def test_a_cable_between_named_ports(monkeypatch):
    uplink = {**MAC, "name": "Port 1", "uplink_remote_port": "Port 2"}
    assert _sync(monkeypatch, "true", uplink) == ("Port 1", "Port 2")


def test_the_local_port_name_is_not_used_for_the_remote_port(monkeypatch):
    # "port_name" names the local port; without a remote name the cable is not made.
    assert _sync(monkeypatch, "true", {**MAC, "port_name": "Port 2"}) is None


def test_no_local_port_is_guessed(monkeypatch):
    assert _sync(monkeypatch, "true", {**MAC, "uplink_remote_port": "Port 2"}) is None


def test_no_remote_port_is_guessed(monkeypatch):
    # The remote port is named but missing, so only a guess could connect it.
    assert _sync(monkeypatch, "true", {**MAC, "name": "Port 1", "uplink_remote_port": "Port 9"}) is None


@pytest.mark.parametrize("strict", [None, "false"])
@pytest.mark.parametrize("uplink, cable", [
    ({**MAC, "port_name": "Port 2"}, ("Port 2", "Port 2")),
    ({**MAC, "uplink_remote_port": "Port 2"}, ("Port 2", "Port 2")),
    ({**MAC, "name": "Port 1", "uplink_remote_port": "Port 9"}, ("Port 1", "Port 1")),
])
def test_by_default_missing_ports_are_guessed(monkeypatch, strict, uplink, cable):
    assert _sync(monkeypatch, strict, uplink) == cable
