import os

from django.conf import settings
from django.db import OperationalError, ProgrammingError

from reader.models import AppConfiguration, Voice


def get_app_configuration():
    try:
        configuration, _ = AppConfiguration.objects.get_or_create(
            singleton=1,
            defaults={
                "max_files_per_user": getattr(
                    settings, "MAX_DOCUMENTS_PER_USER", 10
                ),
                "max_readings_per_user_month": getattr(
                    settings, "MAX_READINGS_PER_USER_MONTH", 10
                ),
                "max_files_per_user_day": getattr(
                    settings,
                    "MAX_DOCUMENTS_PER_USER_PER_DAY",
                    10,
                ),
                "max_document_size_mb": getattr(
                    settings, "MAX_DOCUMENT_SIZE_MB", 20
                ),
                "book_part_characters": getattr(
                    settings, "MAX_CHARACTERS_PER_DOCUMENT", 100_000
                ),
                "book_part_pages": getattr(settings, "BOOK_PART_MAX_PAGES", 10),
            },
        )
        return configuration
    except (OperationalError, ProgrammingError):
        return AppConfiguration(
            max_files_per_user=getattr(settings, "MAX_DOCUMENTS_PER_USER", 10),
            max_readings_per_user_month=getattr(
                settings, "MAX_READINGS_PER_USER_MONTH", 10
            ),
            max_files_per_user_day=getattr(
                settings,
                "MAX_DOCUMENTS_PER_USER_PER_DAY",
                10,
            ),
            max_document_size_mb=getattr(settings, "MAX_DOCUMENT_SIZE_MB", 20),
            book_part_characters=getattr(
                settings, "MAX_CHARACTERS_PER_DOCUMENT", 100_000
            ),
            book_part_pages=getattr(settings, "BOOK_PART_MAX_PAGES", 10),
        )


def effective_tts_provider(configuration=None):
    configuration = configuration or get_app_configuration()
    if configuration.tts_provider != AppConfiguration.TTSProvider.AUTO:
        return configuration.tts_provider
    environment = getattr(settings, "TTS_ENVIRONMENT", "").strip().lower()
    if not environment:
        environment = "development" if settings.DEBUG else "production"
    return (
        Voice.Provider.KOKORO
        if environment in {"development", "local", "dev"}
        else Voice.Provider.AZURE
    )


def sync_voice_catalog(configuration=None):
    provider = effective_tts_provider(configuration)
    Voice.objects.filter(provider__in=(Voice.Provider.KOKORO, Voice.Provider.AZURE)).update(
        is_active=False,
        is_default=False,
    )
    candidates = Voice.objects.filter(provider=provider).order_by("name")
    candidates.update(is_active=True)
    preferred_codes = {
        Voice.Provider.KOKORO: "pf_dora",
        Voice.Provider.AZURE: "pt-BR-FranciscaNeural",
    }
    preferred = candidates.filter(code=preferred_codes.get(provider, "")).first()
    if preferred:
        preferred.is_default = True
        preferred.save(update_fields=("is_default",))


def openai_api_key(configuration=None):
    configuration = configuration or get_app_configuration()
    return configuration.get_secret("openai_api_key") or os.getenv(
        "OPENAI_API_KEY", ""
    ).strip()


def azure_openai_api_key(configuration=None):
    configuration = configuration or get_app_configuration()
    return configuration.get_secret("azure_openai_api_key") or os.getenv(
        "AZURE_OPENAI_API_KEY", ""
    ).strip()


def azure_speech_api_key(configuration=None):
    configuration = configuration or get_app_configuration()
    return configuration.get_secret("azure_speech_key") or getattr(
        settings,
        "AZURE_SPEECH_KEY",
        "",
    ).strip()


def azure_speech_region(configuration=None):
    configuration = configuration or get_app_configuration()
    return configuration.azure_speech_region or getattr(
        settings,
        "AZURE_SPEECH_REGION",
        "",
    ).strip()


def ai_is_configured(configuration=None):
    configuration = configuration or get_app_configuration()
    if configuration.ai_provider == AppConfiguration.AIProvider.OPENAI:
        return bool(openai_api_key(configuration) and configuration.openai_model)
    if configuration.ai_provider == AppConfiguration.AIProvider.AZURE_OPENAI:
        return bool(
            azure_openai_api_key(configuration)
            and configuration.azure_openai_endpoint
            and configuration.azure_openai_deployment
        )
    return False
