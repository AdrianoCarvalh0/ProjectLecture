import time
import wave
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from reader.models import Voice
from reader.services.text_preparation import prepare_for_speech
from reader.services.tts import synthesize_segment


SAMPLE = (
    "Silva et al. observaram uma melhora de 25% no volume 3 do periódico. "
    "Os resultados, apresentados na figura 2, sugerem uma associação relevante. "
    "Entretanto, novos estudos são necessários para confirmar essa hipótese."
)


class Command(BaseCommand):
    help = "Gera amostras comparáveis e mede o tempo de síntese de cada voz."

    def add_arguments(self, parser):
        parser.add_argument("--speed", type=int, default=170)

    def handle(self, *args, **options):
        output_dir = Path(settings.MEDIA_ROOT) / "benchmarks"
        output_dir.mkdir(parents=True, exist_ok=True)
        voices = Voice.objects.filter(is_active=True).order_by("-is_default", "name")
        if not voices:
            raise CommandError("Nenhuma voz ativa foi encontrada.")

        self.stdout.write("voz | motor | áudio | geração | fator tempo real")
        for voice in voices:
            output = output_dir / f"{voice.provider}-{voice.code}.wav"
            started = time.perf_counter()
            synthesize_segment(
                prepare_for_speech(SAMPLE, "academic"),
                output,
                voice.code,
                options["speed"],
                voice.provider,
            )
            elapsed = time.perf_counter() - started
            with wave.open(str(output), "rb") as wav:
                duration = wav.getnframes() / wav.getframerate()
            real_time_factor = elapsed / duration if duration else 0
            self.stdout.write(
                f"{voice.name} | {voice.provider} | {duration:.1f}s | "
                f"{elapsed:.1f}s | {real_time_factor:.2f}x"
            )
        self.stdout.write(self.style.SUCCESS(f"Amostras salvas em {output_dir}"))
