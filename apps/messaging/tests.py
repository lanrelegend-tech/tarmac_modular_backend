from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.accounts.models import AnonymousMarketplaceIdentity
from apps.administration.models import PlatformSettings
from .models import Conversation, Message
from .serializers import create_participant_snapshot


User = get_user_model()


class MessagingInboxTests(APITestCase):
    def setUp(self):
        cache.clear()
        platform = PlatformSettings.get_solo()
        platform.messaging_enabled = True
        platform.student_verification_enabled = True
        platform.email_verification_required = True
        platform.save()
        cache.clear()

        self.ade = User.objects.create_user(
            email="ade@example.test", username="ade", password="StrongPassword123!",
            is_student_verified=True, email_verified=True,
        )
        self.ola = User.objects.create_user(
            email="ola@example.test", username="ola", password="StrongPassword123!",
            is_student_verified=True, email_verified=True, public_name_enabled=True,
            first_name="Ola",
        )
        self.stranger = User.objects.create_user(
            email="stranger@example.test", username="stranger", password="StrongPassword123!",
            is_student_verified=True, email_verified=True,
        )
        self.ade_alias = AnonymousMarketplaceIdentity.objects.create(user=self.ade, handle="User235")

    def tearDown(self):
        cache.clear()

    def make_conversation(self, first, second, first_identity="normal", second_identity="normal", title="Test item"):
        conversation = Conversation.objects.create(
            context_type=Conversation.ContextType.MARKETPLACE,
            context_title=title,
        )
        conversation.participants.add(first, second)
        create_participant_snapshot(conversation, first, identity_type=first_identity)
        create_participant_snapshot(conversation, second, identity_type=second_identity)
        return conversation

    def test_inbox_is_partitioned_by_the_current_users_identity(self):
        anonymous_thread = self.make_conversation(self.ade, self.ola, "anonymous", "normal", "Anonymous enquiry")
        normal_thread = self.make_conversation(self.ade, self.stranger, "normal", "normal", "Normal enquiry")

        self.client.force_authenticate(self.ade)
        anonymous_response = self.client.get("/api/messaging/conversations/?identity_type=anonymous")
        normal_response = self.client.get("/api/messaging/conversations/?identity_type=normal")

        self.assertEqual(anonymous_response.status_code, 200)
        self.assertEqual([row["id"] for row in anonymous_response.data["results"]], [anonymous_thread.pk])
        self.assertEqual([row["id"] for row in normal_response.data["results"]], [normal_thread.pk])

        # The same asymmetric thread belongs in different inboxes for its two participants.
        self.client.force_authenticate(self.ola)
        ola_normal_inbox = self.client.get("/api/messaging/conversations/?identity_type=normal")
        ola_anonymous_inbox = self.client.get("/api/messaging/conversations/?identity_type=anonymous")
        self.assertEqual([row["id"] for row in ola_normal_inbox.data["results"]], [anonymous_thread.pk])
        self.assertEqual(ola_anonymous_inbox.data["results"], [])

    def test_inbox_returns_preview_and_unread_count_without_loading_message_history(self):
        conversation = self.make_conversation(self.ade, self.ola, "anonymous", "normal")
        messages = [Message.objects.create(conversation=conversation, sender=self.ola, body=f"Message {index}") for index in range(30)]

        self.client.force_authenticate(self.ade)
        response = self.client.get("/api/messaging/conversations/?identity_type=anonymous")

        self.assertEqual(response.status_code, 200)
        row = response.data["results"][0]
        self.assertEqual(row["last_message_preview"], "Message 29")
        self.assertEqual(row["unread_count"], 30)
        self.assertIsNotNone(row["last_message_at"])
        self.assertGreaterEqual(row["last_message_at"], messages[0].created_at)
        self.assertNotIn("messages", row)

    def test_message_history_is_paginated_and_limited_to_participants(self):
        conversation = self.make_conversation(self.ade, self.ola)
        private_conversation = self.make_conversation(self.ola, self.stranger)
        for index in range(30):
            Message.objects.create(conversation=conversation, sender=self.ade, body=f"Message {index}")
        Message.objects.create(conversation=private_conversation, sender=self.ola, body="Private")

        self.client.force_authenticate(self.ade)
        own_response = self.client.get(f"/api/messaging/messages/?conversation={conversation.pk}&page=1")
        other_response = self.client.get(f"/api/messaging/messages/?conversation={private_conversation.pk}")

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(own_response.data["count"], 30)
        self.assertEqual(len(own_response.data["results"]), settings.REST_FRAMEWORK["PAGE_SIZE"])
        self.assertEqual(own_response.data["results"][0]["body"], "Message 29")
        self.assertEqual(other_response.status_code, 200)
        self.assertEqual(other_response.data["count"], 0)
        self.assertEqual(other_response.data["results"], [])

    def test_anonymous_participant_data_does_not_expose_private_account_fields(self):
        conversation = self.make_conversation(self.ade, self.ola, "anonymous", "normal")
        self.client.force_authenticate(self.ade)

        response = self.client.get(f"/api/messaging/conversations/{conversation.pk}/")

        self.assertEqual(response.status_code, 200)
        participants = response.data["participants_data"]
        own_identity = next(row for row in participants if row["is_me"])
        other_identity = next(row for row in participants if not row["is_me"])
        self.assertEqual(own_identity["name"], "User235")
        self.assertEqual(own_identity["identity_type"], "anonymous")
        self.assertEqual(other_identity["name"], "Ola")
        for participant in participants:
            self.assertNotIn("email", participant)
            self.assertNotIn("matric_number", participant)
            self.assertNotIn("phone_number", participant)
