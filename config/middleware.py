from django.http import JsonResponse, HttpResponse

from apps.administration.models import PlatformSettings


class MaintenanceModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/") or request.path.startswith("/api/admin-tools/settings/") or request.path == "/api/system/status/":
            return self.get_response(request)
        platform = PlatformSettings.get_solo()
        if platform.maintenance_mode:
            if request.path.startswith("/api/"):
                return JsonResponse({"detail": platform.maintenance_message, "maintenance": True}, status=503)
            return HttpResponse(platform.maintenance_message, status=503, content_type="text/plain; charset=utf-8")
        return self.get_response(request)
