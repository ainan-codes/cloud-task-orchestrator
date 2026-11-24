"""
snapchat_errors.py — the error taxonomy every Snapchat call funnels into.

The scheduler has to answer one question about a failure: retry, stop, or
pause the whole account? String-sniffing an exception message can't answer
that reliably, so every Snap call raises one of these instead.

  SnapAuthError       token expired / revoked / not allowlisted.
                      The account is broken, not the post — the queue stays
                      pending and the account is flagged for re-authorization.
  SnapMediaError      the file itself is wrong (bad codec, 3s long, 480p).
                      Retrying uploads the same bad bytes, so it fails at once.
  SnapTransientError  timeout, 5xx, 429, expired media container.
                      Worth another attempt on the next run.
  SnapApiError        anything else Snap rejected — treated as permanent.
"""


class SnapError(Exception):
    """Base for every Snapchat failure. Carries Snap's own error fields."""

    retryable = False

    def __init__(self, message, error_code=None, request_id=None, http_status=None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.request_id = request_id
        self.http_status = http_status

    def as_dict(self):
        return {
            "message": self.message,
            "error_code": self.error_code,
            "request_id": self.request_id,
            "http_status": self.http_status,
            "kind": type(self).__name__,
            "retryable": self.retryable,
        }


class SnapAuthError(SnapError):
    """Access revoked, token dead, or the app is not allowlisted for this API."""
    retryable = False


class SnapMediaError(SnapError):
    """The media does not meet Snap's published requirements."""
    retryable = False


class SnapTransientError(SnapError):
    """A temporary condition — retry on the next scheduler pass."""
    retryable = True


class SnapApiError(SnapError):
    """Snap rejected the request for a reason that will not change on retry."""
    retryable = False


class SnapConfigError(SnapError):
    """The integration itself is misconfigured (missing client id/secret/URI)."""
    retryable = False


# Snap error codes that describe a condition which clears on its own.
TRANSIENT_ERROR_CODES = {
    "MEDIA_EXPIRED",        # container passed its 24h life — redo the upload
    "INTERNAL_ERROR",
    "SERVICE_UNAVAILABLE",
    "RATE_LIMIT_EXCEEDED",
    "TIMEOUT",
}


def is_snap_auth_error(err):
    """True when a failure means 'this account needs re-authorization'.

    Kept as a function (rather than an isinstance check at every call site)
    because the runner also feeds it plain strings read back from the
    scheduled_posts.error column of an older row.
    """
    if isinstance(err, SnapError):
        return isinstance(err, SnapAuthError)
    s = str(err).lower()
    return any(k in s for k in (
        "snapauth",
        "unauthorized",
        "forbidden",
        "permission",
        "invalid_token",
        "invalid_grant",
        "access_denied",
        "re-authorization",
        "http 401",
        "http 403",
    ))


def is_retryable(err):
    """True when the scheduler should try this post again later."""
    if isinstance(err, SnapError):
        return err.retryable
    return isinstance(err, (TimeoutError, ConnectionError))
