"""GitHub sign-in: only the logins in REVIEW_UI_GITHUB_USERS get a session (a signed, HTTP-only cookie)."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from review_api.wiring import server

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"

router = APIRouter()


def current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="sign in first")
    return user


@router.get("/auth/login")
def login(request: Request) -> RedirectResponse:
    """GitHub returns to the OAuth app's registered callback URL (https://<host>/auth/callback). None is sent, so
    the app and its GitHub settings cannot disagree about it."""
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    query = urlencode(
        {
            "client_id": server().settings.github_client_id,
            "scope": "read:user",
            "state": state,
            "allow_signup": "false",
        }
    )
    return RedirectResponse(f"{AUTHORIZE_URL}?{query}")


@router.get("/auth/callback", name="auth_callback")
def callback(request: Request, code: str = "", state: str = ""):
    settings = server().settings
    expected = request.session.pop("oauth_state", None)
    if not code or not expected or not secrets.compare_digest(state, expected):
        # An old GitHub page (reloaded, or from an earlier attempt): start a fresh sign-in once
        if not request.session.pop("oauth_retried", False):
            request.session["oauth_retried"] = True
            return RedirectResponse("/auth/login")
        return HTMLResponse(
            '<p>The sign-in could not be completed. <a href="/auth/login">Try again</a>.</p>', status_code=400
        )
    request.session.pop("oauth_retried", None)
    token = (
        httpx.post(
            TOKEN_URL,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret.get_secret_value(),
                "code": code,
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
        .json()
        .get("access_token")
    )
    if not token:
        raise HTTPException(status_code=400, detail="GitHub did not grant access")
    user = httpx.get(USER_URL, headers={"Authorization": f"Bearer {token}"}, timeout=20).json()
    login_name = str(user.get("login", "")).lower()
    if login_name not in settings.allowed_users():
        return HTMLResponse("<p>This GitHub account is not allowed to use the review app.</p>", status_code=403)
    request.session["user"] = {
        "login": login_name,
        "name": user.get("name") or login_name,
        "avatar": user.get("avatar_url"),
    }
    return RedirectResponse("/")


@router.post("/auth/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


_signed = Depends(current_user)


@router.get("/api/me")
def me(user: dict = _signed) -> dict:
    return user
