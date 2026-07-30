from django.utils import timezone

from reader.models import Book, Document
from reader.services.runtime_config import get_app_configuration


def document_creation_limit_error(user):
    configuration = get_app_configuration()
    total_limit = configuration.max_files_per_user
    top_level_documents = Document.objects.filter(owner=user, book__isnull=True).count()
    total_files = top_level_documents + Book.objects.filter(owner=user).count()
    if total_limit > 0 and total_files >= total_limit:
        return (
            f"Sua biblioteca atingiu o limite comunitário de {total_limit} arquivos. "
            "Exclua uma leitura antiga para adicionar outra."
        )

    daily_limit = configuration.max_files_per_user_day
    if daily_limit > 0:
        today = timezone.localdate()
        created_today = Document.objects.filter(
            owner=user,
            book__isnull=True,
            created_at__date=today,
        ).count() + Book.objects.filter(
            owner=user,
            created_at__date=today,
        ).count()
        if created_today >= daily_limit:
            return (
                f"Você atingiu o limite comunitário de {daily_limit} documentos por dia. "
                "Tente novamente amanhã."
            )
    return ""
