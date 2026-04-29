"""BlinQ Blender — XMDSource cloud authentication and runtime licensing.

Mirrors the authentication contract used by XMD ToolBox 4.0 exactly:

- JWT authentication via ``/wp-json/jwt-auth/v1/token`` (WordPress jwt-auth plugin)
- Runtime entitlements via ``/wp-json/xmd/v2/entitlements``
- Floating-seat leases via ``/wp-json/xmd/v2/licenses/lease|renew|release``
- Offline grace:  named-user = 24 h,  floating = 6 h  after last verified time
- All HTTP is done with stdlib ``urllib`` — no external dependencies required

Settings keys persisted in XMDPreferences (as StringProperty / IntProperty):
    xmdsource_token              JWT from /wp-json/jwt-auth/v1/token
    xmdsource_token_exp          int unix timestamp of JWT exp claim
    xmdsource_username           email string
    xmdsource_display_name       display_name returned on login
    xmdsource_runtime_product_id entitlement product id
    xmdsource_runtime_entitlement_id
    xmdsource_runtime_license_mode  "named" or "floating"
    xmdsource_runtime_seat_count int
    xmdsource_runtime_active_count int
    xmdsource_runtime_lease_id
    xmdsource_runtime_lease_expires_at  ISO-8601 string
    xmdsource_runtime_last_verified_at  int unix timestamp
    xmdsource_device_id          stable per-device uuid
    xmdsource_install_id         stable per-install uuid
"""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import time
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ADDON_VERSION = "0.1.0"
_FEATURE_TAG = "app.blinq"

_TOKEN_REFRESH_BUFFER = 120          # seconds before exp to treat token as stale
_GRACE_NAMED_USER = 24 * 60 * 60     # 24 h offline grace for named-user licenses
_GRACE_FLOATING   =  6 * 60 * 60     # 6 h offline grace for floating licenses

_BACKEND_OUTAGE_FRAGMENTS = (
    "network error:",
    "server error ",
    "invalid server response",
    "runtime licensing unavailable",
)


def _base_url() -> str:
    """Return the active XMDSource base URL.

    Respects the ``XMDSOURCE_BASE_URL_OVERRIDE`` environment variable so
    developers can point at a staging instance.

    Returns:
        Normalized base URL with no trailing slash.
    """
    override = os.environ.get("XMDSOURCE_BASE_URL_OVERRIDE", "").strip()
    if not override:
        return "https://xmdsource.com"
    if "://" not in override:
        override = f"https://{override}"
    return override.rstrip("/")


# User-Agent that matches python-requests, which xmdsource.com already
# accepts from XMD ToolBox 4.0. Python-urllib is commonly blocked by WAFs.
_USER_AGENT = "python-requests/2.32.3"


def _url(path: str) -> str:
    return f"{_base_url()}{path}"


# ---------------------------------------------------------------------------
# Stdlib HTTP helpers (no external deps)
# ---------------------------------------------------------------------------

def _http_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> tuple[int, dict[str, Any]]:
    """Perform a GET request and return (status_code, json_body).

    Args:
        url: Absolute URL to request.
        headers: Optional request headers.
        timeout: Socket timeout in seconds.

    Returns:
        ``(status_code, body_dict)`` — body_dict is empty on parse failure.
    """
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    req.add_header("User-Agent", _USER_AGENT)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"_raw": raw}
    except Exception as exc:
        import urllib.error
        if isinstance(exc, urllib.error.HTTPError):
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                body_dict = json.loads(raw)
            except Exception:
                body_dict = {"_raw": raw if 'raw' in dir() else ""}
            return exc.code, body_dict
        raise _NetworkError(str(exc)) from exc


def _http_post(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> tuple[int, dict[str, Any]]:
    """Perform a POST request with a JSON body and return (status_code, json_body).

    Args:
        url: Absolute URL to POST to.
        payload: JSON-serializable request body.
        headers: Optional extra headers.
        timeout: Socket timeout in seconds.

    Returns:
        ``(status_code, body_dict)`` — body_dict is empty on parse failure.
    """
    import urllib.request
    body = json.dumps(payload).encode("utf-8")
    merged = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=body, headers=merged, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"_raw": raw}
    except Exception as exc:
        # urllib raises HTTPError (which has a .code) for 4xx/5xx
        import urllib.error
        if isinstance(exc, urllib.error.HTTPError):
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                body_dict = json.loads(raw)
            except Exception:
                body_dict = {"_raw": raw if 'raw' in dir() else ""}
            return exc.code, body_dict
        raise _NetworkError(str(exc)) from exc


class _NetworkError(Exception):
    """Raised by _http_get/_http_post when a socket-level error occurs."""


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _decode_jwt_exp(token: str) -> int:
    """Extract the ``exp`` claim from a JWT without verifying the signature.

    Args:
        token: JWT string (three base64url parts separated by ``"."``).

    Returns:
        Unix timestamp of expiry, or ``0`` on failure.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return 0
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        return int(decoded.get("exp", 0))
    except Exception:
        return 0


def _jwt_iss_matches_base_url(token: str) -> bool:
    """Return whether the JWT issuer matches the active XMDSource base URL.

    Tokens with no issuer claim are accepted (legacy tokens).

    Args:
        token: JWT string to inspect.

    Returns:
        ``True`` when the issuer matches or is absent.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        issuer = str(decoded.get("iss", "")).rstrip("/")
        if not issuer:
            return True
        return issuer == _base_url().rstrip("/")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Settings helpers (thin wrappers so callers pass prefs, not raw keys)
# ---------------------------------------------------------------------------

def _get(prefs, key: str, default: Any = "") -> Any:
    return getattr(prefs, key, default)


def _set(prefs, key: str, value: Any) -> None:
    try:
        setattr(prefs, key, value)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Token validity checks
# ---------------------------------------------------------------------------

def _is_jwt_valid(prefs) -> bool:
    """Return whether the stored JWT is present and not yet expired.

    Args:
        prefs: XMDPreferences instance.

    Returns:
        True when the JWT is usable.
    """
    token = _get(prefs, "xmdsource_token")
    if not token:
        return False
    if not _jwt_iss_matches_base_url(token):
        return False
    exp = int(_get(prefs, "xmdsource_token_exp", 0) or 0)
    if exp and time.time() > exp - _TOKEN_REFRESH_BUFFER:
        return False
    return True


# ---------------------------------------------------------------------------
# CloudClient — primary API surface
# ---------------------------------------------------------------------------

class CloudClient:
    """XMDSource.com REST client for BlinQ Blender.

    All public methods follow the same contract:
    ``(success: bool, payload: dict, message: str)``
    — except :meth:`login` and :meth:`logout` which return ``(bool, str)``.

    Methods are synchronous; call from a background thread when invoked
    from a UI operator to avoid blocking the Blender interface.

    Args:
        prefs: The XMDPreferences add-on preferences instance, used for
               persistent token and license state storage.
    """

    def __init__(self, prefs) -> None:
        self._prefs = prefs

    # ------------------------------------------------------------------ auth

    def login(self, username: str, password: str) -> tuple[bool, str]:
        """Authenticate with XMDSource.com and persist the JWT.

        POSTs to ``/wp-json/jwt-auth/v1/token`` with the user's credentials.
        On success the JWT, its expiry, and the display name are stored in
        add-on preferences.

        Args:
            username: The user's XMDSource email / WordPress username.
            password: The user's account password.

        Returns:
            ``(success, message)`` — message is suitable for display in the UI.
        """
        try:
            status, data = _http_post(
                _url("/wp-json/jwt-auth/v1/token"),
                {"username": username, "password": password},
            )
        except _NetworkError as exc:
            return False, f"Network error: {exc}"

        if status == 200 and data.get("token"):
            self._clear_legacy_runtime_state(release_existing_lease=True)
            token: str = data["token"]
            exp = _decode_jwt_exp(token)
            _set(self._prefs, "xmdsource_token", token)
            _set(self._prefs, "xmdsource_token_exp", exp)
            _set(self._prefs, "xmdsource_username", username)
            display = data.get("user_display_name", username)
            _set(self._prefs, "xmdsource_display_name", display)
            return True, f"Signed in as {display}"

        msg = (
            data.get("message")
            or data.get("code")
            or data.get("_raw", "")
            or f"Login failed (HTTP {status})"
        )
        # Truncate very long raw HTML responses (e.g. WAF block pages)
        msg = str(msg)
        if len(msg) > 200:
            msg = msg[:200] + "…"
        return False, msg

    def logout(self) -> tuple[bool, str]:
        """Sign out: release any active lease, call server logout, clear local state.

        Returns:
            ``(success, message)``
        """
        if _is_jwt_valid(self._prefs):
            try:
                _http_post(
                    _url("/wp-json/xmd/v2/auth/logout"),
                    {},
                    headers=self._auth_headers(),
                )
            except _NetworkError:
                pass

        self._clear_legacy_runtime_state(release_existing_lease=True)
        self._clear_cloud_state()
        return True, "Signed out"

    def is_logged_in(self) -> bool:
        """Return whether the stored JWT is valid.

        Returns:
            True when the user is authenticated.
        """
        return _is_jwt_valid(self._prefs)

    # ------------------------------------------------------------------ runtime license

    def sync_runtime_license(self) -> tuple[bool, dict[str, Any], str]:
        """Fetch entitlements and acquire/renew a lease if needed.

        Mirrors ``XMDSourceClient.sync_runtime_license`` from XMD ToolBox 4.0.
        Stores the resolved product_id, entitlement_id, license_mode, seat info,
        lease_id, and lease_expires_at into add-on preferences so the offline
        grace logic can function without a live connection.

        Returns:
            ``(success, payload, message)``
        """
        if not _is_jwt_valid(self._prefs):
            self._clear_runtime_state(clear_entitlement=True)
            return False, {}, "Log in with your XMDSource account first"

        ok, payload, msg = self.get_entitlements()
        if not ok:
            if "404" in msg or "405" in msg:
                self._clear_runtime_state(clear_entitlement=False)
                return True, {}, "Runtime licensing unavailable"
            return False, {}, msg

        entitlements = payload.get("entitlements", [])
        matches = [
            e for e in entitlements
            if isinstance(e, dict)
            and e.get("status") == "active"
            and _FEATURE_TAG in (e.get("features") or [])
        ]

        if not matches:
            self._clear_runtime_state(clear_entitlement=True)
            return True, payload, "No active BlinQ access found on this account"

        # Prefer named-user over floating
        matches.sort(key=lambda e: 1 if e.get("license_mode") == "floating" else 0)
        entitlement = matches[0]

        _set(self._prefs, "xmdsource_runtime_product_id", entitlement.get("product_id", ""))
        _set(self._prefs, "xmdsource_runtime_entitlement_id", entitlement.get("entitlement_id", ""))
        _set(self._prefs, "xmdsource_runtime_license_mode", entitlement.get("license_mode", ""))
        _set(self._prefs, "xmdsource_runtime_seat_count", int(entitlement.get("seat_count", 0) or 0))
        _set(self._prefs, "xmdsource_runtime_active_count", 0)

        # Named-user with no lease required
        if not entitlement.get("lease_required"):
            self._release_existing_lease_if_any()
            _set(self._prefs, "xmdsource_runtime_lease_id", "")
            _set(self._prefs, "xmdsource_runtime_lease_expires_at", "")
            _set(self._prefs, "xmdsource_runtime_last_verified_at", int(time.time()))
            return True, {"entitlement": entitlement, "lease": None}, "Entitlement active"

        # Floating — acquire or renew lease
        product_id = str(entitlement.get("product_id", ""))
        lease_id = _get(self._prefs, "xmdsource_runtime_lease_id")
        current_product = _get(self._prefs, "xmdsource_runtime_product_id")

        if lease_id and current_product == product_id:
            ok, lp, lm = self.renew_license_lease(lease_id)
            if ok and isinstance(lp, dict) and lp.get("lease"):
                self._store_lease_state(entitlement, lp)
                return True, {"entitlement": entitlement, "lease": lp.get("lease", {})}, "Lease renewed"

        ok, lp, lm = self.acquire_license_lease(product_id)
        if ok and isinstance(lp, dict) and lp.get("lease"):
            self._store_lease_state(entitlement, lp)
            return True, {"entitlement": entitlement, "lease": lp.get("lease", {})}, "Lease granted"

        return False, {"entitlement": entitlement}, lm or "Failed to acquire runtime lease"

    def get_entitlements(self) -> tuple[bool, dict[str, Any], str]:
        """Fetch the user's normalized XMD Cloud entitlements.

        Returns:
            ``(success, payload, message)``
        """
        ok, headers, msg = self._runtime_auth_headers()
        if not ok or headers is None:
            return False, {}, msg
        try:
            status, data = _http_get(_url("/wp-json/xmd/v2/entitlements"), headers)
        except _NetworkError as exc:
            return False, {}, f"Network error: {exc}"
        if status == 401:
            return False, {}, "Session expired — please log in again"
        if status != 200:
            return False, {}, f"Server error {status}"
        return True, data if isinstance(data, dict) else {}, "OK"

    def acquire_license_lease(self, product_id: str) -> tuple[bool, dict[str, Any], str]:
        """Acquire a new floating-seat lease for *product_id*.

        Args:
            product_id: The entitlement product ID to acquire a lease for.

        Returns:
            ``(success, payload, message)``
        """
        return self._post_license_action(
            _url("/wp-json/xmd/v2/licenses/lease"),
            {"product_id": product_id, "device": self._device_payload()},
        )

    def renew_license_lease(self, lease_id: str) -> tuple[bool, dict[str, Any], str]:
        """Renew an existing floating-seat lease.

        Args:
            lease_id: The active lease ID to renew.

        Returns:
            ``(success, payload, message)``
        """
        return self._post_license_action(
            _url("/wp-json/xmd/v2/licenses/renew"),
            {"lease_id": lease_id, "device": self._device_payload()},
        )

    def release_license_lease(self, lease_id: str) -> tuple[bool, str]:
        """Release an active floating-seat lease.

        Args:
            lease_id: The active lease ID to release.

        Returns:
            ``(success, message)``
        """
        ok, payload, msg = self._post_license_action(
            _url("/wp-json/xmd/v2/licenses/release"),
            {"lease_id": lease_id},
        )
        if ok:
            self._clear_runtime_state(clear_entitlement=False)
        return ok, msg

    # ------------------------------------------------------------------ runtime state

    def runtime_license_state(self) -> dict[str, Any]:
        """Return a snapshot of the current runtime license state.

        Mirrors ``XMDSourceClient.runtime_license_state()`` from XMD ToolBox 4.0.
        Includes the resolved status string, grace window, seat info, and lease state.

        Returns:
            A dict suitable for rendering the UI status row.
        """
        prefs = self._prefs
        product_id       = _get(prefs, "xmdsource_runtime_product_id")
        entitlement_id   = _get(prefs, "xmdsource_runtime_entitlement_id")
        license_mode     = _get(prefs, "xmdsource_runtime_license_mode")
        seat_count       = int(_get(prefs, "xmdsource_runtime_seat_count", 0) or 0)
        active_count     = int(_get(prefs, "xmdsource_runtime_active_count", 0) or 0)
        lease_id         = _get(prefs, "xmdsource_runtime_lease_id")
        lease_expires_at = _get(prefs, "xmdsource_runtime_lease_expires_at")
        last_verified_at = int(_get(prefs, "xmdsource_runtime_last_verified_at", 0) or 0)
        is_logged_in     = _is_jwt_valid(prefs)

        grace_deadline = self._grace_deadline(product_id, license_mode, last_verified_at)
        cached_grace_active = bool(grace_deadline and time.time() <= grace_deadline)

        if cached_grace_active and not is_logged_in:
            status = "Cached floating access (grace)" if license_mode == "floating" else "Cached runtime access (grace)"
        elif product_id and license_mode == "floating" and lease_id:
            status = "Floating lease active"
        elif product_id and lease_id:
            status = "Named-user lease active"
        elif product_id and license_mode == "floating":
            status = "Floating entitlement active"
        elif product_id:
            status = "Named-user entitlement active"
        elif is_logged_in:
            status = "No BlinQ access on this account"
        else:
            status = "Sign in to XMDSource to activate"

        if lease_id and active_count <= 0:
            active_count = 1
        if seat_count <= 0 and product_id and license_mode != "floating":
            seat_count = 1

        usage = f"{active_count}/{seat_count}" if seat_count > 0 else ""

        return {
            "status": status,
            "product_id": product_id,
            "entitlement_id": entitlement_id,
            "license_mode": license_mode,
            "seat_count": seat_count,
            "active_count": active_count,
            "usage": usage,
            "lease_id": lease_id,
            "lease_expires_at": lease_expires_at,
            "last_verified_at": _fmt_epoch(last_verified_at),
            "cached_grace_expires_at": _fmt_epoch(grace_deadline),
            "cached_grace_active": cached_grace_active,
            "is_logged_in": is_logged_in,
        }

    def has_active_runtime_access(self) -> bool:
        """Return whether the current state grants BlinQ access.

        Returns:
            True when an entitlement is active or a grace window is open.
        """
        state = self.runtime_license_state()
        if not state.get("product_id"):
            return False
        if state.get("lease_id") or state.get("cached_grace_active"):
            return True
        return "entitlement active" in state.get("status", "").lower()

    def cached_runtime_access_status(
        self, failure_message: str
    ) -> tuple[bool, str]:
        """Return whether cached state can cover a transient backend outage.

        Args:
            failure_message: Error message from the latest license refresh attempt.

        Returns:
            ``(allowed, message)``
        """
        if not self._looks_like_outage(failure_message):
            return False, ""
        state = self.runtime_license_state()
        if not state.get("product_id") or not state.get("cached_grace_active"):
            return False, ""
        grace_until = str(state.get("cached_grace_expires_at", "") or "soon")
        mode = "floating-seat" if state.get("license_mode") == "floating" else "named-user"
        return True, (
            f"XMDSource is temporarily unavailable. Using cached {mode} access. "
            f"Grace window expires {grace_until}."
        )

    # ------------------------------------------------------------------ account

    def get_account(self) -> tuple[bool, dict[str, Any], str]:
        """Fetch account and subscription info from ``/wp-json/xmd/v1/account``.

        Returns:
            ``(success, account_dict, message)``
        """
        token = _get(self._prefs, "xmdsource_token")
        if not token:
            return False, {}, "Not signed in"
        try:
            status, data = _http_get(
                _url("/wp-json/xmd/v1/account"),
                {"Authorization": f"Bearer {token}"},
            )
        except _NetworkError as exc:
            return False, {}, f"Network error: {exc}"
        if status == 200:
            return True, data if isinstance(data, dict) else {}, "OK"
        return False, {}, f"Server error {status}"

    # ------------------------------------------------------------------ internals

    def _auth_headers(self) -> dict[str, str]:
        """Return Bearer authorization headers using the stored JWT."""
        return {"Authorization": f"Bearer {_get(self._prefs, 'xmdsource_token')}"}

    def _runtime_auth_headers(
        self,
    ) -> tuple[bool, dict[str, str] | None, str]:
        """Return auth headers for runtime-license requests, or failure info.

        Returns:
            ``(success, headers_or_None, message)``
        """
        if not _is_jwt_valid(self._prefs):
            return False, None, "Log in with your XMDSource account first"
        token = _get(self._prefs, "xmdsource_token")
        if not _jwt_iss_matches_base_url(token):
            return False, None, "Stored token does not match the configured XMDSource server"
        return True, {"Authorization": f"Bearer {token}"}, "OK"

    def _post_license_action(
        self, url: str, payload: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], str]:
        """POST to a runtime-license endpoint with auth headers.

        Args:
            url: Absolute endpoint URL.
            payload: Request body.

        Returns:
            ``(success, body_dict, message)``
        """
        ok, headers, msg = self._runtime_auth_headers()
        if not ok or headers is None:
            return False, {}, msg
        try:
            status, data = _http_post(url, payload, headers)
        except _NetworkError as exc:
            return False, {}, f"Network error: {exc}"
        if status == 401:
            return False, {}, "Session expired — please log in again"
        if status >= 400:
            err = data.get("message", f"Server error {status}") if isinstance(data, dict) else f"Server error {status}"
            return False, data if isinstance(data, dict) else {}, str(err)
        if isinstance(data, dict) and data.get("status") == "denied":
            return False, data, str(data.get("reason", "Lease denied"))
        return True, data if isinstance(data, dict) else {}, "OK"

    def _device_payload(self) -> dict[str, str]:
        """Build a stable per-device identification payload for lease calls.

        Generates and persists ``device_id`` and ``install_id`` UUIDs on first use.

        Returns:
            Device identification dictionary.
        """
        prefs = self._prefs

        device_id = _get(prefs, "xmdsource_device_id")
        if not device_id:
            device_id = f"dev_{uuid.uuid4().hex}"
            _set(prefs, "xmdsource_device_id", device_id)

        install_id = _get(prefs, "xmdsource_install_id")
        if not install_id:
            install_id = f"inst_{uuid.uuid4().hex}"
            _set(prefs, "xmdsource_install_id", install_id)

        return {
            "device_id": device_id,
            "install_id": install_id,
            "machine_name": socket.gethostname(),
            "platform": sys.platform,
            "app_version": _ADDON_VERSION,
        }

    def _grace_deadline(
        self, product_id: str, license_mode: str, last_verified_at: int
    ) -> int:
        """Return the offline grace deadline timestamp.

        Args:
            product_id: Entitlement product ID.
            license_mode: "named" or "floating".
            last_verified_at: Unix timestamp of last successful verification.

        Returns:
            Unix deadline timestamp, or 0 if grace is not applicable.
        """
        if not product_id or last_verified_at <= 0:
            return 0
        grace = _GRACE_FLOATING if license_mode == "floating" else _GRACE_NAMED_USER
        return last_verified_at + grace

    def _looks_like_outage(self, msg: str) -> bool:
        """Return whether an error message looks like a transient backend outage.

        Args:
            msg: Error message string to classify.

        Returns:
            True when the message matches known outage patterns.
        """
        lowered = msg.lower().strip()
        return any(f in lowered for f in _BACKEND_OUTAGE_FRAGMENTS)

    def _store_lease_state(
        self, entitlement: dict[str, Any], lease_payload: dict[str, Any]
    ) -> None:
        """Persist entitlement and lease state to add-on preferences.

        Args:
            entitlement: The matched entitlement dict from the server.
            lease_payload: The server response from the acquire/renew endpoint.
        """
        lease = lease_payload.get("lease", {}) if isinstance(lease_payload, dict) else {}
        _set(self._prefs, "xmdsource_runtime_product_id", entitlement.get("product_id", ""))
        _set(self._prefs, "xmdsource_runtime_entitlement_id", entitlement.get("entitlement_id", ""))
        _set(self._prefs, "xmdsource_runtime_license_mode", entitlement.get("license_mode", ""))
        _set(self._prefs, "xmdsource_runtime_seat_count",
             int(lease_payload.get("seat_count", entitlement.get("seat_count", 0)) or 0))
        _set(self._prefs, "xmdsource_runtime_active_count",
             int(lease_payload.get("active_count", 0) or 0))
        _set(self._prefs, "xmdsource_runtime_lease_id", lease.get("lease_id", ""))
        _set(self._prefs, "xmdsource_runtime_lease_expires_at", lease.get("expires_at", ""))
        _set(self._prefs, "xmdsource_runtime_last_verified_at", int(time.time()))

    def _release_existing_lease_if_any(self) -> None:
        """Release the stored lease if one is active, silently ignoring errors."""
        lease_id = _get(self._prefs, "xmdsource_runtime_lease_id")
        if lease_id:
            try:
                self.release_license_lease(lease_id)
            except Exception:
                pass

    def _clear_runtime_state(self, *, clear_entitlement: bool) -> None:
        """Clear persisted runtime license state.

        Args:
            clear_entitlement: When True, also clears product/entitlement/mode fields.
        """
        _set(self._prefs, "xmdsource_runtime_lease_id", "")
        _set(self._prefs, "xmdsource_runtime_lease_expires_at", "")
        _set(self._prefs, "xmdsource_runtime_active_count", 0)
        if clear_entitlement:
            _set(self._prefs, "xmdsource_runtime_last_verified_at", 0)
            _set(self._prefs, "xmdsource_runtime_product_id", "")
            _set(self._prefs, "xmdsource_runtime_entitlement_id", "")
            _set(self._prefs, "xmdsource_runtime_license_mode", "")
            _set(self._prefs, "xmdsource_runtime_seat_count", 0)

    def _clear_legacy_runtime_state(self, *, release_existing_lease: bool) -> None:
        """Clear runtime state and optionally release the active lease.

        Args:
            release_existing_lease: When True, calls release_license_lease first.
        """
        if release_existing_lease:
            self._release_existing_lease_if_any()
        self._clear_runtime_state(clear_entitlement=True)

    def _clear_cloud_state(self) -> None:
        """Clear the stored JWT and user display fields."""
        for key in ("xmdsource_token", "xmdsource_username", "xmdsource_display_name"):
            _set(self._prefs, key, "")
        _set(self._prefs, "xmdsource_token_exp", 0)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _fmt_epoch(ts: int) -> str:
    """Format a Unix timestamp as a UTC ISO-8601 string, or empty string for 0.

    Args:
        ts: Unix timestamp integer.

    Returns:
        ISO-8601 formatted string, or ``""`` when ts is 0 or falsy.
    """
    if not ts:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# bpy registration stubs (no Blender classes in this module)
# ---------------------------------------------------------------------------

def register() -> None:
    """No bpy classes to register in this module."""


def unregister() -> None:
    """No bpy classes to unregister in this module."""
