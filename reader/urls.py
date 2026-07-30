from django.urls import path

from . import views

app_name = "reader"

urlpatterns = [
    path("health/", views.healthcheck, name="healthcheck"),
    path("media/<path:path>", views.private_media, name="private-media"),
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("biblioteca/", views.DocumentListView.as_view(), name="document-list"),
    path("livros/", views.BookListView.as_view(), name="book-list"),
    path("livros/novo/", views.BookCreateView.as_view(), name="book-create"),
    path("livros/<int:pk>/", views.BookDetailView.as_view(), name="book-detail"),
    path(
        "livros/<int:pk>/excluir/",
        views.BookDeleteView.as_view(),
        name="book-delete",
    ),
    path(
        "livros/<int:pk>/resumir/",
        views.summarize_book,
        name="book-summary",
    ),
    path("documentos/novo/", views.DocumentCreateView.as_view(), name="document-create"),
    path(
        "documentos/importar/drive/",
        views.import_from_google_drive,
        name="drive-import",
    ),
    path(
        "documentos/<int:pk>/",
        views.DocumentDetailView.as_view(),
        name="document-detail",
    ),
    path(
        "documentos/<int:pk>/excluir/",
        views.DocumentDeleteView.as_view(),
        name="document-delete",
    ),
    path(
        "documentos/<int:pk>/gerar/",
        views.regenerate_document,
        name="document-regenerate",
    ),
    path(
        "documentos/<int:pk>/kindle/",
        views.export_document_to_kindle,
        name="document-kindle",
    ),
    path(
        "documentos/<int:pk>/resumir/",
        views.summarize_document,
        name="document-summary",
    ),
    path(
        "documentos/<int:pk>/traduzir/",
        views.translate_document,
        name="document-translation",
    ),
    path(
        "ia/resultados/<int:pk>/",
        views.AIResultDetailView.as_view(),
        name="ai-result",
    ),
    path("vozes/<int:pk>/amostra/", views.voice_preview, name="voice-preview"),
]
