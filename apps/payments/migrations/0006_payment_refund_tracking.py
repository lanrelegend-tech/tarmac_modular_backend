from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0005_payment_payer_email_alter_payment_user")]
    operations = [
        migrations.AddField(model_name="payment", name="refund_status", field=models.CharField(blank=True, choices=[("pending", "Pending"), ("processing", "Processing"), ("needs_attention", "Needs attention"), ("failed", "Failed"), ("processed", "Processed")], max_length=20)),
        migrations.AddField(model_name="payment", name="refund_reference", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="payment", name="refund_response", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="payment", name="refund_requested_at", field=models.DateTimeField(blank=True, null=True)),
    ]
