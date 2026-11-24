from django.db import migrations, models


def map_conditions_forward(apps, schema_editor):
    Product = apps.get_model("products", "Product")
    database = schema_editor.connection.alias
    for old_value, new_value in (
        ("like_new", "fairly_used"),
        ("very_good", "fairly_used"),
        ("good", "used"),
        ("fair", "old"),
    ):
        Product.objects.using(database).filter(condition=old_value).update(condition=new_value)


def map_conditions_backward(apps, schema_editor):
    Product = apps.get_model("products", "Product")
    database = schema_editor.connection.alias
    for new_value, old_value in (
        ("brand_new", "like_new"),
        ("fairly_used", "very_good"),
        ("used", "good"),
        ("old", "fair"),
    ):
        Product.objects.using(database).filter(condition=new_value).update(condition=old_value)


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0012_alter_category_options_alter_favourite_options_and_more"),
    ]

    operations = [
        migrations.RunPython(map_conditions_forward, map_conditions_backward),
        migrations.AlterField(
            model_name="product",
            name="condition",
            field=models.CharField(
                choices=[
                    ("brand_new", "Brand new"),
                    ("fairly_used", "Fairly used"),
                    ("used", "Used"),
                    ("old", "Old"),
                ],
                max_length=20,
            ),
        ),
    ]
