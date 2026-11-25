from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("netbox_unifi_sync", "0010_safe_defaults")]

    operations = [
        migrations.AddField(
            model_name="unificontroller",
            name="cert_sha256",
            field=models.CharField(
                blank=True,
                default="",
                help_text="SHA-256 fingerprint of the console's certificate. Required to connect.",
                max_length=95,
            ),
        ),
    ]
