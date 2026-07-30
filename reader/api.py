from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import AudioSegment, Document, ReadingProgress, Voice
from .services.streaming import build_word_timings
from .services.usage import reading_limit_error, register_reading
from .serializers import (
    AudioSegmentSerializer,
    DocumentCreateSerializer,
    DocumentSerializer,
    ProgressSerializer,
    VoiceSerializer,
)
from .tasks import dispatch_audio_generation, queue_stream_window


class VoiceViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = VoiceSerializer
    pagination_class = None

    def get_queryset(self):
        return Voice.objects.filter(is_active=True)


class DocumentViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    def get_queryset(self):
        queryset = (
            Document.objects.filter(owner=self.request.user)
            .select_related("voice", "reading_progress")
            .prefetch_related("segments")
        )
        if self.action == "list":
            queryset = queryset.filter(book__isnull=True)
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return DocumentCreateSerializer
        return DocumentSerializer

    @action(detail=True, methods=("post",))
    def generate(self, request, pk=None):
        document = self.get_object()
        limit_error = reading_limit_error(request.user)
        if limit_error:
            return Response(
                {"detail": limit_error},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        if (
            document.status == Document.Status.PROCESSING
            or document.stream_is_building
        ):
            return Response(
                {"detail": "O áudio já está sendo processado."},
                status=status.HTTP_409_CONFLICT,
            )
        document.status = Document.Status.PENDING
        document.error_message = ""
        document.save(update_fields=("status", "error_message", "updated_at"))
        register_reading(request.user, document.char_count)
        dispatch_audio_generation(document)
        return Response(DocumentSerializer(document, context={"request": request}).data)

    @action(detail=True, methods=("get",))
    def stream(self, request, pk=None):
        document = self.get_object()
        segments = list(document.segments.all())
        progressive_segments = [
            segment
            for segment in segments
            if segment.audio_file or segment.status != AudioSegment.Status.PENDING
        ]

        if progressive_segments:
            chunks = AudioSegmentSerializer(
                segments,
                many=True,
                context={"request": request},
            ).data
        elif document.audio_file:
            duration = float(document.duration_seconds or 0)
            chunks = [
                {
                    "order": 0,
                    "text": document.extracted_text,
                    "status": AudioSegment.Status.READY,
                    "start_char": 0,
                    "end_char": document.char_count,
                    "duration_seconds": duration,
                    "start_seconds": 0,
                    "end_seconds": duration,
                    "audio_url": request.build_absolute_uri(document.audio_file.url),
                    "word_timings": build_word_timings(
                        document.extracted_text, duration
                    ),
                    "legacy": True,
                }
            ]
        else:
            chunks = AudioSegmentSerializer(
                segments,
                many=True,
                context={"request": request},
            ).data

        return Response(
            {
                "document_id": document.pk,
                "status": document.status,
                "building": document.stream_is_building,
                "complete": bool(
                    document.audio_file
                    or (
                        segments
                        and not any(
                            segment.status != AudioSegment.Status.READY
                            for segment in segments
                        )
                    )
                ),
                "char_count": document.char_count,
                "duration_seconds": document.duration_seconds,
                "chunks": chunks,
            }
        )

    @action(detail=True, methods=("post",), url_path="stream-prepare")
    def stream_prepare(self, request, pk=None):
        document = self.get_object()
        try:
            start_order = max(0, int(request.data.get("order", 0)))
        except (TypeError, ValueError):
            return Response(
                {"detail": "A ordem inicial do trecho é inválida."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if document.status == Document.Status.FAILED:
            return Response(
                {"detail": "A geração deste documento falhou."},
                status=status.HTTP_409_CONFLICT,
            )
        result = queue_stream_window(document.pk, start_order=start_order)
        return Response(result, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=("get", "put", "patch"))
    def progress(self, request, pk=None):
        document = self.get_object()
        progress, _ = ReadingProgress.objects.get_or_create(
            user=request.user, document=document
        )
        if request.method == "GET":
            return Response(ProgressSerializer(progress).data)
        serializer = ProgressSerializer(
            progress, data=request.data, partial=request.method == "PATCH"
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
