from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0037_growth_announcements_placement"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnnouncementImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image", models.ImageField(upload_to="announcements/gallery/")),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("announcement", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="gallery_images", to="administration.announcement")),
            ],
            options={"ordering": ("sort_order", "pk")},
        ),
    ]
