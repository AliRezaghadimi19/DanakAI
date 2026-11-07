from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("nexa", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="model",
            name="api_key_ciphertext",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="organization",
            name="ldap_password_ciphertext",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="organization",
            name="ldap_ca_pem",
            field=models.TextField(blank=True, verbose_name="گواهی CA برای LDAPS (PEM)"),
        ),
    ]
