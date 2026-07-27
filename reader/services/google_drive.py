from pathlib import Path

import requests
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.text import get_valid_filename


class GoogleDriveImportError(ValueError):
    pass


GOOGLE_DOCUMENT_MIME = "application/vnd.google-apps.document"
SUPPORTED_MIME_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/epub+zip": ".epub",
    "text/plain": ".txt",
}


def _authorization_headers(access_token):
    return {"Authorization": f"Bearer {access_token}"}


def _raise_for_drive_error(response):
    if response.status_code == 401:
        raise GoogleDriveImportError(
            "A autorização do Google expirou. Escolha o arquivo novamente."
        )
    if response.status_code == 403:
        raise GoogleDriveImportError(
            "O Google Drive não liberou acesso a este arquivo."
        )
    if response.status_code == 404:
        raise GoogleDriveImportError("O arquivo não foi encontrado no Google Drive.")
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GoogleDriveImportError(
            "Não foi possível consultar o Google Drive agora."
        ) from exc


def _safe_filename(name, extension):
    filename = get_valid_filename(Path(name or "documento").name)
    if not filename:
        filename = f"documento{extension}"
    if Path(filename).suffix.lower() != extension:
        filename = f"{Path(filename).stem or 'documento'}{extension}"
    return filename


def download_selected_file(file_id, access_token):
    headers = _authorization_headers(access_token)
    try:
        with requests.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            headers=headers,
            params={"fields": "id,name,mimeType,size"},
            timeout=20,
        ) as metadata_response:
            _raise_for_drive_error(metadata_response)
            metadata = metadata_response.json()
    except requests.RequestException as exc:
        raise GoogleDriveImportError(
            "A conexão com o Google Drive falhou. Tente novamente."
        ) from exc
    except ValueError as exc:
        raise GoogleDriveImportError(
            "O Google Drive retornou metadados inválidos."
        ) from exc

    mime_type = metadata.get("mimeType", "")
    if mime_type == GOOGLE_DOCUMENT_MIME:
        extension = ".docx"
        download_url = (
            f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
        )
        params = {
            "mimeType": (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            )
        }
        output_mime = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    elif mime_type in SUPPORTED_MIME_TYPES:
        extension = SUPPORTED_MIME_TYPES[mime_type]
        download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        params = {"alt": "media"}
        output_mime = mime_type
    else:
        raise GoogleDriveImportError(
            "Formato não suportado. Escolha PDF, DOCX, EPUB, TXT ou Google Docs."
        )

    max_bytes = settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024
    try:
        declared_size = int(metadata.get("size") or 0)
    except (TypeError, ValueError):
        declared_size = 0
    if declared_size > max_bytes:
        raise GoogleDriveImportError(
            f"O arquivo excede o limite de {settings.MAX_DOCUMENT_SIZE_MB} MB."
        )

    try:
        with requests.get(
            download_url,
            headers=headers,
            params=params,
            stream=True,
            timeout=(10, 60),
        ) as response:
            _raise_for_drive_error(response)
            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise GoogleDriveImportError(
                        f"O arquivo excede o limite de {settings.MAX_DOCUMENT_SIZE_MB} MB."
                    )
    except GoogleDriveImportError:
        raise
    except requests.RequestException as exc:
        raise GoogleDriveImportError(
            "A conexão com o Google Drive falhou. Tente novamente."
        ) from exc

    if not content:
        raise GoogleDriveImportError("O arquivo selecionado está vazio.")
    filename = _safe_filename(metadata.get("name", ""), extension)
    return SimpleUploadedFile(
        filename,
        bytes(content),
        content_type=output_mime,
    )
