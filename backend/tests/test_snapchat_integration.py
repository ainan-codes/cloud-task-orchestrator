"""
test_snapchat_integration.py — OAuth, crypto, media pipeline and publishing
against the official Snapchat Public Profile API contract.

These tests assert the exact wire contract Snap documents: the endpoint
paths, the field names, the request_status envelope, and the fact that a
Post Story response carries no story id. A mock that accepted a different
shape would let a rewrite silently break real publishing, so each HTTP mock
here checks the URL and body it was called with.

No database and no network is required to run them:

    cd backend && python -m unittest discover -s tests -v
"""

import base64
import contextlib
import datetime as dt
import os
import struct
import sys
import unittest
from unittest.mock import DEFAULT, MagicMock, patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("CREDENTIAL_CACHE_TTL", "0")   # no cross-test cache bleed
# Pinned, not setdefault: db.py loads .env on import, and those keys are
# deliberately empty now that the real values live in the vault.
os.environ["SNAPCHAT_CLIENT_ID"] = "test-client-id"
os.environ["SNAPCHAT_CLIENT_SECRET"] = "test-client-secret"
os.environ["SNAPCHAT_REDIRECT_URI"] = "https://reels-dashboard-omega.vercel.app/api/snapchat/oauth/callback"

import logger as applog                      # noqa: E402
import snapchat_api                          # noqa: E402
import snapchat_crypto                       # noqa: E402
import snapchat_media                        # noqa: E402
import snapchat_oauth                        # noqa: E402
import snapchat_poster                       # noqa: E402
from snapchat_errors import (                # noqa: E402
    SnapApiError,
    SnapAuthError,
    SnapMediaError,
    SnapTransientError,
)

# Credential resolution reads the vault first. These tests have no database,
# so point it at an empty one: every lookup falls through to the environment
# immediately instead of waiting on a connection that will never open.
import credentials as _credentials                            # noqa: E402
from unittest.mock import MagicMock as _MagicMock             # noqa: E402

_credentials.db = _MagicMock()
_credentials.db.get_app_credential.return_value = None
_credentials.invalidate()



# ======================================================================
# helpers
# ======================================================================
def _box(box_type, payload):
    return struct.pack(">I", len(payload) + 8) + box_type + payload


def make_mp4(duration_seconds=10.0, width=1080, height=1920, timescale=1000,
             brand=b"isom", rotate=False):
    """A minimal but structurally valid MP4 header, for the probe tests.

    Only the boxes the validator reads are present: ftyp, and a moov holding
    mvhd (duration) plus a video trak with tkhd (display size) and an mdia
    whose hdlr marks the track as video.
    """
    ftyp = _box(b"ftyp", brand + struct.pack(">I", 512) + brand + b"mp42")

    mvhd_payload = (
        b"\x00\x00\x00\x00"                                   # version 0 + flags
        + struct.pack(">I", 0)                                # creation
        + struct.pack(">I", 0)                                # modification
        + struct.pack(">I", timescale)
        + struct.pack(">I", int(duration_seconds * timescale))
        + b"\x00" * 80                                        # rate..next_track_id
    )
    mvhd = _box(b"mvhd", mvhd_payload)

    identity = [0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000]
    rotated = [0, 0x00010000, 0, -0x00010000 & 0xFFFFFFFF, 0, 0, 0, 0, 0x40000000]
    matrix = b"".join(struct.pack(">I", v) for v in (rotated if rotate else identity))

    # A rotated track stores the pre-rotation size; the matrix swaps it back.
    stored_w, stored_h = (height, width) if rotate else (width, height)

    tkhd_payload = (
        b"\x00\x00\x00\x07"                                   # version 0, enabled
        + struct.pack(">I", 0) + struct.pack(">I", 0)         # creation, modification
        + struct.pack(">I", 1)                                # track_ID
        + struct.pack(">I", 0)                                # reserved
        + struct.pack(">I", int(duration_seconds * timescale))
        + b"\x00" * 8                                         # reserved
        + struct.pack(">h", 0)                                # layer
        + struct.pack(">h", 0)                                # alternate_group
        + struct.pack(">h", 0)                                # volume
        + b"\x00\x00"                                         # reserved
        + matrix
        + struct.pack(">I", stored_w << 16)
        + struct.pack(">I", stored_h << 16)
    )
    tkhd = _box(b"tkhd", tkhd_payload)

    hdlr = _box(b"hdlr", b"\x00\x00\x00\x00" + b"\x00" * 4 + b"vide" + b"\x00" * 12 + b"Video\x00")
    mdia = _box(b"mdia", hdlr)
    trak = _box(b"trak", tkhd + mdia)
    moov = _box(b"moov", mvhd + trak)

    # Padding stands in for the mdat payload so the file has realistic size.
    return ftyp + moov + _box(b"mdat", b"\x00" * 4096)


def snap_response(status_code=200, payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.json.return_value = payload if payload is not None else {"request_status": "SUCCESS"}
    resp.text = str(payload)
    return resp


def make_account(**overrides):
    account = {
        "id": 1,
        "name": "Whale Life",
        "snapchat_username": "whalelife",
        "public_profile_id": "profile-aaa",
        "default_placement": "spotlight",
        "default_locale": "en_US",
        "extra_hashtags": [],
        "caption_footer": "",
        "access_token_encrypted": snapchat_crypto.encrypt_token("access-aaa"),
        "refresh_token_encrypted": snapchat_crypto.encrypt_token("refresh-aaa"),
        "token_expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
    }
    account.update(overrides)
    return account


# ======================================================================
# 1. credential encryption at rest
# ======================================================================
class TestCredentialEncryption(unittest.TestCase):
    def test_round_trip(self):
        token = "snap-access-token-abc123"
        encrypted = snapchat_crypto.encrypt_token(token)
        self.assertNotEqual(encrypted, token)
        self.assertNotIn(token, encrypted)
        self.assertEqual(snapchat_crypto.decrypt_token(encrypted), token)

    def test_none_passes_through(self):
        self.assertIsNone(snapchat_crypto.encrypt_token(None))
        self.assertIsNone(snapchat_crypto.decrypt_token(None))

    def test_ciphertext_is_not_deterministic(self):
        """Fernet nonces mean two encryptions of one token never match, so a
        database dump cannot be scanned for accounts sharing a token."""
        a = snapchat_crypto.encrypt_token("same-token")
        b = snapchat_crypto.encrypt_token("same-token")
        self.assertNotEqual(a, b)
        self.assertEqual(snapchat_crypto.decrypt_token(a), snapchat_crypto.decrypt_token(b))

    def test_wrong_key_is_reported_not_silently_empty(self):
        encrypted = snapchat_crypto.encrypt_token("token")
        with patch.dict(os.environ, {"SNAPCHAT_ENCRYPTION_KEY": "a-completely-different-key"}):
            with patch.object(snapchat_crypto, "_legacy_key",
                              return_value=base64.urlsafe_b64encode(b"z" * 32)):
                with self.assertRaises(ValueError):
                    snapchat_crypto.decrypt_token(encrypted)


# ======================================================================
# 2. OAuth
# ======================================================================
class TestOAuth(unittest.TestCase):
    def test_authorization_url_matches_snaps_contract(self):
        url, state = snapchat_oauth.generate_authorization_url()
        self.assertTrue(url.startswith("https://accounts.snapchat.com/login/oauth2/authorize?"))
        self.assertIn("client_id=test-client-id", url)
        self.assertIn("response_type=code", url)
        self.assertIn("scope=snapchat-profile-api", url)
        self.assertIn(f"state={state}", url)
        self.assertIn(
            "redirect_uri=https%3A%2F%2Freels-dashboard-omega.vercel.app%2Fapi%2Fsnapchat%2Foauth%2Fcallback",
            url,
        )

    def test_state_is_unguessable_and_unique(self):
        states = {snapchat_oauth.generate_authorization_url()[1] for _ in range(25)}
        self.assertEqual(len(states), 25)
        self.assertTrue(all(len(s) >= 32 for s in states))

    def test_missing_client_id_is_a_config_error_not_a_broken_url(self):
        with patch.dict(os.environ, {"SNAPCHAT_CLIENT_ID": ""}), patch("snapchat_oauth.get_client_id", return_value=""):
            with self.assertRaises(Exception) as ctx:
                snapchat_oauth.generate_authorization_url()
            self.assertIn("SNAPCHAT_CLIENT_ID", str(ctx.exception))

    @patch("snapchat_oauth.requests.post")
    def test_code_exchange_stores_encrypted_tokens(self, mock_post):
        mock_post.return_value = snap_response(200, {
            "access_token": "at-live",
            "refresh_token": "rt-live",
            "expires_in": 1800,
            "scope": "snapchat-profile-api",
        })

        result = snapchat_oauth.exchange_code_for_tokens("auth-code", redirect_uri="https://x/cb")

        sent = mock_post.call_args
        self.assertEqual(sent[0][0], "https://accounts.snapchat.com/login/oauth2/access_token")
        self.assertEqual(sent[1]["data"]["grant_type"], "authorization_code")
        self.assertEqual(sent[1]["data"]["code"], "auth-code")
        self.assertEqual(sent[1]["data"]["redirect_uri"], "https://x/cb")

        self.assertEqual(result["access_token"], "at-live")
        # What gets persisted is ciphertext, and it opens back to the token.
        self.assertNotIn("at-live", result["access_token_encrypted"])
        self.assertEqual(snapchat_crypto.decrypt_token(result["access_token_encrypted"]), "at-live")
        self.assertEqual(snapchat_crypto.decrypt_token(result["refresh_token_encrypted"]), "rt-live")
        self.assertGreater(result["expires_at"], dt.datetime.now(dt.timezone.utc))

    @patch("snapchat_oauth.requests.post")
    def test_revoked_authorization_raises_auth_error(self, mock_post):
        mock_post.return_value = snap_response(400, {
            "error": "invalid_grant",
            "error_description": "Refresh token has been revoked",
        })
        with self.assertRaises(SnapAuthError):
            snapchat_oauth.refresh_access_token(snapchat_crypto.encrypt_token("rt-dead"))

    @patch("snapchat_oauth.requests.post")
    def test_snap_outage_is_transient_not_a_revocation(self, mock_post):
        mock_post.return_value = snap_response(503, {"error": "service_unavailable"})
        with self.assertRaises(SnapTransientError):
            snapchat_oauth.refresh_access_token(snapchat_crypto.encrypt_token("rt-live"))

    @patch("snapchat_oauth.requests.post")
    def test_refresh_keeps_existing_refresh_token_when_snap_does_not_rotate(self, mock_post):
        mock_post.return_value = snap_response(200, {"access_token": "at-2", "expires_in": 1800})
        result = snapchat_oauth.refresh_access_token(snapchat_crypto.encrypt_token("rt-1"))
        self.assertEqual(snapchat_crypto.decrypt_token(result["refresh_token_encrypted"]), "rt-1")


class TestAutomaticTokenRefresh(unittest.TestCase):
    def test_valid_token_is_used_without_a_refresh_call(self):
        account = make_account()
        with patch.object(snapchat_oauth, "refresh_access_token") as mock_refresh:
            token = snapchat_oauth.ensure_valid_token(account)
        self.assertEqual(token, "access-aaa")
        mock_refresh.assert_not_called()

    def test_expiring_token_refreshes_and_persists_before_returning(self):
        """The new pair must be written before the token is handed out: a
        crash in between would otherwise strand the account on a refresh
        token Snap has already rotated away."""
        account = make_account(
            token_expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)
        )
        fake_db = MagicMock()
        with patch.object(snapchat_oauth, "refresh_access_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "at-fresh",
                "refresh_token": "rt-fresh",
                "expires_in": 1800,
                "expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30),
                "scope": "snapchat-profile-api",
                "access_token_encrypted": snapchat_crypto.encrypt_token("at-fresh"),
                "refresh_token_encrypted": snapchat_crypto.encrypt_token("rt-fresh"),
            }
            token = snapchat_oauth.ensure_valid_token(account, db_module=fake_db)

        self.assertEqual(token, "at-fresh")
        fake_db.update_snapchat_tokens.assert_called_once()
        self.assertEqual(fake_db.update_snapchat_tokens.call_args[1]["account_id"], 1)
        # The caller's dict is updated too, so a second call does not re-refresh.
        self.assertEqual(snapchat_crypto.decrypt_token(account["access_token_encrypted"]), "at-fresh")

    def test_already_expired_token_refreshes(self):
        account = make_account(
            token_expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
        )
        with patch.object(snapchat_oauth, "refresh_access_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "at-new", "refresh_token": "rt", "expires_in": 1800,
                "expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30),
                "scope": "snapchat-profile-api",
                "access_token_encrypted": snapchat_crypto.encrypt_token("at-new"),
                "refresh_token_encrypted": snapchat_crypto.encrypt_token("rt"),
            }
            self.assertEqual(snapchat_oauth.ensure_valid_token(account), "at-new")

    def test_expired_with_no_refresh_token_demands_reconnection(self):
        account = make_account(
            refresh_token_encrypted=None,
            token_expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
        )
        with self.assertRaises(SnapAuthError):
            snapchat_oauth.ensure_valid_token(account)

    def test_never_connected_account_is_rejected(self):
        with self.assertRaises(SnapAuthError):
            snapchat_oauth.ensure_valid_token(
                {"id": 9, "name": "Fresh", "access_token_encrypted": None,
                 "refresh_token_encrypted": None}
            )


# ======================================================================
# 3. media validation, encryption, chunking
# ======================================================================
class TestMediaValidation(unittest.TestCase):
    def test_probe_reads_duration_and_resolution(self):
        info = snapchat_media.probe_mp4(make_mp4(12.5, 1080, 1920))
        self.assertAlmostEqual(info["duration_seconds"], 12.5, places=2)
        self.assertEqual((info["width"], info["height"]), (1080, 1920))

    def test_rotation_matrix_is_applied(self):
        """A phone-shot portrait clip stores a landscape track plus a 90°
        matrix. Reading tkhd naively would reject it as 1920x1080."""
        info = snapchat_media.probe_mp4(make_mp4(10, 1080, 1920, rotate=True))
        self.assertEqual((info["width"], info["height"]), (1080, 1920))

    def test_non_mp4_is_rejected(self):
        with self.assertRaises(SnapMediaError):
            snapchat_media.probe_mp4(b"\x00" * 512)

    def test_extension_is_checked_before_anything_is_read(self):
        with self.assertRaises(SnapMediaError) as ctx:
            snapchat_media.validate(make_mp4(10), file_name="clip.mov")
        self.assertIn(".mp4", str(ctx.exception))

    def test_spotlight_duration_window_is_6_to_60(self):
        snapchat_media.validate(make_mp4(6.0), placement="spotlight")
        snapchat_media.validate(make_mp4(60.0), placement="spotlight")
        with self.assertRaises(SnapMediaError) as short:
            snapchat_media.validate(make_mp4(5.0), placement="spotlight")
        self.assertIn("at least 6s", str(short.exception))
        with self.assertRaises(SnapMediaError) as long:
            snapchat_media.validate(make_mp4(61.0), placement="spotlight")
        self.assertIn("at most 60s", str(long.exception))

    def test_story_duration_window_is_5_to_60(self):
        snapchat_media.validate(make_mp4(5.0), placement="story")
        with self.assertRaises(SnapMediaError):
            snapchat_media.validate(make_mp4(4.0), placement="story")
        with self.assertRaises(SnapMediaError):
            snapchat_media.validate(make_mp4(61.0), placement="story")

    def test_five_second_clip_is_valid_for_story_but_not_spotlight(self):
        """The two placements really do differ by one second, and the
        publisher must not use one window for both."""
        clip = make_mp4(5.2)
        snapchat_media.validate(clip, placement="story")
        with self.assertRaises(SnapMediaError):
            snapchat_media.validate(clip, placement="spotlight")

    def test_resolution_floor_is_540x960(self):
        snapchat_media.validate(make_mp4(10, 540, 960))
        with self.assertRaises(SnapMediaError) as ctx:
            snapchat_media.validate(make_mp4(10, 480, 854))
        self.assertIn("540x960", str(ctx.exception))

    def test_error_names_the_actual_value_so_a_creator_can_fix_it(self):
        with self.assertRaises(SnapMediaError) as ctx:
            snapchat_media.validate(make_mp4(3.4), placement="spotlight")
        self.assertIn("3.4", str(ctx.exception))


class TestMediaEncryption(unittest.TestCase):
    def test_aes_256_cbc_round_trip_with_base64_raw_key(self):
        """Snap decrypts with the key/iv we send, so the base64 must be of
        the RAW 32 bytes (openssl's `xxd -r -p | base64`), not of the hex."""
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        plaintext = b"snapchat-media-payload" * 100
        ciphertext, key_b64, iv_b64 = snapchat_media.encrypt_media(plaintext)

        key = base64.b64decode(key_b64)
        iv = base64.b64decode(iv_b64)
        self.assertEqual(len(key), 32)
        self.assertEqual(len(iv), 16)

        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        self.assertEqual(unpadder.update(padded) + unpadder.finalize(), plaintext)

    def test_every_upload_uses_a_fresh_key(self):
        _, k1, i1 = snapchat_media.encrypt_media(b"a" * 64)
        _, k2, i2 = snapchat_media.encrypt_media(b"a" * 64)
        self.assertNotEqual(k1, k2)
        self.assertNotEqual(i1, i2)

    def test_ciphertext_is_block_aligned_and_padded(self):
        ciphertext, _, _ = snapchat_media.encrypt_media(b"x" * 16)
        self.assertEqual(len(ciphertext) % 16, 0)
        self.assertEqual(len(ciphertext), 32)   # PKCS#7 adds a full block

    def test_chunking_numbers_parts_from_one(self):
        parts = snapchat_media.chunk_media(b"x" * 100, chunk_bytes=30)
        self.assertEqual([n for n, _ in parts], [1, 2, 3, 4])
        self.assertEqual(b"".join(c for _, c in parts), b"x" * 100)

    def test_single_part_upload_is_still_a_part(self):
        parts = snapchat_media.chunk_media(b"x" * 10)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][0], 1)

    def test_too_many_parts_is_refused_before_upload(self):
        with self.assertRaises(SnapMediaError) as ctx:
            snapchat_media.chunk_media(b"x" * (36 * 10), chunk_bytes=10)
        self.assertIn("35", str(ctx.exception))

    def test_prepare_validates_then_encrypts(self):
        prepared = snapchat_media.prepare(make_mp4(10), placement="spotlight",
                                          file_name="clip.mp4")
        self.assertEqual(prepared["info"]["width"], 1080)
        self.assertEqual(len(base64.b64decode(prepared["key_b64"])), 32)
        self.assertGreaterEqual(len(prepared["parts"]), 1)

    def test_prepare_refuses_a_bad_file_before_spending_cpu_on_encryption(self):
        with patch.object(snapchat_media, "encrypt_media") as mock_encrypt:
            with self.assertRaises(SnapMediaError):
                snapchat_media.prepare(make_mp4(2.0), placement="spotlight")
        mock_encrypt.assert_not_called()


# ======================================================================
# 4. the API client's wire contract
# ======================================================================
class TestApiClientContract(unittest.TestCase):
    @patch("snapchat_api.requests.request")
    def test_my_profile_endpoint_and_parsing(self, mock_req):
        mock_req.return_value = snap_response(200, {
            "request_status": "SUCCESS",
            "public_profile": {
                "id": "d6ed1cac", "organization_id": "af00bec3",
                "display_name": "test user", "snap_user_name": "0043d29b",
                "profile_type": "PUBLIC_PROFILE",
            },
        })
        profile = snapchat_api.get_my_profile("at")
        self.assertEqual(
            mock_req.call_args[0][1],
            "https://businessapi.snapchat.com/v1/public_profiles/my_profile",
        )
        self.assertEqual(mock_req.call_args[1]["headers"]["Authorization"], "Bearer at")
        self.assertEqual(profile["id"], "d6ed1cac")

    @patch("snapchat_api.requests.request")
    def test_create_media_sends_type_name_key_iv(self, mock_req):
        mock_req.return_value = snap_response(200, {
            "request_status": "SUCCESS",
            "media_id": "media-1",
            "add_path": "/us/v1/public_profiles/p1/media/media-1/multipart-upload",
            "finalize_path": "/us/v1/public_profiles/p1/media/media-1/multipart-upload",
        })
        result = snapchat_api.create_media("at", "p1", "clip.mp4", "KEY64", "IV64")

        url = mock_req.call_args[0][1]
        body = mock_req.call_args[1]["json"]
        self.assertEqual(url, "https://businessapi.snapchat.com/v1/public_profiles/p1/media")
        self.assertEqual(body, {"type": "VIDEO", "name": "clip.mp4", "key": "KEY64", "iv": "IV64"})
        self.assertEqual(result["media_id"], "media-1")

    @patch("snapchat_api.requests.request")
    def test_upload_paths_from_snap_are_used_verbatim(self, mock_req):
        """add_path arrives with a region prefix ("/us/..."). Rebuilding it
        by hand would send the upload to the wrong region."""
        mock_req.return_value = snap_response(200, {"request_status": "SUCCESS"})
        snapchat_api.upload_part("at", "/us/v1/public_profiles/p1/media/m1/multipart-upload", 2, b"bytes")

        self.assertEqual(
            mock_req.call_args[0][1],
            "https://businessapi.snapchat.com/us/v1/public_profiles/p1/media/m1/multipart-upload",
        )
        self.assertEqual(mock_req.call_args[1]["data"], {"action": "ADD", "part_number": "2"})
        self.assertIn("file", mock_req.call_args[1]["files"])

    @patch("snapchat_api.requests.request")
    def test_finalize_sends_the_finalize_action(self, mock_req):
        mock_req.return_value = snap_response(200, {"request_status": "SUCCESS"})
        snapchat_api.finalize_upload("at", "/us/v1/x/multipart-upload")
        self.assertEqual(mock_req.call_args[1]["data"], {"action": "FINALIZE"})

    @patch("snapchat_api.requests.request")
    def test_spotlight_body_matches_the_documented_fields(self, mock_req):
        mock_req.return_value = snap_response(200, {
            "request_status": "SUCCESS", "spotlight_id": "spot-1",
            "request_id": "req-1",
        })
        result = snapchat_api.post_spotlight(
            "at", "p1", "media-1", description="hello #world",
            locale="en_US", skip_save_to_profile=True,
        )
        self.assertEqual(
            mock_req.call_args[0][1],
            "https://businessapi.snapchat.com/v1/public_profiles/p1/spotlights",
        )
        self.assertEqual(mock_req.call_args[1]["json"], {
            "media_id": "media-1",
            "locale": "en_US",
            "skip_save_to_profile": True,
            "description": "hello #world",
        })
        self.assertEqual(result["spotlight_id"], "spot-1")

    @patch("snapchat_api.requests.request")
    def test_spotlight_description_is_capped_at_160(self, mock_req):
        mock_req.return_value = snap_response(200, {"request_status": "SUCCESS", "spotlight_id": "s"})
        snapchat_api.post_spotlight("at", "p1", "m1", description="x" * 400)
        self.assertEqual(len(mock_req.call_args[1]["json"]["description"]), 160)

    @patch("snapchat_api.requests.request")
    def test_story_body_is_only_the_media_id(self, mock_req):
        """Snap's Post Story takes media_id alone, and returns no story id."""
        mock_req.return_value = snap_response(200, {
            "request_id": "5007083c", "request_status": "SUCCESS",
        })
        result = snapchat_api.post_story("at", "p1", "media-9")
        self.assertEqual(
            mock_req.call_args[0][1],
            "https://businessapi.snapchat.com/v1/public_profiles/p1/stories",
        )
        self.assertEqual(mock_req.call_args[1]["json"], {"media_id": "media-9"})
        self.assertEqual(result, {"request_id": "5007083c"})

    @patch("snapchat_api.requests.request")
    def test_request_status_error_on_http_200_is_a_failure(self, mock_req):
        """Snap can answer 200 OK with request_status ERROR. Trusting the
        HTTP code alone would report a rejected publish as a success."""
        mock_req.return_value = snap_response(200, {
            "request_status": "ERROR",
            "error_code": "INVALID_MEDIA",
            "debug_message": "media failed validation",
            "request_id": "req-9",
        })
        with self.assertRaises(SnapMediaError) as ctx:
            snapchat_api.post_spotlight("at", "p1", "m1")
        self.assertEqual(ctx.exception.error_code, "INVALID_MEDIA")
        self.assertEqual(ctx.exception.request_id, "req-9")

    @patch("snapchat_api.requests.request")
    def test_media_expired_is_transient(self, mock_req):
        mock_req.return_value = snap_response(400, {
            "request_status": "ERROR", "error_code": "MEDIA_EXPIRED",
            "debug_message": "media object expired",
        })
        with self.assertRaises(SnapTransientError):
            snapchat_api.post_story("at", "p1", "old-media")

    @patch("snapchat_api.requests.request")
    def test_401_is_an_auth_error(self, mock_req):
        mock_req.return_value = snap_response(401, {"request_status": "ERROR"})
        with self.assertRaises(SnapAuthError):
            snapchat_api.get_my_profile("dead-token")

    @patch("snapchat_api.time.sleep", lambda *_: None)
    @patch("snapchat_api.requests.request")
    def test_server_error_is_retried_then_surfaces_as_transient(self, mock_req):
        mock_req.return_value = snap_response(502, {"request_status": "ERROR"})
        with self.assertRaises(SnapTransientError):
            snapchat_api.get_my_profile("at")
        self.assertEqual(mock_req.call_count, snapchat_api.HTTP_RETRIES + 1)

    @patch("snapchat_api.time.sleep", lambda *_: None)
    @patch("snapchat_api.requests.request")
    def test_network_timeout_becomes_transient(self, mock_req):
        import requests as _requests
        mock_req.side_effect = _requests.Timeout("read timed out")
        with self.assertRaises(SnapTransientError):
            snapchat_api.get_my_profile("at")

    @patch("snapchat_api.requests.request")
    def test_create_media_without_upload_paths_raises(self, mock_req):
        """A container with no upload path is unusable; the old code invented
        a media_id here and reported success."""
        mock_req.return_value = snap_response(200, {"request_status": "SUCCESS"})
        with self.assertRaises(SnapApiError):
            snapchat_api.create_media("at", "p1", "n", "k", "i")


# ======================================================================
# 5. the publish pipeline
# ======================================================================
class TestPublishPipeline(unittest.TestCase):
    def setUp(self):
        self.account = make_account()
        self.video = make_mp4(12.0, 1080, 1920)

    @contextlib.contextmanager
    def _patched(self):
        """Stand in for every Snap API call the pipeline makes, with the
        response shapes Snap documents. Yields the mocks so each test can
        assert on the calls that were actually made."""
        names = ("get_my_profile", "create_media", "upload_part", "finalize_upload",
                 "post_spotlight", "post_story", "latest_story")
        with patch.multiple("snapchat_poster.snapchat_api",
                            **{n: DEFAULT for n in names}) as mocks:
            mocks["get_my_profile"].return_value = {"id": "profile-aaa"}
            mocks["create_media"].return_value = {
                "media_id": "media-1",
                "add_path": "/us/v1/add",
                "finalize_path": "/us/v1/finalize",
            }
            mocks["upload_part"].return_value = {"request_status": "SUCCESS"}
            mocks["finalize_upload"].return_value = {"request_status": "SUCCESS"}
            mocks["post_spotlight"].return_value = {"spotlight_id": "spot-1", "request_id": "r1"}
            mocks["post_story"].return_value = {"request_id": "r2"}
            mocks["latest_story"].return_value = {"id": "story-1", "created_at": "2026-09-08"}
            yield mocks

    def test_spotlight_publish_runs_the_documented_sequence(self):
        with self._patched() as mocks:
            result = snapchat_poster.publish(
                media_bytes=self.video, account=self.account,
                placement="spotlight", caption="Sunset dive #ocean",
            )

        mocks["create_media"].assert_called_once()
        mocks["upload_part"].assert_called_once()
        mocks["finalize_upload"].assert_called_once()
        mocks["post_spotlight"].assert_called_once()
        mocks["post_story"].assert_not_called()

        self.assertEqual(result["post_id"], "spot-1")
        self.assertEqual(result["post_id_kind"], "spotlight_id")
        self.assertEqual(result["media_id"], "media-1")
        self.assertEqual(result["locale"], "en_US")
        self.assertEqual(result["status"], "published")
        self.assertIn("#ocean", result["description"])

    def test_story_publish_resolves_the_id_snap_does_not_return(self):
        with self._patched() as mocks:
            result = snapchat_poster.publish(
                media_bytes=make_mp4(5.5), account=self.account, placement="story",
            )
        mocks["post_story"].assert_called_once()
        self.assertEqual(result["post_id"], "story-1")
        self.assertEqual(result["post_id_kind"], "story_id")

    def test_story_still_succeeds_when_the_id_lookup_fails(self):
        """The story is live either way — failing to read its id back must
        not turn a successful publish into a failure."""
        with self._patched() as mocks:
            mocks["latest_story"].side_effect = SnapTransientError("listing unavailable")
            result = snapchat_poster.publish(
                media_bytes=make_mp4(5.5), account=self.account, placement="story",
            )
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["post_id_kind"], "request_id")
        self.assertEqual(result["post_id"], "r2")

    def test_multipart_upload_sends_every_part_in_order(self):
        with patch.object(snapchat_media, "CHUNK_BYTES", 512):
            with self._patched() as mocks:
                snapchat_poster.publish(media_bytes=self.video, account=self.account)
        part_numbers = [call[0][2] for call in mocks["upload_part"].call_args_list]
        self.assertGreater(len(part_numbers), 1)
        self.assertEqual(part_numbers, list(range(1, len(part_numbers) + 1)))

    def test_encryption_key_given_to_snap_decrypts_the_uploaded_bytes(self):
        """End-to-end proof the pipeline hands Snap a usable key: reassemble
        the parts it uploaded and decrypt them with the key it declared."""
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        with patch.object(snapchat_media, "CHUNK_BYTES", 1024):
            with self._patched() as mocks:
                snapchat_poster.publish(media_bytes=self.video, account=self.account)

        key = base64.b64decode(mocks["create_media"].call_args[1]["key_b64"])
        iv = base64.b64decode(mocks["create_media"].call_args[1]["iv_b64"])
        ciphertext = b"".join(call[0][3] for call in mocks["upload_part"].call_args_list)

        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        self.assertEqual(unpadder.update(padded) + unpadder.finalize(), self.video)

    def test_invalid_media_never_reaches_the_upload(self):
        with self._patched() as mocks:
            with self.assertRaises(SnapMediaError):
                snapchat_poster.publish(media_bytes=make_mp4(2.0), account=self.account)
        mocks["create_media"].assert_not_called()
        mocks["post_spotlight"].assert_not_called()

    def test_publish_failure_raises_instead_of_reporting_success(self):
        with self._patched() as mocks:
            mocks["post_spotlight"].side_effect = SnapApiError("Spotlight rejected")
            with self.assertRaises(SnapApiError):
                snapchat_poster.publish(media_bytes=self.video, account=self.account)

    def test_a_token_for_another_profile_is_refused(self):
        """The scheduled job names account A's profile, but the credential
        belongs to profile B — publishing anyway would post to the wrong
        creator's account."""
        with self._patched() as mocks:
            mocks["get_my_profile"].return_value = {"id": "profile-SOMEONE-ELSE"}
            with self.assertRaises(SnapAuthError) as ctx:
                snapchat_poster.publish(media_bytes=self.video, account=self.account)
        self.assertIn("profile-aaa", str(ctx.exception))
        mocks["create_media"].assert_not_called()

    def test_unknown_placement_is_rejected(self):
        with self.assertRaises(SnapApiError):
            snapchat_poster.publish(media_bytes=self.video, account=self.account,
                                    placement="saved_story")

    def test_skip_save_to_profile_is_forwarded(self):
        with self._patched() as mocks:
            snapchat_poster.publish(media_bytes=self.video, account=self.account,
                                    skip_save_to_profile=True)
        self.assertIs(mocks["post_spotlight"].call_args[1]["skip_save_to_profile"], True)

    def test_locale_falls_back_to_the_account_default(self):
        account = make_account(default_locale="fr_FR")
        with self._patched() as mocks:
            snapchat_poster.publish(media_bytes=self.video, account=account)
        self.assertEqual(mocks["post_spotlight"].call_args[1]["locale"], "fr_FR")


class TestDescriptionBuilder(unittest.TestCase):
    def test_tags_are_appended_without_duplicating(self):
        desc = snapchat_poster.build_description("Sunset #ocean", ["ocean", "diving"], ["reef"])
        self.assertEqual(desc.count("#ocean"), 1)
        self.assertIn("#diving", desc)
        self.assertIn("#reef", desc)

    def test_never_exceeds_160_characters(self):
        desc = snapchat_poster.build_description(
            "word " * 60, ["a" * 30, "b" * 30], footer="the footer",
        )
        self.assertLessEqual(len(desc), 160)

    def test_a_tag_is_dropped_rather_than_truncated_mid_word(self):
        desc = snapchat_poster.build_description("x" * 150, ["averylonghashtag"])
        self.assertNotIn("#averylong", desc)


# ======================================================================
# 6. health check
# ======================================================================
class TestHealthCheck(unittest.TestCase):
    def test_healthy_account_reports_its_profile(self):
        with patch.object(snapchat_poster.snapchat_api, "get_my_profile",
                          return_value={"id": "profile-aaa", "display_name": "Whale Life",
                                        "snap_user_name": "whalelife"}):
            health = snapchat_poster.check_health(make_account())
        self.assertTrue(health["healthy"])
        self.assertTrue(health["profile_matches_stored"])
        self.assertEqual(health["token_status"], "valid")

    def test_revoked_account_reports_the_reason_without_raising(self):
        with patch.object(snapchat_poster.snapchat_api, "get_my_profile",
                          side_effect=SnapAuthError("Access revoked")):
            health = snapchat_poster.check_health(make_account())
        self.assertFalse(health["healthy"])
        self.assertEqual(health["token_status"], "invalid")
        self.assertIn("revoked", health["error"].lower())

    def test_profile_mismatch_is_visible(self):
        with patch.object(snapchat_poster.snapchat_api, "get_my_profile",
                          return_value={"id": "someone-else"}):
            health = snapchat_poster.check_health(make_account())
        self.assertFalse(health["profile_matches_stored"])


# ======================================================================
# 7. secrets never reach logs or responses
# ======================================================================
class TestSecretHygiene(unittest.TestCase):
    def test_redactor_masks_tokens_and_secrets(self):
        raw = ("access_token: sk-live-abcdef123456 refresh_token=rt-abcdef123456 "
               "client_secret: my_super_secret Authorization: Bearer eyJhbGciOiJIUzI1")
        out = applog.redact_secrets(raw)
        for secret in ("sk-live-abcdef123456", "rt-abcdef123456", "my_super_secret",
                       "eyJhbGciOiJIUzI1"):
            self.assertNotIn(secret, out)
        self.assertIn("[REDACTED]", out)

    def test_redactor_masks_a_database_password(self):
        out = applog.redact_secrets("postgresql://postgres:hunter2pass@host:6543/postgres")
        self.assertNotIn("hunter2pass", out)

    def test_redactor_masks_configured_env_values_anywhere(self):
        with patch.dict(os.environ, {"SNAPCHAT_CLIENT_SECRET": "zqx-secret-value-99"}):
            out = applog.redact_secrets("Snap said no to zqx-secret-value-99 today")
        self.assertNotIn("zqx-secret-value-99", out)

    def test_config_status_exposes_booleans_not_the_secret(self):
        with patch.dict(os.environ, {"SNAPCHAT_CLIENT_SECRET": "super-secret-value"}):
            status = snapchat_oauth.config_status()
        self.assertTrue(status["client_secret_configured"])
        self.assertNotIn("super-secret-value", str(status))

    def test_oauth_module_does_not_log_the_payload(self):
        """A failed token exchange must log Snap's error code, never the
        client secret or the code that was sent."""
        with patch("snapchat_oauth.requests.post") as mock_post:
            mock_post.return_value = snap_response(400, {"error": "invalid_grant"})
            with patch("snapchat_oauth.logger.warning") as mock_log:
                with self.assertRaises(SnapAuthError):
                    snapchat_oauth.exchange_code_for_tokens("the-secret-code")
        logged = " ".join(str(a) for a in mock_log.call_args[0])
        self.assertNotIn("the-secret-code", logged)
        self.assertNotIn("test-client-secret", logged)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestMonetizationAdvisory(unittest.TestCase):
    """Snap accepts a 6s Spotlight but only pays on 30s+. That gap is a
    reporting concern, never a rejection."""

    def test_a_short_spotlight_still_validates(self):
        info = snapchat_media.validate(make_mp4(12.0), placement="spotlight")
        self.assertFalse(info["monetizable_length"])
        self.assertIn("30s", info["monetization_note"])

    def test_a_thirty_second_spotlight_is_monetizable(self):
        info = snapchat_media.validate(make_mp4(30.0), placement="spotlight")
        self.assertTrue(info["monetizable_length"])
        self.assertNotIn("monetization_note", info)

    def test_stories_carry_no_monetization_length_claim(self):
        """Story revenue is mid-roll and gated on account-level view time,
        not on the clip's length, so asserting a threshold here would be
        inventing a rule Snap does not publish."""
        info = snapchat_media.validate(make_mp4(10.0), placement="story")
        self.assertFalse(info["monetizable_length"])
        self.assertNotIn("monetization_note", info)
