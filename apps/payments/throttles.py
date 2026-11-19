from rest_framework.throttling import UserRateThrottle


class PaymentInitializationThrottle(UserRateThrottle):
    scope = "payment_initialization"
