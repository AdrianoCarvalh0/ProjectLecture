from django.core.management.base import BaseCommand

from reader.models import Voice


class Command(BaseCommand):
    help = "Cria as vozes locais iniciais."

    def handle(self, *args, **options):
        voices = [
            {
                "code": "pf_dora",
                "name": "Lia",
                "language": "pt-BR",
                "description": "Clara e acolhedora para leituras longas.",
                "provider": Voice.Provider.KOKORO,
                "avatar": "img/voices/lia.png",
                "style_label": "Clara · acolhedora",
                "quality_label": "Neural HD",
                "is_default": True,
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
            },
        ]
        for data in voices:
            Voice.objects.update_or_create(code=data["code"], defaults=data)
        Voice.objects.filter(code="pt").update(is_active=False)
        self.stdout.write(self.style.SUCCESS("Vozes iniciais disponíveis."))
