from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse


class Voice(models.Model):
    class Provider(models.TextChoices):
        ESPEAK = "espeak", "Local básica"
        KOKORO = "kokoro", "Neural Kokoro"
        CHATTERBOX = "chatterbox", "Neural Chatterbox"

    name = models.CharField("nome", max_length=80)
    code = models.CharField("código", max_length=40, unique=True)
    language = models.CharField("idioma", max_length=20, default="pt-BR")
    description = models.CharField("descrição", max_length=160, blank=True)
    provider = models.CharField(
        "provedor", max_length=20, choices=Provider.choices, default=Provider.ESPEAK
    )
    avatar = models.CharField(
        "avatar", max_length=160, blank=True, help_text="Caminho relativo em static."
    )
    style_label = models.CharField("estilo", max_length=80, blank=True)
    quality_label = models.CharField(
        "qualidade", max_length=40, default="Local"
    )
    is_default = models.BooleanField("padrão", default=False)
    is_active = models.BooleanField("ativa", default=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "voz"
        verbose_name_plural = "vozes"

    def __str__(self):
        return self.name


class Document(models.Model):
    class SourceType(models.TextChoices):
        TEXT = "text", "Texto colado"
        PDF = "pdf", "PDF"
        DOCX = "docx", "Word"
        EPUB = "epub", "EPUB"
        TXT = "txt", "TXT"

    class Status(models.TextChoices):
        PENDING = "pending", "Na fila"
        PROCESSING = "processing", "Gerando áudio"
        READY = "ready", "Pronto"
        FAILED = "failed", "Falhou"

    class ReadingMode(models.TextChoices):
        ACADEMIC = "academic", "Leitura acadêmica"
        NATURAL = "natural", "Leitura natural"
        LITERAL = "literal", "Leitura literal"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="proprietário",
    )
    title = models.CharField("título", max_length=180)
    source_type = models.CharField(
        "origem", max_length=10, choices=SourceType.choices, default=SourceType.TEXT
    )
    original_file = models.FileField(
        "arquivo original", upload_to="documents/%Y/%m/", blank=True
    )
    extracted_text = models.TextField("texto extraído")
    voice = models.ForeignKey(
        Voice, on_delete=models.PROTECT, related_name="documents", verbose_name="voz"
    )
    speed = models.PositiveSmallIntegerField(
        "velocidade",
        default=170,
        validators=[MinValueValidator(80), MaxValueValidator(320)],
        help_text="Palavras por minuto.",
    )
    reading_mode = models.CharField(
        "modo de leitura",
        max_length=20,
        choices=ReadingMode.choices,
        default=ReadingMode.ACADEMIC,
    )
    synthesis_provider = models.CharField(
        "provedor utilizado", max_length=20, blank=True
    )
    stream_is_building = models.BooleanField(
        "preparando blocos", default=False
    )
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.PENDING
    )
    audio_file = models.FileField(
        "áudio", upload_to="audio/%Y/%m/", blank=True
    )
    char_count = models.PositiveIntegerField("caracteres", default=0)
    duration_seconds = models.PositiveIntegerField("duração em segundos", default=0)
    error_message = models.TextField("erro", blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)
    completed_at = models.DateTimeField("concluído em", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        indexes = [
            models.Index(fields=("owner", "status")),
            models.Index(fields=("owner", "-created_at")),
        ]

    def save(self, *args, **kwargs):
        self.char_count = len(self.extracted_text or "")
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("reader:document-detail", kwargs={"pk": self.pk})

    @property
    def progress_percent(self):
        progress = getattr(self, "reading_progress", None)
        if not progress or not self.char_count:
            return 0
        return min(100, round(progress.char_offset * 100 / self.char_count))

    def __str__(self):
        return self.title


class AudioSegment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Na fila"
        PROCESSING = "processing", "Sintetizando"
        READY = "ready", "Pronto"
        FAILED = "failed", "Falhou"

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="segments"
    )
    order = models.PositiveIntegerField()
    text = models.TextField()
    spoken_text = models.TextField(blank=True)
    audio_file = models.FileField(
        upload_to="audio/chunks/%Y/%m/", blank=True
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    duration_seconds = models.FloatField(default=0)
    word_timings = models.JSONField(default=list, blank=True)
    start_char = models.PositiveIntegerField(default=0)
    end_char = models.PositiveIntegerField(default=0)
    start_seconds = models.FloatField(default=0)
    end_seconds = models.FloatField(default=0)

    class Meta:
        ordering = ("order",)
        constraints = [
            models.UniqueConstraint(
                fields=("document", "order"), name="unique_document_segment_order"
            )
        ]

    def __str__(self):
        return f"{self.document} — trecho {self.order + 1}"


class ReadingProgress(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reading_progress",
    )
    document = models.OneToOneField(
        Document,
        on_delete=models.CASCADE,
        related_name="reading_progress",
    )
    position_seconds = models.FloatField(default=0)
    char_offset = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        verbose_name = "progresso de leitura"
        verbose_name_plural = "progressos de leitura"

    def __str__(self):
        return f"{self.user} — {self.document}"
