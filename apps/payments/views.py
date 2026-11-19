import os
import requests
from django.db import transaction
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from apps.accounts.permissions import IsVerifiedStudent
from apps.administration.models import PlatformSettings
from apps.administration.platform import FeatureUnavailable
from apps.products.models import Product
from apps.ads.models import AdCampaign
from .models import Payment
from .serializers import PaymentSerializer
from .throttles import PaymentInitializationThrottle
class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    platform_feature = "marketplace_enabled"
    serializer_class=PaymentSerializer; permission_classes=[IsVerifiedStudent]
    def get_queryset(self): return Payment.objects.filter(user=self.request.user)
    def get_throttles(self):
        if getattr(self, "action", None) == "initialize_listing_fee":
            return [PaymentInitializationThrottle()]
        return super().get_throttles()

    @action(detail=False, methods=["post"], url_path="initialize-listing-fee")
    @transaction.atomic
    def initialize_listing_fee(self, request):
        try:
            product_id = int(request.data.get("product"))
        except (TypeError, ValueError):
            raise ValidationError({"product": "Provide a valid product ID."})
        try:
            product = Product.objects.select_for_update().get(pk=product_id)
        except Product.DoesNotExist:
            raise ValidationError({"product": "Listing not found."})
        if product.seller_id != request.user.id:
            raise PermissionDenied("You can only pay the listing fee for your own listing.")
        if product.status != Product.Status.PENDING_PAYMENT:
            raise ValidationError({"product": "This listing is not awaiting payment."})
        platform = PlatformSettings.get_solo()
        if not platform.marketplace_enabled:
            raise FeatureUnavailable("The marketplace is currently disabled.")
        if not platform.listing_fee_enabled:
            raise ValidationError({"product": "Listing fees are currently disabled; submit the listing for free."})
        if not platform.paystack_enabled:
            raise FeatureUnavailable("Paystack payments are currently disabled.")
        secret = os.getenv("PAYSTACK_SECRET_KEY")
        if not secret:
            raise APIException("Listing payments are not configured. Contact the site administrator.")
        existing = Payment.objects.filter(product=product, status=Payment.Status.INITIALIZED).order_by("-created_at").first()
        if existing and existing.provider_response.get("data", {}).get("authorization_url"):
            return Response({"reference": existing.reference, "authorization_url": existing.provider_response["data"]["authorization_url"]})
        payment = Payment.objects.create(user=request.user, product=product, amount_kobo=platform.listing_fee_kobo)
        callback_url = os.getenv("PAYSTACK_CALLBACK_URL", "")
        payload = {
            "email": request.user.email,
            "amount": payment.amount_kobo,
            "currency": payment.currency,
            "reference": payment.reference,
            "metadata": {"product_id": product.pk, "user_id": request.user.pk},
        }
        if callback_url:
            payload["callback_url"] = callback_url
        try:
            response = requests.post("https://api.paystack.co/transaction/initialize", json=payload,
                                     headers={"Authorization": f"Bearer {secret}"}, timeout=15)
            response.raise_for_status()
            provider_response = response.json()
            if not provider_response.get("status") or not provider_response.get("data", {}).get("authorization_url"):
                raise RuntimeError(provider_response.get("message", "Paystack did not return a checkout URL."))
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            payment.status = Payment.Status.FAILED
            payment.provider_response = {"error": str(exc)[:1000]}
            payment.save(update_fields=("status", "provider_response"))
            return Response({"detail": "Could not initialize payment. Please try again."}, status=status.HTTP_502_BAD_GATEWAY)
        payment.provider_response = provider_response
        payment.save(update_fields=("provider_response",))
        return Response({"reference": payment.reference, "authorization_url": provider_response["data"]["authorization_url"]}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="initialize-ad-fee")
    @transaction.atomic
    def initialize_ad_fee(self, request):
        try:
            ad_id = int(request.data.get("advertisement"))
            ad = AdCampaign.objects.select_for_update().get(pk=ad_id, advertiser=request.user)
        except (TypeError, ValueError, AdCampaign.DoesNotExist):
            raise ValidationError({"advertisement": "Provide a valid ad campaign that you own."})
        if ad.status != AdCampaign.Status.PENDING_PAYMENT:
            raise ValidationError({"advertisement": "This campaign is not awaiting payment."})
        platform = PlatformSettings.get_solo()
        if not platform.ads_enabled:
            raise FeatureUnavailable("Advertising is currently disabled.")
        if not platform.ad_fee_enabled:
            raise ValidationError({"advertisement": "Ad fees are disabled; submit the campaign for free."})
        if not platform.paystack_enabled:
            raise FeatureUnavailable("Paystack payments are currently disabled.")
        secret = os.getenv("PAYSTACK_SECRET_KEY")
        if not secret:
            raise APIException("Paystack payments are not configured.")
        existing = Payment.objects.filter(advertisement=ad, status=Payment.Status.INITIALIZED).order_by("-created_at").first()
        if existing and existing.provider_response.get("data", {}).get("authorization_url"):
            return Response({"reference": existing.reference, "authorization_url": existing.provider_response["data"]["authorization_url"]})
        payment = Payment.objects.create(user=request.user, advertisement=ad, amount_kobo=platform.ad_fee_kobo)
        payload = {"email": request.user.email, "amount": payment.amount_kobo, "currency": payment.currency, "reference": payment.reference,
                   "metadata": {"advertisement_id": ad.pk, "user_id": request.user.pk}}
        callback_url = os.getenv("PAYSTACK_CALLBACK_URL", "")
        if callback_url:
            payload["callback_url"] = callback_url
        try:
            response = requests.post("https://api.paystack.co/transaction/initialize", json=payload,
                                     headers={"Authorization": f"Bearer {secret}"}, timeout=15)
            response.raise_for_status()
            provider_response = response.json()
            if not provider_response.get("status") or not provider_response.get("data", {}).get("authorization_url"):
                raise RuntimeError(provider_response.get("message", "Paystack did not return a checkout URL."))
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            payment.status = Payment.Status.FAILED
            payment.provider_response = {"error": str(exc)[:1000]}
            payment.save(update_fields=("status", "provider_response"))
            return Response({"detail": "Could not initialize payment. Please try again."}, status=status.HTTP_502_BAD_GATEWAY)
        payment.provider_response = provider_response
        payment.save(update_fields=("provider_response",))
        return Response({"reference": payment.reference, "authorization_url": provider_response["data"]["authorization_url"]}, status=status.HTTP_201_CREATED)
