from django.db import migrations, models


def _change(name, field):
    return migrations.AlterField(model_name="globalsyncsettings", name=name, field=field)


class Migration(migrations.Migration):
    """New defaults only: saved settings keep their values."""

    dependencies = [("netbox_unifi_sync", "0009_unique_global_site_mapping")]

    operations = [
        _change("asset_tag_enabled", models.BooleanField(default=False)),
        _change("sync_stale_cleanup", models.BooleanField(default=False)),
        _change("dhcp_auto_discover", models.BooleanField(default=False)),
        _change(
            "sync_dhcp_ranges",
            models.BooleanField(default=False, help_text="Sync DHCP IP ranges to NetBox IPAM."),
        ),
        _change("max_controller_threads", models.PositiveIntegerField(default=1)),
        _change("max_site_threads", models.PositiveIntegerField(default=1)),
        _change("max_device_threads", models.PositiveIntegerField(default=1)),
    ]
