from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from reader.views import RegisterView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("conta/entrar/", auth_views.LoginView.as_view(), name="login"),
    path("conta/sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("conta/cadastrar/", RegisterView.as_view(), name="register"),
    path("api/", include("reader.api_urls")),
    path("", include("reader.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
