from rest_framework.routers import DefaultRouter

from .api import DocumentViewSet, VoiceViewSet

app_name = "reader-api"

router = DefaultRouter()
router.register("documents", DocumentViewSet, basename="document")
router.register("voices", VoiceViewSet, basename="voice")

urlpatterns = router.urls
