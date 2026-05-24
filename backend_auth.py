from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from fastapi import Header, HTTPException


DEFAULT_DEV_USER_ID = "dev-local-user"
DEFAULT_DEV_USER_EMAIL = "dev@example.local"


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str
    auth_provider: str


def auth_mode() -> str:
    return os.getenv("TRANSLATOR_AUTH_MODE", "dev").strip().lower() or "dev"


def auth_error_detail(message: str, code: str, action: str | None = None) -> dict[str, str]:
    detail = {
        "message": message,
        "code": code,
    }
    if action:
        detail["action"] = action
    return detail


def get_current_user(
    authorization: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
    x_dev_user_email: str | None = Header(default=None),
) -> AuthenticatedUser:
    mode = auth_mode()
    if mode in {"google", "production"}:
        return google_user_from_authorization_header(authorization)
    if mode in {"dev", "test", "local"}:
        return dev_user_from_headers(x_dev_user_id, x_dev_user_email)
    raise HTTPException(
        status_code=500,
        detail=auth_error_detail(
            "Server authentication is misconfigured.",
            "auth_mode_invalid",
            "Ask support to check TRANSLATOR_AUTH_MODE.",
        ),
    )


def dev_user_from_headers(
    x_dev_user_id: str | None,
    x_dev_user_email: str | None,
) -> AuthenticatedUser:
    user_id = (x_dev_user_id or DEFAULT_DEV_USER_ID).strip()
    email = (x_dev_user_email or DEFAULT_DEV_USER_EMAIL).strip()
    if not user_id:
        user_id = DEFAULT_DEV_USER_ID
    if not email:
        email = DEFAULT_DEV_USER_EMAIL
    return AuthenticatedUser(
        user_id=user_id,
        email=email,
        auth_provider="dev",
    )


def google_user_from_authorization_header(
    authorization: str | None,
) -> AuthenticatedUser:
    token = bearer_token(authorization)
    client_id = google_oauth_client_id()
    claims = verify_google_id_token(token=token, client_id=client_id)
    user_id = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip()
    email_verified = claims.get("email_verified")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail=auth_error_detail(
                "Your Google sign-in could not be verified.",
                "google_token_invalid",
                "Sign in again and retry.",
            ),
        )
    if not email:
        raise HTTPException(
            status_code=401,
            detail=auth_error_detail(
                "Your Google account did not provide an email address.",
                "google_email_missing",
                "Sign in with a Google account that has a verified email.",
            ),
        )
    if email_verified is not True:
        raise HTTPException(
            status_code=403,
            detail=auth_error_detail(
                "Your Google account email must be verified before you can use this service.",
                "google_email_unverified",
                "Verify your email with Google, then sign in again.",
            ),
        )
    return AuthenticatedUser(
        user_id=user_id,
        email=email,
        auth_provider="google",
    )


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail=auth_error_detail(
                "Please sign in with Google before continuing.",
                "authentication_required",
                "Sign in and retry.",
            ),
        )
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail=auth_error_detail(
                "Your sign-in session could not be verified.",
                "authorization_header_invalid",
                "Sign in again and retry.",
            ),
        )
    return token.strip()


def google_oauth_client_id() -> str:
    client_id = (
        os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
        or os.getenv("GOOGLE_CLIENT_ID", "").strip()
    )
    if not client_id:
        raise HTTPException(
            status_code=500,
            detail=auth_error_detail(
                "Google sign-in is not configured on this server.",
                "google_oauth_not_configured",
                "Ask support to configure GOOGLE_OAUTH_CLIENT_ID.",
            ),
        )
    return client_id


def verify_google_id_token(token: str, client_id: str) -> dict[str, Any]:
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=auth_error_detail(
                "Google sign-in support is not installed on this server.",
                "google_auth_dependency_missing",
                "Ask support to install the Google auth dependency.",
            ),
        ) from exc

    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            client_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail=auth_error_detail(
                "Your Google sign-in token is invalid or expired.",
                "google_token_invalid",
                "Sign in again and retry.",
            ),
        ) from exc

    if not isinstance(claims, dict):
        raise HTTPException(
            status_code=401,
            detail=auth_error_detail(
                "Your Google sign-in token is invalid.",
                "google_token_invalid",
                "Sign in again and retry.",
            ),
        )
    return claims
