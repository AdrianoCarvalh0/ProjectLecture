import hashlib
import shutil
import tempfile
from pathlib import Path

from celery import chain, shared_task
from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.text import slugify

from .models import AIResult, AudioCache, AudioSegment, Book, Document
from .services.extractors import (
    ExtractionError,
    create_pdf_slice,
    source_type_for,
    split_into_parts,
)
from .services.runtime_config import get_app_configuration
from .services.streaming import (
    build_word_timings,
    map_spoken_word_timings,
    wav_duration,
)
from .services.ai import summarize_text, translate_text
from .services.text_preparation import prepare_for_speech
from .services.tts import split_text, synthesize_segment


def _audio_cache_key(document, spoken_text):
    payload = "\0".join(
        (
            document.voice.provider,
            document.voice.code,
            str(document.speed),
            spoken_text,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _synthesize_with_cache(document, segment, wav_path):
    cache_key = _audio_cache_key(document, segment.spoken_text)
    cached = AudioCache.objects.filter(cache_key=cache_key).first()
    if cached and cached.audio_file and default_storage.exists(cached.audio_file.name):
        with default_storage.open(cached.audio_file.name, "rb") as source:
            with wav_path.open("wb") as destination:
                shutil.copyfileobj(source, destination)
        AudioCache.objects.filter(pk=cached.pk).update(
            hit_count=F("hit_count") + 1,
            last_used_at=timezone.now(),
        )
        return cached.word_timings

    spoken_timings = synthesize_segment(
        segment.spoken_text,
        wav_path,
        document.voice.code,
        document.speed,
        document.voice.provider,
    )
    duration = wav_duration(wav_path)
    cache, created = AudioCache.objects.get_or_create(
        cache_key=cache_key,
        defaults={
            "provider": document.voice.provider,
            "voice_code": document.voice.code,
            "speed": document.speed,
            "duration_seconds": duration,
            "word_timings": spoken_timings,
        },
    )
    if created:
        with wav_path.open("rb") as audio:
            cache.audio_file.save(f"{cache_key}.wav", File(audio), save=True)
    return spoken_timings


def _delete_segment_files(document):
    for segment in document.segments.exclude(audio_file=""):
        segment.audio_file.delete(save=False)


def _refresh_book_status(document):
    # Book.status represents preparation of the playlist, not synthesis of every
    # part. Audio is intentionally produced lazily as the reader advances.
    return


def _document_storage_names(document):
    names = []
    if document.original_file:
        names.append(document.original_file.name)
    if document.audio_file:
        names.append(document.audio_file.name)
    names.extend(
        document.segments.exclude(audio_file="").values_list(
            "audio_file", flat=True
        )
    )
    return names


@shared_task
def prepare_book(book_id):
    book = Book.objects.select_related("owner", "voice").get(pk=book_id)
    if book.status == Book.Status.READY and book.parts.exists():
        return {"book_id": book.pk, "parts": book.parts.count(), "reused": True}
    book.status = Book.Status.PROCESSING
    book.error_message = ""
    book.save(update_fields=("status", "error_message", "updated_at"))
    saved_part_files = []

    try:
        with book.original_file.open("rb") as source:
            source_bytes = source.read()
        source_name = Path(book.original_file.name).name
        source_type = source_type_for(source_name)
        configuration = get_app_configuration()
        upload = ContentFile(source_bytes, name=source_name)
        parts = split_into_parts(
            upload,
            configuration.book_part_characters,
            max_pages=configuration.book_part_pages,
        )
        if not parts:
            raise ExtractionError(
                "Nenhum texto legível foi encontrado. "
                "PDFs escaneados precisarão de OCR."
            )

        physical_parts = {}
        if source_type == Document.SourceType.PDF:
            for part in parts:
                physical_parts[part.order] = create_pdf_slice(
                    source_bytes,
                    part.page_start,
                    part.page_end,
                )

        old_part_files = [
            storage_name
            for old_part in book.parts.all()
            for storage_name in _document_storage_names(old_part)
        ]

        with transaction.atomic():
            book.parts.all().delete()
            part_count = len(parts)
            for part in parts:
                document = Document(
                    owner=book.owner,
                    book=book,
                    book_order=part.order,
                    page_start=part.page_start,
                    page_end=part.page_end,
                    title=(
                        f"{book.title} — Parte {part.order + 1}"
                        if part_count > 1
                        else book.title
                    ),
                    source_type=source_type,
                    extracted_text=part.text,
                    voice=book.voice,
                    speed=book.speed,
                    reading_mode=book.reading_mode,
                    status=Document.Status.PENDING,
                )
                if part.order in physical_parts:
                    safe_title = slugify(book.title) or f"livro-{book.pk}"
                    filename = (
                        f"{safe_title}-parte-{part.order + 1:04d}.pdf"
                    )
                    document.original_file.save(
                        filename,
                        ContentFile(physical_parts[part.order]),
                        save=False,
                    )
                    saved_part_files.append(document.original_file.name)
                document.save()

            book.source_type = source_type
            book.page_count = max(
                (part.page_end or 0 for part in parts),
                default=0,
            )
            book.char_count = sum(len(part.text) for part in parts)
            book.status = Book.Status.READY
            book.error_message = ""
            book.save(
                update_fields=(
                    "source_type",
                    "page_count",
                    "char_count",
                    "status",
                    "error_message",
                    "updated_at",
                )
            )
        for storage_name in old_part_files:
            if default_storage.exists(storage_name):
                default_storage.delete(storage_name)
        return {"book_id": book.pk, "parts": len(parts)}
    except Exception as exc:
        for storage_name in saved_part_files:
            if default_storage.exists(storage_name):
                default_storage.delete(storage_name)
        Book.objects.filter(pk=book.pk).update(
            status=Book.Status.FAILED,
            error_message=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        raise


def dispatch_book_preparation(book):
    try:
        return prepare_book.delay(book.pk)
    except Exception:
        return prepare_book.apply(args=(book.pk,))


def ensure_book_audio_window(document):
    """Queue a small initial audio window only for the selected book part."""
    if not document.book_id:
        return 0
    claimed = Document.objects.filter(
        pk=document.pk,
        status=Document.Status.PENDING,
        stream_is_building=False,
    ).update(
        status=Document.Status.PROCESSING,
        stream_is_building=True,
        error_message="",
        updated_at=timezone.now(),
    )
    if not claimed:
        return 0
    document.refresh_from_db()
    dispatch_audio_generation(document)
    return 1


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
            spoken_timings = _synthesize_with_cache(document, segment, wav_path)
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
        _refresh_book_status(document)
        raise


@shared_task
def finalize_stream(document_id):
    document = Document.objects.get(pk=document_id)
    failed = document.segments.filter(status=AudioSegment.Status.FAILED).exists()
    unfinished = document.segments.exclude(status=AudioSegment.Status.READY).exists()
    ready = document.segments.filter(status=AudioSegment.Status.READY).exists()
    if failed:
        document.status = Document.Status.FAILED
        document.error_message = (
            "Um ou mais blocos da leitura não puderam ser preparados."
        )
    elif ready:
        total = (
            document.segments.filter(
                status=AudioSegment.Status.READY
            ).aggregate(total=Sum("duration_seconds"))["total"]
            or 0
        )
        document.duration_seconds = round(total)
        document.status = Document.Status.READY
        document.error_message = ""
        document.completed_at = None if unfinished else timezone.now()
    else:
        document.status = Document.Status.PENDING
    document.stream_is_building = False
    document.save()
    _refresh_book_status(document)
    return {
        "document_id": document.pk,
        "duration_seconds": document.duration_seconds,
        "status": document.status,
    }


def queue_stream_window(document_id, start_order=0):
    """Claim and synthesize one bounded window of pending audio chunks."""
    window_size = max(1, int(getattr(settings, "STREAM_PREFETCH_CHUNKS", 6)))
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document_id)
        if document.stream_is_building:
            return {"document_id": document.pk, "queued": 0, "building": True}
        segment_ids = list(
            document.segments.filter(
                status=AudioSegment.Status.PENDING,
                order__gte=max(0, int(start_order or 0)),
            )
            .order_by("order")
            .values_list("pk", flat=True)[:window_size]
        )
        if not segment_ids:
            return {"document_id": document.pk, "queued": 0, "building": False}
        AudioSegment.objects.filter(pk__in=segment_ids).update(
            status=AudioSegment.Status.PROCESSING
        )
        document.stream_is_building = True
        document.status = (
            Document.Status.READY
            if document.segments.filter(status=AudioSegment.Status.READY).exists()
            else Document.Status.PROCESSING
        )
        document.error_message = ""
        document.save(
            update_fields=(
                "stream_is_building",
                "status",
                "error_message",
                "updated_at",
            )
        )

    workflow = chain(
        *[generate_stream_chunk.si(segment_id) for segment_id in segment_ids],
        finalize_stream.si(document_id),
    )
    try:
        result = workflow.apply_async()
    except Exception:
        AudioSegment.objects.filter(
            pk__in=segment_ids,
            status=AudioSegment.Status.PROCESSING,
        ).update(status=AudioSegment.Status.PENDING)
        Document.objects.filter(pk=document_id).update(
            stream_is_building=False,
            status=Document.Status.READY
            if AudioSegment.objects.filter(
                document_id=document_id,
                status=AudioSegment.Status.READY,
            ).exists()
            else Document.Status.PENDING,
            updated_at=timezone.now(),
        )
        raise
    return {
        "document_id": document_id,
        "queued": len(segment_ids),
        "workflow_id": result.id,
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
        document.stream_is_building = False
        document.save(
            update_fields=("stream_is_building", "updated_at")
        )
        window = queue_stream_window(document.pk)
        return {
            "document_id": document.pk,
            "chunks": len(text_segments),
            "queued": window["queued"],
            "workflow_id": window.get("workflow_id"),
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
        _refresh_book_status(document)
        raise


def dispatch_audio_generation(document):
    try:
        return generate_audio.delay(document.pk)
    except Exception:
        return generate_audio.apply(args=(document.pk,))


@shared_task
def generate_ai_result(result_id):
    result = AIResult.objects.select_related("document", "book").get(pk=result_id)
    result.status = AIResult.Status.PROCESSING
    result.error_message = ""
    result.save(update_fields=("status", "error_message"))
    try:
        if result.book_id:
            parts = result.book.parts.order_by("book_order")
            source_text = "\n\n".join(part.extracted_text for part in parts)
            title = result.book.title
        else:
            source_text = result.document.extracted_text
            title = result.document.title

        if result.operation == AIResult.Operation.SUMMARY:
            content, model_name = summarize_text(source_text, title)
        else:
            content, model_name = translate_text(
                source_text,
                result.target_language,
            )
        result.content = content
        result.model_name = model_name
        result.status = AIResult.Status.READY
        result.completed_at = timezone.now()
        result.save(
            update_fields=(
                "content",
                "model_name",
                "status",
                "completed_at",
            )
        )
        return {"result_id": result.pk, "status": result.status}
    except Exception as exc:
        result.status = AIResult.Status.FAILED
        result.error_message = str(exc)[:2000]
        result.save(update_fields=("status", "error_message"))
        raise


def dispatch_ai_generation(result):
    try:
        return generate_ai_result.delay(result.pk)
    except Exception:
        return generate_ai_result.apply(args=(result.pk,))
