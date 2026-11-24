import uuid

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def live(request):
    """Process liveness probe; deliberately does not depend on other services."""
    return JsonResponse({"status": "ok"})


@require_GET
def ready(request):
    """Readiness probe for the database and shared cache used by API workers."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        key = f"tarmac.health.{uuid.uuid4().hex}"
        cache.set(key, "ok", timeout=5)
        cache_ok = cache.get(key) == "ok"
        cache.delete(key)
        if not cache_ok:
            raise RuntimeError("Shared cache health check failed")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
