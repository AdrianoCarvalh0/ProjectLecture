from django.urls import path

from . import views

app_name = "reader"

urlpatterns = [
    path("health/", views.healthcheck, name="healthcheck"),
    path("media/<path:path>", views.private_media, name="private-media"),
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("biblioteca/", views.DocumentListView.as_view(), name="document-list"),
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
    path("vozes/<int:pk>/amostra/", views.voice_preview, name="voice-preview"),
]
