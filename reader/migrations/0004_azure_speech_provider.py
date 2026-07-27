from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reader", "0003_progressive_streaming"),
    ]

    operations = [
        migrations.AlterField(
            model_name="voice",
            name="provider",
            field=models.CharField(
                choices=[
                    ("espeak", "Local básica"),
                    ("kokoro", "Neural Kokoro"),
                    ("chatterbox", "Neural Chatterbox"),
                    ("azure", "Azure Speech"),
                ],
                default="espeak",
                max_length=20,
                verbose_name="provedor",
            ),
        ),
    ]
