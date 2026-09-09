"""Google sign-in for Light accounts.

The frontend obtains a Google ID token via Google Identity Services and
posts it here. We verify the token against Google's public keys, require a
verified email on the allowed Workspace domain, and store the user in a
signed session cookie. Auth is enforced (see app.main) only when
GOOGLE_CLIENT_ID is configured, so local development stays open.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import settings

logger = logging.getLogger("sunshine.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/config")
async def auth_config():
    return {
        "auth_required": settings.auth_required,
        "client_id": settings.google_client_id,
        "allowed_domain": settings.auth_allowed_domain,
    }


@router.post("/google")
async def login_with_google(request: Request, body: dict):
    if not settings.auth_required:
        raise HTTPException(status_code=400, detail="Google sign-in is not configured")

    credential = (body or {}).get("credential", "")
    if not credential:
        raise HTTPException(status_code=400, detail="credential is required")

    try:
        # Verifies signature, audience, issuer, and expiry. A little clock-skew
        # tolerance avoids spurious "token used too early" failures.
        claims = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            settings.google_client_id.strip(),
            clock_skew_in_seconds=10,
        )
    except Exception as e:
        logger.warning("Google token verification failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid Google credential")

    email = (claims.get("email") or "").lower()
    domain = settings.auth_allowed_domain.lower()
    domain_ok = claims.get("hd", "").lower() == domain or email.endswith(f"@{domain}")
    if not claims.get("email_verified") or not email or not domain_ok:
        raise HTTPException(
            status_code=403,
            detail=f"Only {settings.auth_allowed_domain} Google accounts can sign in",
        )

    user = {
        "email": email,
        "name": claims.get("name", ""),
        "picture": claims.get("picture", ""),
    }
    request.session["user"] = user
    return user


@router.get("/me")
async def me(request: Request):
    if not settings.auth_required:
        return {"email": "", "name": "Local development", "picture": ""}
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"status": "signed_out"}
