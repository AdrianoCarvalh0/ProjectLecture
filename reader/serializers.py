from django.conf import settings
from rest_framework import serializers

from .models import AudioSegment, Document, ReadingProgress, Voice
from .services.extractors import ExtractionError, extract_text, source_type_for
from .tasks import dispatch_audio_generation


class VoiceSerializer(serializers.ModelSerializer):
    provider_label = serializers.CharField(source="get_provider_display", read_only=True)
    avatar_url = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()

    class Meta:
        model = Voice
        fields = (
            "id",
            "name",
            "code",
            "language",
            "description",
            "provider",
            "provider_label",
            "avatar",
            "avatar_url",
            "style_label",
            "quality_label",
            "is_default",
            "preview_url",
        )

    def get_preview_url(self, obj):
        from django.urls import reverse

        request = self.context.get("request")
        path = reverse("reader:voice-preview", kwargs={"pk": obj.pk})
        return request.build_absolute_uri(path) if request else path

    def get_avatar_url(self, obj):
        if not obj.avatar:
            return None
        from django.templatetags.static import static

        request = self.context.get("request")
        path = static(obj.avatar)
        return request.build_absolute_uri(path) if request else path


class AudioSegmentSerializer(serializers.ModelSerializer):
    audio_url = serializers.SerializerMethodField()

    class Meta:
        model = AudioSegment
        fields = (
            "order",
            "text",
            "status",
            "start_char",
            "end_char",
            "duration_seconds",
            "start_seconds",
            "end_seconds",
            "audio_url",
            "word_timings",
        )

    def get_audio_url(self, obj):
        if not obj.audio_file:
            return None
        request = self.context.get("request")
        return (
            request.build_absolute_uri(obj.audio_file.url)
            if request
            else obj.audio_file.url
        )


class AudioSegmentSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = AudioSegment
        fields = (
            "order",
            "text",
            "status",
            "start_char",
            "end_char",
            "start_seconds",
            "end_seconds",
        )


class ProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReadingProgress
        fields = ("position_seconds", "char_offset", "completed", "updated_at")
        read_only_fields = ("updated_at",)

    def validate_position_seconds(self, value):
        if value < 0:
            raise serializers.ValidationError("A posição não pode ser negativa.")
        return value


class DocumentSerializer(serializers.ModelSerializer):
    voice = VoiceSerializer(read_only=True)
    audio_url = serializers.SerializerMethodField()
    original_file_url = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    segments = AudioSegmentSummarySerializer(many=True, read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.CharField(source="get_source_type_display", read_only=True)

    class Meta:
        model = Document
        fields = (
            "id",
            "title",
            "source_type",
            "source_label",
            "extracted_text",
            "voice",
            "speed",
            "reading_mode",
            "synthesis_provider",
            "stream_is_building",
            "status",
            "status_label",
            "audio_url",
            "original_file_url",
            "char_count",
            "duration_seconds",
            "error_message",
            "progress",
            "segments",
            "created_at",
            "updated_at",
            "completed_at",
        )

    def _absolute_url(self, field):
        if not field:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(field.url) if request else field.url

    def get_audio_url(self, obj):
        return self._absolute_url(obj.audio_file)

    def get_original_file_url(self, obj):
        return self._absolute_url(obj.original_file)

    def get_progress(self, obj):
        try:
            progress = obj.reading_progress
        except ReadingProgress.DoesNotExist:
            return None
        return ProgressSerializer(progress).data


class DocumentCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=180)
    text = serializers.CharField(
        required=False, allow_blank=True, write_only=True, style={"base_template": "textarea.html"}
    )
    original_file = serializers.FileField(required=False, write_only=True)
    voice = serializers.PrimaryKeyRelatedField(
        queryset=Voice.objects.filter(is_active=True)
    )
    speed = serializers.IntegerField(min_value=80, max_value=320, default=170)
    reading_mode = serializers.ChoiceField(
        choices=Document.ReadingMode.choices,
        default=Document.ReadingMode.ACADEMIC,
    )

    def validate(self, attrs):
        text = (attrs.get("text") or "").strip()
        uploaded = attrs.get("original_file")
        if bool(text) == bool(uploaded):
            raise serializers.ValidationError(
                "Informe exatamente uma origem: texto ou arquivo."
            )
        if len(text) > settings.MAX_CHARACTERS_PER_DOCUMENT:
            raise serializers.ValidationError(
                f"O texto excede {settings.MAX_CHARACTERS_PER_DOCUMENT} caracteres."
            )
        if uploaded:
            extension = source_type_for(uploaded.name)
            if extension == "text":
                raise serializers.ValidationError("Envie PDF, DOCX, EPUB ou TXT.")
            if uploaded.size > settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024:
                raise serializers.ValidationError("O arquivo excede o tamanho permitido.")
        return attrs

    def create(self, validated_data):
        uploaded = validated_data.pop("original_file", None)
        text = validated_data.pop("text", "").strip()
        if uploaded:
            try:
                text = extract_text(uploaded)
            except ExtractionError as exc:
                raise serializers.ValidationError({"original_file": str(exc)}) from exc
            uploaded.seek(0)
        if not text:
            raise serializers.ValidationError("Nenhum texto legível foi encontrado.")
        if len(text) > settings.MAX_CHARACTERS_PER_DOCUMENT:
            raise serializers.ValidationError(
                f"O conteúdo extraído excede {settings.MAX_CHARACTERS_PER_DOCUMENT} caracteres."
            )

        document = Document.objects.create(
            owner=self.context["request"].user,
            extracted_text=text,
            original_file=uploaded,
            source_type=source_type_for(uploaded.name) if uploaded else Document.SourceType.TEXT,
            status=Document.Status.PENDING,
            **validated_data,
        )
        dispatch_audio_generation(document)
        return document

    def to_representation(self, instance):
        return DocumentSerializer(instance, context=self.context).data
