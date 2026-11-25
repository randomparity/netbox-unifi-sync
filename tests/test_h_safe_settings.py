"""Unset, every setting is the safe one; set otherwise, a sync is refused."""
import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.configuration import (
    DEFAULT_SETTINGS,
    normalize_plugin_settings,
    plugin_settings_to_env,
)
from netbox_unifi_sync.services import branch_guard, safe_settings, sync_engine, sync_service
from netbox_unifi_sync.services.safe_settings import UnsafeSettingsError
from tests.test_review_regressions import _load_function
from tests.test_threading_env import _import_sync_engine_with_env

PACKAGE = Path(__file__).resolve().parents[1] / "netbox_unifi_sync"
TEN = (
    "schedule_enabled", "sync_stale_cleanup", "cleanup_enabled", "sync_client_ips",
    "dhcp_auto_discover", "sync_dhcp_ranges", "dhcp_writeback_enabled", "sync_device_status",
    "asset_tag_enabled", "specs_auto_refresh",
)
THREADS = ("max_controller_threads", "max_site_threads", "max_device_threads")
ENV_OFF = (
    "SYNC_STALE_CLEANUP", "NETBOX_CLEANUP", "SYNC_CLIENT_IPS", "DHCP_AUTO_DISCOVER",
    "SYNC_DHCP_RANGES", "DHCP_WRITEBACK_ENABLED", "SYNC_DEVICE_STATUS", "UNIFI_ASSET_TAG_ENABLED",
    "UNIFI_SPECS_AUTO_REFRESH", "SYNC_OFFLINE_CABLE_DELETE", "SYNC_INTERFACE_DELETE",
    "SYNC_LIVE_READINGS",
)
ENV_ON = ("SYNC_KEEP_EXISTING", "SYNC_CABLES_STRICT")
# Plugin settings have other names for some, and no schedule.
CONFIG_NAMES = {
    "cleanup_enabled": "netbox_cleanup", "specs_auto_refresh": "unifi_specs_auto_refresh",
}


def _model_defaults():
    tree = ast.parse((PACKAGE / "models.py").read_text())
    model = next(n for n in tree.body if getattr(n, "name", "") == "GlobalSyncSettings")
    return {
        node.targets[0].id: ast.literal_eval(keyword.value)
        for node in model.body if isinstance(node, ast.Assign)
        for keyword in getattr(node.value, "keywords", ())
        if keyword.arg == "default" and isinstance(keyword.value, ast.Constant)
    }


@pytest.mark.parametrize("name", TEN)
def test_model_default_is_off(name):
    assert _model_defaults()[name] is False


@pytest.mark.parametrize("name", THREADS)
def test_model_default_is_one_thread(name):
    assert _model_defaults()[name] == 1


@pytest.mark.parametrize("name", (*TEN[1:], *THREADS))
def test_configuration_default(name):
    value = DEFAULT_SETTINGS.get(CONFIG_NAMES.get(name, name), False)
    assert value == 1 if name in THREADS else value is False


@pytest.fixture
def unset(monkeypatch):
    for name in (*ENV_OFF, *ENV_ON):
        monkeypatch.delenv(name, raising=False)


ONE = {"MAX_DEVICE_THREADS": 1}


def test_nothing_set_is_safe_and_so_are_the_defaults(unset, monkeypatch):
    safe_settings.require_safe_environment(ONE)
    settings = normalize_plugin_settings({}, include_defaults=True)
    for name, value in plugin_settings_to_env(settings).items():
        monkeypatch.setenv(name, value)
    safe_settings.require_safe_environment(ONE)


def test_engine_threads_default_to_one():
    result = _import_sync_engine_with_env({})
    assert result.stdout.strip().endswith("1 1 1")


@pytest.mark.parametrize("name", ENV_OFF)
@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "maybe"])
def test_refuses_environment_on(unset, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(UnsafeSettingsError, match=f"{name} must be off"):
        safe_settings.require_safe_environment(ONE)


@pytest.mark.parametrize("name", ENV_ON)
@pytest.mark.parametrize("value", ["false", "0", "no", "off", "maybe"])
def test_refuses_environment_off(unset, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(UnsafeSettingsError, match=f"{name} must be on"):
        safe_settings.require_safe_environment(ONE)


@pytest.mark.parametrize("threads", [0, 2, 8])
def test_refuses_threads(unset, threads):
    with pytest.raises(UnsafeSettingsError, match="MAX_DEVICE_THREADS must be 1"):
        safe_settings.require_safe_environment({"MAX_DEVICE_THREADS": threads})


def test_unset_every_switch_the_engine_reads_is_the_safe_one(unset):
    """Read the defaults out of the engine, so a switch read in a second place is covered too."""
    text = (PACKAGE / "services" / "sync_engine.py").read_text()
    found = {}
    for pattern in (
        r'_sync_option\("(\w+)"(?:, default=(True|False))?\)',
        r'_parse_env_bool\(os\.getenv\("(\w+)"\), default=(True|False)\)',
        r'os\.getenv\("(\w+)", "(true|false)"\)',
    ):
        for name, default in re.findall(pattern, text):
            found.setdefault(name, set()).add((default or "true").lower())
    for name in ENV_OFF:
        assert found[name] == {"false"}, name
    for name in ENV_ON:
        assert found[name] == {"true"}, name
    # A read in another form would escape the patterns above.
    for name in (*ENV_OFF, *ENV_ON):
        reads = len(re.findall(f'"{name}"', text))
        matched = sum(
            len(re.findall(p.replace(r"(\w+)", f"({name})"), text)) for p in (
                r'_sync_option\("(\w+)"', r'_parse_env_bool\(os\.getenv\("(\w+)"\), default',
                r'os\.getenv\("(\w+)", "(?:true|false)"',
            )
        )
        assert reads == matched, name


def _saved(**changes):
    return SimpleNamespace(
        **{**dict.fromkeys(TEN, False), **dict.fromkeys(THREADS, 1), **changes}
    )


def test_accepts_safe_saved_settings():
    safe_settings.require_safe_settings(_saved(), cleanup_requested=False)


@pytest.mark.parametrize("name", TEN)
def test_refuses_saved_setting(name):
    with pytest.raises(UnsafeSettingsError, match=f"{name} must be off"):
        safe_settings.require_safe_settings(_saved(**{name: True}), cleanup_requested=False)


@pytest.mark.parametrize("name", THREADS)
def test_refuses_saved_threads(name):
    with pytest.raises(UnsafeSettingsError, match=f"{name} must be 1"):
        safe_settings.require_safe_settings(_saved(**{name: 8}), cleanup_requested=False)


def test_refuses_requested_cleanup():
    with pytest.raises(UnsafeSettingsError, match="cleanup was requested"):
        safe_settings.require_safe_settings(_saved(), cleanup_requested=True)


def test_engine_refuses_before_it_reads_configuration(unset, monkeypatch):
    monkeypatch.setattr(branch_guard, "require_active_branch", lambda: None)
    monkeypatch.setenv("SYNC_INTERFACE_DELETE", "true")
    load = Mock()
    monkeypatch.setattr(sync_engine, "_load_runtime_or_exit", load)
    with pytest.raises(UnsafeSettingsError):
        sync_engine.run_sync_once()
    load.assert_not_called()


@pytest.mark.parametrize("name", ["MAX_CONTROLLER_THREADS", "MAX_SITE_THREADS",
                                  "MAX_DEVICE_THREADS"])
def test_engine_refuses_its_own_thread_counts(unset, monkeypatch, name):
    monkeypatch.setattr(branch_guard, "require_active_branch", lambda: None)
    monkeypatch.setattr(sync_engine, name, 2)
    monkeypatch.setattr(sync_engine, "_load_runtime_or_exit", Mock(side_effect=AssertionError))
    with pytest.raises(UnsafeSettingsError, match=f"{name} must be 1"):
        sync_engine.run_sync_once()


def test_service_passes_the_settings_to_the_engine_check(unset, monkeypatch):
    monkeypatch.setattr(branch_guard, "require_active_branch", lambda: None)
    monkeypatch.setattr(sync_engine, "_load_runtime_or_exit", Mock(side_effect=AssertionError))
    with pytest.raises(UnsafeSettingsError, match="SYNC_CLIENT_IPS must be off"):
        sync_service.execute_sync(config_overrides={
            "unifi_url": "https://unifi.local", "api_key": "k", "netbox_import_tenant": "T",
            "netbox_roles": {"LAN": "Switch"}, "sync_client_ips": True,
        })


@pytest.mark.parametrize("dry_run", [True, False])
def test_orchestrator_refuses_before_it_reads_controllers(dry_run):
    from typing import Any, Iterable

    controllers = Mock()
    run = _load_function("services/orchestrator.py", "run_sync", {
        "Any": Any, "Iterable": Iterable, "branch_guard": Mock(), "safe_settings": safe_settings,
        "get_or_create_global_settings": lambda: _saved(enabled=True, sync_client_ips=True),
        "get_enabled_controllers": controllers,
    })
    with pytest.raises(UnsafeSettingsError):
        run(dry_run=dry_run, cleanup_requested=False)
    controllers.assert_not_called()
