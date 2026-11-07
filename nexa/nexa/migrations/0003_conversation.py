import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nexa", "0002_integration_credentials"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Conversation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=120, verbose_name="عنوان")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="ایجاد")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="آخرین پیام")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL, verbose_name="کاربر")),
            ],
            options={
                "verbose_name": "گفتگو",
                "verbose_name_plural": "گفتگوها",
                "indexes": [models.Index(fields=["user", "-updated_at"], name="conversation_recent")],
            },
        ),
        migrations.AddField(
            model_name="audit",
            name="conversation",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="turns", to="nexa.conversation", verbose_name="گفتگو"),
        ),
        migrations.AddField(
            model_name="audit",
            name="sources",
            field=models.JSONField(blank=True, default=list, verbose_name="منابع"),
        ),
    ]
