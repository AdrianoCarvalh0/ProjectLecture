from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reader", "0002_neural_voices"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="stream_is_building",
            field=models.BooleanField(default=False, verbose_name="preparando blocos"),
        ),
        migrations.AddField(
            model_name="audiosegment",
            name="audio_file",
            field=models.FileField(blank=True, upload_to="audio/chunks/%Y/%m/"),
        ),
        migrations.AddField(
            model_name="audiosegment",
            name="duration_seconds",
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name="audiosegment",
            name="spoken_text",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="audiosegment",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Na fila"),
                    ("processing", "Sintetizando"),
                    ("ready", "Pronto"),
                    ("failed", "Falhou"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="audiosegment",
            name="word_timings",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
