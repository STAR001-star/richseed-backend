"""Login protection for the Richseed API.

After a correct PIN / password, the server hands back a signed token.
The website sends that token with every request, and the server checks it
before showing or changing anything private. Tokens expire on their own.
"""
import os
import hmac
import json
import time
import base64
import hashlib
import secrets
from collections import defaultdict

from fastapi import HTTPException, Request, Depends
from sqlalchemy.orm import Session

from . import models
from .database import get_db

# Set AUTH_SECRET in Render (any long random text). If it is missing we make a
# temporary one, which still works but logs everybody out whenever Render restarts.
_SECRET = (os.environ.get("AUTH_SECRET") or secrets.token_hex(32)).encode()
AUTH_SECRET_IS_SET = bool(os.environ.get("AUTH_SECRET"))

TTL = {"admin": 8 * 3600, "teacher": 12 * 3600, "parent": 7 * 24 * 3600}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(role: str, sub: int = 0) -> str:
    body = _b64(json.dumps({"r": role, "s": sub, "e": int(time.time()) + TTL[role]}).encode())
    sig = _b64(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest())
    return body + "." + sig


def _claims(request: Request):
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    try:
        body, sig = token.split(".", 1)
        good = _b64(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(body))
        if data.get("e", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def _need(request: Request) -> dict:
    c = _claims(request)
    if not c:
        raise HTTPException(status_code=401, detail="Please sign in again")
    return c


def require_admin(request: Request):
    if _need(request)["r"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


def require_teacher(teacher_id: int, request: Request):
    c = _need(request)
    if c["r"] == "admin":
        return
    if c["r"] != "teacher" or c["s"] != teacher_id:
        raise HTTPException(status_code=403, detail="Not allowed")


def require_parent(request: Request):
    c = _need(request)
    if c["r"] not in ("parent", "admin"):
        raise HTTPException(status_code=403, detail="Not allowed")


def require_parent_self(parent_id: int, request: Request):
    c = _need(request)
    if c["r"] == "admin":
        return
    if c["r"] != "parent" or c["s"] != parent_id:
        raise HTTPException(status_code=403, detail="Not allowed")


def require_child_access(student_id: int, request: Request, db: Session = Depends(get_db)):
    c = _need(request)
    if c["r"] == "admin":
        return
    if c["r"] != "parent":
        raise HTTPException(status_code=403, detail="Not allowed")
    s = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not s or s.parent_id != c["s"]:
        raise HTTPException(status_code=403, detail="This child is not on your account")


# ---------- simple brute-force protection (kept in memory) ----------
_hits = defaultdict(list)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?"))


def too_many(key: str, limit: int, window: int) -> bool:
    now = time.time()
    _hits[key] = [t for t in _hits[key] if now - t < window]
    return len(_hits[key]) >= limit


def record(key: str):
    _hits[key].append(time.time())


def guard(key: str, limit: int, window: int):
    """Raise 429 if this key already hit its limit."""
    if too_many(key, limit, window):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a few minutes and try again.")


def clear(key: str):
    _hits.pop(key, None)
