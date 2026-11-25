import os
import hashlib
import requests
from urllib.parse import urlencode
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from apps.accounts.permissions import IsVerifiedStudent
from apps.administration.models import PlatformSettings
from apps.administration.platform import FeatureUnavailable
from apps.products.models import Product
from apps.ads.models import AdCampaign
from apps.ads.models import AdAdvertiser, AdPlacement, AdSlotHold
from apps.ads.services import get_slot_availability
from .models import Payment
from .serializers import PaymentSerializer
from .throttles import PaymentInitializationThrottle
from apps.ads.throttles import PublicAdPaymentThrottle
from .provider import PaystackVerificationError, verify_paystack_transaction
from .settlement import confirm_successful_payment


def _payment_callback_url():
    configured = os.getenv("PAYSTACK_CALLBACK_URL", "").strip()
    if configured:
        return configured
    origin = os.getenv("FRONTEND_ORIGIN", "").split(",")[0].rstrip("/")
    return f"{origin}/payment-return" if origin else ""


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=PaymentSerializer; permission_classes=[IsVerifiedStudent]
    def get_queryset(self):
        queryset = Payment.objects.filter(user=self.request.user).select_related("product", "advertisement", "subscription_plan").order_by("-created_at", "-pk")
        reference = self.request.query_params.get("reference", "").strip()
        return queryset.filter(reference=reference) if reference else queryset
    def get_permissions(self):
        if getattr(self, "action", None) in ("initialize_external_ad", "verify_external_ad"):
            return [permissions.AllowAny()]
        return [IsVerifiedStudent()]
    def get_throttles(self):
        if getattr(self, "action", None) in ("initialize_listing_fee", "initialize_external_ad", "verify_external_ad", "verify"):
            if getattr(self, "action", None) in ("initialize_external_ad", "verify_external_ad"):
                return [PublicAdPaymentThrottle()]
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
        if not platform.paystack_enabled:
            raise FeatureUnavailable("Paystack payments are currently disabled.")
        listing_fee = platform.listing_fee_kobo if platform.listing_fee_enabled else 0
        sponsor_fee = product.sponsorship_fee_kobo if product.sponsor_requested and platform.sponsored_product_fee_enabled else 0
        amount_kobo = listing_fee + sponsor_fee
        if amount_kobo <= 0:
            raise ValidationError({"product": "No payment is due for this listing."})
        secret = os.getenv("PAYSTACK_SECRET_KEY")
        if not secret:
            raise APIException("Listing payments are not configured. Contact the site administrator.")
        existing = Payment.objects.filter(product=product, status=Payment.Status.INITIALIZED).order_by("-created_at").first()
        if existing and existing.provider_response.get("data", {}).get("authorization_url"):
            return Response({"reference": existing.reference, "authorization_url": existing.provider_response["data"]["authorization_url"]})
        payment = Payment.objects.create(user=request.user, product=product, amount_kobo=amount_kobo)
        callback_url = _payment_callback_url()
        payload = {
            "email": request.user.email,
            "amount": payment.amount_kobo,
            "currency": payment.currency,
            "reference": payment.reference,
            "metadata": {"product_id": product.pk, "user_id": request.user.pk, "listing_fee_kobo": listing_fee, "sponsorship_fee_kobo": sponsor_fee},
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
    @action(detail=False, methods=["post"], url_path="initialize-external-ad")
    @transaction.atomic
    def initialize_external_ad(self, request):
        token = str(request.data.get("token", "")).strip()
        try:
            campaign_id = int(request.data.get("campaign"))
        except (TypeError, ValueError):
            raise ValidationError({"campaign": "Provide a valid campaign ID."})
        token_hash = hashlib.sha256(token.encode()).hexdigest() if token else ""
        campaign = AdCampaign.objects.select_for_update(of=("self",)).select_related("business_advertiser", "package", "ad_placement").filter(pk=campaign_id, business_advertiser__isnull=False, management_token_hash=token_hash).first()
        if campaign is None:
            advertiser = AdAdvertiser.objects.filter(management_token_hash=token_hash).first() if token_hash else None
            if not advertiser:
                raise PermissionDenied("This private campaign link is invalid or expired.")
            campaign = AdCampaign.objects.select_for_update(of=("self",)).select_related("business_advertiser", "package", "ad_placement").filter(pk=campaign_id, business_advertiser=advertiser).first()
        if not campaign:
            raise PermissionDenied("This private campaign link does not grant access to this campaign.")
        advertiser = campaign.business_advertiser
        if advertiser is None:
            raise PermissionDenied("This campaign is not an external business advertisement.")
        if campaign.status != AdCampaign.Status.PENDING_PAYMENT:
            raise ValidationError({"campaign": "This campaign is not awaiting payment."})
        platform = PlatformSettings.get_solo()
        if not platform.ads_enabled:
            raise FeatureUnavailable("Advertising is currently disabled.")
        if not campaign.payment_required_snapshot:
            campaign.status = AdCampaign.Status.PENDING_REVIEW
            campaign.save(update_fields=("status", "updated_at"))
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("ads", "Advertisement needs review", f'"{campaign.title}" is waiting for approval.', {"kind": "ad_review", "campaign_id": campaign.pk}, f"ad-review:{campaign.pk}")
            placements = list(campaign.ad_placements.order_by("pk")) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
            for slot in placements:
                AdSlotHold.objects.update_or_create(campaign=campaign, placement=slot, defaults={"starts_at": campaign.starts_at, "ends_at": campaign.ends_at, "expires_at": campaign.ends_at, "is_confirmed": True})
            return Response({"free": True, "status": campaign.status})
        placements = list(campaign.ad_placements.order_by("pk")) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
        if not placements:
            raise ValidationError({"campaign": "This campaign has no assigned advertising slot."})
        placements = list(AdPlacement.objects.select_for_update().filter(pk__in=[item.pk for item in placements]).order_by("pk"))
        holds = {hold.placement_id: hold for hold in AdSlotHold.objects.filter(campaign=campaign)}
        now = timezone.now()
        if any(not holds.get(slot.pk) or holds[slot.pk].expires_at <= now for slot in placements):
            full_slots = [slot for slot in placements if get_slot_availability(slot, campaign.starts_at, campaign.ends_at, exclude_campaign_id=campaign.pk)["remaining"] <= 0]
            if full_slots:
                AdSlotHold.objects.filter(campaign=campaign, is_confirmed=False).delete()
                raise ValidationError({"availability": f"Temporary slot holds expired and these dates are now fully booked for {', '.join(slot.position for slot in full_slots)}. Join the waitlist."})
        for slot in placements:
            AdSlotHold.objects.update_or_create(campaign=campaign, placement=slot, defaults={"starts_at": campaign.starts_at, "ends_at": campaign.ends_at, "expires_at": now + timedelta(minutes=30), "is_confirmed": False})
        existing = Payment.objects.filter(advertisement=campaign, status=Payment.Status.INITIALIZED).order_by("-created_at").first()
        if existing and existing.provider_response.get("data", {}).get("authorization_url"):
            return Response({"reference": existing.reference, "authorization_url": existing.provider_response["data"]["authorization_url"]})
        secret = os.getenv("PAYSTACK_SECRET_KEY")
        if not secret:
            raise APIException("Paystack payments are not configured. Contact Tarmac Support.")
        payment = Payment.objects.create(user=None, payer_email=advertiser.email, advertisement=campaign, amount_kobo=campaign.price_kobo)
        payload = {"email": advertiser.email, "amount": payment.amount_kobo, "currency": payment.currency, "reference": payment.reference, "metadata": {"advertisement_id": campaign.pk, "external_advertiser": True}}
        frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").split(",")[0].rstrip("/")
        callback_url = f"{frontend_origin}/advertise/manage?{urlencode({'token': token})}"
        if callback_url:
            payload["callback_url"] = callback_url
        try:
            response = requests.post("https://api.paystack.co/transaction/initialize", json=payload, headers={"Authorization": f"Bearer {secret}"}, timeout=15)
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

    @action(detail=False, methods=["post"], url_path="verify-external-ad")
    def verify_external_ad(self, request):
        token = str(request.data.get("token", "")).strip()
        reference = str(request.data.get("reference", "")).strip()
        try:
            campaign_id = int(request.data.get("campaign"))
        except (TypeError, ValueError):
            raise ValidationError({"campaign": "Provide a valid campaign ID."})
        token_hash = hashlib.sha256(token.encode()).hexdigest() if token else ""
        campaign = AdCampaign.objects.filter(
            pk=campaign_id,
            business_advertiser__isnull=False,
            management_token_hash=token_hash,
        ).first() if token_hash else None
        if campaign is None and token_hash:
            advertiser = AdAdvertiser.objects.filter(management_token_hash=token_hash).first()
            campaign = AdCampaign.objects.filter(pk=campaign_id, business_advertiser=advertiser).first() if advertiser else None
        if not campaign:
            raise PermissionDenied("This private campaign link is invalid or expired.")
        payment = Payment.objects.filter(advertisement=campaign, reference=reference).first()
        if not payment:
            raise ValidationError({"reference": "No payment matches this campaign."})
        try:
            provider_data = verify_paystack_transaction(reference)
        except PaystackVerificationError as exc:
            raise exc
        if provider_data.get("status") == "success":
            _, result = confirm_successful_payment(reference, provider_data)
            if result == "mismatch":
                raise ValidationError({"payment": "Paystack returned payment details that do not match the campaign charge."})
        payment.refresh_from_db()
        campaign.refresh_from_db()
        return Response({"payment_status": payment.status, "campaign_status": campaign.status, "provider_status": provider_data.get("status")})

    @action(detail=False, methods=["post"], url_path="verify")
    def verify(self, request):
        reference = str(request.data.get("reference", "")).strip()
        if not reference:
            raise ValidationError({"reference": "Provide the payment reference."})
        payment = Payment.objects.filter(user=request.user, reference=reference).first()
        if not payment:
            raise PermissionDenied("This payment does not belong to your account.")
        try:
            provider_data = verify_paystack_transaction(reference)
        except PaystackVerificationError as exc:
            raise exc
        if provider_data.get("status") == "success":
            _, result = confirm_successful_payment(reference, provider_data)
            if result == "mismatch":
                raise ValidationError({"payment": "Paystack returned payment details that do not match this charge."})
        elif provider_data.get("status") == "failed" and payment.status != Payment.Status.SUCCESS:
            payment.status = Payment.Status.FAILED
            payment.provider_response = {"status": provider_data.get("status"), "gateway_response": provider_data.get("gateway_response")}
            payment.save(update_fields=("status", "provider_response"))
        payment.refresh_from_db()
        return Response({"reference": reference, "status": payment.status, "provider_status": provider_data.get("status")})

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
        if not ad.payment_required_snapshot:
            ad.status = AdCampaign.Status.PENDING_REVIEW
            ad.save(update_fields=("status", "updated_at"))
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("ads", "Advertisement needs review", f'"{ad.title}" is waiting for approval.', {"kind": "ad_review", "campaign_id": ad.pk}, f"ad-review:{ad.pk}")
            placements = list(ad.ad_placements.order_by("pk")) or list(AdPlacement.objects.filter(pk=ad.ad_placement_id))
            for slot in placements:
                AdSlotHold.objects.update_or_create(campaign=ad, placement=slot, defaults={"starts_at": ad.starts_at, "ends_at": ad.ends_at, "expires_at": ad.ends_at, "is_confirmed": True})
            return Response({"free": True, "status": ad.status})
        secret = os.getenv("PAYSTACK_SECRET_KEY")
        if not secret:
            raise APIException("Paystack payments are not configured.")
        existing = Payment.objects.filter(advertisement=ad, status=Payment.Status.INITIALIZED).order_by("-created_at").first()
        if existing and existing.provider_response.get("data", {}).get("authorization_url"):
            return Response({"reference": existing.reference, "authorization_url": existing.provider_response["data"]["authorization_url"]})
        amount_kobo = ad.price_kobo
        if amount_kobo == 0:
            raise ValidationError({"advertisement": "This campaign does not require a payment."})
        placements = list(ad.ad_placements.order_by("pk")) or list(AdPlacement.objects.select_for_update().filter(pk=ad.ad_placement_id))
        if not placements:
            raise ValidationError({"advertisement": "This campaign has no assigned placement."})
        now = timezone.now()
        holds = {hold.placement_id: hold for hold in AdSlotHold.objects.select_for_update().filter(campaign=ad)}
        if any(slot.pk not in holds or holds[slot.pk].expires_at <= now for slot in placements):
            full = [slot for slot in placements if get_slot_availability(slot, ad.starts_at, ad.ends_at, exclude_campaign_id=ad.pk)["remaining"] <= 0]
            if full:
                AdSlotHold.objects.filter(campaign=ad, is_confirmed=False).delete()
                raise ValidationError({"availability": f"These dates filled while you were preparing payment: {', '.join(slot.position for slot in full)}. Join the waitlist."})
        for slot in placements:
            AdSlotHold.objects.update_or_create(campaign=ad, placement=slot, defaults={"starts_at": ad.starts_at, "ends_at": ad.ends_at, "expires_at": now + timedelta(minutes=30), "is_confirmed": False})
        payment = Payment.objects.create(user=request.user, advertisement=ad, amount_kobo=amount_kobo)
        payload = {"email": request.user.email, "amount": payment.amount_kobo, "currency": payment.currency, "reference": payment.reference,
                   "metadata": {"advertisement_id": ad.pk, "user_id": request.user.pk}}
        callback_url = _payment_callback_url()
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
