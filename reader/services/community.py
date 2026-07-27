from django.conf import settings
from django.utils import timezone

from reader.models import Document


def document_creation_limit_error(user):
    total_limit = settings.MAX_DOCUMENTS_PER_USER
    if total_limit > 0 and Document.objects.filter(owner=user).count() >= total_limit:
        return (
            f"Sua biblioteca atingiu o limite comunitário de {total_limit} documentos. "
            "Exclua uma leitura antiga para adicionar outra."
        )

    daily_limit = settings.MAX_DOCUMENTS_PER_USER_PER_DAY
    if daily_limit > 0:
        today = timezone.localdate()
        created_today = Document.objects.filter(
            owner=user,
            created_at__date=today,
        ).count()
        if created_today >= daily_limit:
            return (
                f"Você atingiu o limite comunitário de {daily_limit} documentos por dia. "
                "Tente novamente amanhã."
            )
    return ""
