from rest_framework.permissions import SAFE_METHODS
from apps.accounts.permissions import IsVerifiedStudent

class VerifiedStudentOrReadOnly(IsVerifiedStudent):
    def has_permission(self, request, view):
        # The landing page may show only public featured listings to visitors.
        # The corresponding image view narrows its queryset to those same listings.
        if request.method in SAFE_METHODS:
            if getattr(view, "action", None) == "list" and request.query_params.get("featured") == "true":
                return True
            if getattr(view, "action", None) == "file":
                return True
        return super().has_permission(request, view)

    def has_object_permission(self, request, view, obj):
        return request.method in SAFE_METHODS or obj.seller_id == request.user.id
