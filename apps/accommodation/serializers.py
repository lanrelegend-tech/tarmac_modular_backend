from rest_framework import serializers
from apps.administration.campuses import validate_campus_choice

from .models import AccommodationImage, AccommodationListing, HostelAgentRequest


class AccommodationImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccommodationImage
        fields = ("id", "image", "position")


class AccommodationListingSerializer(serializers.ModelSerializer):
    campus = serializers.CharField(required=True, max_length=120)
    images = serializers.ListField(child=serializers.ImageField(), write_only=True, required=False)
    photos = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()
    poster_label = serializers.SerializerMethodField()
    poster_public_id = serializers.SerializerMethodField()
    hostel_agent_badge = serializers.BooleanField(source="owner.is_hostel_agent", read_only=True)

    class Meta:
        model = AccommodationListing
        fields = (
            "id", "public_id", "post_type", "accommodation_type", "title", "description", "location_area", "campus",
            "rent_amount", "budget_min", "budget_max", "rent_period", "agency_fee", "caution_fee",
            "service_fee", "other_fees", "other_fees_note", "water_available", "electricity_available",
            "road_access", "security_available", "bathroom_available", "kitchen_available", "furnished",
            "current_occupants", "room_capacity", "roommates_needed", "available_from", "available_until",
            "status", "reviewer_note", "photos", "images", "is_owner", "poster_label", "poster_public_id", "created_at", "expires_at",
            "accuracy_confirmed", "authorization_confirmed", "hostel_agent_badge",
        )
        read_only_fields = ("status", "reviewer_note", "is_owner", "poster_label", "created_at", "expires_at", "photos")

    def get_photos(self, obj):
        return AccommodationImageSerializer(obj.images.all(), many=True, context=self.context).data

    def get_is_owner(self, obj):
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and obj.owner_id == request.user.pk)

    def get_poster_label(self, obj):
        return "Verified KWASU student"

    def get_poster_public_id(self, obj):
        if obj.owner.is_active and not obj.owner.is_suspended and obj.owner.public_profile_enabled:
            return str(obj.owner.public_id)
        return ""

    def validate_campus(self, value):
        return validate_campus_choice(value)

    def validate_images(self, images):
        if len(images) > 8:
            raise serializers.ValidationError("Upload no more than 8 accommodation photos.")
        for image in images:
            if image.size > 5 * 1024 * 1024:
                raise serializers.ValidationError("Each photo must be 5 MB or smaller.")
            if image.content_type not in ("image/jpeg", "image/png", "image/webp"):
                raise serializers.ValidationError("Use JPG, PNG, or WEBP photos.")
        return images

    def validate(self, attrs):
        post_type = attrs.get("post_type", getattr(self.instance, "post_type", None))
        for field in ("rent_amount", "budget_min", "budget_max", "agency_fee", "caution_fee", "service_fee", "other_fees"):
            value = attrs.get(field, getattr(self.instance, field, None) if self.instance else None)
            if value is not None and value < 0:
                raise serializers.ValidationError({field: "Enter an amount of zero or more."})
        starts = attrs.get("available_from", getattr(self.instance, "available_from", None) if self.instance else None)
        ends = attrs.get("available_until", getattr(self.instance, "available_until", None) if self.instance else None)
        if starts and ends and ends < starts:
            raise serializers.ValidationError({"available_until": "The end date must be on or after the available-from date."})
        if not attrs.get("authorization_confirmed", getattr(self.instance, "authorization_confirmed", False)):
            raise serializers.ValidationError({"authorization_confirmed": "Confirm that you are authorised to make this accommodation post."})
        if post_type == AccommodationListing.PostType.OFFER_ACCOMMODATION:
            images = attrs.get("images", [])
            existing = self.instance.images.count() if self.instance and not images else 0
            if len(images) + existing < 3:
                raise serializers.ValidationError({"images": "Add at least 3 photos of the accommodation."})
            if not attrs.get("authorization_confirmed", getattr(self.instance, "authorization_confirmed", False)):
                raise serializers.ValidationError({"authorization_confirmed": "Confirm that you are authorised to offer this accommodation."})
            rent = attrs.get("rent_amount", getattr(self.instance, "rent_amount", None))
            if rent is None or rent <= 0:
                raise serializers.ValidationError({"rent_amount": "Enter the rent amount."})
        if post_type == AccommodationListing.PostType.SEEK_ROOMMATE:
            current = attrs.get("current_occupants", getattr(self.instance, "current_occupants", None))
            capacity = attrs.get("room_capacity", getattr(self.instance, "room_capacity", None))
            if current is None or capacity is None or capacity <= current:
                raise serializers.ValidationError({"room_capacity": "Enter the current occupants and a larger total room capacity."})
            if attrs.get("rent_amount", getattr(self.instance, "rent_amount", None)) is None:
                raise serializers.ValidationError({"rent_amount": "Enter the rent amount to be shared."})
            if not attrs.get("authorization_confirmed", getattr(self.instance, "authorization_confirmed", False)):
                raise serializers.ValidationError({"authorization_confirmed": "Confirm that you are authorised to invite a roommate."})
            needed = attrs.get("roommates_needed", getattr(self.instance, "roommates_needed", None))
            if needed is not None and needed > capacity - current:
                raise serializers.ValidationError({"roommates_needed": "Roommates needed cannot exceed the remaining room capacity."})
        if post_type == AccommodationListing.PostType.SEEK_ACCOMMODATION:
            low = attrs.get("budget_min", getattr(self.instance, "budget_min", None))
            high = attrs.get("budget_max", getattr(self.instance, "budget_max", None))
            if low is not None and high is not None and high < low:
                raise serializers.ValidationError({"budget_max": "Maximum budget must be at least the minimum budget."})
            if low is None and high is None:
                raise serializers.ValidationError({"budget_max": "Enter at least one budget amount so students know your price range."})
        if not attrs.get("accuracy_confirmed", getattr(self.instance, "accuracy_confirmed", False)):
            raise serializers.ValidationError({"accuracy_confirmed": "Confirm that the information is accurate."})
        return attrs

    def _save_images(self, listing, images):
        if images is None:
            return
        listing.images.all().delete()
        AccommodationImage.objects.bulk_create([
            AccommodationImage(listing=listing, image=image, position=index)
            for index, image in enumerate(images)
        ])

    def create(self, validated_data):
        images = validated_data.pop("images", [])
        listing = AccommodationListing.objects.create(**validated_data)
        self._save_images(listing, images)
        return listing

    def update(self, instance, validated_data):
        images = validated_data.pop("images", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        self._save_images(instance, images)
        return instance


class AdminAccommodationListingSerializer(serializers.ModelSerializer):
    owner_name = serializers.SerializerMethodField()
    owner_email = serializers.EmailField(source="owner.email", read_only=True)
    owner_public_id = serializers.CharField(source="owner.public_id", read_only=True)
    photos = serializers.SerializerMethodField()

    class Meta:
        model = AccommodationListing
        fields = tuple(field.name for field in AccommodationListing._meta.fields) + ("owner_name", "owner_email", "owner_public_id", "photos")
        read_only_fields = tuple(field.name for field in AccommodationListing._meta.fields if field.name not in ("status", "reviewer_note")) + ("owner_name", "owner_email", "owner_public_id", "photos")

    def get_owner_name(self, obj):
        return obj.owner.get_full_name().strip() or obj.owner.username

    def get_photos(self, obj):
        return AccommodationImageSerializer(obj.images.all(), many=True, context=self.context).data


class HostelAgentRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = HostelAgentRequest
        fields = ("id", "reason", "status", "admin_note", "created_at", "reviewed_at")
        read_only_fields = ("id", "status", "admin_note", "created_at", "reviewed_at")

    def validate_reason(self, value):
        value = value.strip()
        if len(value) < 20:
            raise serializers.ValidationError("Explain your accommodation experience or plans in at least 20 characters.")
        return value


class AdminHostelAgentRequestSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_email = serializers.EmailField(source="user.email", read_only=True)
    student_public_id = serializers.UUIDField(source="user.public_id", read_only=True)
    student_campus = serializers.CharField(source="user.campus", read_only=True)
    student_status = serializers.CharField(source="user.student_status", read_only=True)
    is_hostel_agent = serializers.BooleanField(source="user.is_hostel_agent", read_only=True)

    class Meta:
        model = HostelAgentRequest
        fields = ("id", "user", "student_name", "student_email", "student_public_id", "student_campus", "student_status", "is_hostel_agent", "reason", "status", "admin_note", "reviewed_by", "reviewed_at", "created_at")
        read_only_fields = ("id", "user", "student_name", "student_email", "student_public_id", "student_campus", "student_status", "is_hostel_agent", "reviewed_by", "reviewed_at", "created_at")

    def get_student_name(self, obj):
        return obj.user.get_full_name().strip() or obj.user.username
