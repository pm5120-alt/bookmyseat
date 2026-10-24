from django.test import TestCase

# Create your tests here.
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AuthenticationSmokeTests(TestCase):
    def test_registration_login_and_profile_access(self):
        register_url = reverse("register")
        self.assertEqual(self.client.get(register_url).status_code, 200)
        response = self.client.post(register_url, {
            "username": "smoke-viewer",
            "email": "smoke-viewer@example.test",
            "password1": "SafeTestPassword!9472",
            "password2": "SafeTestPassword!9472",
        })
        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        user = get_user_model().objects.get(username="smoke-viewer")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)

        self.client.logout()
        login_url = reverse("login")
        self.assertEqual(self.client.get(login_url).status_code, 200)
        response = self.client.post(login_url, {
            "username": "smoke-viewer",
            "password": "SafeTestPassword!9472",
        })
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
