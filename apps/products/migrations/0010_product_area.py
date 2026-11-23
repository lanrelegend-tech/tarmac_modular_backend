from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0009_product_campus"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="area",
            field=models.CharField(blank=True, db_index=True, max_length=120),
        ),
    ]
