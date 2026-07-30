import hashlib
import mimetypes
from pathlib import PurePosixPath

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import Count
from django.db import connection, transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView

from .forms import (
    BookForm,
    DocumentForm,
    DriveImportForm,
    RegenerateAudioForm,
    RegistrationForm,
    TranslationForm,
)
from .models import AIResult, AudioSegment, Book, Document, Voice
from .services.community import document_creation_limit_error
from .services.extractors import (
    ExtractionError,
    extract_text,
    source_type_for,
)
from .services.google_drive import GoogleDriveImportError, download_selected_file
from .services.kindle import document_as_epub
from .services.language_detection import looks_like_english
from .services.runtime_config import ai_is_configured, get_app_configuration
from .services.streaming import tokenize_display_text
from .services.text_preparation import prepare_for_speech
from .services.tts import synthesize_segment
from .services.usage import (
    current_usage,
    reading_limit_error,
    register_ai_request,
    register_reading,
)
from .tasks import (
    dispatch_ai_generation,
    dispatch_audio_generation,
    dispatch_book_preparation,
    ensure_book_audio_window,
)


def healthcheck(request):
    connection.ensure_connection()
    return JsonResponse({"status": "ok"})


@login_required
@xframe_options_sameorigin
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
    owns_book_file = Book.objects.filter(
        owner=request.user,
        original_file=storage_name,
    ).exists()
    if not (owns_document_file or owns_segment_file or owns_book_file):
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
    form_class = RegistrationForm
    template_name = "registration/register.html"
    success_url = reverse_lazy("reader:dashboard")

    def dispatch(self, request, *args, **kwargs):
        if not settings.ALLOW_PUBLIC_REGISTRATION:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(
            self.request,
            self.object,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        return response


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "reader/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        documents = Document.objects.filter(
            owner=self.request.user,
            book__isnull=True,
        ).select_related("voice", "reading_progress")
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
        books = Book.objects.filter(owner=self.request.user)
        context["recent_books"] = books[:4]
        context["total_items"] = counts["total"] + books.count()
        context["usage"] = current_usage(self.request.user)
        context["app_configuration"] = get_app_configuration()
        return context


def models_q(**kwargs):
    from django.db.models import Q

    return Q(**kwargs)


class DocumentListView(LoginRequiredMixin, ListView):
    template_name = "reader/document_list.html"
    context_object_name = "documents"
    paginate_by = 12

    def get_queryset(self):
        queryset = Document.objects.filter(
            owner=self.request.user,
            book__isnull=True,
        ).select_related("voice", "reading_progress")
        query = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        if query:
            queryset = queryset.filter(title__icontains=query)
        if status in Document.Status.values:
            queryset = queryset.filter(status=status)
        return queryset


class BookListView(LoginRequiredMixin, ListView):
    template_name = "reader/book_list.html"
    context_object_name = "books"
    paginate_by = 12

    def get_queryset(self):
        queryset = Book.objects.filter(owner=self.request.user).prefetch_related(
            "parts__reading_progress"
        )
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(title__icontains=query)
        return queryset


class BookCreateView(LoginRequiredMixin, FormView):
    form_class = BookForm
    template_name = "reader/book_form.html"

    def form_valid(self, form):
        limit_error = document_creation_limit_error(self.request.user)
        if limit_error:
            form.add_error(None, limit_error)
            return self.form_invalid(form)
        monthly_error = reading_limit_error(self.request.user)
        if monthly_error:
            form.add_error(None, monthly_error)
            return self.form_invalid(form)

        uploaded = form.cleaned_data["original_file"]
        source_type = source_type_for(uploaded.name)
        with transaction.atomic():
            book = Book.objects.create(
                owner=self.request.user,
                title=form.cleaned_data["title"],
                original_file=uploaded,
                source_type=source_type,
                voice=form.cleaned_data["voice"],
                speed=form.cleaned_data["speed"],
                reading_mode=form.cleaned_data["reading_mode"],
                status=Book.Status.PROCESSING,
            )
            register_reading(self.request.user, 0)

        dispatch_book_preparation(book)
        messages.success(
            self.request,
            "Livro recebido. A divisão em partes e a playlist estão sendo "
            "preparadas em segundo plano.",
        )
        return redirect(book)


class OwnedBookMixin(LoginRequiredMixin):
    def get_queryset(self):
        return Book.objects.filter(owner=self.request.user)


class BookDetailView(OwnedBookMixin, DetailView):
    template_name = "reader/book_detail.html"
    context_object_name = "book"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("voice")
            .prefetch_related("parts__reading_progress", "ai_results")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["parts"] = self.object.parts.order_by("book_order")
        context["ai_enabled"] = ai_is_configured()
        context["summary_results"] = self.object.ai_results.filter(
            operation=AIResult.Operation.SUMMARY
        )
        return context


class BookDeleteView(OwnedBookMixin, DeleteView):
    template_name = "reader/book_confirm_delete.html"
    success_url = reverse_lazy("reader:book-list")

    def form_valid(self, form):
        book = self.get_object()
        if book.original_file:
            book.original_file.delete(save=False)
        for document in book.parts.all():
            if document.original_file:
                document.original_file.delete(save=False)
            if document.audio_file:
                document.audio_file.delete(save=False)
            for segment in document.segments.exclude(audio_file=""):
                segment.audio_file.delete(save=False)
        messages.success(self.request, "Livro removido da biblioteca.")
        return super().form_valid(form)


class DocumentCreateView(LoginRequiredMixin, FormView):
    form_class = DocumentForm
    template_name = "reader/document_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["google_drive_enabled"] = settings.GOOGLE_DRIVE_ENABLED
        if settings.GOOGLE_DRIVE_ENABLED:
            context["google_drive_client_id"] = settings.GOOGLE_OAUTH_CLIENT_ID
            context["google_drive_api_key"] = settings.GOOGLE_DRIVE_API_KEY
            context["google_cloud_project_number"] = (
                settings.GOOGLE_CLOUD_PROJECT_NUMBER
            )
        return context

    def form_valid(self, form):
        limit_error = document_creation_limit_error(self.request.user)
        if limit_error:
            form.add_error(None, limit_error)
            return self.form_invalid(form)
        monthly_error = reading_limit_error(self.request.user)
        if monthly_error:
            form.add_error(None, monthly_error)
            return self.form_invalid(form)

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
        character_limit = get_app_configuration().book_part_characters
        if len(text) > character_limit:
            form.add_error(
                "original_file",
                "O conteúdo é grande demais para um documento. "
                f"Envie-o em Livros; o limite aqui é {character_limit:,} caracteres.",
            )
            return self.form_invalid(form)

        with transaction.atomic():
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
            register_reading(self.request.user, len(text))
        dispatch_audio_generation(document)
        messages.success(
            self.request,
            "Documento adicionado. A geração do áudio começou em segundo plano.",
        )
        return redirect(document)


@login_required
@require_POST
def import_from_google_drive(request):
    if not settings.GOOGLE_DRIVE_ENABLED:
        raise Http404

    limit_error = document_creation_limit_error(request.user)
    if limit_error:
        return JsonResponse({"error": limit_error}, status=429)
    monthly_error = reading_limit_error(request.user)
    if monthly_error:
        return JsonResponse({"error": monthly_error}, status=429)

    form = DriveImportForm(request.POST)
    if not form.is_valid():
        return JsonResponse(
            {
                "error": "Revise as opções da leitura.",
                "fields": form.errors.get_json_data(),
            },
            status=400,
        )

    try:
        uploaded = download_selected_file(
            form.cleaned_data["file_id"],
            form.cleaned_data["access_token"],
        )
        text = extract_text(uploaded)
        uploaded.seek(0)
    except (GoogleDriveImportError, ExtractionError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    if not text:
        return JsonResponse(
            {"error": "Nenhum texto legível foi encontrado no arquivo."},
            status=400,
        )
    character_limit = get_app_configuration().book_part_characters
    if len(text) > character_limit:
        return JsonResponse(
            {
                "error": (
                    "O conteúdo extraído deve ser importado como livro porque excede "
                    f"{character_limit:,} caracteres."
                )
            },
            status=400,
        )

    with transaction.atomic():
        document = Document.objects.create(
            owner=request.user,
            title=form.cleaned_data["title"],
            source_type=source_type_for(uploaded.name),
            original_file=uploaded,
            extracted_text=text,
            voice=form.cleaned_data["voice"],
            speed=form.cleaned_data["speed"],
            reading_mode=form.cleaned_data["reading_mode"],
        )
        register_reading(request.user, len(text))
    dispatch_audio_generation(document)
    messages.success(
        request,
        "Arquivo importado do Google Drive. A geração do áudio começou.",
    )
    return JsonResponse({"redirect_url": document.get_absolute_url()}, status=201)


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
            .select_related("voice", "reading_progress", "book")
            .prefetch_related("segments")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.object.book_id and self.object.book.status == Book.Status.READY:
            ensure_book_audio_window(self.object)
            self.object.refresh_from_db()
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
        context["ai_enabled"] = ai_is_configured()
        context["translation_available"] = looks_like_english(
            self.object.extracted_text
        )
        context["translation_form"] = TranslationForm()
        context["ai_results"] = self.object.ai_results.all()
        if self.object.book_id:
            siblings = self.object.book.parts.order_by("book_order")
            context["previous_part"] = siblings.filter(
                book_order__lt=self.object.book_order
            ).last()
            context["next_part"] = siblings.filter(
                book_order__gt=self.object.book_order
            ).first()
            uses_derived_pdf = bool(self.object.original_file)
            context["pdf_file"] = (
                (self.object.original_file or self.object.book.original_file)
                if self.object.source_type == Document.SourceType.PDF
                else None
            )
            context["pdf_download_file"] = (
                self.object.book.original_file
                if self.object.book.source_type == Document.SourceType.PDF
                else None
            )
            if uses_derived_pdf:
                context["pdf_view_page_start"] = 1
                context["pdf_view_page_end"] = (
                    (self.object.page_end - self.object.page_start + 1)
                    if self.object.page_start and self.object.page_end
                    else 0
                )
            else:
                context["pdf_view_page_start"] = self.object.page_start or 1
                context["pdf_view_page_end"] = self.object.page_end or 0
        else:
            context["pdf_file"] = (
                self.object.original_file
                if self.object.source_type == Document.SourceType.PDF
                else None
            )
            context["pdf_download_file"] = context["pdf_file"]
            context["pdf_view_page_start"] = self.object.page_start or 1
            context["pdf_view_page_end"] = self.object.page_end or 0
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


@login_required
def export_document_to_kindle(request, pk):
    document = Document.objects.filter(owner=request.user, pk=pk).first()
    if not document:
        raise Http404
    try:
        output = document_as_epub(document)
    except Exception:
        messages.error(
            request,
            "Não foi possível preparar este documento para o Kindle.",
        )
        return redirect(document)
    filename = f"{slugify(document.title) or f'documento-{document.pk}'}.epub"
    return FileResponse(
        output,
        as_attachment=True,
        filename=filename,
        content_type="application/epub+zip",
    )


def regenerate_document(request, pk):
    if request.method != "POST" or not request.user.is_authenticated:
        raise Http404
    document = Document.objects.filter(owner=request.user, pk=pk).first()
    if not document:
        raise Http404
    monthly_error = reading_limit_error(request.user)
    if monthly_error:
        messages.error(request, monthly_error)
        return redirect(document)
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
        register_reading(request.user, document.char_count)
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


def _queue_ai_result(request, *, operation, document=None, book=None):
    configuration = get_app_configuration()
    if not ai_is_configured(configuration):
        messages.error(
            request,
            "A IA ainda não foi configurada pelo administrador.",
        )
        return redirect(book or document)

    if book:
        source_text = "\n\n".join(
            book.parts.order_by("book_order").values_list(
                "extracted_text",
                flat=True,
            )
        )
    else:
        source_text = document.extracted_text
    if len(source_text) > configuration.ai_max_input_characters:
        messages.error(
            request,
            "O conteúdo ultrapassa o limite administrativo de "
            f"{configuration.ai_max_input_characters:,} caracteres para IA.",
        )
        return redirect(book or document)

    target_language = ""
    if operation == AIResult.Operation.TRANSLATION:
        if book:
            raise Http404
        if not looks_like_english(source_text):
            messages.error(
                request,
                "A tradução está disponível somente para textos identificados em inglês.",
            )
            return redirect(document)
        form = TranslationForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Escolha um idioma de destino válido.")
            return redirect(document)
        target_language = form.cleaned_data["target_language"]

    digest_source = "\0".join(
        (
            source_text,
            operation,
            target_language,
            configuration.ai_provider,
            configuration.openai_model,
            configuration.azure_openai_deployment,
        )
    )
    input_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    existing = AIResult.objects.filter(
        owner=request.user,
        operation=operation,
        target_language=target_language,
        input_hash=input_hash,
        status=AIResult.Status.READY,
    ).first()
    if existing:
        messages.info(request, "Resultado recuperado do cache.")
        return redirect("reader:ai-result", pk=existing.pk)

    result = AIResult.objects.create(
        owner=request.user,
        document=document,
        book=book,
        operation=operation,
        target_language=target_language,
        input_hash=input_hash,
        provider=configuration.ai_provider,
    )
    register_ai_request(request.user)
    dispatch_ai_generation(result)
    messages.info(
        request,
        f"{result.get_operation_display()} colocado na fila.",
    )
    return redirect("reader:ai-result", pk=result.pk)


@login_required
@require_POST
def summarize_document(request, pk):
    document = Document.objects.filter(owner=request.user, pk=pk).first()
    if not document:
        raise Http404
    return _queue_ai_result(
        request,
        operation=AIResult.Operation.SUMMARY,
        document=document,
    )


@login_required
@require_POST
def translate_document(request, pk):
    document = Document.objects.filter(owner=request.user, pk=pk).first()
    if not document:
        raise Http404
    return _queue_ai_result(
        request,
        operation=AIResult.Operation.TRANSLATION,
        document=document,
    )


@login_required
@require_POST
def summarize_book(request, pk):
    book = Book.objects.filter(owner=request.user, pk=pk).first()
    if not book:
        raise Http404
    return _queue_ai_result(
        request,
        operation=AIResult.Operation.SUMMARY,
        book=book,
    )


class AIResultDetailView(LoginRequiredMixin, DetailView):
    template_name = "reader/ai_result.html"
    context_object_name = "result"

    def get_queryset(self):
        return AIResult.objects.filter(owner=self.request.user).select_related(
            "document",
            "book",
        )
