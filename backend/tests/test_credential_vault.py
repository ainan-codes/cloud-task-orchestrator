"""
test_credential_vault.py — encrypted credential storage and its admin routes.

The vault only earns its place if three things hold: a stored value is
ciphertext and not the secret, resolution prefers the vault but survives
without it, and no route ever hands a secret back. Each is asserted here.

    cd backend && python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(backend_dir, ".env"))

os.environ.setdefault("CREDENTIAL_CACHE_TTL", "0")   # no cross-test cache bleed
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/testdb")
# Pinned, not setdefault: db.py loads .env on import, and those keys are
# deliberately empty now that the real values live in the vault.
os.environ["SNAPCHAT_ENCRYPTION_KEY"] = "8Wd3vQ2mS1kP9xR7tY4nB6hJ0gL5cF8aZ2eD1uI3oK4="

import credentials                              # noqa: E402
import snapchat_crypto                          # noqa: E402
import snapchat_oauth                           # noqa: E402

SECRET = "vault-test-secret-9f2c41ab7de0"
CLIENT_ID = "11111111-2222-3333-4444-555555555555"


class VaultTestCase(unittest.TestCase):
    """Backs credentials.py with an in-memory table."""

    def setUp(self):
        credentials.invalidate()
        self.rows = {}

        def get_row(key):
            return self.rows.get(key)

        def upsert(key, value_encrypted, description=None):
            row = {"key": key, "value_encrypted": value_encrypted,
                   "description": description, "updated_at": "now"}
            self.rows[key] = row
            return row

        def delete(key):
            return self.rows.pop(key, None) is not None

        self.db = MagicMock()
        self.db.get_app_credential.side_effect = get_row
        self.db.upsert_app_credential.side_effect = upsert
        self.db.delete_app_credential.side_effect = delete
        self.db.list_app_credentials.side_effect = lambda: list(self.rows.values())

        p = patch.object(credentials, "db", self.db)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(credentials.invalidate)


class TestVaultStorage(VaultTestCase):
    def test_a_stored_secret_is_ciphertext_not_the_secret(self):
        credentials.put("snapchat_client_secret", SECRET)
        stored = self.rows["snapchat_client_secret"]["value_encrypted"]

        self.assertNotIn(SECRET, stored)
        self.assertTrue(stored.startswith("gAAAAA"))          # Fernet envelope
        self.assertEqual(snapchat_crypto.decrypt_token(stored), SECRET)

    def test_put_then_get_round_trips(self):
        credentials.put("snapchat_client_id", CLIENT_ID)
        self.assertEqual(credentials.get("snapchat_client_id"), CLIENT_ID)
        self.assertEqual(credentials.source_of("snapchat_client_id"), "database")

    def test_the_vault_wins_over_the_environment(self):
        with patch.dict(os.environ, {"SNAPCHAT_CLIENT_ID": "from-env"}):
            credentials.put("snapchat_client_id", "from-vault")
            self.assertEqual(credentials.get("snapchat_client_id"), "from-vault")

    def test_the_environment_is_the_fallback_when_the_vault_is_empty(self):
        with patch.dict(os.environ, {"SNAPCHAT_CLIENT_ID": "from-env"}):
            self.assertEqual(credentials.get("snapchat_client_id"), "from-env")
            self.assertEqual(credentials.source_of("snapchat_client_id"), "environment")

    def test_a_database_outage_falls_back_instead_of_failing(self):
        """A vault that cannot be reached must not take publishing offline."""
        self.db.get_app_credential.side_effect = RuntimeError("connection refused")
        with patch.dict(os.environ, {"SNAPCHAT_CLIENT_ID": "from-env"}):
            self.assertEqual(credentials.get("snapchat_client_id"), "from-env")

    def test_deleting_reverts_to_the_environment(self):
        credentials.put("snapchat_client_id", "from-vault")
        with patch.dict(os.environ, {"SNAPCHAT_CLIENT_ID": "from-env"}):
            self.assertEqual(credentials.get("snapchat_client_id"), "from-vault")
            credentials.delete("snapchat_client_id")
            self.assertEqual(credentials.get("snapchat_client_id"), "from-env")

    def test_rotation_takes_effect_immediately(self):
        """The read cache must be dropped on write, or a rotated secret would
        keep failing for up to the cache TTL."""
        credentials.put("snapchat_client_secret", "old-secret-value")
        self.assertEqual(credentials.get("snapchat_client_secret"), "old-secret-value")
        credentials.put("snapchat_client_secret", "new-secret-value")
        self.assertEqual(credentials.get("snapchat_client_secret"), "new-secret-value")

    def test_unknown_keys_are_refused(self):
        for bad in ("snapchat_clientsecret", "aws_key", ""):
            with self.assertRaises(ValueError):
                credentials.put(bad, "x")
            with self.assertRaises(ValueError):
                credentials.get(bad)

    def test_an_empty_value_is_refused(self):
        with self.assertRaises(ValueError):
            credentials.put("snapchat_client_secret", "   ")

    def test_put_never_returns_the_ciphertext(self):
        out = credentials.put("snapchat_client_secret", SECRET)
        self.assertNotIn("value_encrypted", out)
        self.assertNotIn(SECRET, str(out))


class TestVaultStatus(VaultTestCase):
    def test_status_never_contains_the_secret(self):
        credentials.put("snapchat_client_secret", SECRET)
        status = credentials.status()
        self.assertNotIn(SECRET, str(status))
        self.assertTrue(status["snapchat_client_secret"]["configured"])
        self.assertEqual(status["snapchat_client_secret"]["length"], len(SECRET))

    def test_the_fingerprint_identifies_a_secret_without_revealing_it(self):
        credentials.put("snapchat_client_secret", SECRET)
        first = credentials.status()["snapchat_client_secret"]["fingerprint"]

        credentials.put("snapchat_client_secret", SECRET)
        self.assertEqual(credentials.status()["snapchat_client_secret"]["fingerprint"], first)

        credentials.put("snapchat_client_secret", "a-different-secret")
        self.assertNotEqual(credentials.status()["snapchat_client_secret"]["fingerprint"], first)
        self.assertEqual(len(first), 8)

    def test_non_secret_values_are_shown_in_full(self):
        """An operator has to compare these against Snapchat's console."""
        credentials.put("snapchat_client_id", CLIENT_ID)
        credentials.put("snapchat_redirect_uri", "https://example.test/cb")
        status = credentials.status()
        self.assertEqual(status["snapchat_client_id"]["value"], CLIENT_ID)
        self.assertEqual(status["snapchat_redirect_uri"]["value"], "https://example.test/cb")


class TestOAuthReadsTheVault(VaultTestCase):
    def test_the_authorization_url_is_built_from_vault_credentials(self):
        credentials.put("snapchat_client_id", CLIENT_ID)
        credentials.put("snapchat_client_secret", SECRET)
        credentials.put("snapchat_redirect_uri", "https://example.test/api/snapchat/oauth/callback")

        url, _ = snapchat_oauth.generate_authorization_url()

        self.assertIn(f"client_id={CLIENT_ID}", url)
        self.assertIn("scope=snapchat-profile-api", url)
        self.assertIn("example.test%2Fapi%2Fsnapchat%2Foauth%2Fcallback", url)
        self.assertNotIn(SECRET, url)          # the secret is never in a URL

    def test_config_status_reports_where_each_credential_came_from(self):
        credentials.put("snapchat_client_id", CLIENT_ID)
        credentials.put("snapchat_client_secret", SECRET)
        status = snapchat_oauth.config_status()

        self.assertEqual(status["credential_sources"]["snapchat_client_id"], "database")
        self.assertEqual(status["credential_sources"]["snapchat_client_secret"], "database")
        self.assertNotIn(SECRET, str(status))

    def test_an_empty_vault_and_empty_env_is_a_config_error(self):
        with patch.dict(os.environ, {"SNAPCHAT_CLIENT_ID": "", "SNAPCHAT_CLIENT_SECRET": ""}):
            with self.assertRaises(Exception) as ctx:
                snapchat_oauth.generate_authorization_url()
            self.assertIn("SNAPCHAT_CLIENT_ID", str(ctx.exception))


class TestAdminRoutes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import app as app_module

        credentials.invalidate()
        self.addCleanup(credentials.invalidate)

        self.app_module = app_module
        self.creds = MagicMock()
        self.creds.KEYS = credentials.KEYS
        self.creds.encryption_ready.return_value = True
        self.creds.status.return_value = {
            "snapchat_client_secret": {"configured": True, "source": "database",
                                       "length": 20, "fingerprint": "5483bd08"},
        }
        self.creds.put.return_value = {"key": "snapchat_client_secret", "stored": True}
        self.creds.delete.return_value = True

        p = patch.object(app_module, "credentials", self.creds)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(app_module.app)

    def test_the_routes_require_the_admin_token(self):
        with patch.dict(os.environ, {"RUN_TOKEN": "the-admin-token", "CRON_SECRET": ""}):
            self.assertEqual(self.client.get("/api/admin/credentials").status_code, 401)
            self.assertEqual(
                self.client.put("/api/admin/credentials/snapchat_client_secret",
                                json={"value": "x"}).status_code, 401)
            self.assertEqual(
                self.client.delete("/api/admin/credentials/snapchat_client_secret").status_code, 401)
        self.creds.put.assert_not_called()

    def test_status_is_readable_with_the_token_and_hides_the_value(self):
        with patch.dict(os.environ, {"RUN_TOKEN": "the-admin-token", "CRON_SECRET": ""}):
            resp = self.client.get("/api/admin/credentials",
                                   headers={"X-Run-Token": "the-admin-token"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["credentials"]["snapchat_client_secret"]["fingerprint"], "5483bd08")
        self.assertNotIn("value_encrypted", str(body))

    def test_rotating_a_secret_stores_it_and_returns_no_value(self):
        with patch.dict(os.environ, {"RUN_TOKEN": "the-admin-token", "CRON_SECRET": ""}):
            resp = self.client.put(
                "/api/admin/credentials/snapchat_client_secret",
                json={"value": "brand-new-secret"},
                headers={"X-Run-Token": "the-admin-token"},
            )
        self.assertEqual(resp.status_code, 200)
        self.creds.put.assert_called_once()
        self.assertEqual(self.creds.put.call_args[0][1], "brand-new-secret")
        self.assertNotIn("brand-new-secret", resp.text)

    def test_writing_is_refused_without_an_explicit_encryption_key(self):
        """Otherwise the vault would be encrypted with a key derived from the
        client secret that is about to be stored inside it."""
        self.creds.encryption_ready.return_value = False
        with patch.dict(os.environ, {"RUN_TOKEN": "the-admin-token", "CRON_SECRET": ""}):
            resp = self.client.put(
                "/api/admin/credentials/snapchat_client_secret",
                json={"value": "x"}, headers={"X-Run-Token": "the-admin-token"},
            )
        self.assertEqual(resp.status_code, 500)
        self.assertIn("SNAPCHAT_ENCRYPTION_KEY", resp.json()["detail"])
        self.creds.put.assert_not_called()

    def test_an_unknown_key_is_rejected(self):
        self.creds.put.side_effect = ValueError("Unknown credential 'nope'.")
        with patch.dict(os.environ, {"RUN_TOKEN": "the-admin-token", "CRON_SECRET": ""}):
            resp = self.client.put("/api/admin/credentials/nope", json={"value": "x"},
                                   headers={"X-Run-Token": "the-admin-token"})
        self.assertEqual(resp.status_code, 400)

    def test_deleting_a_credential_that_is_not_stored_is_404(self):
        self.creds.delete.return_value = False
        with patch.dict(os.environ, {"RUN_TOKEN": "the-admin-token", "CRON_SECRET": ""}):
            resp = self.client.delete("/api/admin/credentials/snapchat_client_id",
                                      headers={"X-Run-Token": "the-admin-token"})
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
