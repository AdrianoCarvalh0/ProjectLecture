from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import Document, Voice
from .services.runtime_config import get_app_configuration

User = get_user_model()


class BootstrapFormMixin:
    def apply_bootstrap(self):
        for field in self.fields.values():
            css_class = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs["class"] = css_class


class RegistrationForm(BootstrapFormMixin, UserCreationForm):
    email = forms.EmailField(
        label="E-mail",
        required=True,
        help_text="Usado para identificar e recuperar sua conta.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(("username", "email", "password1", "password2"))
        self.apply_bootstrap()
        self.fields["username"].label = "Usuário"
        self.fields["password1"].label = "Senha"
        self.fields["password2"].label = "Confirme a senha"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Já existe uma conta com este e-mail.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


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
        configuration = get_app_configuration()
        self.fields["original_file"].help_text = (
            "PDF, DOCX, EPUB ou TXT. "
            f"Limite configurado: {configuration.max_document_size_mb} MB."
        )
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
        configuration = get_app_configuration()
        if len(text) > configuration.book_part_characters:
            raise forms.ValidationError(
                "Textos maiores devem ser enviados como livro. "
                f"Limite de documento: {configuration.book_part_characters:,} caracteres."
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
        configuration = get_app_configuration()
        max_bytes = configuration.max_document_size_mb * 1024 * 1024
        if uploaded.size > max_bytes:
            raise forms.ValidationError(
                f"O arquivo excede o limite de {configuration.max_document_size_mb} MB."
            )
        return uploaded


class BookForm(BootstrapFormMixin, forms.Form):
    title = forms.CharField(label="Título do livro", max_length=180)
    original_file = forms.FileField(
        label="Arquivo do livro",
        help_text="PDF, DOCX, EPUB ou TXT.",
    )
    voice = forms.ModelChoiceField(
        label="Voz", queryset=Voice.objects.none(), empty_label=None
    )
    reading_mode = forms.ChoiceField(
        label="Modo de leitura",
        choices=Document.ReadingMode.choices,
        initial=Document.ReadingMode.NATURAL,
    )
    speed = forms.IntegerField(
        label="Velocidade",
        min_value=80,
        max_value=320,
        initial=170,
        widget=forms.NumberInput(attrs={"step": 10}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        configuration = get_app_configuration()
        self.fields["original_file"].help_text = (
            "PDF, DOCX, EPUB ou TXT. O livro será dividido internamente em partes "
            f"de até {configuration.book_part_characters:,} caracteres"
            f" e, para PDF, {configuration.book_part_pages} páginas. "
            f"Limite: {configuration.max_book_size_mb} MB."
        )
        voices = Voice.objects.filter(is_active=True).order_by("-is_default", "name")
        self.fields["voice"].queryset = voices
        default_voice = voices.filter(is_default=True).first() or voices.first()
        if default_voice:
            self.fields["voice"].initial = default_voice
        self.apply_bootstrap()

    def clean_original_file(self):
        uploaded = self.cleaned_data["original_file"]
        extension = Path(uploaded.name).suffix.lower()
        if extension not in {".pdf", ".docx", ".epub", ".txt"}:
            raise forms.ValidationError(
                "Formato inválido. Envie PDF, DOCX, EPUB ou TXT."
            )
        maximum = get_app_configuration().max_book_size_mb * 1024 * 1024
        if uploaded.size > maximum:
            raise forms.ValidationError(
                "O livro excede o limite configurado de "
                f"{get_app_configuration().max_book_size_mb} MB."
            )
        return uploaded


class TranslationForm(BootstrapFormMixin, forms.Form):
    target_language = forms.ChoiceField(
        label="Traduzir para",
        choices=(
            ("Português do Brasil", "Português do Brasil"),
            ("Inglês", "Inglês"),
            ("Espanhol", "Espanhol"),
            ("Francês", "Francês"),
            ("Italiano", "Italiano"),
            ("Alemão", "Alemão"),
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_bootstrap()


class DriveImportForm(BootstrapFormMixin, forms.Form):
    title = forms.CharField(max_length=180)
    voice = forms.ModelChoiceField(
        queryset=Voice.objects.filter(is_active=True)
    )
    reading_mode = forms.ChoiceField(choices=Document.ReadingMode.choices)
    speed = forms.IntegerField(min_value=80, max_value=320)
    file_id = forms.RegexField(
        regex=r"^[A-Za-z0-9_-]{10,200}$",
        max_length=200,
    )
    access_token = forms.CharField(max_length=4096)

    def clean_title(self):
        return self.cleaned_data["title"].strip()


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
