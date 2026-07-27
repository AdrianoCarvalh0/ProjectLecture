from django.core.management.base import BaseCommand
from django.conf import settings

from reader.models import Voice


class Command(BaseCommand):
    help = "Cria e seleciona o catálogo inicial de vozes."

    def handle(self, *args, **options):
        local_voices = [
            {
                "code": "pf_dora",
                "name": "Lia",
                "language": "pt-BR",
                "description": "Clara e acolhedora para leituras longas.",
                "provider": Voice.Provider.KOKORO,
                "avatar": "img/voices/lia.png",
                "style_label": "Clara · acolhedora",
                "quality_label": "Neural HD",
                "is_default": not settings.AZURE_SPEECH_ENABLED,
                "is_active": not settings.AZURE_SPEECH_ENABLED,
            },
            {
                "code": "pm_alex",
                "name": "Caio",
                "language": "pt-BR",
                "description": "Objetiva e contemporânea, com boa dicção.",
                "provider": Voice.Provider.KOKORO,
                "avatar": "img/voices/caio.png",
                "style_label": "Objetiva · dinâmica",
                "quality_label": "Neural HD",
                "is_default": False,
                "is_active": not settings.AZURE_SPEECH_ENABLED,
            },
            {
                "code": "pm_santa",
                "name": "Ravi",
                "language": "pt-BR",
                "description": "Grave e calma para leitura reflexiva.",
                "provider": Voice.Provider.KOKORO,
                "avatar": "img/voices/ravi.png",
                "style_label": "Grave · reflexiva",
                "quality_label": "Neural HD",
                "is_default": False,
                "is_active": not settings.AZURE_SPEECH_ENABLED,
            },
            {
                "code": "pt-br",
                "name": "Voz local",
                "language": "pt-BR",
                "description": "Fallback leve para computadores modestos.",
                "provider": Voice.Provider.ESPEAK,
                "style_label": "Sintética · econômica",
                "quality_label": "Compatibilidade",
                "is_default": False,
                "is_active": False,
            },
        ]
        azure_voices = [
            {
                "code": "pt-BR-FranciscaNeural",
                "name": "Francisca",
                "language": "pt-BR",
                "description": "Calma e natural para artigos e leituras prolongadas.",
                "provider": Voice.Provider.AZURE,
                "style_label": "Calma · acolhedora",
                "quality_label": "Azure Neural",
                "is_default": settings.AZURE_SPEECH_ENABLED,
                "is_active": settings.AZURE_SPEECH_ENABLED,
            },
            {
                "code": "pt-BR-AntonioNeural",
                "name": "Antonio",
                "language": "pt-BR",
                "description": "Clara e objetiva para textos técnicos.",
                "provider": Voice.Provider.AZURE,
                "style_label": "Clara · objetiva",
                "quality_label": "Azure Neural",
                "is_default": False,
                "is_active": settings.AZURE_SPEECH_ENABLED,
            },
            {
                "code": "pt-BR-ThalitaNeural",
                "name": "Thalita",
                "language": "pt-BR",
                "description": "Leve e fluida para leitura cotidiana.",
                "provider": Voice.Provider.AZURE,
                "style_label": "Leve · fluida",
                "quality_label": "Azure Neural",
                "is_default": False,
                "is_active": settings.AZURE_SPEECH_ENABLED,
            },
            {
                "code": "pt-BR-DonatoNeural",
                "name": "Donato",
                "language": "pt-BR",
                "description": "Grave e serena para leitura reflexiva.",
                "provider": Voice.Provider.AZURE,
                "style_label": "Grave · serena",
                "quality_label": "Azure Neural",
                "is_default": False,
                "is_active": settings.AZURE_SPEECH_ENABLED,
            },
        ]
        voices = local_voices + azure_voices
        for data in voices:
            Voice.objects.update_or_create(code=data["code"], defaults=data)
        Voice.objects.filter(code="pt").update(is_active=False)
        catalog = "Azure Speech" if settings.AZURE_SPEECH_ENABLED else "Kokoro local"
        self.stdout.write(
            self.style.SUCCESS(f"Vozes iniciais disponíveis: {catalog}.")
        )
