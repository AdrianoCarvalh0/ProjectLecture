from io import BytesIO
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

import fitz
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import (
    AIResult,
    AppConfiguration,
    AudioCache,
    AudioSegment,
    Book,
    Document,
    MonthlyUsage,
    ReadingProgress,
    Voice,
)
from .services.extractors import (
    extract_pdf_pages,
    extract_text,
    source_type_for,
)
from .services.language_detection import looks_like_english
from .services.streaming import (
    build_word_timings,
    map_spoken_word_timings,
    tokenize_display_text,
)
from .services.text_preparation import prepare_for_speech
from .services.tts import azure_ssml, finalize_azure_timings, split_text
from .tasks import (
    _synthesize_with_cache,
    ensure_book_audio_window,
    generate_audio,
    prepare_book,
)

User = get_user_model()


class BaseReaderTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("leitor", password="senha-forte-123")
        self.voice = Voice.objects.create(
            name="Português Brasil", code="pt-br", language="pt-BR"
        )

    def make_document(self, owner=None, **kwargs):
        return Document.objects.create(
            owner=owner or self.user,
            title=kwargs.pop("title", "Documento de teste"),
            extracted_text=kwargs.pop("extracted_text", "Um texto para leitura."),
            voice=self.voice,
            **kwargs,
        )


class ExtractorTests(TestCase):
    def test_extracts_utf8_txt(self):
        upload = SimpleUploadedFile(
            "exemplo.txt", "Primeira linha.\nSegunda linha.".encode(), content_type="text/plain"
        )
        self.assertEqual(extract_text(upload), "Primeira linha.\nSegunda linha.")
        self.assertEqual(source_type_for(upload.name), "txt")

    def test_splits_text_into_bounded_segments(self):
        text = "Primeira frase. " + ("palavra " * 200) + "Fim."
        segments = split_text(text, max_chars=120)
        self.assertGreater(len(segments), 2)
        self.assertTrue(all(len(segment.text) <= 120 for segment in segments))
        self.assertEqual(segments[0].start_char, 0)

    def test_prepares_academic_notation_for_natural_reading(self):
        prepared = prepare_for_speech(
            "Silva et al. [12] encontrou 25% no vol. 3.", mode="academic"
        )
        self.assertIn("e colaboradores", prepared)
        self.assertIn("referência 12", prepared)
        self.assertIn("25 por cento", prepared)
        self.assertIn("volume 3", prepared)

    def test_ignores_decorative_underscores_during_speech(self):
        prepared = prepare_for_speech(
            "Introdução ___________ conteúdo principal.", mode="natural"
        )
        self.assertEqual(prepared, "Introdução conteúdo principal.")

    def test_ignores_isolated_page_numbers_during_speech(self):
        prepared = prepare_for_speech(
            "Texto da página anterior.\n- 12 -\nTexto da página seguinte.",
            mode="natural",
        )
        self.assertEqual(
            prepared,
            "Texto da página anterior. Texto da página seguinte.",
        )

    def test_pdf_extraction_removes_repeated_headers_and_page_numbers(self):
        pdf = fitz.open()
        for page_number in range(1, 4):
            page = pdf.new_page()
            page.insert_text((72, 35), "Periódico de Pesquisa Aplicada")
            page.insert_text((28, 80), f"- {page_number} -")
            page.insert_text(
                (72, 150),
                f"Conteúdo científico exclusivo da página {page_number}.",
            )
            page.insert_text((540, 815), str(page_number))
        upload = SimpleUploadedFile(
            "artigo.pdf",
            pdf.tobytes(),
            content_type="application/pdf",
        )
        pages = extract_pdf_pages(upload)
        combined = "\n".join(page.text for page in pages)
        self.assertNotIn("Periódico de Pesquisa Aplicada", combined)
        self.assertNotRegex(combined, r"(?m)^[123]$")
        self.assertIn("Conteúdo científico exclusivo", combined)

    def test_detects_english_without_external_service(self):
        self.assertTrue(
            looks_like_english(
                "This study examines the results of a method for reading. "
                "The findings are discussed with evidence from the literature."
            )
        )
        self.assertFalse(
            looks_like_english(
                "Este estudo examina os resultados de um método de leitura. "
                "As conclusões são discutidas com base na literatura."
            )
        )

    def test_builds_safe_azure_ssml_with_requested_speed(self):
        ssml = azure_ssml(
            "Pesquisa & desenvolvimento <aberto>.",
            "pt-BR-FranciscaNeural",
            204,
        )
        self.assertIn('rate="+20%"', ssml)
        self.assertIn("Pesquisa &amp; desenvolvimento &lt;aberto&gt;.", ssml)
        self.assertIn('name="pt-BR-FranciscaNeural"', ssml)


class StreamingTests(TestCase):
    def test_tokenizes_text_without_losing_offsets_or_whitespace(self):
        text = "Uma linha.\nOutra linha!"
        tokens = tokenize_display_text(text)
        self.assertEqual("".join(token.text for token in tokens), text)
        words = [token for token in tokens if token.is_word]
        self.assertEqual(words[2].text, "Outra")
        self.assertEqual(words[2].start_char, text.index("Outra"))

    def test_word_timings_cover_the_complete_audio_duration(self):
        timings = build_word_timings("Texto curto, com pausa.", 8.5)
        self.assertEqual(timings[0]["start"], 0)
        self.assertEqual(timings[-1]["end"], 8.5)
        self.assertTrue(
            all(left["end"] <= right["start"] for left, right in zip(timings, timings[1:]))
        )

    def test_maps_neural_timings_and_preserves_punctuation_pauses(self):
        timings = map_spoken_word_timings(
            "Olá, mundo. Depois",
            [
                ["Olá,", 0.25, 1.1],
                ["mundo.", 1.1, 2.9],
                ["Depois", 2.9, 3.7],
            ],
            3.7,
        )
        self.assertEqual(timings[0]["start"], 0.25)
        self.assertEqual(timings[0]["end"], 1.1)
        self.assertEqual(timings[1]["end"], 2.9)
        self.assertEqual(timings[2]["start"], 2.9)

    def test_maps_spoken_expansion_back_to_the_displayed_word(self):
        timings = map_spoken_word_timings(
            "O índice foi 25%.",
            [
                ["O", 0.1, 0.3],
                ["índice", 0.3, 0.8],
                ["foi", 0.8, 1.0],
                ["25", 1.0, 1.2],
                ["por", 1.2, 1.4],
                ["cento.", 1.4, 2.0],
            ],
            2.0,
        )
        self.assertEqual(len(timings), 4)
        self.assertEqual(timings[-1]["text"], "25%.")
        self.assertEqual(timings[-1]["end"], 2.0)

    def test_azure_boundaries_keep_punctuation_pause_on_previous_word(self):
        timings = finalize_azure_timings(
            [
                {"text": "Olá", "start": 0.2},
                {"text": ",", "start": 0.7},
                {"text": "mundo", "start": 1.4},
                {"text": ".", "start": 2.0},
                {"text": "Depois", "start": 2.8},
            ],
            3.5,
        )
        self.assertEqual(timings[0], ["Olá", 0.2, 1.4])
        self.assertEqual(timings[1], ["mundo", 1.4, 2.8])
        self.assertEqual(timings[2], ["Depois", 2.8, 3.5])


class VoiceCatalogTests(TestCase):
    @override_settings(TTS_ENVIRONMENT="production")
    def test_azure_configuration_activates_exactly_four_cloud_voices(self):
        call_command("seed_voices", stdout=StringIO())
        active = Voice.objects.filter(is_active=True)
        self.assertEqual(active.count(), 4)
        self.assertFalse(active.exclude(provider=Voice.Provider.AZURE).exists())
        self.assertEqual(active.filter(is_default=True).get().name, "Francisca")

    @override_settings(TTS_ENVIRONMENT="development")
    def test_missing_azure_configuration_restores_local_catalog(self):
        call_command("seed_voices", stdout=StringIO())
        active = Voice.objects.filter(is_active=True)
        self.assertEqual(active.count(), 3)
        self.assertFalse(active.exclude(provider=Voice.Provider.KOKORO).exists())
        self.assertEqual(active.filter(is_default=True).get().name, "Lia")


class BookPreparationTests(BaseReaderTest):
    def test_prepares_small_physical_pdf_parts_without_audio(self):
        pdf = fitz.open()
        for page_number in range(1, 13):
            page = pdf.new_page()
            page.insert_text(
                (72, 250),
                (
                    f"Capítulo e conteúdo exclusivo da página {page_number}. "
                    "Este trecho deve integrar a leitura acadêmica."
                ),
            )
        AppConfiguration.objects.create(
            book_part_characters=100_000,
            book_part_pages=5,
        )

        with tempfile.TemporaryDirectory() as media_dir, self.settings(
            MEDIA_ROOT=Path(media_dir)
        ):
            book = Book.objects.create(
                owner=self.user,
                title="Livro em PDF",
                original_file=SimpleUploadedFile(
                    "livro.pdf",
                    pdf.tobytes(),
                    content_type="application/pdf",
                ),
                source_type=Document.SourceType.PDF,
                voice=self.voice,
            )
            result = prepare_book.run(book.pk)
            book.refresh_from_db()
            parts = list(book.parts.order_by("book_order"))

            self.assertEqual(result["parts"], 3)
            self.assertEqual(book.status, Book.Status.READY)
            self.assertEqual(book.page_count, 12)
            self.assertEqual(
                [part.status for part in parts],
                [Document.Status.PENDING] * 3,
            )
            self.assertFalse(
                AudioSegment.objects.filter(document__book=book).exists()
            )
            for part in parts:
                self.assertTrue(part.original_file)
                with fitz.open(part.original_file.path) as physical_part:
                    self.assertLessEqual(physical_part.page_count, 5)

    @patch("reader.tasks.dispatch_audio_generation")
    def test_queues_only_the_opened_book_part(self, dispatch):
        book = Book.objects.create(
            owner=self.user,
            title="Livro sob demanda",
            original_file=SimpleUploadedFile("livro.txt", b"livro"),
            source_type=Document.SourceType.TXT,
            voice=self.voice,
            status=Book.Status.READY,
        )
        parts = [
            Document.objects.create(
                owner=self.user,
                book=book,
                book_order=index,
                title=f"Parte {index + 1}",
                source_type=Document.SourceType.TXT,
                extracted_text=f"Conteúdo da parte {index + 1}.",
                voice=self.voice,
            )
            for index in range(4)
        ]

        self.assertEqual(ensure_book_audio_window(parts[1]), 1)
        self.assertEqual(
            list(
                book.parts.order_by("book_order").values_list(
                    "status", flat=True
                )
            ),
            [
                Document.Status.PENDING,
                Document.Status.PROCESSING,
                Document.Status.PENDING,
                Document.Status.PENDING,
            ],
        )
        self.assertEqual(
            [call.args[0].book_order for call in dispatch.call_args_list],
            [1],
        )
        self.assertEqual(ensure_book_audio_window(parts[1]), 0)
        self.assertEqual(dispatch.call_count, 1)

    @override_settings(STREAM_CHUNK_CHARS=40, STREAM_PREFETCH_CHUNKS=2)
    @patch("reader.tasks.queue_stream_window")
    def test_initializes_all_chunks_but_queues_only_one_small_window(
        self, queue_window
    ):
        queue_window.return_value = {
            "queued": 2,
            "workflow_id": "workflow-test",
        }
        document = self.make_document(
            extracted_text=(
                "Primeiro trecho para leitura progressiva. "
                "Segundo trecho para leitura progressiva. "
                "Terceiro trecho para leitura progressiva."
            )
        )

        result = generate_audio.run(document.pk)

        self.assertGreater(document.segments.count(), 2)
        queue_window.assert_called_once_with(document.pk)
        self.assertEqual(result["queued"], 2)
        self.assertFalse(
            document.segments.exclude(status=AudioSegment.Status.PENDING).exists()
        )


class WebViewsTests(BaseReaderTest):
    @patch("reader.views.dispatch_book_preparation")
    def test_queues_book_playlist_preparation_without_generating_audio(
        self, dispatch
    ):
        AppConfiguration.objects.create(
            max_files_per_user=10,
            max_readings_per_user_month=10,
            book_part_characters=10_000,
        )
        content = (
            ("Primeiro capítulo com conteúdo acadêmico. " * 180)
            + "\n\n"
            + ("Segundo capítulo com conteúdo acadêmico. " * 180)
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("reader:book-create"),
            {
                "title": "Livro extenso",
                "original_file": SimpleUploadedFile(
                    "livro.txt",
                    content.encode(),
                    content_type="text/plain",
                ),
                "voice": self.voice.pk,
                "speed": 170,
                "reading_mode": Document.ReadingMode.NATURAL,
            },
        )
        book = Book.objects.get(title="Livro extenso")
        self.assertRedirects(response, book.get_absolute_url())
        self.assertEqual(book.status, Book.Status.PROCESSING)
        self.assertEqual(book.parts.count(), 0)
        dispatch.assert_called_once_with(book)
        self.assertEqual(
            MonthlyUsage.objects.get(user=self.user).readings,
            1,
        )

    def test_monthly_reading_limit_blocks_new_content(self):
        AppConfiguration.objects.create(
            max_files_per_user=10,
            max_readings_per_user_month=1,
        )
        MonthlyUsage.objects.create(
            user=self.user,
            year=2026,
            month=7,
            readings=1,
        )
        self.client.force_login(self.user)
        with patch("reader.services.usage.timezone.localdate") as localdate:
            localdate.return_value = __import__("datetime").date(2026, 7, 30)
            response = self.client.post(
                reverse("reader:document-create"),
                {
                    "title": "Leitura excedente",
                    "text": "Este texto não deve ser criado.",
                    "voice": self.voice.pk,
                    "speed": 170,
                    "reading_mode": Document.ReadingMode.NATURAL,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "limite de 1 leituras")
        self.assertFalse(
            Document.objects.filter(title="Leitura excedente").exists()
        )

    @patch("reader.views.dispatch_ai_generation")
    def test_queues_article_summary_with_configured_ai(self, dispatch):
        configuration = AppConfiguration.objects.create(
            ai_provider=AppConfiguration.AIProvider.OPENAI,
        )
        configuration.set_secret("openai_api_key", "chave-de-teste")
        configuration.save()
        document = self.make_document(title="Artigo para resumir")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                "reader:document-summary",
                kwargs={"pk": document.pk},
            )
        )
        result = AIResult.objects.get(document=document)
        self.assertRedirects(
            response,
            reverse("reader:ai-result", kwargs={"pk": result.pk}),
        )
        self.assertNotEqual(
            configuration.openai_api_key_encrypted,
            "chave-de-teste",
        )
        dispatch.assert_called_once_with(result)

    def test_translation_is_visible_only_for_english_text(self):
        english = self.make_document(
            title="English article",
            extracted_text=(
                "This study presents the results of a reading method. "
                "The conclusions are based on evidence from the literature."
            ),
        )
        portuguese = self.make_document(
            title="Artigo em português",
            extracted_text=(
                "Este estudo apresenta os resultados de um método de leitura. "
                "As conclusões usam evidências encontradas na literatura."
            ),
        )
        self.client.force_login(self.user)
        translation_url = reverse(
            "reader:document-translation",
            kwargs={"pk": english.pk},
        )
        self.assertContains(
            self.client.get(english.get_absolute_url()),
            translation_url,
        )
        self.assertNotContains(
            self.client.get(portuguese.get_absolute_url()),
            reverse(
                "reader:document-translation",
                kwargs={"pk": portuguese.pk},
            ),
        )

    def test_google_popup_can_report_back_to_the_main_window(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(
            response["Cross-Origin-Opener-Policy"],
            "same-origin-allow-popups",
        )

    def test_login_is_required(self):
        response = self.client.get(reverse("reader:dashboard"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('reader:dashboard')}",
        )

    @override_settings(
        GOOGLE_LOGIN_ENABLED=True,
        SOCIALACCOUNT_PROVIDERS={
            "google": {
                "APP": {
                    "client_id": "google-client-id",
                    "secret": "google-client-secret",
                    "key": "",
                }
            }
        },
    )
    def test_login_page_offers_google_when_configured(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Continuar com Google")
        self.assertContains(response, "/contas/google/login/")

    @override_settings(ALLOW_PUBLIC_REGISTRATION=False)
    def test_public_registration_can_be_disabled(self):
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 404)

    @override_settings(ALLOW_PUBLIC_REGISTRATION=False)
    def test_allauth_signup_is_also_closed(self):
        self.client.post(
            "/contas/signup/",
            {
                "username": "cadastro-alternativo",
                "email": "alternativo@example.com",
                "password1": "senha-segura-789",
                "password2": "senha-segura-789",
            },
        )
        self.assertFalse(
            User.objects.filter(username="cadastro-alternativo").exists()
        )

    def test_public_registration_requires_and_saves_unique_email(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "nova-leitora",
                "email": "leitora@example.com",
                "password1": "senha-segura-456",
                "password2": "senha-segura-456",
            },
        )
        self.assertRedirects(response, reverse("reader:dashboard"))
        user = User.objects.get(username="nova-leitora")
        self.assertEqual(user.email, "leitora@example.com")

    def test_library_only_shows_own_documents(self):
        other = User.objects.create_user("outro", password="senha-forte-123")
        own = self.make_document(title="Meu documento")
        self.make_document(owner=other, title="Documento alheio")
        self.client.force_login(self.user)
        response = self.client.get(reverse("reader:document-list"))
        self.assertContains(response, own.title)
        self.assertNotContains(response, "Documento alheio")

    def test_media_file_is_available_only_to_its_owner(self):
        document = self.make_document(
            original_file=SimpleUploadedFile(
                "artigo.txt", b"conteudo privado", content_type="text/plain"
            )
        )
        other = User.objects.create_user("outro", password="senha-forte-123")

        self.client.force_login(self.user)
        response = self.client.get(document.original_file.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

        self.client.force_login(other)
        response = self.client.get(document.original_file.url)
        self.assertEqual(response.status_code, 404)

    def test_pdf_reader_embeds_the_owned_original(self):
        document = self.make_document(
            source_type=Document.SourceType.PDF,
            original_file=SimpleUploadedFile(
                "artigo.pdf",
                b"%PDF-1.4 arquivo de teste",
                content_type="application/pdf",
            ),
        )
        self.client.force_login(self.user)
        response = self.client.get(document.get_absolute_url())
        self.assertContains(response, 'class="pdf-reader"')
        self.assertContains(response, document.original_file.url)
        self.assertContains(response, "js/pdf-reader.js")
        self.assertContains(response, 'data-pdf-mode="true"')
        self.assertNotContains(response, "<iframe")

    @patch("reader.views.ensure_book_audio_window")
    def test_book_part_reader_uses_the_small_derived_pdf(self, ensure_audio):
        book = Book.objects.create(
            owner=self.user,
            title="Livro completo",
            original_file=SimpleUploadedFile(
                "livro-completo.pdf",
                b"%PDF-1.4 livro completo",
                content_type="application/pdf",
            ),
            source_type=Document.SourceType.PDF,
            voice=self.voice,
            status=Book.Status.READY,
        )
        part = self.make_document(
            book=book,
            book_order=0,
            page_start=11,
            page_end=13,
            source_type=Document.SourceType.PDF,
            original_file=SimpleUploadedFile(
                "parte-0001.pdf",
                b"%PDF-1.4 parte pequena",
                content_type="application/pdf",
            ),
        )
        self.client.force_login(self.user)
        response = self.client.get(part.get_absolute_url())

        self.assertContains(response, part.original_file.url)
        self.assertContains(response, book.original_file.url)
        self.assertContains(response, 'data-page-start="1"')
        self.assertContains(response, 'data-page-end="3"')
        ensure_audio.assert_called_once_with(part)

    def test_new_document_uses_visual_voice_picker(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("reader:document-create"))
        self.assertContains(response, 'class="voice-option')
        self.assertContains(response, self.voice.name)
        self.assertContains(
            response, reverse("reader:voice-preview", kwargs={"pk": self.voice.pk})
        )

    def test_document_detail_shows_voice_change_picker(self):
        alternate = Voice.objects.create(
            name="Voz alternativa", code="alternate", language="pt-BR"
        )
        document = self.make_document()
        self.client.force_login(self.user)
        response = self.client.get(document.get_absolute_url())
        self.assertContains(response, "Trocar voz")
        self.assertContains(response, "Escolha uma das quatro vozes")
        self.assertContains(response, self.voice.name)
        self.assertContains(response, alternate.name)
        self.assertContains(response, 'class="reading-word"')
        self.assertContains(response, 'data-char-start="0"')

    @patch("reader.views.dispatch_audio_generation")
    def test_changes_voice_when_regenerating_audio(self, dispatch):
        alternate = Voice.objects.create(
            name="Voz alternativa",
            code="alternate",
            language="pt-BR",
            provider=Voice.Provider.KOKORO,
        )
        document = self.make_document(status=Document.Status.READY)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("reader:document-regenerate", kwargs={"pk": document.pk}),
            {
                "voice": alternate.pk,
                "speed": 190,
                "reading_mode": Document.ReadingMode.NATURAL,
            },
        )
        document.refresh_from_db()
        self.assertRedirects(response, document.get_absolute_url())
        self.assertEqual(document.voice, alternate)
        self.assertEqual(document.speed, 190)
        self.assertEqual(document.reading_mode, Document.ReadingMode.NATURAL)
        self.assertEqual(document.status, Document.Status.PENDING)
        dispatch.assert_called_once_with(document)

    @patch("reader.views.dispatch_audio_generation")
    def test_creates_document_from_pasted_text(self, dispatch):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("reader:document-create"),
            {
                "title": "Nova leitura",
                "text": "Conteúdo colado para ser narrado.",
                "voice": self.voice.pk,
                "speed": 180,
                "reading_mode": Document.ReadingMode.ACADEMIC,
            },
        )
        document = Document.objects.get(title="Nova leitura")
        self.assertRedirects(response, document.get_absolute_url())
        self.assertEqual(document.owner, self.user)
        self.assertEqual(document.source_type, Document.SourceType.TEXT)
        dispatch.assert_called_once_with(document)

    @override_settings(MAX_DOCUMENTS_PER_USER=1)
    @patch("reader.views.dispatch_audio_generation")
    def test_community_library_limit_blocks_new_document(self, dispatch):
        self.make_document()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("reader:document-create"),
            {
                "title": "Documento além do limite",
                "text": "Não deve ser criado.",
                "voice": self.voice.pk,
                "speed": 180,
                "reading_mode": Document.ReadingMode.ACADEMIC,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "limite comunitário")
        self.assertFalse(
            Document.objects.filter(title="Documento além do limite").exists()
        )
        dispatch.assert_not_called()

    @override_settings(GOOGLE_DRIVE_ENABLED=True)
    @patch("reader.views.dispatch_audio_generation")
    @patch("reader.views.download_selected_file")
    def test_imports_selected_google_drive_file(self, download, dispatch):
        download.return_value = SimpleUploadedFile(
            "artigo.txt",
            "Texto importado do Drive.".encode(),
            content_type="text/plain",
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("reader:drive-import"),
            {
                "title": "Artigo no Drive",
                "voice": self.voice.pk,
                "speed": 170,
                "reading_mode": Document.ReadingMode.ACADEMIC,
                "file_id": "arquivo_drive_12345",
                "access_token": "token-temporario",
            },
        )
        self.assertEqual(response.status_code, 201)
        document = Document.objects.get(title="Artigo no Drive")
        self.assertEqual(document.owner, self.user)
        self.assertEqual(document.source_type, Document.SourceType.TXT)
        self.assertEqual(document.extracted_text, "Texto importado do Drive.")
        dispatch.assert_called_once_with(document)

    def test_exports_owned_document_as_kindle_epub(self):
        document = self.make_document(title="Artigo para Kindle")
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("reader:document-kindle", kwargs={"pk": document.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/epub+zip")
        self.assertIn("artigo-para-kindle.epub", response["Content-Disposition"])
        content = b"".join(response.streaming_content)
        self.assertTrue(content.startswith(b"PK"))


class ApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("api-user", password="senha-forte-123")
        self.other = User.objects.create_user("other-api", password="senha-forte-123")
        self.voice = Voice.objects.create(
            name="Português Brasil", code="pt-br", language="pt-BR"
        )
        self.document = Document.objects.create(
            owner=self.user,
            title="Documento API",
            extracted_text="Conteúdo via API.",
            voice=self.voice,
        )
        Document.objects.create(
            owner=self.other,
            title="Documento privado",
            extracted_text="Não deve aparecer.",
            voice=self.voice,
        )
        self.client.force_authenticate(self.user)

    def test_lists_only_authenticated_users_documents(self):
        response = self.client.get("/api/documents/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Documento API")

    @patch("reader.serializers.dispatch_audio_generation")
    def test_creates_text_document(self, dispatch):
        response = self.client.post(
            "/api/documents/",
            {
                "title": "Criado pela API",
                "text": "Um novo texto criado pela API interna.",
                "voice": self.voice.pk,
                "speed": 190,
            },
        )
        self.assertEqual(response.status_code, 201)
        document = Document.objects.get(title="Criado pela API")
        self.assertEqual(document.owner, self.user)
        dispatch.assert_called_once_with(document)

    def test_updates_reading_progress(self):
        url = f"/api/documents/{self.document.pk}/progress/"
        response = self.client.patch(
            url,
            {"position_seconds": 18.5, "char_offset": 12},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        progress = ReadingProgress.objects.get(document=self.document)
        self.assertEqual(progress.user, self.user)
        self.assertEqual(progress.position_seconds, 18.5)

    def test_stream_manifest_supports_an_existing_full_audio_file(self):
        self.document.status = Document.Status.READY
        self.document.duration_seconds = 12
        self.document.audio_file = SimpleUploadedFile(
            "documento-legado.wav", b"RIFF-audio-legado", content_type="audio/wav"
        )
        self.document.save()

        response = self.client.get(f"/api/documents/{self.document.pk}/stream/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["building"])
        self.assertEqual(len(response.data["chunks"]), 1)
        chunk = response.data["chunks"][0]
        self.assertTrue(chunk["legacy"])
        self.assertEqual(chunk["status"], AudioSegment.Status.READY)
        self.assertEqual(chunk["end_seconds"], 12)
        self.assertTrue(chunk["word_timings"])

    def test_stream_manifest_returns_progressive_audio_chunks(self):
        self.document.status = Document.Status.READY
        self.document.stream_is_building = True
        self.document.save()
        segment = AudioSegment.objects.create(
            document=self.document,
            order=0,
            text=self.document.extracted_text,
            audio_file=SimpleUploadedFile(
                "trecho.wav", b"RIFF-trecho", content_type="audio/wav"
            ),
            status=AudioSegment.Status.READY,
            duration_seconds=3.2,
            start_char=0,
            end_char=self.document.char_count,
            start_seconds=0,
            end_seconds=3.2,
            word_timings=build_word_timings(
                self.document.extracted_text, 3.2
            ),
        )

        response = self.client.get(f"/api/documents/{self.document.pk}/stream/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["building"])
        self.assertEqual(response.data["chunks"][0]["order"], segment.order)
        self.assertIn("/media/audio/chunks/", response.data["chunks"][0]["audio_url"])

    @patch("reader.api.queue_stream_window")
    def test_requests_only_the_next_audio_window(self, queue_window):
        queue_window.return_value = {
            "document_id": self.document.pk,
            "queued": 6,
            "workflow_id": "window-test",
        }

        response = self.client.post(
            f"/api/documents/{self.document.pk}/stream-prepare/",
            {"order": 12},
            format="json",
        )

        self.assertEqual(response.status_code, 202)
        queue_window.assert_called_once_with(self.document.pk, start_order=12)
        self.assertEqual(response.data["queued"], 6)

    def test_cannot_access_another_users_document(self):
        private_document = Document.objects.get(owner=self.other)
        response = self.client.get(f"/api/documents/{private_document.pk}/")
        self.assertEqual(response.status_code, 404)


class AudioCacheTests(BaseReaderTest):
    def test_reuses_an_existing_synthesized_chunk(self):
        document = self.make_document()
        segment = AudioSegment.objects.create(
            document=document,
            order=0,
            text="Um texto para leitura.",
            spoken_text="Um texto para leitura.",
        )
        from .tasks import _audio_cache_key

        cache = AudioCache.objects.create(
            cache_key=_audio_cache_key(document, segment.spoken_text),
            provider=document.voice.provider,
            voice_code=document.voice.code,
            speed=document.speed,
            audio_file=SimpleUploadedFile(
                "cache.wav",
                b"RIFF-audio-em-cache",
                content_type="audio/wav",
            ),
            word_timings=[["Um", 0, 0.2]],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "segment.wav"
            timings = _synthesize_with_cache(document, segment, target)
            self.assertEqual(target.read_bytes(), b"RIFF-audio-em-cache")
        cache.refresh_from_db()
        self.assertEqual(cache.hit_count, 1)
        self.assertEqual(timings, [["Um", 0, 0.2]])
