import base64
import hashlib

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from cryptography.fernet import Fernet, InvalidToken


def _secret_cipher():
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class AppConfiguration(models.Model):
    class TTSProvider(models.TextChoices):
        AUTO = "auto", "Automático pelo ambiente"
        KOKORO = "kokoro", "Kokoro local"
        AZURE = "azure", "Azure Speech"

    class AIProvider(models.TextChoices):
        DISABLED = "disabled", "Desativado"
        OPENAI = "openai", "OpenAI"
        AZURE_OPENAI = "azure_openai", "Azure OpenAI"

    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    max_files_per_user = models.PositiveSmallIntegerField(
        "arquivos por usuário", default=10
    )
    max_readings_per_user_month = models.PositiveSmallIntegerField(
        "leituras por usuário/mês", default=10
    )
    max_files_per_user_day = models.PositiveSmallIntegerField(
        "novos arquivos por usuário/dia", default=10
    )
    max_document_size_mb = models.PositiveSmallIntegerField(
        "tamanho máximo de documento (MB)", default=20
    )
    max_book_size_mb = models.PositiveSmallIntegerField(
        "tamanho máximo de livro (MB)", default=20
    )
    book_part_characters = models.PositiveIntegerField(
        "caracteres por parte interna",
        default=100_000,
        validators=[MinValueValidator(10_000), MaxValueValidator(500_000)],
    )
    book_part_pages = models.PositiveSmallIntegerField(
        "páginas por parte de PDF",
        default=10,
        validators=[MinValueValidator(2), MaxValueValidator(50)],
        help_text=(
            "Limite adicional para que o navegador nunca precise abrir uma parte "
            "muito grande do PDF."
        ),
    )
    tts_provider = models.CharField(
        "provedor de voz",
        max_length=20,
        choices=TTSProvider.choices,
        default=TTSProvider.AUTO,
    )
    azure_speech_region = models.CharField(
        "região do Azure Speech",
        max_length=80,
        default="brazilsouth",
        blank=True,
    )
    azure_speech_key_encrypted = models.TextField(editable=False, blank=True)
    ai_provider = models.CharField(
        "provedor de IA",
        max_length=20,
        choices=AIProvider.choices,
        default=AIProvider.DISABLED,
    )
    openai_model = models.CharField(
        "modelo OpenAI", max_length=80, default="gpt-5.6-luna"
    )
    openai_api_key_encrypted = models.TextField(editable=False, blank=True)
    azure_openai_endpoint = models.URLField(
        "endpoint Azure OpenAI", max_length=300, blank=True
    )
    azure_openai_deployment = models.CharField(
        "implantação/modelo Azure OpenAI", max_length=100, blank=True
    )
    azure_openai_api_key_encrypted = models.TextField(editable=False, blank=True)
    ai_max_input_characters = models.PositiveIntegerField(
        "máximo de caracteres por operação de IA",
        default=500_000,
        validators=[MinValueValidator(10_000), MaxValueValidator(2_000_000)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "configuração da aplicação"
        verbose_name_plural = "configuração da aplicação"

    def save(self, *args, **kwargs):
        self.singleton = 1
        super().save(*args, **kwargs)

    def set_secret(self, field_name, value):
        encrypted_field = f"{field_name}_encrypted"
        setattr(
            self,
            encrypted_field,
            _secret_cipher().encrypt(value.encode("utf-8")).decode("ascii")
            if value
            else "",
        )

    def get_secret(self, field_name):
        encrypted = getattr(self, f"{field_name}_encrypted", "")
        if not encrypted:
            return ""
        try:
            return _secret_cipher().decrypt(encrypted.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""

    def __str__(self):
        return "Configuração geral do ProjectLecture"


class Voice(models.Model):
    class Provider(models.TextChoices):
        ESPEAK = "espeak", "Local básica"
        KOKORO = "kokoro", "Neural Kokoro"
        CHATTERBOX = "chatterbox", "Neural Chatterbox"
        AZURE = "azure", "Azure Speech"

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


class Book(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Preparando"
        READY = "ready", "Pronto"
        FAILED = "failed", "Falhou"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="books",
        verbose_name="proprietário",
    )
    title = models.CharField("título", max_length=180)
    original_file = models.FileField(
        "arquivo original", upload_to="books/%Y/%m/"
    )
    source_type = models.CharField("formato", max_length=10, default="pdf")
    voice = models.ForeignKey(
        Voice, on_delete=models.PROTECT, related_name="books", verbose_name="voz"
    )
    speed = models.PositiveSmallIntegerField(
        "velocidade",
        default=170,
        validators=[MinValueValidator(80), MaxValueValidator(320)],
    )
    reading_mode = models.CharField(
        "modo de leitura",
        max_length=20,
        choices=(
            ("academic", "Leitura acadêmica"),
            ("natural", "Leitura natural"),
            ("literal", "Leitura literal"),
        ),
        default="academic",
    )
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.PROCESSING
    )
    page_count = models.PositiveIntegerField("páginas", default=0)
    char_count = models.PositiveIntegerField("caracteres", default=0)
    error_message = models.TextField("erro", blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "livro"
        verbose_name_plural = "livros"
        indexes = [models.Index(fields=("owner", "-created_at"))]

    def get_absolute_url(self):
        return reverse("reader:book-detail", kwargs={"pk": self.pk})

    @property
    def completed_parts(self):
        return self.parts.filter(status="ready").count()

    @property
    def progress_percent(self):
        parts = list(self.parts.all())
        if not parts:
            return 0
        return round(sum(part.progress_percent for part in parts) / len(parts))

    def __str__(self):
        return self.title


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
    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="parts",
        null=True,
        blank=True,
        verbose_name="livro",
    )
    book_order = models.PositiveIntegerField("ordem no livro", null=True, blank=True)
    page_start = models.PositiveIntegerField("página inicial", null=True, blank=True)
    page_end = models.PositiveIntegerField("página final", null=True, blank=True)
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
            models.Index(fields=("book", "book_order")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("book", "book_order"),
                name="unique_book_part_order",
            )
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


class AudioCache(models.Model):
    cache_key = models.CharField(max_length=64, unique=True)
    provider = models.CharField(max_length=20)
    voice_code = models.CharField(max_length=80)
    speed = models.PositiveSmallIntegerField()
    audio_file = models.FileField(upload_to="audio/cache/%Y/%m/")
    duration_seconds = models.FloatField(default=0)
    word_timings = models.JSONField(default=list, blank=True)
    hit_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "áudio em cache"
        verbose_name_plural = "áudios em cache"

    def __str__(self):
        return f"{self.provider}/{self.voice_code} — {self.cache_key[:10]}"


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


class MonthlyUsage(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monthly_usage",
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    readings = models.PositiveIntegerField(default=0)
    synthesized_characters = models.PositiveBigIntegerField(default=0)
    ai_requests = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-year", "-month")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "year", "month"),
                name="unique_user_monthly_usage",
            )
        ]
        verbose_name = "uso mensal"
        verbose_name_plural = "usos mensais"

    def __str__(self):
        return f"{self.user} — {self.month:02d}/{self.year}"


class AIResult(models.Model):
    class Operation(models.TextChoices):
        SUMMARY = "summary", "Resumo"
        TRANSLATION = "translation", "Tradução"

    class Status(models.TextChoices):
        PENDING = "pending", "Na fila"
        PROCESSING = "processing", "Processando"
        READY = "ready", "Pronto"
        FAILED = "failed", "Falhou"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_results",
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="ai_results",
        null=True,
        blank=True,
    )
    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="ai_results",
        null=True,
        blank=True,
    )
    operation = models.CharField(max_length=20, choices=Operation.choices)
    target_language = models.CharField(max_length=60, blank=True)
    input_hash = models.CharField(max_length=64)
    provider = models.CharField(max_length=30, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    content = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "resultado de IA"
        verbose_name_plural = "resultados de IA"
        indexes = [
            models.Index(fields=("owner", "operation", "-created_at")),
            models.Index(fields=("input_hash", "operation")),
        ]

    def __str__(self):
        target = self.book or self.document
        return f"{self.get_operation_display()} — {target}"
