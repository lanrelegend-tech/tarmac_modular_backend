from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase, APIClient

from apps.administration.models import PlatformSettings


User = get_user_model()


class CookieAuthenticationTests(APITestCase):
    def setUp(self):
        cache.clear()
        platform = PlatformSettings.get_solo()
        platform.student_verification_enabled = False
        platform.email_verification_required = False
        platform.save()
        cache.clear()
        self.user = User.objects.create_user(
            email="cookie-user@example.test", username="cookie-user",
            password="StrongPassword123!", email_verified=True,
        )

    def tearDown(self):
        cache.clear()

    def test_login_issues_httponly_cookies_and_cookie_authenticates_api(self):
        client = APIClient(enforce_csrf_checks=True)
        csrf_response = client.get("/api/auth/csrf/")
        csrf_token = csrf_response.data["csrfToken"]

        login_response = client.post(
            "/api/auth/token/",
            {"email": self.user.email, "password": "StrongPassword123!"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertNotIn("access", login_response.data)
        self.assertNotIn("refresh", login_response.data)
        access_cookie = login_response.cookies[settings.AUTH_ACCESS_COOKIE]
        refresh_cookie = login_response.cookies[settings.AUTH_REFRESH_COOKIE]
        self.assertTrue(access_cookie["httponly"])
        self.assertTrue(refresh_cookie["httponly"])

        profile_response = client.get("/api/accounts/me/")
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(profile_response.data["email"], self.user.email)

    def test_bearer_token_without_auth_cookies_is_not_accepted(self):
        from rest_framework_simplejwt.tokens import AccessToken

        token = str(AccessToken.for_user(self.user))
        response = APIClient().get(
            "/api/accounts/me/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 401)
