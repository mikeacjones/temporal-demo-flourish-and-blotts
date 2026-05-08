"""HMAC-signed tokens for customer HITL Approve/Deny email links.

Used by both the API (to mint links and verify incoming ones) and the
CustomerConfirmationWorkflow (which calls an activity to mint links for outgoing emails).
The secret lives in HITL_TOKEN_SECRET; changing it invalidates any outstanding links.
"""
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired


def _signer(secret: str) -> TimestampSigner:
    # Salt namespaces the signature so the same secret can sign different kinds
    # of tokens without cross-use.
    return TimestampSigner(secret, salt="flourish-blotts/hitl-decision")


def make_token(order_id: str, decision: str, secret: str) -> str:
    """Mint a token encoding (order_id, decision) with a timestamp.

    decision must be 'approve' or 'deny'. The token carries a timestamp so the
    API can enforce an expiry window on verification.
    """
    if decision not in ("approve", "deny"):
        raise ValueError(f"decision must be 'approve' or 'deny', got {decision!r}")
    payload = f"{order_id}|{decision}"
    return _signer(secret).sign(payload.encode("utf-8")).decode("utf-8")


def verify_token(token: str, secret: str, max_age_seconds: int) -> tuple[str, str]:
    """Verify a token and return (order_id, decision).

    Raises ValueError on any verification failure (bad signature, expired, malformed).
    """
    try:
        payload_bytes = _signer(secret).unsign(token, max_age=max_age_seconds)
    except SignatureExpired:
        raise ValueError("token expired")
    except BadSignature:
        raise ValueError("invalid token signature")

    try:
        order_id, decision = payload_bytes.decode("utf-8").split("|", 1)
    except ValueError:
        raise ValueError("malformed token payload")

    if decision not in ("approve", "deny"):
        raise ValueError(f"unexpected decision value {decision!r}")

    return order_id, decision
