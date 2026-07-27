import tempfile
from pathlib import Path

from celery import chain, shared_task
from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import AudioSegment, Document
from .services.streaming import (
    build_word_timings,
    map_spoken_word_timings,
    wav_duration,
)
from .services.text_preparation import prepare_for_speech
from .services.tts import split_text, synthesize_segment


def _delete_segment_files(document):
    for segment in document.segments.exclude(audio_file=""):
        segment.audio_file.delete(save=False)


@shared_task
def generate_stream_chunk(segment_id):
    segment = AudioSegment.objects.select_related(
        "document", "document__voice"
    ).get(pk=segment_id)
    document = segment.document
    segment.status = AudioSegment.Status.PROCESSING
    segment.save(update_fields=("status",))

    try:
        with tempfile.TemporaryDirectory(prefix="projectlecture-chunk-") as temp_dir:
            wav_path = Path(temp_dir) / f"chunk-{segment.order:05d}.wav"
            spoken_timings = synthesize_segment(
                segment.spoken_text,
                wav_path,
                document.voice.code,
                document.speed,
                document.voice.provider,
            )
            duration = wav_duration(wav_path)
            previous_duration = (
                AudioSegment.objects.filter(
                    document=document,
                    order__lt=segment.order,
                    status=AudioSegment.Status.READY,
                ).aggregate(total=Sum("duration_seconds"))["total"]
                or 0
            )
            if segment.audio_file:
                segment.audio_file.delete(save=False)
            with wav_path.open("rb") as audio:
                segment.audio_file.save(
                    f"documento-{document.pk}-trecho-{segment.order:05d}.wav",
                    File(audio),
                    save=False,
                )
            segment.duration_seconds = duration
            segment.start_seconds = previous_duration
            segment.end_seconds = previous_duration + duration
            segment.word_timings = map_spoken_word_timings(
                segment.text,
                spoken_timings,
                duration,
                segment.start_char,
            ) or build_word_timings(
                segment.text,
                duration,
                segment.start_char,
            )
            segment.status = AudioSegment.Status.READY
            segment.save()

        document.duration_seconds = round(segment.end_seconds)
        document.status = Document.Status.READY
        document.synthesis_provider = document.voice.provider
        document.error_message = ""
        document.save(
            update_fields=(
                "duration_seconds",
                "status",
                "synthesis_provider",
                "error_message",
                "updated_at",
            )
        )
        return {"segment_id": segment.pk, "duration_seconds": duration}
    except Exception as exc:
        segment.status = AudioSegment.Status.FAILED
        segment.save(update_fields=("status",))
        document.status = Document.Status.FAILED
        document.stream_is_building = False
        document.error_message = str(exc)[:2000]
        document.save(
            update_fields=(
                "status",
                "stream_is_building",
                "error_message",
                "updated_at",
            )
        )
        raise


@shared_task
def finalize_stream(document_id):
    document = Document.objects.get(pk=document_id)
    failed = document.segments.filter(status=AudioSegment.Status.FAILED).exists()
    pending = document.segments.exclude(status=AudioSegment.Status.READY).exists()
    if failed or pending:
        document.status = Document.Status.FAILED
        document.error_message = (
            "Um ou mais blocos da leitura não puderam ser preparados."
        )
    else:
        total = (
            document.segments.aggregate(total=Sum("duration_seconds"))["total"] or 0
        )
        document.duration_seconds = round(total)
        document.status = Document.Status.READY
        document.completed_at = timezone.now()
        document.error_message = ""
    document.stream_is_building = False
    document.save()
    return {
        "document_id": document.pk,
        "duration_seconds": document.duration_seconds,
        "status": document.status,
    }


@shared_task
def generate_audio(document_id):
    document = Document.objects.select_related("voice").get(pk=document_id)
    document.status = Document.Status.PROCESSING
    document.stream_is_building = True
    document.duration_seconds = 0
    document.completed_at = None
    document.error_message = ""
    document.save(
        update_fields=(
            "status",
            "stream_is_building",
            "duration_seconds",
            "completed_at",
            "error_message",
            "updated_at",
        )
    )

    try:
        text_segments = split_text(
            document.extracted_text, max_chars=settings.STREAM_CHUNK_CHARS
        )
        if not text_segments:
            raise ValueError("O documento não possui texto para sintetizar.")

        _delete_segment_files(document)
        if document.audio_file:
            document.audio_file.delete(save=False)
            document.save(update_fields=("audio_file", "updated_at"))

        with transaction.atomic():
            document.segments.all().delete()
            AudioSegment.objects.bulk_create(
                [
                    AudioSegment(
                        document=document,
                        order=index,
                        text=segment.text,
                        spoken_text=prepare_for_speech(
                            segment.text, document.reading_mode
                        ),
                        start_char=segment.start_char,
                        end_char=segment.end_char,
                    )
                    for index, segment in enumerate(text_segments)
                ]
            )
            # MySQL does not guarantee that bulk_create populates primary keys.
            # Reload the rows before their IDs are passed to Celery.
            segments = list(document.segments.order_by("order"))

        workflow = chain(
            *[generate_stream_chunk.si(segment.pk) for segment in segments],
            finalize_stream.si(document.pk),
        )
        result = workflow.apply_async()
        return {
            "document_id": document.pk,
            "chunks": len(segments),
            "workflow_id": result.id,
        }
    except Exception as exc:
        document.status = Document.Status.FAILED
        document.stream_is_building = False
        document.error_message = str(exc)[:2000]
        document.save(
            update_fields=(
                "status",
                "stream_is_building",
                "error_message",
                "updated_at",
            )
        )
        raise


def dispatch_audio_generation(document):
    try:
        return generate_audio.delay(document.pk)
    except Exception:
        return generate_audio.apply(args=(document.pk,))
