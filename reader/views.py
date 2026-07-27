import mimetypes
from pathlib import PurePosixPath

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import Count
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.text import slugify
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView

from .forms import DocumentForm, RegenerateAudioForm
from .models import AudioSegment, Document, Voice
from .services.extractors import ExtractionError, extract_text, source_type_for
from .services.streaming import tokenize_display_text
from .services.text_preparation import prepare_for_speech
from .services.tts import synthesize_segment
from .tasks import dispatch_audio_generation


def healthcheck(request):
    connection.ensure_connection()
    return JsonResponse({"status": "ok"})


@login_required
def private_media(request, path):
    media_path = PurePosixPath(path)
    if media_path.is_absolute() or ".." in media_path.parts:
        raise Http404

    storage_name = media_path.as_posix()
    owns_document_file = Document.objects.filter(owner=request.user).filter(
        models_q(original_file=storage_name) | models_q(audio_file=storage_name)
    ).exists()
    owns_segment_file = AudioSegment.objects.filter(
        document__owner=request.user,
        audio_file=storage_name,
    ).exists()
    if not (owns_document_file or owns_segment_file):
        raise Http404

    try:
        media_file = default_storage.open(storage_name, "rb")
    except (FileNotFoundError, OSError):
        raise Http404

    content_type, _ = mimetypes.guess_type(storage_name)
    return FileResponse(
        media_file,
        content_type=content_type or "application/octet-stream",
        filename=media_path.name,
    )


class RegisterView(CreateView):
    form_class = UserCreationForm
    template_name = "registration/register.html"
    success_url = reverse_lazy("reader:dashboard")

    def dispatch(self, request, *args, **kwargs):
        if not settings.ALLOW_PUBLIC_REGISTRATION:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "reader/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        documents = Document.objects.filter(owner=self.request.user).select_related(
            "voice", "reading_progress"
        )
        counts = documents.aggregate(
            total=Count("id"),
            ready=Count("id", filter=models_q(status=Document.Status.READY)),
            processing=Count(
                "id",
                filter=models_q(
                    status__in=(Document.Status.PENDING, Document.Status.PROCESSING)
                ),
            ),
        )
        context.update(counts)
        context["characters"] = sum(doc.char_count for doc in documents)
        context["recent_documents"] = documents[:6]
        return context


def models_q(**kwargs):
    from django.db.models import Q

    return Q(**kwargs)


class DocumentListView(LoginRequiredMixin, ListView):
    template_name = "reader/document_list.html"
    context_object_name = "documents"
    paginate_by = 12

    def get_queryset(self):
        queryset = Document.objects.filter(owner=self.request.user).select_related(
            "voice", "reading_progress"
        )
        query = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        if query:
            queryset = queryset.filter(title__icontains=query)
        if status in Document.Status.values:
            queryset = queryset.filter(status=status)
        return queryset


class DocumentCreateView(LoginRequiredMixin, FormView):
    form_class = DocumentForm
    template_name = "reader/document_form.html"

    def form_valid(self, form):
        uploaded = form.cleaned_data["original_file"]
        text = form.cleaned_data["text"].strip()
        try:
            if uploaded:
                text = extract_text(uploaded)
                uploaded.seek(0)
        except ExtractionError as exc:
            form.add_error("original_file", str(exc))
            return self.form_invalid(form)

        if not text:
            form.add_error("original_file", "Nenhum texto legível foi encontrado.")
            return self.form_invalid(form)
        if len(text) > settings.MAX_CHARACTERS_PER_DOCUMENT:
            form.add_error(
                "original_file",
                f"O conteúdo extraído excede {settings.MAX_CHARACTERS_PER_DOCUMENT:,} caracteres.",
            )
            return self.form_invalid(form)

        document = Document.objects.create(
            owner=self.request.user,
            title=form.cleaned_data["title"],
            source_type=source_type_for(uploaded.name) if uploaded else Document.SourceType.TEXT,
            original_file=uploaded,
            extracted_text=text,
            voice=form.cleaned_data["voice"],
            speed=form.cleaned_data["speed"],
            reading_mode=form.cleaned_data["reading_mode"],
        )
        dispatch_audio_generation(document)
        messages.success(
            self.request,
            "Documento adicionado. A geração do áudio começou em segundo plano.",
        )
        return redirect(document)


class OwnedDocumentMixin(LoginRequiredMixin):
    def get_queryset(self):
        return Document.objects.filter(owner=self.request.user)


class DocumentDetailView(OwnedDocumentMixin, DetailView):
    template_name = "reader/document_detail.html"
    context_object_name = "document"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("voice", "reading_progress")
            .prefetch_related("segments")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["regenerate_form"] = RegenerateAudioForm(document=self.object)
        context["reading_tokens"] = tokenize_display_text(
            self.object.extracted_text
        )
        context["stream_available"] = bool(
            self.object.audio_file
            or self.object.segments.filter(
                status="ready"
            ).exclude(audio_file="").exists()
        )
        return context


class DocumentDeleteView(OwnedDocumentMixin, DeleteView):
    template_name = "reader/document_confirm_delete.html"
    success_url = reverse_lazy("reader:document-list")

    def form_valid(self, form):
        document = self.get_object()
        if document.original_file:
            document.original_file.delete(save=False)
        if document.audio_file:
            document.audio_file.delete(save=False)
        for segment in document.segments.exclude(audio_file=""):
            segment.audio_file.delete(save=False)
        messages.success(self.request, "Documento removido da biblioteca.")
        return super().form_valid(form)


def regenerate_document(request, pk):
    if request.method != "POST" or not request.user.is_authenticated:
        raise Http404
    document = Document.objects.filter(owner=request.user, pk=pk).first()
    if not document:
        raise Http404
    if (
        document.status != Document.Status.PROCESSING
        and not document.stream_is_building
    ):
        if request.POST.get("voice"):
            form = RegenerateAudioForm(request.POST, document=document)
            if not form.is_valid():
                messages.error(
                    request,
                    "Não foi possível alterar a narração. Revise as opções escolhidas.",
                )
                return redirect(document)
            document.voice = form.cleaned_data["voice"]
            document.speed = form.cleaned_data["speed"]
            document.reading_mode = form.cleaned_data["reading_mode"]
        document.status = Document.Status.PENDING
        document.error_message = ""
        document.save(
            update_fields=(
                "voice",
                "speed",
                "reading_mode",
                "status",
                "error_message",
                "updated_at",
            )
        )
        dispatch_audio_generation(document)
        messages.info(
            request,
            f"O áudio entrou novamente na fila com a voz {document.voice.name}.",
        )
    return redirect(document)


def voice_preview(request, pk):
    if not request.user.is_authenticated:
        raise Http404
    voice = Voice.objects.filter(pk=pk, is_active=True).first()
    if not voice:
        raise Http404
    preview_dir = settings.MEDIA_ROOT / "voice_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_name = slugify(f"{voice.provider}-{voice.code}") or f"voice-{voice.pk}"
    preview_path = preview_dir / f"{preview_name}.wav"
    if not preview_path.exists():
        sample = (
            f"Olá, eu sou {voice.name}. "
            "Esta é uma amostra da minha voz para acompanhar suas leituras."
        )
        synthesize_segment(
            prepare_for_speech(sample, Document.ReadingMode.NATURAL),
            preview_path,
            voice.code,
            165,
            voice.provider,
        )
    return FileResponse(
        preview_path.open("rb"),
        content_type="audio/wav",
        filename=f"amostra-{voice.code}.wav",
    )
