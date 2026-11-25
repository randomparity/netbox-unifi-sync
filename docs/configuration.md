# Configuration Reference

`netbox_unifi_sync` is configured in NetBox UI first.  
`PLUGINS_CONFIG` is optional bootstrap/default input.

## Configuration Layers

1. Plugin UI models (authoritative runtime state):
   - `Settings` (`GlobalSyncSettings`)
   - `Controllers` (`UnifiController`)
   - `Site mappings` (`SiteMapping`)
2. Optional bootstrap defaults from `PLUGINS_CONFIG["netbox_unifi_sync"]`
3. Internal compatibility mapping into legacy engine keys (handled by plugin services)

Credential policy:
- UniFi credentials are read from `Controllers` UI fields only.
- `PLUGINS_CONFIG` should not be used to store UniFi credentials.

## Minimum Required Setup (UI)

Before first sync, set:

- `Settings`:
  - `tenant_name` (required)
  - `netbox_roles` JSON mapping (required)
- `Controllers`:
  - at least one enabled controller
  - `auth_mode` and matching credentials
- `Site mappings`:
  - required when UniFi site names differ from NetBox site names

## Optional `PLUGINS_CONFIG` Bootstrap

You can keep this minimal:

```python
PLUGINS = ["netbox_unifi_sync"]

PLUGINS_CONFIG = {
    "netbox_unifi_sync": {}
}
```

You can also preseed defaults:

```python
PLUGINS_CONFIG = {
    "netbox_unifi_sync": {
        "verify_ssl": True,
        "default_site": "",
        "dry_run": False,
    }
}
```

## Controller Auth Modes

### `api_key` (recommended)

- Uses Integration API v1
- Requires `api_key_ref` on the controller row
- Optional custom header `api_key_header` (default `X-API-KEY`)

### `login` (legacy fallback)

- Uses username/password session login
- Requires `username_ref` + `password_ref` on the controller row
- Optional `mfa_secret_ref`

Note: local Integration API keys are required for Integration API mode.  
`unifi.ui.com` cloud API keys are not drop-in compatible.

## Key Runtime Settings (UI)

### Sync scope and behavior

- `enabled`
- `sync_devices`
- `sync_interfaces`
- `sync_radio_interfaces`
- `sync_gateway_interfaces`
- `sync_primary_ips`
- `sync_device_status`
- `sync_device_custom_fields`
- `sync_vlans`
- `sync_wlans`
- `sync_cables`
- `sync_stale_cleanup`
- `sync_client_ips`
- `cleanup_enabled`
- `cleanup_grace_days`
- `dry_run_default`

`sync_client_ips` creates/updates NetBox `IPAddress` objects for online UniFi
clients. Synced IPs are tagged `unifi-client`, include a stable
`unifi-client:<MAC>` marker in the description for cleanup, and are assigned to a
matching NetBox `dcim.Interface` or `virtualization.VMInterface` when the client
MAC address exists on that interface. Integration API field names such as
`macAddress`, `ipAddress`, `connectedAt`, and `lastSeenAt` are supported.

### IPAM and DHCP

- `dhcp_auto_discover`
- `dhcp_ranges` (one CIDR per line in UI)
- `sync_dhcp_ranges`
- `dhcp_writeback_enabled`
- `default_gateway` (fallback for DHCP-to-static flow)
- `default_dns` (comma-separated fallback DNS list)
- `netbox_device_status`
- `sync_prefixes`
- Prefix sync is enabled by default (`sync_prefixes = true`)
- DHCP scopes are created as NetBox IP Ranges when `sync_dhcp_ranges = true` (default)

`dhcp_writeback_enabled` gates the DHCP-to-static writeback path. When it is
disabled, the plugin can still discover DHCP ranges and sync IPAM data, but it
will not push a static IP change back to UniFi.

With `sync_prefixes=false`, neither network-config sync nor inferred device
subnets create prefixes. Existing parent prefixes are still used for primary-IP
masks; an address without a parent uses `/32` for IPv4 or `/128` for IPv6.
The toggle does not delete prefixes already present. `sync_dhcp_ranges` is a
separate control. Prefix creation is serialized across threads in one process;
avoid overlapping independent worker processes.

### Identity and mapping

- `tenant_name`
- `default_site`
- `tag_strategy`
- `default_tags`
- `asset_tag_enabled`
- `asset_tag_patterns`
- `asset_tag_uppercase`
- `netbox_roles` JSON mapping

Role keys are `WIRELESS`, `LAN`, `GATEWAY`, `ROUTER` and `UNKNOWN`. Legacy
aliases such as `SWITCH` map to canonical keys; explicit canonical mappings win.
Controller-specific site mappings override global rows. Controllers run in the
same execution only when credentials and effective mappings match; otherwise
separate executions preserve each controller's mapping.

### VRF and serial strategy

- `vrf_mode`: `none` | `existing` | `create`
- `default_vrf_name`

| `serial_mode` | Value written to NetBox's serial field |
| --- | --- |
| `mac` (default) | UniFi serial, otherwise MAC, otherwise UniFi ID |
| `unifi` | UniFi serial only |
| `id` | UniFi serial, otherwise UniFi ID |
| `none` | No identifier; device processing is skipped because hardware matching requires one |

Devices match by site and resolved serial. A different device colliding on its
name receives a deterministic `_<serial>` suffix on creation or rename. Names
are bounded to 64 characters; unusually long identifiers use a stable hash
suffix. Missing or ambiguous identifiers produce a warning and skip that device.
Changing serial strategy can change identity matching; validate it in staging.

### Reliability and concurrency

- `verify_ssl_default`
- `request_timeout`
- `http_retries`
- `retry_backoff_base`
- `retry_backoff_max`
- `max_controller_threads`
- `max_site_threads`
- `max_device_threads`
- `rate_limit_per_second`

Runtime environment overrides are serialized within one process so concurrent
runs cannot replace each other's credentials. This does not coordinate separate
RQ processes; schedule jobs to avoid overlap. Cleanup is skipped when credentials
or effective site mappings require more than one execution group.

Stale `running` sync runs older than the runtime reconciliation threshold are
marked failed before new status data is shown. This prevents interrupted worker
processes from leaving the dashboard permanently stuck in a running state.

### Branch required

Every sync, dry runs included, is refused unless a netbox-branching branch is active and ready, its
schema exists, and inventory reads and writes go to it. Background jobs and the schedule have no
branch, so the dashboard queues none, and a job or schedule that runs is refused. Start a sync from
code that activates the branch.

A run holds its branch until it ends. Deleting, archiving, merging, reverting, syncing or
migrating the branch meanwhile is refused at once with "A UniFi sync is running in branch ...",
and a second run of it, or a run during one of those actions, is refused too. Every statement of
the run locks a table of the branch's schema in its own transaction, so a dropped schema stops the
statement instead of letting it reach main, and fails the run. A single table of the branch dropped
by hand is not locked: statements on it reach main's table until the run ends, and the run then
fails because the table is gone. A killed run's locks end with its connections. The engine's
inventory adapter refuses all access outside a run.

### Safe settings

A sync, dry runs included, is refused with any of these on: `schedule_enabled`,
`sync_stale_cleanup`, `cleanup_enabled` (or a requested cleanup), `sync_client_ips`,
`dhcp_auto_discover`, `sync_dhcp_ranges`, `dhcp_writeback_enabled`, `sync_device_status`,
`asset_tag_enabled`, `specs_auto_refresh`. All are off by default. The three thread counts must
be 1, the default; the engine reads `MAX_*_THREADS` when it is imported. Settings saved by an
earlier version keep their values, so turn them off.

The engine also refuses `SYNC_KEEP_EXISTING` or `SYNC_CABLES_STRICT` off, and
`SYNC_OFFLINE_CABLE_DELETE`, `SYNC_INTERFACE_DELETE` or `SYNC_LIVE_READINGS` on. Unset, each has
its safe value.

With `SYNC_KEEP_EXISTING`, the name and status of a VLAN that exists are kept, also when UniFi
disables its network.

### Console certificate

Each controller has `cert_sha256`, the SHA-256 fingerprint of its console's certificate, as 64 hex
digits with or without colons. Nothing connects without one: not a sync, a connection test or the
site list of the mapping form. A console that shows any other certificate is refused before a
request is sent, on every connection, whether for a redirect, a retry, a login or the legacy API.
Plain HTTP and proxies are refused, and proxies, `.netrc` and CA bundles of the environment are
not used. `verify_ssl` is checked as well, so it must be off for a self-signed certificate.

When the certificate is renewed, everything is refused until `cert_sha256` is changed. Check the
new fingerprint at the console itself, not from the refusal. A branch has its own copy of the
controller: one made before the change keeps the old pin, so make the branch afterwards.

### Scheduling

- `schedule_enabled`
- `sync_interval_minutes`

Scheduler behavior:
- NetBox system job runs every 60 seconds and checks if a sync is due.
- Effective minimum interval is 60 seconds (`sync_interval_minutes` values below 1 minute are treated as 1 minute in scheduler logic).
- Scheduled runs use `dry_run_default` and `cleanup_enabled`.

### Specs refresh (optional)

- `specs_auto_refresh`
- `specs_include_store`
- `specs_refresh_timeout`
- `specs_store_timeout`
- `specs_store_max_workers`
- `specs_write_cache`

## Advanced: Engine Key Reference

The plugin maps UI state into these internal engine keys (for compatibility/debugging):

| Internal key | Source in plugin UI |
|---|---|
| `UNIFI_URLS` | Enabled controller URLs (`base_url`) |
| `UNIFI_API_KEY` | `api_key_ref` |
| `UNIFI_API_KEY_HEADER` | `api_key_header` |
| `UNIFI_USERNAME` | `username_ref` |
| `UNIFI_PASSWORD` | `password_ref` |
| `UNIFI_MFA_SECRET` | `mfa_secret_ref` |
| `UNIFI_VERIFY_SSL` | controller `verify_ssl` or global default |
| `UNIFI_REQUEST_TIMEOUT` | `request_timeout` |
| `UNIFI_HTTP_RETRIES` | `http_retries` |
| `UNIFI_RETRY_BACKOFF_BASE` | `retry_backoff_base` |
| `UNIFI_RETRY_BACKOFF_MAX` | `retry_backoff_max` |
| `NETBOX_IMPORT_TENANT` | `tenant_name` |
| `NETBOX_DEFAULT_VRF` | `default_vrf_name` |
| `NETBOX_VRF_MODE` | `vrf_mode` |
| `NETBOX_SERIAL_MODE` | `serial_mode` |
| `UNIFI_SITE_MAPPINGS` | `Site mappings` model rows |
| `UNIFI_TAG_STRATEGY` | `tag_strategy` |
| `SYNC_DEVICES` | `sync_devices` |
| `SYNC_INTERFACES` | `sync_interfaces` |
| `SYNC_RADIO_INTERFACES` | `sync_radio_interfaces` |
| `SYNC_GATEWAY_INTERFACES` | `sync_gateway_interfaces` |
| `SYNC_PRIMARY_IPS` | `sync_primary_ips` |
| `SYNC_DEVICE_STATUS` | `sync_device_status` |
| `SYNC_DEVICE_CUSTOM_FIELDS` | `sync_device_custom_fields` |
| `SYNC_VLANS` | `sync_vlans` |
| `SYNC_WLANS` | `sync_wlans` |
| `SYNC_CABLES` | `sync_cables` |
| `SYNC_STALE_CLEANUP` | `sync_stale_cleanup` |
| `SYNC_CLIENT_IPS` | `sync_client_ips` |
| `SYNC_PREFIXES` | `sync_prefixes` (default `true`) |
| `SYNC_DHCP_RANGES` | `sync_dhcp_ranges` (default `false`) |
| `DHCP_AUTO_DISCOVER` | `dhcp_auto_discover` |
| `DHCP_RANGES` | `dhcp_ranges` |
| `DHCP_WRITEBACK_ENABLED` | `dhcp_writeback_enabled` |
| `DEFAULT_GATEWAY` | `default_gateway` |
| `DEFAULT_DNS` | `default_dns` |
| `NETBOX_DEVICE_STATUS` | `netbox_device_status` |
| `NETBOX_CLEANUP` | `cleanup_enabled` |
| `CLEANUP_STALE_DAYS` | `cleanup_grace_days` |

## Advanced: Optional Bootstrap Keys

These are valid in `PLUGINS_CONFIG["netbox_unifi_sync"]` when you need preseed defaults:

- `unifi_url` or `unifi_urls`
- `enabled`
- `verify_ssl`
- `default_site`
- `dry_run`
- `netbox_import_tenant`, `netbox_roles`
- `default_vrf_name`, `vrf_mode`, `serial_mode`
- `unifi_site_mappings`
- `tag_strategy`, `default_tags`
- `asset_tag_enabled`, `asset_tag_patterns`, `asset_tag_uppercase`
- `sync_devices`, `sync_interfaces`, `sync_radio_interfaces`, `sync_gateway_interfaces`, `sync_primary_ips`
- `sync_device_status`, `sync_device_custom_fields`, `sync_vlans`, `sync_wlans`, `sync_cables`
- `sync_stale_cleanup`, `sync_client_ips`
- `dhcp_auto_discover`, `dhcp_ranges`, `sync_dhcp_ranges`, `default_gateway`, `default_dns`
- `netbox_device_status`, `sync_prefixes`
- `cleanup_enabled`, `cleanup_grace_days`
- `schedule_enabled`, `sync_interval_minutes`
- `request_timeout`, `http_retries`, `retry_backoff_base`, `retry_backoff_max`
- `max_controller_threads`, `max_site_threads`, `max_device_threads`
- `specs_auto_refresh`, `specs_include_store`, `specs_refresh_timeout`, `specs_store_timeout`, `specs_store_max_workers`, `specs_write_cache`
- `rate_limit_per_second`

## Secret Handling

Credentials are set in `Controllers` UI fields (`api_key_ref`, `username_ref`, `password_ref`, `mfa_secret_ref`).
Supported formats in those controller fields:

- `env:VAR_NAME`
- `file:/absolute/path/to/secret`
- direct pasted credential value

## Change Log

NetBox Change Log is enabled for these plugin models:

- `GlobalSyncSettings`
- `UnifiController`
- `SiteMapping`

Use the object's **Change Log** tab/link from the plugin UI to audit settings,
controller, and mapping changes. Sync run records and audit events remain runtime
history and are viewed from the plugin dashboard/run detail pages.

## Advanced Compatibility Notes

- The sync engine still consumes environment-style keys internally.
- Plugin services map UI state into those keys at runtime.
- You normally do not need to set `NETBOX_URL`/`NETBOX_TOKEN` manually in plugin mode.
- Legacy CLI/standalone configuration is not required for normal plugin operation.

## Advanced: live readings

`SYNC_LIVE_READINGS` is a process environment option, default `false`, which omits
frequently changing uptime/last-seen, PoE draw, radio utilization and client
AP/signal/timestamp metadata. A sync is refused with it on. Stable
identity, firmware, MAC, IP and SSID information continues to sync. Existing
custom-field values can remain unchanged; description fields may be rewritten
without live readings. See [device specs](device-specs.md).

MFA seeds and API/password/token fields are redacted from configuration snapshots.
Restrict controller edit permissions, database backups and secret-file access;
redaction is not encryption of stored controller values.
