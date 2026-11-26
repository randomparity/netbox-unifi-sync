from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.services import sync_engine as engine


@pytest.mark.parametrize("value, stored", [
    (5.1, "5.10"), ("3", "3.00"), (0.1 + 0.2, "0.30"), (2.345, "2.34"), (2, "2.00")])
def test_weight_has_the_two_decimals_of_the_field(value, stored):
    weight = engine._spec_weight(value)
    assert isinstance(weight, Decimal)
    assert str(weight) == stored


def _sync(weight, monkeypatch):
    monkeypatch.setattr(engine, "_sync_templates", lambda *args: None)
    kind = SimpleNamespace(id=1, part_number="", comments="", weight=weight, save=Mock())
    engine._ensure_device_type_specs_inner(Mock(), kind, "X", {"weight": 5.1})
    return kind


def test_a_stored_weight_is_not_written_again(monkeypatch):
    kind = _sync(Decimal("5.10"), monkeypatch)
    kind.save.assert_not_called()


def test_another_weight_is_written_as_a_decimal(monkeypatch):
    kind = _sync(None, monkeypatch)
    kind.save.assert_called_once()
    assert isinstance(kind.weight, Decimal)


@pytest.mark.parametrize("weight", ["abc", "", "inf"])
def test_a_malformed_weight_is_skipped(weight, monkeypatch):
    monkeypatch.setattr(engine, "_sync_templates", lambda *args: None)
    kind = SimpleNamespace(id=1, part_number="", comments="", weight=None, save=Mock())
    engine._ensure_device_type_specs_inner(Mock(), kind, "X", {"weight": weight})
    assert kind.weight is None


def _create_type(weight, monkeypatch):
    """Sync a device of a model that has no device type yet; return the type's create payload."""
    role = SimpleNamespace(id=1, name="Switch")
    for name, value in {
        "select_netbox_role_for_device": lambda d: (role, "LAN"),
        "get_vrf_for_site": lambda *a: (None, None),
        "_resolve_device_specs": lambda m: {"weight": weight, "part_number": "P"},
        "find_device_type": lambda *a: (None, False),
        "ensure_device_type_specs": Mock(),
        "ensure_tag": lambda *a, **k: None,
        "sync_device_interfaces": Mock(),
    }.items():
        monkeypatch.setattr(engine, name, value)
    monkeypatch.setenv("SYNC_DEVICE_CUSTOM_FIELDS", "false")
    monkeypatch.setenv("SYNC_PRIMARY_IPS", "false")
    nb = Mock()
    nb.dcim.devices.get.return_value = None
    engine.process_device(Mock(), nb, SimpleNamespace(id=3, name="S"),
                          {"name": "sw", "serial": "SERIAL1", "model": "M"},
                          SimpleNamespace(id=1, name="Ubiquiti"), SimpleNamespace(id=1))
    return nb.dcim.device_types.create.call_args.args[0]


@pytest.mark.parametrize("weight", ["abc", "", "inf"])
def test_a_new_type_skips_a_malformed_weight(weight, monkeypatch):
    payload = _create_type(weight, monkeypatch)
    assert payload["part_number"] == "P"
    assert "weight" not in payload


def test_a_new_type_gets_a_two_decimal_weight(monkeypatch):
    assert _create_type(5.1, monkeypatch)["weight"] == Decimal("5.10")
