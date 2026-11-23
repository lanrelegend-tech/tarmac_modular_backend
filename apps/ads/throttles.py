from rest_framework.throttling import AnonRateThrottle


class PublicAdSubmissionThrottle(AnonRateThrottle):
    scope = "external_ad_submission"


class PublicAdPaymentThrottle(AnonRateThrottle):
    scope = "external_ad_payment"


class PublicAdManageThrottle(AnonRateThrottle):
    scope = "external_ad_manage"


class PublicAdImpressionThrottle(AnonRateThrottle):
    scope = "external_ad_impression"
