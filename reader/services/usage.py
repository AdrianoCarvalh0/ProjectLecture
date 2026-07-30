from django.db import transaction
from django.utils import timezone

from reader.models import MonthlyUsage
from reader.services.runtime_config import get_app_configuration


def current_usage(user):
    now = timezone.localdate()
    usage, _ = MonthlyUsage.objects.get_or_create(
        user=user,
        year=now.year,
        month=now.month,
    )
    return usage


def reading_limit_error(user):
    configuration = get_app_configuration()
    usage = current_usage(user)
    if (
        configuration.max_readings_per_user_month > 0
        and usage.readings >= configuration.max_readings_per_user_month
    ):
        return (
            "Você atingiu o limite de "
            f"{configuration.max_readings_per_user_month} leituras neste mês."
        )
    return ""


@transaction.atomic
def register_reading(user, character_count=0):
    configuration = get_app_configuration()
    now = timezone.localdate()
    usage, _ = MonthlyUsage.objects.select_for_update().get_or_create(
        user=user,
        year=now.year,
        month=now.month,
    )
    if (
        configuration.max_readings_per_user_month > 0
        and usage.readings >= configuration.max_readings_per_user_month
    ):
        raise ValueError(
            "O limite mensal de leituras foi atingido para esta conta."
        )
    usage.readings += 1
    usage.synthesized_characters += max(0, int(character_count))
    usage.save(
        update_fields=(
            "readings",
            "synthesized_characters",
            "updated_at",
        )
    )
    return usage


def register_ai_request(user):
    now = timezone.localdate()
    usage, _ = MonthlyUsage.objects.get_or_create(
        user=user,
        year=now.year,
        month=now.month,
    )
    usage.ai_requests += 1
    usage.save(update_fields=("ai_requests", "updated_at"))
    return usage
