from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from .credentials import reveal
from .models import Model, Organization
from .services import generate


class CredentialAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("credential-admin"))

    def test_model_admin_encrypts_keeps_and_removes_secret(self):
        fields = {
            "name": "مدل آزمایشی", "endpoint": "https://example.com/v1/chat/completions",
            "identifier": "test", "enabled": "on", "key_env": "NEXA_MODEL_KEY_TEST",
        }
        secret = "test-api-secret"
        response = self.client.post("/admin/nexa/model/add/", {**fields, "secret": secret})
        self.assertEqual(response.status_code, 302)
        model = Model.objects.get()
        original = model.api_key_ciphertext
        self.assertNotIn(secret, original)
        path = f"/admin/nexa/model/{model.pk}/change/"
        page = self.client.get(path)
        self.assertNotContains(page, secret)
        self.assertNotContains(page, original)
        self.assertEqual(self.client.post(path, fields).status_code, 302)
        model.refresh_from_db()
        self.assertEqual(model.api_key_ciphertext, original)

        opener = MagicMock()
        upstream = opener.open.return_value.__enter__.return_value
        upstream.read.return_value = b'{"choices":[{"message":{"content":"ok"}}]}'
        with patch.dict("os.environ", {"NEXA_MODEL_KEY_TEST": "old-key"}):
            with patch("nexa.services.build_opener", return_value=opener):
                self.assertEqual(generate(model, "test", []), "ok")
        self.assertEqual(opener.open.call_args.args[0].get_header("Authorization"), "Bearer " + secret)
        self.assertEqual(self.client.post(path, {**fields, "remove_secret": "on"}).status_code, 302)
        model.refresh_from_db()
        self.assertEqual(model.api_key_ciphertext, "")

    def test_directory_settings_encrypt_password_and_reject_invalid_ca(self):
        fields = {
            "auth_mode": "both", "ldap_url": "ldaps://ad.example.com",
            "ldap_base": "dc=example,dc=com", "ldap_bind_dn": "cn=bind,dc=example,dc=com",
            "mode": "knowledge", "secret": "test-ad-password",
        }
        path = "/admin/nexa/organization/add/"
        invalid = self.client.post(path, {**fields, "ldap_ca_pem": "invalid certificate"})
        self.assertContains(invalid, "گواهی معتبر PEM وارد کنید.")
        self.assertNotContains(invalid, "test-ad-password")
        self.assertFalse(Organization.objects.exists())
        self.assertEqual(self.client.post(path, fields).status_code, 302)
        organization = Organization.objects.get()
        self.assertNotEqual(organization.ldap_password_ciphertext, "test-ad-password")
        self.assertEqual(reveal(organization.ldap_password_ciphertext), "test-ad-password")
        page = self.client.get(f"/admin/nexa/organization/{organization.pk}/change/")
        self.assertNotContains(page, "test-ad-password")
