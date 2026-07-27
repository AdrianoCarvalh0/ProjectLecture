from django.conf import settings


def runtime_options(request):
    return {
        "allow_public_registration": settings.ALLOW_PUBLIC_REGISTRATION,
        "google_login_enabled": settings.GOOGLE_LOGIN_ENABLED,
    }
