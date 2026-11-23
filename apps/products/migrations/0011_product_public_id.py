import uuid
from django.db import migrations, models


def populate_public_ids(apps, schema_editor):
    Model = apps.get_model("products", "Product")
    batch = []
    for row in Model.objects.filter(public_id__isnull=True).only("pk").iterator(chunk_size=1000):
        row.public_id = uuid.uuid4()
        batch.append(row)
        if len(batch) == 1000:
            Model.objects.bulk_update(batch, ["public_id"], batch_size=1000)
            batch.clear()
    if batch:
        Model.objects.bulk_update(batch, ["public_id"], batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [("products", "0010_product_area")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="public_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(populate_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="product",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, db_index=True, editable=False, unique=True),
        ),
    ]
