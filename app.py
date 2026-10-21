"""Secure, account-linked Sentinel backend.

All persistent Sentinel state uses the managed MySQL database. Roblox credentials
are deliberately process-memory-only and are never written to the database.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlparse

import httpx
import jwt as pyjwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from storage import Storage, StorageError, utcnow_string

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
STATIC.mkdir(exist_ok=True)
app = FastAPI(title="Sentinel API", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/sentinel/static", StaticFiles(directory=STATIC), name="static")

EXE_API_BASE = os.getenv("EXE_API_BASE", "https://exe-accounts-api.onrender.com").rstrip("/")
EXE_JWT_SECRET = os.getenv("EXE_JWT_SECRET", "")
COOKIE_NAME = "sentinel_session"  # deliberately not platform-reserved app_session_id
TTL = max(300, int(os.getenv("SENTINEL_SESSION_TTL_SECONDS", str(7 * 86400))))
# A dedicated key may be supplied. Otherwise derive a namespaced encryption key
# from the private, stable database credential; neither material reaches clients.
# Rotating either credential deliberately invalidates encrypted provider tokens.
_crypto_secret = os.getenv("SENTINEL_SESSION_SECRET") or os.getenv("DATABASE_URL") or secrets.token_urlsafe(48)
fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("sentinel:exe-token:v1:" + _crypto_secret).encode()).digest()))

try:
    db = Storage()
    DB_ERROR = ""
except StorageError as exc:
    db = Storage(None)
    DB_ERROR = str(exc)
DB_READY = False

ASSET_TYPES = ["Audio", "Decal", "Video", "Mesh", "Plugin", "Animation", "Model", "Package"]
NON_ARCHIVABLE = {"Image"}
DEFAULT_CONFIG: dict[str, Any] = {
    "pollingInterval": 60, "allowFastPolling": False, "archiveDelay": 0,
    "archiveExisting": False, "saveCookies": False, "cookieSaveMode": "never",
    "autoStartMonitoring": False, "assetTypeFilters": ASSET_TYPES,
    **{f"whitelist_{kind}": [] for kind in [*ASSET_TYPES, "Image"]}, "whitelist_all": [],
}


def info(msg: str, level: str = "INFO") -> None:
    print(f"[SENTINEL][{level}] {msg}")


def require_db() -> Storage:
    if not db.configured or not DB_READY:
        raise HTTPException(503, DB_ERROR or "DATABASE_URL is not configured")
    return db


@app.on_event("startup")
async def start() -> None:
    global DB_READY
    DB_READY = False
    if not db.configured:
        info("DATABASE_URL unavailable; protected API will fail closed", "WARN")
        return
    try:
        db.init_schema()
        DB_READY = True
        info(f"{db.kind.capitalize()} schema ready")
    except Exception as exc:
        info(f"{db.kind.capitalize()} schema initialization failed: {exc}", "ERROR")


# ---- route helper: /sentinel/api is canonical; /api is a non-advertised alias ----
def api(method: str, path: str, *, auth: bool = True):
    def decorate(fn):
        app.add_api_route("/sentinel/api" + path, fn, methods=[method])
        app.add_api_route("/api" + path, fn, methods=[method], include_in_schema=False)
        return fn
    return decorate


# ---- CSRF and opaque server session ------------------------------------------------
def _origin_ok(origin: str, request: Request) -> bool:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    req_host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    host = parsed.hostname.lower()
    if host == req_host or host in {"localhost", "127.0.0.1", "::1"}:
        return True
    allowed = {item.strip().rstrip("/") for item in os.getenv("SENTINEL_PUBLIC_ORIGIN", "").split(",") if item.strip()}
    return origin.rstrip("/") in allowed


def csrf(request: Request) -> None:
    """Block cookie-authenticated cross-site writes without credentialed wildcard CORS."""
    if request.headers.get("X-Sentinel-Request") != "1":
        raise HTTPException(403, "Missing X-Sentinel-Request header")
    origin = request.headers.get("origin")
    if origin and not _origin_ok(origin, request):
        raise HTTPException(403, "Cross-origin request rejected")


def h(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def enc(value: str | None) -> str | None:
    return fernet.encrypt(value.encode()).decode() if value else None


def dec(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return fernet.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def date_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            item = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return item.replace(tzinfo=timezone.utc) if item.tzinfo is None else item.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


@dataclass
class User:
    id: str
    name: str
    email: str
    avatar_url: str
    session_hash: str
    access_token: str | None = None
    refresh_token: str | None = None

    def public(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "email": self.email, "avatar_url": self.avatar_url}


def user_from_request(request: Request, tokens: bool = False) -> User:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw or len(raw) < 32:
        raise HTTPException(401, "Authentication required")
    store = require_db()
    session_hash = h(raw)
    row = store.fetch_one("SELECT * FROM sessions WHERE token_hash=?", (session_hash,))
    if not row or not (expires := date_value(row.get("expires_at"))) or expires <= datetime.now(timezone.utc):
        if row:
            store.execute("DELETE FROM sessions WHERE token_hash=?", (session_hash,))
        raise HTTPException(401, "Invalid or expired session")
    account = store.fetch_one("SELECT id,name,email,avatar_url FROM users WHERE id=?", (row["user_id"],))
    if not account:
        store.execute("DELETE FROM sessions WHERE token_hash=?", (session_hash,))
        raise HTTPException(401, "Session account was not found")
    store.execute("UPDATE sessions SET last_seen_at=CURRENT_TIMESTAMP WHERE token_hash=?", (session_hash,))
    return User(str(account["id"]), str(account.get("name") or ""), str(account.get("email") or ""),
                str(account.get("avatar_url") or ""), session_hash,
                dec(row.get("access_token_enc")) if tokens else None,
                dec(row.get("refresh_token_enc")) if tokens else None)


def owner(user: User, profile_id: str | None) -> str:
    if profile_id and str(profile_id) != user.id:
        raise HTTPException(403, "profile_id does not match the authenticated account")
    return user.id


def cookie_settings(request: Request) -> tuple[bool, Literal["lax", "none"]]:
    force = os.getenv("SENTINEL_COOKIE_SECURE", "").lower()
    if force in {"1", "true", "yes"}:
        return True, "none"
    if force in {"0", "false", "no"}:
        return False, "lax"
    host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    public = host not in {"localhost", "127.0.0.1", "::1", "testserver"}
    return public, "none" if public else "lax"


def install_cookie(response: JSONResponse, request: Request, value: str) -> None:
    secure, samesite = cookie_settings(request)
    response.set_cookie(COOKIE_NAME, value, httponly=True, secure=secure, samesite=samesite, max_age=TTL, path="/")


def remove_cookie(response: JSONResponse, request: Request) -> None:
    secure, samesite = cookie_settings(request)
    response.delete_cookie(COOKIE_NAME, httponly=True, secure=secure, samesite=samesite, path="/")


# ---- EXE Account proxy and authoritative token verification ----------------------
def provider_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        message = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(message, str) and message.strip():
            return message.strip()[:300]
    return fallback


async def exe(method: str, path: str, payload: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5)) as client:
            response = await client.request(method, EXE_API_BASE + path, json=payload, headers=headers)
    except httpx.RequestError:
        raise HTTPException(503, "EXE account service is temporarily unavailable. Please try again shortly.")
    try:
        data: Any = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 500:
        raise HTTPException(503, "EXE account service is temporarily unavailable. Please try again shortly.")
    if response.status_code >= 400:
        raise HTTPException(response.status_code, provider_message(data, "EXE account request was rejected"))
    if not isinstance(data, dict):
        raise HTTPException(503, "EXE account service returned an invalid response")
    return data


def identity(payload: dict[str, Any]) -> dict[str, str]:
    value = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    if not isinstance(value, dict):
        raise HTTPException(401, "Invalid EXE identity response")
    user_id = value.get("id") or value.get("user_id") or value.get("userId") or value.get("sub")
    if not isinstance(user_id, (str, int)) or not str(user_id):
        raise HTTPException(401, "EXE identity response has no user id")
    email = str(value.get("email") or "")
    name = str(value.get("display_name") or value.get("displayName") or value.get("name") or value.get("username") or email.split("@", 1)[0] or "EXE Account")
    avatar = str(value.get("avatar_url") or value.get("avatarUrl") or "")
    return {"id": str(user_id), "email": email, "name": name, "avatar_url": avatar}


async def verify_access(access_token: str) -> dict[str, str]:
    if not access_token or len(access_token) < 8:
        raise HTTPException(401, "Missing EXE access token")
    if EXE_JWT_SECRET:
        try:
            claims = pyjwt.decode(access_token, EXE_JWT_SECRET, algorithms=["HS256"])
        except pyjwt.ExpiredSignatureError:
            raise HTTPException(401, "EXE access token expired")
        except pyjwt.InvalidTokenError:
            raise HTTPException(401, "Invalid EXE access token")
        return identity(claims)
    # If no shared signing secret is configured, EXE's /auth/me is authoritative.
    # Network faults fail closed; the server never invents an account identity.
    return identity(await exe("GET", "/auth/me", token=access_token))


async def create_session(request: Request, access_token: str, refresh_token: str | None = None) -> JSONResponse:
    account = await verify_access(access_token)
    store = require_db()
    store.upsert_user(account["id"], account["email"], account["name"], account["avatar_url"])
    raw = secrets.token_urlsafe(48)
    store.execute("INSERT INTO sessions (token_hash,user_id,access_token_enc,refresh_token_enc,expires_at) VALUES (?,?,?,?,?)",
                  (h(raw), account["id"], enc(access_token), enc(refresh_token), datetime.now(timezone.utc) + timedelta(seconds=TTL)))
    response = JSONResponse({"ok": True, "user": {key: account[key] for key in ("id", "name", "email")}})
    install_cookie(response, request, raw)
    return response


class RegisterBody(BaseModel):
    email: str
    password: str
    display_name: str


class LoginBody(BaseModel):
    email: str
    password: str


class TwoFactorBody(BaseModel):
    challenge_token: str
    code: str


class EmailBody(BaseModel):
    email: str


class MePatch(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None


async def provider_login(request: Request, path: str, body: dict[str, Any]) -> JSONResponse:
    csrf(request)
    response = await exe("POST", path, body)
    if response.get("requires_2fa"):
        return JSONResponse({"requires_2fa": True, "challenge_token": response.get("challenge_token", "")})
    token = response.get("access_token")
    if not isinstance(token, str):
        raise HTTPException(503, "EXE account service returned no access token")
    refresh = response.get("refresh_token") if isinstance(response.get("refresh_token"), str) else None
    return await create_session(request, token, refresh)


@api("POST", "/auth/register")
async def register(body: RegisterBody, request: Request):
    csrf(request)
    # Forward provider success so UI can ask the user to sign in/verify next.
    return JSONResponse(await exe("POST", "/auth/register", {**body.model_dump(), "app": "sentinel-web"}))


@api("POST", "/auth/login")
async def login(body: LoginBody, request: Request):
    return await provider_login(request, "/auth/login", {**body.model_dump(), "app": "sentinel-web"})


@api("POST", "/auth/verify-2fa")
async def verify_2fa(body: TwoFactorBody, request: Request):
    return await provider_login(request, "/auth/verify-2fa", body.model_dump())


@api("POST", "/auth/forgot-password")
async def forgot_password(body: EmailBody, request: Request):
    csrf(request)
    return JSONResponse(await exe("POST", "/auth/forgot-password", body.model_dump()))


@api("POST", "/auth/refresh")
async def refresh(request: Request):
    csrf(request)
    user = user_from_request(request, tokens=True)
    if not user.refresh_token:
        raise HTTPException(401, "Session cannot be refreshed; please sign in again")
    reply = await exe("POST", "/auth/refresh", {"refresh_token": user.refresh_token})
    access = reply.get("access_token")
    if not isinstance(access, str):
        raise HTTPException(503, "EXE account service returned no access token")
    account = await verify_access(access)
    if account["id"] != user.id:
        raise HTTPException(401, "Refreshed EXE account does not match this session")
    require_db().upsert_user(account["id"], account["email"], account["name"], account["avatar_url"])
    next_refresh = reply.get("refresh_token") if isinstance(reply.get("refresh_token"), str) else user.refresh_token
    require_db().execute("UPDATE sessions SET access_token_enc=?,refresh_token_enc=? WHERE token_hash=?", (enc(access), enc(next_refresh), user.session_hash))
    return {"ok": True, "user": {key: account[key] for key in ("id", "name", "email")}}


@api("POST", "/auth/logout")
async def logout(request: Request):
    csrf(request)
    raw = request.cookies.get(COOKIE_NAME)
    session_hash = h(raw) if raw else ""
    if session_hash and db.configured:
        row = db.fetch_one("SELECT refresh_token_enc FROM sessions WHERE token_hash=?", (session_hash,))
        refresh_token = dec(row.get("refresh_token_enc")) if row else None
        if refresh_token:
            try:
                await exe("POST", "/auth/logout", {"refresh_token": refresh_token})
            except HTTPException as exc:
                info(f"EXE logout returned {exc.status_code}; local session still cleared", "WARN")
        db.execute("DELETE FROM sessions WHERE token_hash=?", (session_hash,))
    response = JSONResponse({"ok": True})
    remove_cookie(response, request)
    return response


@api("GET", "/auth/me")
def auth_me(request: Request):
    return {"user": user_from_request(request).public()}


@api("PATCH", "/auth/me")
async def update_me(body: MePatch, request: Request):
    csrf(request)
    user = user_from_request(request, tokens=True)
    if body.display_name is None and body.avatar_url is None:
        raise HTTPException(400, "Provide display_name or avatar_url")
    if not user.access_token:
        raise HTTPException(401, "Session provider token is unavailable; please sign in again")
    reply = await exe("PATCH", "/auth/me", body.model_dump(exclude_none=True), user.access_token)
    try:
        account = identity(reply)
    except HTTPException:
        account = {"id": user.id, "email": user.email, "name": body.display_name or user.name, "avatar_url": body.avatar_url or user.avatar_url}
    if account["id"] != user.id:
        raise HTTPException(401, "Updated EXE account does not match this session")
    require_db().upsert_user(account["id"], account["email"], account["name"], account["avatar_url"])
    return {"ok": True, "user": {key: account[key] for key in ("id", "name", "email")}}


# Compatibility only: creates the same cookie session; no legacy PIN/profile migration route exists.
class LegacyLogin(BaseModel):
    access_token: str


@api("POST", "/exe/login")
async def legacy_exe_login(body: LegacyLogin, request: Request):
    csrf(request)
    return await create_session(request, body.access_token)


# ---- Roblox monitoring -----------------------------------------------------------
class RuntimeSession:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.cookie: str | None = None
        self.account: dict[str, Any] | None = None
        self.running = False
        self.task: asyncio.Task | None = None
        self.known: dict[str, set[str]] = {}


runtime: dict[str, RuntimeSession] = {}
csrf_tokens: dict[str, tuple[str, float]] = {}
unsupported: dict[tuple[str, str], float] = {}


def session(user_id: str) -> RuntimeSession:
    return runtime.setdefault(user_id, RuntimeSession(user_id))


def config(user_id: str) -> dict[str, Any]:
    answer = dict(DEFAULT_CONFIG)
    for item in require_db().fetch_all("SELECT config_key,config_value FROM config WHERE user_id=?", (user_id,)):
        try:
            answer[item["config_key"]] = json.loads(item["config_value"])
        except (TypeError, ValueError):
            answer[item["config_key"]] = item["config_value"]
    return answer


def set_config(user_id: str, key: str, value: Any) -> None:
    require_db().upsert_config(user_id, key, value)


async def roblox_get(url: str, cookie: str | None = None, params: dict[str, Any] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        return await client.get(url, cookies={".ROBLOSECURITY": cookie} if cookie else {}, params=params)


async def roblox_csrf(cookie: str) -> str:
    old = csrf_tokens.get(cookie)
    if old and time.time() - old[1] < 110:
        return old[0]
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post("https://auth.roblox.com/v2/logout", cookies={".ROBLOSECURITY": cookie})
    value = response.headers.get("x-csrf-token", "")
    csrf_tokens[cookie] = (value, time.time())
    return value


async def validate_roblox_cookie(cookie: str) -> dict[str, Any]:
    response = await roblox_get("https://users.roblox.com/v1/users/authenticated", cookie)
    if response.status_code != 200:
        raise HTTPException(400, "Invalid or expired Roblox cookie")
    item = response.json()
    user_id = str(item["id"])
    avatar_url = ""
    try:
        thumb = await roblox_get("https://thumbnails.roblox.com/v1/users/avatar-headshot", cookie, {"userIds": user_id, "size": "150x150", "format": "Png", "isCircular": "false"})
        rows = thumb.json().get("data", []) if thumb.status_code == 200 else []
        avatar_url = str(rows[0].get("imageUrl") or "") if rows else ""
    except Exception:
        pass
    return {"userId": user_id, "username": item.get("name", ""), "displayName": item.get("displayName") or item.get("name", ""), "avatarUrl": avatar_url}


async def group_name(group_id: str, cookie: str | None) -> str:
    try:
        response = await roblox_get(f"https://groups.roblox.com/v1/groups/{group_id}", cookie)
        if response.status_code == 200:
            return str(response.json().get("name") or f"Group {group_id}")
    except Exception:
        pass
    return f"Group {group_id}"


async def group_assets(group_id: str, kind: str, cookie: str | None) -> list[dict[str, Any]] | None:
    """Original bounded retries/CSRF refresh semantics for Roblox listings."""
    if not cookie:
        return None
    try:
        token = await roblox_csrf(cookie)
    except Exception:
        token = ""
    output: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"assetType": kind, "isArchived": "false", "groupId": group_id, "pageSize": 100}
        if cursor:
            params["cursor"] = cursor
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                    response = await client.get("https://itemconfiguration.roblox.com/v1/creations/get-assets", cookies={".ROBLOSECURITY": cookie}, headers={"X-CSRF-TOKEN": token} if token else {}, params=params)
                break
            except httpx.RequestError:
                await asyncio.sleep(2 ** attempt)
        if response is None:
            return []  # transient network failure
        if response.status_code == 401:
            return None
        if response.status_code == 429:
            await asyncio.sleep(min(int(response.headers.get("Retry-After", "10")), 30))
            return []
        if response.status_code == 403:
            new_token = response.headers.get("x-csrf-token")
            if not new_token:
                return None  # permission failure, retry in later cycle
            csrf_tokens[cookie] = (new_token, time.time())
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.get("https://itemconfiguration.roblox.com/v1/creations/get-assets", cookies={".ROBLOSECURITY": cookie}, headers={"X-CSRF-TOKEN": new_token}, params=params)
            except httpx.RequestError:
                return []
            if response.status_code == 403:
                return None
        if response.status_code != 200:
            return []
        data = response.json()
        output.extend({"id": str(row.get("assetId", row.get("id", ""))), "name": row.get("name", "Unknown"), "creatorId": str(row.get("creatorTargetId", "")), "creatorName": row.get("creatorName", "") or "", "assetType": kind} for row in data.get("data", []))
        cursor = data.get("nextPageCursor")
        if not cursor:
            break
    return output


async def asset_action(asset_id: str, action: str, cookie: str | None) -> bool:
    if not cookie:
        return False
    token = await roblox_csrf(cookie)
    url = f"https://develop.roblox.com/v1/assets/{asset_id}/{action}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, headers={"X-CSRF-TOKEN": token}, cookies={".ROBLOSECURITY": cookie})
        if response.status_code == 403 and (fresh := response.headers.get("x-csrf-token")):
            csrf_tokens[cookie] = (fresh, time.time())
            response = await client.post(url, headers={"X-CSRF-TOKEN": fresh}, cookies={".ROBLOSECURITY": cookie})
    info(f"{action} {asset_id}: HTTP {response.status_code}")
    return response.status_code in (200, 204)


async def monitor(user_id: str) -> None:
    """Retains baseline/diff, whitelist, archive delay, archive and history behavior."""
    active = session(user_id)
    try:
        while active.running:
            started = time.time()
            cfg = config(user_id)
            poll = max(10, int(cfg.get("pollingInterval", 60)))
            delay = max(0, int(cfg.get("archiveDelay", 0)))
            filters = [kind for kind in cfg.get("assetTypeFilters", ASSET_TYPES) if kind in ASSET_TYPES]
            global_wl = {str(x).strip().lower() for x in cfg.get("whitelist_all", [])}
            for group in require_db().fetch_all("SELECT id,name FROM sentinel_groups WHERE user_id=?", (user_id,)):
                if not active.running:
                    break
                gid, gname = str(group["id"]), str(group["name"])
                assets: list[dict[str, Any]] = []
                for kind in filters:
                    key = (gid, kind)
                    if key in unsupported and time.time() - unsupported[key] < 1800:
                        continue
                    fetched = await group_assets(gid, kind, active.cookie)
                    if fetched is None:
                        unsupported[key] = time.time()
                    else:
                        assets.extend(fetched)
                current = {str(x["id"]): x for x in assets if x.get("id")}
                known_key = f"{user_id}:{gid}"
                if known_key not in active.known:
                    active.known[known_key] = set(current)
                    new_ids = set(current) if cfg.get("archiveExisting") else set()
                else:
                    new_ids = set(current) - active.known[known_key]
                    active.known[known_key].intersection_update(current)
                for asset_id in new_ids:
                    if not active.running:
                        break
                    asset = current[asset_id]
                    creator_id = str(asset.get("creatorId") or "")
                    creator = str(asset.get("creatorName") or creator_id)
                    kind = str(asset.get("assetType") or "Audio")
                    type_wl = {str(x).strip().lower() for x in cfg.get(f"whitelist_{kind}", [])}
                    if creator_id.lower() in global_wl | type_wl or creator.lower() in global_wl | type_wl or kind in NON_ARCHIVABLE:
                        active.known[known_key].add(asset_id)
                        continue
                    restored = require_db().scalar("SELECT COUNT(*) FROM history WHERE audio_id=? AND user_id=? AND archived=0", (asset_id, user_id)) or 0
                    if restored:
                        active.known[known_key].add(asset_id)
                        continue
                    if delay:
                        await asyncio.sleep(delay)
                    if not active.running:
                        break
                    try:
                        ok = await asset_action(asset_id, "archive", active.cookie)
                    except Exception as exc:
                        info(f"archive {asset_id} failed: {exc}", "ERROR")
                        ok = False
                    active.known[known_key].add(asset_id)
                    require_db().insert_history_ignore({"id": f"{asset_id}_{uuid.uuid4().hex}", "user_id": user_id, "username": creator, "display_name": creator, "roblox_user_id": creator_id, "audio_name": str(asset.get("name") or "Unknown"), "audio_id": asset_id, "asset_type": kind, "group_id": gid, "group_name": gname, "occurred_at": utcnow_string(), "dm_status": "n/a", "archived": int(ok)})
            remaining = max(5, poll - (time.time() - started))
            while active.running and remaining:
                step = min(5, remaining)
                await asyncio.sleep(step)
                remaining -= step
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        info(f"monitor error for {user_id}: {exc}", "ERROR")
    finally:
        active.running, active.task = False, None
        if db.configured:
            set_config(user_id, "_monitoringActive", False)


# ---- Protected moderation API ----------------------------------------------------
class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    profile_id: Optional[str] = None


class CookieBody(ProfileBody):
    cookie: str = Field(min_length=1, max_length=8192)


class GroupBody(ProfileBody):
    id: str
    name: str = ""


class ConfigBody(ProfileBody):
    pollingInterval: Optional[int] = None
    allowFastPolling: Optional[bool] = None
    archiveDelay: Optional[int] = None
    archiveExisting: Optional[bool] = None
    saveCookies: Optional[bool] = None
    cookieSaveMode: Optional[str] = None
    autoStartMonitoring: Optional[bool] = None
    assetTypeFilters: Optional[list[str]] = None
    whitelist_Audio: Optional[list[str]] = None
    whitelist_Image: Optional[list[str]] = None
    whitelist_Decal: Optional[list[str]] = None
    whitelist_Video: Optional[list[str]] = None
    whitelist_Mesh: Optional[list[str]] = None
    whitelist_Plugin: Optional[list[str]] = None
    whitelist_Animation: Optional[list[str]] = None
    whitelist_Model: Optional[list[str]] = None
    whitelist_Package: Optional[list[str]] = None
    whitelist_all: Optional[list[str]] = None


class AssetBody(ProfileBody):
    asset_id: str
    history_id: Optional[str] = None


class BulkRestoreBody(ProfileBody):
    asset_ids: list[str]


@api("GET", "/health")
def health():
    return {"ok": True, "service": "sentinel", "database": bool(db.configured and DB_READY)}


@api("GET", "/status")
def status(request: Request, profile_id: str = ""):
    uid = owner(user_from_request(request), profile_id)
    active = session(uid)
    running = bool(active.running and active.task and not active.task.done())
    if active.running and not running:
        active.running = False
    return {"monitoring": running, "account": active.account, "hasCredential": bool(active.cookie), "extensionLinked": False, "monitorHealthy": running}


@api("POST", "/credentials/manual")
async def manual_cookie(body: CookieBody, request: Request):
    csrf(request)
    uid = owner(user_from_request(request), body.profile_id)
    account = await validate_roblox_cookie(body.cookie)
    active = session(uid)
    active.cookie, active.account = body.cookie, account  # never persistent
    return {**account, "profile_id": uid}


@api("POST", "/credentials/relink")
async def relink(body: CookieBody, request: Request):
    return await manual_cookie(body, request)


@api("POST", "/credentials/clear")
def clear_cookie(body: ProfileBody, request: Request):
    csrf(request)
    active = session(owner(user_from_request(request), body.profile_id))
    active.cookie, active.account = None, None
    return {"cleared": True}


@api("GET", "/saved-accounts")
def saved_accounts(request: Request, profile_id: str = ""):
    owner(user_from_request(request), profile_id)
    return []  # feature retired: there is no persisted Roblox credential vault


@api("POST", "/monitoring/start")
async def monitor_start(body: ProfileBody, request: Request):
    csrf(request)
    uid = owner(user_from_request(request), body.profile_id)
    active = session(uid)
    if not active.cookie:
        raise HTTPException(400, "No credentials. Connect a Roblox account first.")
    if active.running and active.task and not active.task.done():
        return {"status": "already_running"}
    unsupported.clear()
    active.running = True
    set_config(uid, "_monitoringActive", True)
    active.task = asyncio.create_task(monitor(uid))  # called only while an event loop is running
    return {"status": "started"}


@api("POST", "/monitoring/stop")
async def monitor_stop(body: ProfileBody, request: Request):
    csrf(request)
    uid = owner(user_from_request(request), body.profile_id)
    active = session(uid)
    active.running = False
    set_config(uid, "_monitoringActive", False)
    if active.task and not active.task.done():
        active.task.cancel()
        try:
            await active.task
        except (asyncio.CancelledError, Exception):
            pass
    active.task = None
    return {"status": "stopped"}


@api("GET", "/groups")
def groups(request: Request, profile_id: str = ""):
    uid = owner(user_from_request(request), profile_id)
    return require_db().fetch_all("SELECT id,name,added_at FROM sentinel_groups WHERE user_id=? ORDER BY added_at DESC", (uid,))


@api("POST", "/groups")
async def add_group(body: GroupBody, request: Request):
    csrf(request)
    uid = owner(user_from_request(request), body.profile_id)
    gid = body.id.strip()
    if not gid.isdigit():
        raise HTTPException(400, "Group ID must be numeric")
    name = body.name.strip() or await group_name(gid, session(uid).cookie)
    created = time.time()
    require_db().upsert_group(gid, uid, name, created)
    return {"id": gid, "name": name, "added_at": created}


@api("DELETE", "/groups/{group_id}")
def delete_group(group_id: str, request: Request, profile_id: str = ""):
    csrf(request)
    uid = owner(user_from_request(request), profile_id)
    require_db().execute("DELETE FROM sentinel_groups WHERE id=? AND user_id=?", (group_id, uid))
    session(uid).known.pop(f"{uid}:{group_id}", None)
    return {"deleted": True}


@api("GET", "/history")
def history(request: Request, profile_id: str = "", limit: int = 200, search: str = ""):
    uid = owner(user_from_request(request), profile_id)
    limit = max(1, min(500, limit))
    selected = "SELECT id,user_id AS profile_id,username,display_name,roblox_user_id AS user_id,audio_name,audio_id,asset_type,group_id,group_name,occurred_at AS time,dm_status,archived FROM history"
    if search:
        term = f"%{search[:100]}%"
        return require_db().fetch_all(selected + " WHERE user_id=? AND (username LIKE ? OR audio_name LIKE ? OR audio_id LIKE ?) ORDER BY occurred_at DESC LIMIT ?", (uid, term, term, term, limit))
    return require_db().fetch_all(selected + " WHERE user_id=? ORDER BY occurred_at DESC LIMIT ?", (uid, limit))


@api("DELETE", "/history")
def clear_history(request: Request, profile_id: str = ""):
    csrf(request)
    require_db().execute("DELETE FROM history WHERE user_id=?", (owner(user_from_request(request), profile_id),))
    return {"cleared": True}


async def account_session(uid: str) -> RuntimeSession:
    active = session(uid)
    if not active.cookie:
        raise HTTPException(400, "No credentials — connect a Roblox account first")
    return active


@api("POST", "/archive-asset")
async def archive(body: AssetBody, request: Request):
    csrf(request)
    uid = owner(user_from_request(request), body.profile_id)
    asset_id = body.asset_id.strip()
    if not asset_id:
        raise HTTPException(400, "asset_id is required")
    ok = await asset_action(asset_id, "archive", (await account_session(uid)).cookie)
    if ok:
        require_db().execute("UPDATE history SET archived=1 WHERE audio_id=? AND user_id=?", (asset_id, uid))
    return {"ok": ok, "asset_id": asset_id}


@api("POST", "/restore")
async def restore(body: AssetBody, request: Request):
    csrf(request)
    uid = owner(user_from_request(request), body.profile_id)
    asset_id = body.asset_id.strip()
    if not asset_id:
        raise HTTPException(400, "asset_id is required")
    ok = await asset_action(asset_id, "restore", (await account_session(uid)).cookie)
    if ok:
        if body.history_id:
            require_db().execute("UPDATE history SET archived=0 WHERE id=? AND user_id=?", (body.history_id, uid))
        else:
            require_db().execute("UPDATE history SET archived=0 WHERE audio_id=? AND user_id=?", (asset_id, uid))
    return {"ok": ok, "asset_id": asset_id}


@api("POST", "/restore/bulk")
async def restore_bulk(body: BulkRestoreBody, request: Request):
    """Restore a bounded batch while retaining Roblox rate-limit spacing."""
    csrf(request)
    uid = owner(user_from_request(request), body.profile_id)
    asset_ids = [str(item).strip() for item in body.asset_ids if str(item).strip()][:100]
    if not asset_ids:
        raise HTTPException(400, "asset_ids list is empty")
    active = await account_session(uid)
    result: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        ok = await asset_action(asset_id, "restore", active.cookie)
        if ok:
            require_db().execute("UPDATE history SET archived=0 WHERE audio_id=? AND user_id=?", (asset_id, uid))
        result.append({"asset_id": asset_id, "ok": ok})
        await asyncio.sleep(0.3)
    return {"results": result, "succeeded": sum(1 for item in result if item["ok"]), "total": len(result)}


@api("GET", "/stats")
def stats(request: Request, profile_id: str = ""):
    uid = owner(user_from_request(request), profile_id)
    store = require_db()
    return {"archived": store.scalar("SELECT COUNT(*) FROM history WHERE user_id=? AND archived=1", (uid,)) or 0, "dms": 0, "groups": store.scalar("SELECT COUNT(*) FROM sentinel_groups WHERE user_id=?", (uid,)) or 0, "whitelisted": len(config(uid).get("whitelist_all", []))}


@api("GET", "/config")
def get_config_route(request: Request, profile_id: str = ""):
    return config(owner(user_from_request(request), profile_id))


@api("POST", "/config")
def update_config(body: ConfigBody, request: Request):
    csrf(request)
    uid = owner(user_from_request(request), body.profile_id)
    updates = body.model_dump(exclude_none=True)
    updates.pop("profile_id", None)
    if "cookieSaveMode" in updates:
        if updates["cookieSaveMode"] not in {"ask", "always", "never"}:
            raise HTTPException(400, "cookieSaveMode must be 'ask', 'always', or 'never'")
        updates["cookieSaveMode"] = "never"  # credentials cannot be persisted
    if "pollingInterval" in updates:
        updates["pollingInterval"] = max(10, min(3600, int(updates["pollingInterval"])))
    if "archiveDelay" in updates:
        updates["archiveDelay"] = max(0, min(3600, int(updates["archiveDelay"])))
    if "assetTypeFilters" in updates:
        updates["assetTypeFilters"] = [kind for kind in updates["assetTypeFilters"] if kind in ASSET_TYPES]
    for key, value in updates.items():
        if key in DEFAULT_CONFIG:
            set_config(uid, key, value)
    return config(uid)


@api("GET", "/asset-types")
def asset_types(request: Request):
    user_from_request(request)
    return {"types": ASSET_TYPES, "nonArchivable": sorted(NON_ARCHIVABLE)}


# ---- Static application paths ----------------------------------------------------
def static_file(name: str) -> FileResponse:
    target = STATIC / name
    if not target.is_file():
        raise HTTPException(404, f"Static file {name} is not available")
    return FileResponse(target)


@app.get("/sentinel/Dashboard")
def dashboard(request: Request):
    try:
        user_from_request(request)
    except HTTPException:
        return RedirectResponse("/sentinel/login", status_code=303)
    return static_file("dashboard.html")


@app.get("/sentinel/login")
def login_page():
    return static_file("login.html")


@app.get("/sentinel")
@app.get("/")
def landing_page():
    return static_file("index.html")


@app.get("/manus-routes.json")
def routes_manifest():
    return static_file("manus-routes.json")
