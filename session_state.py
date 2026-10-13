"""Private in-memory course sessions and honest source links for this lab."""

from __future__ import annotations

import copy
import ipaddress
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qsl, urlsplit

COOKIE_NAME = "lab_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
MAX_SESSIONS = 128


@dataclass
class Session:
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    run_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    credentials: dict | None = field(default=None, repr=False)
    models: list[dict] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    exercises: dict = field(default_factory=dict)
    checkpoints: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic, repr=False)
    last_seen: float = field(default_factory=time.monotonic, repr=False)

    def report(self) -> dict:
        """Return independent course progress data without credentials or models."""
        with self.lock:
            completed = [
                step for step, checkpoint in self.checkpoints.items()
                if checkpoint is True or (isinstance(checkpoint, dict) and checkpoint.get("passed") is True)
            ]
            keys = ("run_id", "mode", "scenario", "completed", "stop_reason", "metrics", "checks")
            report = {
                "completed_steps": completed,
                "checkpoints": self.checkpoints,
                "exercise_results": self.exercises,
                "runs": [{key: run.get(key) for key in keys} for run in self.runs],
            }
            return copy.deepcopy(report)


_sessions: dict[str, Session] = {}
_sessions_lock = threading.RLock()
_token_pattern = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_codespace_pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_repository_pattern = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}$")


def _forget(token: str) -> None:
    old = _sessions.pop(token, None)
    if old is not None:
        with old.lock:
            old.credentials = None
            old.models.clear()


def get_session(cookie_header: str) -> tuple[str, Session, bool]:
    """Reuse a known random cookie or create a session, expiring old ones lazily."""
    token = None
    if isinstance(cookie_header, str) and len(cookie_header) <= 16384:
        try:
            cookies = SimpleCookie()
            cookies.load(cookie_header)
            value = cookies.get(COOKIE_NAME)
            candidate = value.value if value else ""
            if _token_pattern.fullmatch(candidate):
                token = candidate
        except (CookieError, ValueError):
            pass
    now = time.monotonic()
    with _sessions_lock:
        for stale in [key for key, item in _sessions.items() if now - item.created_at >= SESSION_TTL_SECONDS]:
            _forget(stale)
        if token in _sessions:
            session = _sessions[token]
            session.last_seen = now
            return token, session, False
        while len(_sessions) >= MAX_SESSIONS:
            oldest = min(_sessions, key=lambda key: _sessions[key].last_seen)
            _forget(oldest)
        token = secrets.token_urlsafe(32)
        while token in _sessions:
            token = secrets.token_urlsafe(32)
        session = Session(created_at=now, last_seen=now)
        _sessions[token] = session
        return token, session, True


def _source_url(value: str) -> str | None:
    """Accept HTTPS source URLs without embedded authentication information."""
    if not value or any(character.isspace() or ord(character) < 32 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            hostname = parsed.hostname.encode("idna").decode("ascii").rstrip(".")
            if len(hostname) > 253 or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in hostname.split(".")
            ):
                return None
        if any(character in parsed.netloc for character in ("\\", "<", ">", '"', "'")):
            return None
        credential_keys = {"token", "access_token", "api_key", "apikey", "key", "password", "secret", "authorization"}
        if any(key.lower() in credential_keys for key, _ in parse_qsl(parsed.query)):
            return None
    except (ValueError, UnicodeError):
        return None
    return value


def source_config() -> dict:
    """Point at this actual Codespace, a configured source, or a real generic fallback."""
    codespace = os.environ.get("CODESPACE_NAME", "").strip()
    if _codespace_pattern.fullmatch(codespace):
        url = f"https://{codespace}.github.dev/"
        return {"source_url": url, "source_label": "Open source in Codespaces", "editor_url": url, "source_notice": ""}
    configured = _source_url(os.environ.get("LAB_SOURCE_URL", "").strip())
    if configured:
        return {"source_url": configured, "source_label": "Open source", "editor_url": configured, "source_notice": ""}
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if _repository_pattern.fullmatch(repository) and repository.split("/")[1] not in (".", ".."):
        url = f"https://codespaces.new/{repository}"
        return {"source_url": url, "source_label": "Open source in Codespaces", "editor_url": None, "source_notice": ""}
    return {
        "source_url": "https://github.com/codespaces",
        "source_label": "Open Codespaces",
        "editor_url": None,
        "source_notice": "Publish this lab to a GitHub repository to open its source in Codespaces.",
    }
