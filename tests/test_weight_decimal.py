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
