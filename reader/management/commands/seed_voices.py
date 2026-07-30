from django.core.management.base import BaseCommand

from reader.models import Voice
from reader.services.runtime_config import effective_tts_provider, sync_voice_catalog


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
                "is_default": False,
                "is_active": False,
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
                "is_active": False,
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
                "is_active": False,
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
                "is_default": False,
                "is_active": False,
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
                "is_active": False,
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
                "is_active": False,
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
                "is_active": False,
            },
        ]
        voices = local_voices + azure_voices
        for data in voices:
            Voice.objects.update_or_create(code=data["code"], defaults=data)
        Voice.objects.filter(code="pt").update(is_active=False)
        sync_voice_catalog()
        catalog = (
            "Azure Speech"
            if effective_tts_provider() == Voice.Provider.AZURE
            else "Kokoro local"
        )
        self.stdout.write(
            self.style.SUCCESS(f"Vozes iniciais disponíveis: {catalog}.")
        )
