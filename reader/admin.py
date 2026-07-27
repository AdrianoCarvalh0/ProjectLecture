from django.contrib import admin

from .models import AudioSegment, Document, ReadingProgress, Voice


@admin.register(Voice)
class VoiceAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "provider", "quality_label", "is_default", "is_active")
    list_filter = ("provider", "language", "is_default", "is_active")
    search_fields = ("name", "code")


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


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "source_type",
        "voice",
        "status",
        "char_count",
        "created_at",
    )
    list_filter = ("status", "source_type", "reading_mode", "voice")
    search_fields = ("title", "owner__username", "extracted_text")
    readonly_fields = ("char_count", "duration_seconds", "created_at", "updated_at")
    inlines = (AudioSegmentInline,)


@admin.register(ReadingProgress)
class ReadingProgressAdmin(admin.ModelAdmin):
    list_display = ("document", "user", "position_seconds", "completed", "updated_at")
    list_filter = ("completed",)
