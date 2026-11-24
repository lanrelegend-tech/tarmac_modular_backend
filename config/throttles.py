from rest_framework.throttling import ScopedRateThrottle


class ActionRateLimitMixin:
    """Add an action-specific limit while preserving the global API limits."""

    action_throttle_scopes = {}

    def get_throttles(self):
        scope = self.action_throttle_scopes.get(getattr(self, "action", None))
        if not scope:
            return super().get_throttles()
        self.throttle_scope = scope
        return [*super().get_throttles(), ScopedRateThrottle()]
