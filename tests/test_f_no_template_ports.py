from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import sync_engine as engine

PORTS = {"interfaces": {"ports": [{"idx": 1}]}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    engine._clear_run_state()
    yield
    engine._clear_run_state()


def _synced(devices, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_sync_templates", lambda *args: calls.append(args[-1]))
    monkeypatch.setattr(engine, "_resolve_device_specs", lambda model: {
        "ports": [("Port {n} (PoE++)", "2.5gbase-t", 2)], "power_ports": [{"name": "PSU"}]})
    engine._note_port_models(devices)
    kind = SimpleNamespace(id=1, part_number="", comments="", save=Mock())
    engine.ensure_device_type_specs(Mock(), kind, "X")
    return calls


@pytest.mark.parametrize("listed", [
    {"interfaces": ["ports"]}, PORTS, {"port_table": [{"port_idx": 1}]}])
def test_no_interface_templates_when_unifi_lists_ports(listed, monkeypatch):
    # One device of the model is enough, wherever it is in the list.
    devices = [{"model": "X", "interfaces": []}, {"model": "X", **listed}]
    assert _synced(devices, monkeypatch) == ["power-port"]


@pytest.mark.parametrize("other", [
    {"model": "X", "interfaces": []}, {"model": "X", "interfaces": ["radios"]}, {"model": "Y", **PORTS}])
def test_templates_when_unifi_lists_none(other, monkeypatch):
    assert _synced([other], monkeypatch) == ["interface", "power-port"]


def test_templates_when_interfaces_are_not_synced(monkeypatch):
    monkeypatch.setenv("SYNC_INTERFACES", "false")
    assert _synced([{"model": "X", **PORTS}], monkeypatch) == ["interface", "power-port"]
