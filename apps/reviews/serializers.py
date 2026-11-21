from rest_framework import serializers

from apps.accounts.models import AnonymousMarketplaceIdentity
from apps.accounts.permissions import is_student_feature_eligible
from apps.jobs.models import JobEngagement
from apps.messaging.models import Conversation, ConversationParticipantSnapshot
from .models import ReviewReport, SellerReview


class SellerReviewSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.CharField(source="reviewer_display_name", read_only=True)
    seller_name = serializers.CharField(source="identity_display_name", read_only=True)
    identity = serializers.CharField(source="identity_public_id", read_only=True)
    conversation = serializers.PrimaryKeyRelatedField(queryset=Conversation.objects.all(), write_only=True)
    job_engagement = serializers.PrimaryKeyRelatedField(queryset=JobEngagement.objects.all(), write_only=True, required=False)
    conversation_id = serializers.IntegerField(read_only=True)
    job_engagement_id = serializers.IntegerField(read_only=True)
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()
    sentiment = serializers.CharField(read_only=True)
    category = serializers.ChoiceField(choices=SellerReview.Category.choices, required=False, allow_null=True, default=None)

    class Meta:
        model = SellerReview
        fields = ("id", "category", "conversation", "conversation_id", "job_engagement", "job_engagement_id", "reviewer_name", "seller_name", "identity", "identity_type", "rating", "sentiment", "comment", "image", "image_url", "status", "created_at")
        read_only_fields = ("id", "reviewer_name", "seller_name", "identity", "identity_type", "status", "created_at")

    def validate_image(self, image):
        if image and image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Review images must be 5 MB or smaller.")
        return image

    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        if obj.status != SellerReview.Status.APPROVED and not (request and (request.user.is_staff or request.user.pk == obj.reviewer_id)):
            return None
        return obj.image.url

    def validate(self, attrs):
        request = self.context["request"]
        conversation = attrs.get("conversation")
        engagement = attrs.get("job_engagement")
        category = attrs.get("category")
        if conversation is None:
            raise serializers.ValidationError({"conversation": "Choose the conversation for this completed interaction."})
        if not conversation.participants.filter(pk=request.user.pk).exists():
            raise serializers.ValidationError({"conversation": "You can review only a conversation you took part in."})
        people = list(conversation.participants.filter(is_staff=False))
        if len(people) != 2 or not all(is_student_feature_eligible(person) for person in people):
            raise serializers.ValidationError({"conversation": "Both participants must have active verified accounts."})
        other = next((person for person in people if person.pk != request.user.pk), None)
        if not other:
            raise serializers.ValidationError({"conversation": "The other participant could not be found."})

        inferred = SellerReview.Category.JOBS if conversation.context_type == Conversation.ContextType.JOBS else SellerReview.Category.MARKETPLACE
        category = category or inferred
        if category != inferred:
            raise serializers.ValidationError({"category": "The review category must match this conversation."})
        if category == SellerReview.Category.MARKETPLACE:
            if engagement:
                raise serializers.ValidationError({"job_engagement": "Marketplace reviews do not use a Jobs & Gigs engagement."})
            if conversation.status != Conversation.Status.RESOLVED or conversation.completed_by.count() != 2:
                raise serializers.ValidationError({"conversation": "Both participants must mark the marketplace interaction complete first."})
            duplicate = SellerReview.objects.filter(conversation=conversation, reviewer=request.user, category=category).exists()
        else:
            if not engagement or engagement.conversation_id != conversation.pk or engagement.status != JobEngagement.Status.COMPLETED:
                raise serializers.ValidationError({"job_engagement": "Both participants must confirm a completed job or gig before leaving feedback."})
            if request.user.pk not in (engagement.poster_id, engagement.applicant_id):
                raise serializers.ValidationError({"job_engagement": "You were not part of this job or gig."})
            other_id = engagement.applicant_id if request.user.pk == engagement.poster_id else engagement.poster_id
            if other.pk != other_id:
                raise serializers.ValidationError({"conversation": "This conversation does not match the completed engagement."})
            duplicate = SellerReview.objects.filter(job_engagement=engagement, reviewer=request.user, category=category).exists()
        if duplicate:
            raise serializers.ValidationError({"conversation": "You already reviewed this completed interaction."})
        attrs["category"] = category
        attrs["_reviewed_user"] = other
        return attrs

    def create(self, validated_data):
        conversation = validated_data.pop("conversation")
        reviewed_user = validated_data.pop("_reviewed_user")
        engagement = validated_data.get("job_engagement")
        request = self.context["request"]
        reviewer_snapshot = ConversationParticipantSnapshot.objects.filter(conversation=conversation, user=request.user).first()
        target_snapshot = ConversationParticipantSnapshot.objects.filter(conversation=conversation, user=reviewed_user).first()
        if not reviewer_snapshot or not target_snapshot:
            raise serializers.ValidationError("This conversation is missing its identity history. Contact support.")
        alias = AnonymousMarketplaceIdentity.objects.filter(user=reviewed_user, public_id=target_snapshot.public_identity_id).first() if target_snapshot.identity_type == "anonymous" else None
        validated_data.pop("job_engagement", None)
        return SellerReview.objects.create(
            conversation=conversation, job_engagement=engagement, reviewer=request.user, seller=reviewed_user,
            identity_type=target_snapshot.identity_type, anonymous_identity=alias,
            identity_public_id=target_snapshot.public_identity_id,
            identity_display_name=target_snapshot.display_name,
            reviewer_display_name=reviewer_snapshot.display_name,
            **validated_data,
        )


class ReviewReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewReport
        fields = ("id", "review", "reason", "details", "status", "created_at")
        read_only_fields = ("id", "status", "created_at")

    def validate(self, attrs):
        request = self.context["request"]
        review = attrs["review"]
        if review.status != SellerReview.Status.APPROVED:
            raise serializers.ValidationError({"review": "Only a published review can be reported."})
        if review.reviewer_id == request.user.pk:
            raise serializers.ValidationError({"review": "You cannot report your own review."})
        if ReviewReport.objects.filter(review=review, reporter=request.user, status__in=(ReviewReport.Status.OPEN, ReviewReport.Status.REVIEWING)).exists():
            raise serializers.ValidationError({"review": "You already have an open report for this review."})
        return attrs
