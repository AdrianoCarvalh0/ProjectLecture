from io import BytesIO
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import AudioSegment, Document, ReadingProgress, Voice
from .services.extractors import extract_text, source_type_for
from .services.streaming import (
    build_word_timings,
    map_spoken_word_timings,
    tokenize_display_text,
)
from .services.text_preparation import prepare_for_speech
from .services.tts import azure_ssml, finalize_azure_timings, split_text

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
    @override_settings(AZURE_SPEECH_ENABLED=True)
    def test_azure_configuration_activates_exactly_four_cloud_voices(self):
        call_command("seed_voices", stdout=StringIO())
        active = Voice.objects.filter(is_active=True)
        self.assertEqual(active.count(), 4)
        self.assertFalse(active.exclude(provider=Voice.Provider.AZURE).exists())
        self.assertEqual(active.filter(is_default=True).get().name, "Francisca")

    @override_settings(AZURE_SPEECH_ENABLED=False)
    def test_missing_azure_configuration_restores_local_catalog(self):
        call_command("seed_voices", stdout=StringIO())
        active = Voice.objects.filter(is_active=True)
        self.assertEqual(active.count(), 3)
        self.assertFalse(active.exclude(provider=Voice.Provider.KOKORO).exists())
        self.assertEqual(active.filter(is_default=True).get().name, "Lia")


class WebViewsTests(BaseReaderTest):
    def test_login_is_required(self):
        response = self.client.get(reverse("reader:dashboard"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('reader:dashboard')}",
        )

    @override_settings(ALLOW_PUBLIC_REGISTRATION=False)
    def test_public_registration_can_be_disabled(self):
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 404)

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
        response.close()

        self.client.force_login(other)
        response = self.client.get(document.original_file.url)
        self.assertEqual(response.status_code, 404)

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

    def test_cannot_access_another_users_document(self):
        private_document = Document.objects.get(owner=self.other)
        response = self.client.get(f"/api/documents/{private_document.pk}/")
        self.assertEqual(response.status_code, 404)
