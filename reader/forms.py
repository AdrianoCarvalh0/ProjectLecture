from pathlib import Path

from django import forms
from django.conf import settings

from .models import Document, Voice


class BootstrapFormMixin:
    def apply_bootstrap(self):
        for field in self.fields.values():
            css_class = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs["class"] = css_class


class DocumentForm(BootstrapFormMixin, forms.Form):
    title = forms.CharField(label="Título", max_length=180)
    text = forms.CharField(
        label="Cole o texto",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 11,
                "placeholder": "Cole aqui o artigo, parecer, lei ou outro conteúdo...",
            }
        ),
    )
    original_file = forms.FileField(
        label="Ou envie um arquivo",
        required=False,
        help_text="PDF, DOCX, EPUB ou TXT. Limite configurado: 20 MB.",
    )
    voice = forms.ModelChoiceField(
        label="Voz", queryset=Voice.objects.none(), empty_label=None
    )
    reading_mode = forms.ChoiceField(
        label="Modo de leitura",
        choices=Document.ReadingMode.choices,
        initial=Document.ReadingMode.ACADEMIC,
    )
    speed = forms.IntegerField(
        label="Velocidade (palavras por minuto)",
        min_value=80,
        max_value=320,
        initial=170,
        widget=forms.NumberInput(attrs={"step": 10}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        voices = Voice.objects.filter(is_active=True).order_by("-is_default", "name")
        self.fields["voice"].queryset = voices
        default_voice = voices.filter(is_default=True).first() or voices.first()
        if default_voice:
            self.fields["voice"].initial = default_voice
        self.apply_bootstrap()

    def clean(self):
        cleaned = super().clean()
        text = (cleaned.get("text") or "").strip()
        original_file = cleaned.get("original_file")
        if not text and not original_file:
            raise forms.ValidationError("Cole um texto ou selecione um arquivo.")
        if text and original_file:
            raise forms.ValidationError("Use somente uma origem: texto colado ou arquivo.")
        if len(text) > settings.MAX_CHARACTERS_PER_DOCUMENT:
            raise forms.ValidationError(
                f"O texto excede o limite de {settings.MAX_CHARACTERS_PER_DOCUMENT:,} caracteres."
            )
        return cleaned

    def clean_original_file(self):
        uploaded = self.cleaned_data.get("original_file")
        if not uploaded:
            return uploaded
        allowed = {".pdf", ".docx", ".epub", ".txt"}
        extension = Path(uploaded.name).suffix.lower()
        if extension not in allowed:
            raise forms.ValidationError("Formato inválido. Envie PDF, DOCX, EPUB ou TXT.")
        max_bytes = settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024
        if uploaded.size > max_bytes:
            raise forms.ValidationError(
                f"O arquivo excede o limite de {settings.MAX_DOCUMENT_SIZE_MB} MB."
            )
        return uploaded


class RegenerateAudioForm(BootstrapFormMixin, forms.Form):
    voice = forms.ModelChoiceField(
        label="Voz", queryset=Voice.objects.none(), empty_label=None
    )
    reading_mode = forms.ChoiceField(
        label="Modo de leitura", choices=Document.ReadingMode.choices
    )
    speed = forms.IntegerField(
        label="Velocidade",
        min_value=80,
        max_value=320,
        widget=forms.NumberInput(attrs={"step": 10}),
    )

    def __init__(self, *args, document=None, **kwargs):
        super().__init__(*args, **kwargs)
        voices = Voice.objects.filter(is_active=True).order_by("-is_default", "name")
        self.fields["voice"].queryset = voices
        if document and not self.is_bound:
            selected_voice = (
                voices.filter(pk=document.voice_id).first()
                or voices.filter(is_default=True).first()
                or voices.first()
            )
            self.initial.update(
                {
                    "voice": selected_voice.pk if selected_voice else None,
                    "reading_mode": document.reading_mode,
                    "speed": document.speed,
                }
            )
        self.apply_bootstrap()
