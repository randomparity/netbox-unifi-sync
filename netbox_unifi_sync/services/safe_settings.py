"""Refuse to sync with a setting that deletes or overwrites what people typed, or floods the log."""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from ._validation import SyncConfigurationError

MUST_BE_OFF = (
    "schedule_enabled",
    "sync_stale_cleanup",
    "cleanup_enabled",
    "sync_client_ips",
    "dhcp_auto_discover",
    "sync_dhcp_ranges",
    "dhcp_writeback_enabled",
    "sync_device_status",
    "asset_tag_enabled",
    "specs_auto_refresh",
)
THREADS = ("max_controller_threads", "max_site_threads", "max_device_threads")

# Unset means the engine's default, which is the safe value for each of these.
ENV_OFF = (
    "SYNC_STALE_CLEANUP",
    "NETBOX_CLEANUP",
    "SYNC_CLIENT_IPS",
    "DHCP_AUTO_DISCOVER",
    "SYNC_DHCP_RANGES",
    "DHCP_WRITEBACK_ENABLED",
    "SYNC_DEVICE_STATUS",
    "UNIFI_ASSET_TAG_ENABLED",
    "UNIFI_SPECS_AUTO_REFRESH",
    "SYNC_OFFLINE_CABLE_DELETE",
    "SYNC_INTERFACE_DELETE",
    "SYNC_LIVE_READINGS",
)
ENV_ON = ("SYNC_KEEP_EXISTING", "SYNC_CABLES_STRICT")


class UnsafeSettingsError(SyncConfigurationError):
    """Raised when a setting would let a sync do what it must not."""


def _refuse(wrong: list[str]) -> None:
    if wrong:
        raise UnsafeSettingsError(f"Sync refused: {', '.join(wrong)}.")


def require_safe_settings(settings: Any, *, cleanup_requested: bool) -> None:
    """Check the saved plugin settings."""
    wrong = [f"{name} must be off" for name in MUST_BE_OFF if getattr(settings, name)]
    wrong += [f"{name} must be 1" for name in THREADS if getattr(settings, name) != 1]
    if cleanup_requested:
        wrong.append("cleanup was requested and must be off")
    _refuse(wrong)


def require_safe_environment(threads: Mapping[str, int]) -> None:
    """Check what the engine reads, and the thread counts it read when it was imported."""
    wrong = []
    for names, allowed, word in (
        (ENV_OFF, {"", "0", "false", "no", "off"}, "off"),
        (ENV_ON, {"", "1", "true", "yes", "on"}, "on"),
    ):
        wrong += [
            f"{name} must be {word}"
            for name in names
            if os.environ.get(name, "").strip().lower() not in allowed
        ]
    wrong += [f"{name} must be 1" for name, count in threads.items() if count != 1]
    _refuse(wrong)
