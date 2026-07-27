from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reader", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="voice",
            name="avatar",
            field=models.CharField(
                blank=True,
                help_text="Caminho relativo em static.",
                max_length=160,
                verbose_name="avatar",
            ),
        ),
        migrations.AddField(
            model_name="voice",
            name="is_default",
            field=models.BooleanField(default=False, verbose_name="padrão"),
        ),
        migrations.AddField(
            model_name="voice",
            name="provider",
            field=models.CharField(
                choices=[
                    ("espeak", "Local básica"),
                    ("kokoro", "Neural Kokoro"),
                    ("chatterbox", "Neural Chatterbox"),
                ],
                default="espeak",
                max_length=20,
                verbose_name="provedor",
            ),
        ),
        migrations.AddField(
            model_name="voice",
            name="quality_label",
            field=models.CharField(
                default="Local", max_length=40, verbose_name="qualidade"
            ),
        ),
        migrations.AddField(
            model_name="voice",
            name="style_label",
            field=models.CharField(blank=True, max_length=80, verbose_name="estilo"),
        ),
        migrations.AddField(
            model_name="document",
            name="reading_mode",
            field=models.CharField(
                choices=[
                    ("academic", "Leitura acadêmica"),
                    ("natural", "Leitura natural"),
                    ("literal", "Leitura literal"),
                ],
                default="academic",
                max_length=20,
                verbose_name="modo de leitura",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="synthesis_provider",
            field=models.CharField(
                blank=True, max_length=20, verbose_name="provedor utilizado"
            ),
        ),
    ]
