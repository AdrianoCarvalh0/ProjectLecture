from django import forms
from django.contrib import admin

from .models import (
    AIResult,
    AppConfiguration,
    AudioCache,
    AudioSegment,
    Book,
    Document,
    MonthlyUsage,
    ReadingProgress,
    Voice,
)
from .services.runtime_config import sync_voice_catalog


@admin.register(Voice)
class VoiceAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "provider", "quality_label", "is_default", "is_active")
    list_filter = ("provider", "language", "is_default", "is_active")
    search_fields = ("name", "code")


class AppConfigurationAdminForm(forms.ModelForm):
    openai_api_key = forms.CharField(
        label="Chave OpenAI",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Deixe em branco para manter a chave atual ou usar OPENAI_API_KEY.",
    )
    azure_openai_api_key = forms.CharField(
        label="Chave Azure OpenAI",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Deixe em branco para manter a chave atual ou usar AZURE_OPENAI_API_KEY.",
    )
    azure_speech_key = forms.CharField(
        label="Chave Azure Speech",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Deixe em branco para manter a chave atual ou usar AZURE_SPEECH_KEY.",
    )

    class Meta:
        model = AppConfiguration
        exclude = (
            "singleton",
            "azure_speech_key_encrypted",
            "openai_api_key_encrypted",
            "azure_openai_api_key_encrypted",
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        for field_name in (
            "azure_speech_key",
            "openai_api_key",
            "azure_openai_api_key",
        ):
            value = self.cleaned_data.get(field_name)
            if value:
                instance.set_secret(field_name, value)
        if commit:
            instance.save()
        return instance


@admin.register(AppConfiguration)
class AppConfigurationAdmin(admin.ModelAdmin):
    form = AppConfigurationAdminForm
    fieldsets = (
        (
            "Limites comunitários",
            {
                "fields": (
                    "max_files_per_user",
                    "max_readings_per_user_month",
                    "max_files_per_user_day",
                    "max_document_size_mb",
                    "max_book_size_mb",
                    "book_part_characters",
                    "book_part_pages",
                )
            },
        ),
        (
            "Voz",
            {
                "fields": (
                    "tts_provider",
                    "azure_speech_region",
                    "azure_speech_key",
                )
            },
        ),
        (
            "Inteligência artificial",
            {
                "fields": (
                    "ai_provider",
                    "ai_max_input_characters",
                    "openai_model",
                    "openai_api_key",
                    "azure_openai_endpoint",
                    "azure_openai_deployment",
                    "azure_openai_api_key",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        return not AppConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        sync_voice_catalog(obj)


class AudioSegmentInline(admin.TabularInline):
    model = AudioSegment
    extra = 0
    readonly_fields = (
        "order",
        "status",
        "audio_file",
        "duration_seconds",
        "start_char",
        "end_char",
        "start_seconds",
        "end_seconds",
    )


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "source_type",
        "status",
        "page_count",
        "char_count",
        "created_at",
    )
    list_filter = ("status", "source_type", "reading_mode")
    search_fields = ("title", "owner__username", "owner__email")
    readonly_fields = ("page_count", "char_count", "created_at", "updated_at")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "book",
        "book_order",
        "source_type",
        "voice",
        "status",
        "char_count",
        "created_at",
    )
    list_filter = ("status", "source_type", "reading_mode", "voice", "book")
    search_fields = ("title", "owner__username", "extracted_text")
    readonly_fields = ("char_count", "duration_seconds", "created_at", "updated_at")
    inlines = (AudioSegmentInline,)


@admin.register(ReadingProgress)
class ReadingProgressAdmin(admin.ModelAdmin):
    list_display = ("document", "user", "position_seconds", "completed", "updated_at")
    list_filter = ("completed",)


@admin.register(MonthlyUsage)
class MonthlyUsageAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "month",
        "year",
        "readings",
        "synthesized_characters",
        "ai_requests",
    )
    list_filter = ("year", "month")
    search_fields = ("user__username", "user__email")


@admin.register(AudioCache)
class AudioCacheAdmin(admin.ModelAdmin):
    list_display = (
        "voice_code",
        "provider",
        "speed",
        "duration_seconds",
        "hit_count",
        "last_used_at",
    )
    list_filter = ("provider", "voice_code", "speed")
    readonly_fields = (
        "cache_key",
        "provider",
        "voice_code",
        "speed",
        "audio_file",
        "duration_seconds",
        "word_timings",
        "hit_count",
        "created_at",
        "last_used_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(AIResult)
class AIResultAdmin(admin.ModelAdmin):
    list_display = (
        "operation",
        "owner",
        "document",
        "book",
        "provider",
        "model_name",
        "status",
        "created_at",
    )
    list_filter = ("operation", "provider", "status")
    search_fields = ("owner__username", "document__title", "book__title", "content")
    readonly_fields = (
        "owner",
        "document",
        "book",
        "operation",
        "target_language",
        "input_hash",
        "provider",
        "model_name",
        "content",
        "status",
        "error_message",
        "created_at",
        "completed_at",
    )

    def has_add_permission(self, request):
        return False
