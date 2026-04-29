"""
Spain visa appointment: login at appointment.thespainvisa.com, then image captcha.

Replaces the old Google reCAPTCHA demo. Uses the same host paths as the site:
  - Login: /Global/account/login  -> POST /Global/account/LoginSubmit
  - Captcha: /Global/newcaptcha/logincaptcha?data=... -> POST form action (e.g. NewCaptcha/LoginCaptchaSubmit)

Field names and captcha box ids are dynamic; this module discovers them from HTML.

Environment:
  SPAIN_VISA_EMAIL     — account email
  SPAIN_VISA_PASSWORD  — account password (used on login and captcha password fields)

Optional:
  SPAIN_VISA_CAPTCHA_URL — if set, skip login and open this captcha URL with the same session
    (only useful if you already have a valid session cookie; otherwise run the full login flow).
  SPAIN_VISA_LOGIN_EMAIL_INPUT_INDEX — if the site does not expose the active field in script,
    use the Nth email-style text input (0-based) among candidates (default 0).
  SPAIN_VISA_LOGIN_RESPONSE_DATA_JSON — "active_only" (default) or "matrix".
    active_only sends ResponseData as {"<id>":"<email>"} only; matrix sends every honeypot key
    with "" except the active field (closer to some browser JSON.stringify loops).
  SPAIN_VISA_LOGIN_DEBUG — set to "1" to print which email field was chosen and ResponseData length.
  SPAIN_VISA_LOGIN_POST_ACTIVE_EMAIL_ONLY — "1" to POST only hidden fields + the one active email
    input (no other matrix names). Try if the default POST still returns ?err=.
  SPAIN_VISA_HTTP_RETRIES — how many times to retry after HTTP 429 (default 4).
  SPAIN_VISA_HTTP_BACKOFF_SEC — base wait seconds before first 429 retry (default 8; doubles each time).
  SPAIN_VISA_MIN_REQUEST_GAP_SEC — minimum delay between requests (default 2.5) to avoid burst traffic.
  SPAIN_VISA_429_COOLDOWN_SEC — cooldown before each retry when 429 block page is detected (default 60).
  SPAIN_VISA_NET_RESET_RETRIES — retries for transient connection reset/abort errors (default 3).
  SPAIN_VISA_FAIL_ON_429 — "1" (default) to stop immediately if still blocked after retries.
  SPAIN_VISA_START_DELAY_SEC — optional extra delay in seconds before the first request (e.g. 30).
  SPAIN_VISA_STEP2_BOOK_NOW — "1" (default) to automatically open Book New Appointment
    after login/captcha when possible, "0" to stop at the first post-login page.
  SPAIN_VISA_BOOK_URL — optional absolute/relative URL override for step 2 (e.g.
    /Global/appointment/newappointment).
  SPAIN_VISA_IGNORE_LOGIN_ERR — set to "1" to still attempt "Book New Appointment" when the
    server redirects to /Global/account/Login?err=... (opaque token). Uses SPAIN_VISA_BOOK_URL
    if set, otherwise /Global/appointment/newappointment. Session may still be rejected if
    login did not issue valid cookies.
  SPAIN_VISA_PLAYWRIGHT_DEBUG — "1" to print Playwright URL/status trace and server alerts.
  SPAIN_VISA_PLAYWRIGHT_HOLD_ON_FAIL_SEC — keep browser open N seconds on failure (default 5 in headed mode).

Dependencies: pip install requests beautifulsoup4 pillow
OCR (recommended): install Tesseract, then pip install pytesseract
OCR fallback (optional, stronger on noisy images): pip install easyocr opencv-python numpy
Browser mode (recommended when requests login is blocked): pip install playwright
  then install Chromium for that Python (Windows: the playwright CLI is often not on PATH):
    py -m playwright install chromium

  Proxy (optional — only where allowed by the site’s terms and applicable law):
  SPAIN_VISA_PROXY — single proxy URL for both http and https, e.g. http://host:8080 or
    http://user:pass@host:8080 (must be reachable; a placeholder like 127.0.0.1:8080 fails if nothing listens).
  SPAIN_VISA_HTTP_PROXY / SPAIN_VISA_HTTPS_PROXY — set separately if needed (https falls back to http).
  SPAIN_VISA_PROXY_LIST — comma/semicolon/newline-separated URLs, or path to a text file (one proxy per line,
    lines starting with # ignored). One entry is chosen at random when the session starts (same proxy for the
    whole run). Proxies do not bypass consent or anti-abuse rules; combine with pacing env vars above.
  SPAIN_VISA_DISABLE_PROXY — set to "1" to ignore all proxy env vars (direct connection).
  SPAIN_VISA_PROXY_DEBUG — set to "1" to log proxy host/port (no password).

This tool is for personal/automation assistance on sites you are authorized to use. Do not use proxies to
violate terms of service, robots.txt where enforceable, or local laws.
"""

from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import sys
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://appointment.thespainvisa.com"
LOGIN_PAGE = f"{BASE}/Global/account/login"
# Reference captcha URL shape (after login); `data` query is server-specific.
CAPTCHA_PAGE_TEMPLATE = f"{BASE}/Global/newcaptcha/logincaptcha?data="
# Used when the login page shows ?err= and has no book link, or when forcing step 2.
DEFAULT_BOOK_NEW_APPOINTMENT_URL = f"{BASE}/Global/appointment/newappointment"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

_LAST_REQUEST_TS = 0.0


def _normalize_proxy_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    return u


def _load_proxy_pool_from_env() -> list[str]:
    raw = os.environ.get("SPAIN_VISA_PROXY_LIST", "").strip()
    if not raw:
        return []
    path = os.path.expanduser(raw)
    if os.path.isfile(path):
        out: list[str] = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                nu = _normalize_proxy_url(line)
                if nu:
                    out.append(nu)
        return out
    parts = re.split(r"[\s,;]+", raw)
    return [_normalize_proxy_url(p) for p in parts if p.strip()]


def build_requests_proxies() -> dict[str, str] | None:
    """Return requests.Session proxies dict, or None if unset."""
    if os.environ.get("SPAIN_VISA_DISABLE_PROXY", "").strip() == "1":
        return None
    single = os.environ.get("SPAIN_VISA_PROXY", "").strip()
    http_p = os.environ.get("SPAIN_VISA_HTTP_PROXY", "").strip()
    https_p = os.environ.get("SPAIN_VISA_HTTPS_PROXY", "").strip()
    pool = _load_proxy_pool_from_env()

    if single:
        u = _normalize_proxy_url(single)
        return {"http": u, "https": u}
    if http_p or https_p:
        out: dict[str, str] = {}
        if http_p:
            out["http"] = _normalize_proxy_url(http_p)
        if https_p:
            out["https"] = _normalize_proxy_url(https_p)
        elif http_p:
            out["https"] = out["http"]
        return out or None
    if pool:
        u = random.choice(pool)
        return {"http": u, "https": u}
    return None


def _proxy_log_safe(proxies: dict[str, str]) -> str:
    u = proxies.get("https") or proxies.get("http") or ""
    try:
        p = urlparse(u)
        port = f":{p.port}" if p.port else ""
        host = p.hostname or "?"
        return f"{p.scheme}://{host}{port}"
    except Exception:
        return "(proxy)"


def playwright_proxy_from_env() -> dict[str, str] | None:
    """Playwright browser.new_context(proxy={...}) fragment; None if no proxy configured."""
    proxies = build_requests_proxies()
    if not proxies:
        return None
    url = proxies.get("https") or proxies.get("http") or ""
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    server = f"{parsed.scheme}://{parsed.hostname}:{port}"
    pw: dict[str, str] = {"server": server}
    if parsed.username:
        pw["username"] = parsed.username
    if parsed.password:
        pw["password"] = parsed.password
    return pw


def _apply_proxies_to_session(session: requests.Session) -> None:
    proxies = build_requests_proxies()
    if proxies:
        session.proxies.update(proxies)
        if os.environ.get("SPAIN_VISA_PROXY_DEBUG", "0").strip() == "1":
            print(f"[proxy] Using {_proxy_log_safe(proxies)}", file=sys.stderr)


def _pace_requests() -> None:
    global _LAST_REQUEST_TS
    min_gap = max(0.0, _env_float("SPAIN_VISA_MIN_REQUEST_GAP_SEC", 2.5))
    if min_gap <= 0:
        return
    now = time.time()
    wait = (_LAST_REQUEST_TS + min_gap) - now
    if wait > 0:
        time.sleep(wait)
    _LAST_REQUEST_TS = time.time()


def _is_rate_limit_html(text: str) -> bool:
    t = (text or "").lower()
    markers = (
        "<h1>too many requests</h1>",
        "our service is currently receiving unusually high traffic",
        "detected excessive requests from your ip",
        "please try again after some time",
    )
    return any(m in t for m in markers)


def _is_rate_limited_response(resp: requests.Response) -> bool:
    if resp.status_code == 429:
        return True
    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/html" in ctype and _is_rate_limit_html(resp.text):
        return True
    return False


def _force_https(url: str) -> str:
    if url.lower().startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _is_transient_network_reset_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    markers = (
        "connection aborted",
        "forcibly closed by the remote host",
        "connection reset",
        "10054",
        "remote end closed connection without response",
    )
    return any(m in msg for m in markers)


def _login_err_in_url(url: str) -> bool:
    return bool(re.search(r"[?&]err=", url or "", re.I))


def _ignore_login_err_for_book_step() -> bool:
    return os.environ.get("SPAIN_VISA_IGNORE_LOGIN_ERR", "0").strip() == "1"


def _looks_like_login_page(url: str, html: str) -> bool:
    ul = (url or "").lower()
    # Do not treat captcha / post-login pages as "login" just because they share generic markers.
    if "newcaptcha" in ul or "logincaptcha" in ul:
        return False
    if "/global/account/login" in ul:
        return True
    hl = (html or "").lower()
    markers = (
        "/global/account/loginsubmit",
        "returnurl=",
        "id=\"btnsubmit\"",
        "onsubmitverify(",
    )
    return any(m in hl for m in markers)


class FlowResult:
    def __init__(self, url: str, status_code: int, text: str) -> None:
        self.url = url
        self.status_code = status_code
        self.text = text


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)).strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)).strip())
    except ValueError:
        return default


def request_with_429_retry(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    """
    Retry when rate-limited (HTTP 429 or explicit block HTML page), and for
    transient network reset/abort errors. Also forces HTTPS across redirects.
    Returns first non-blocked response.
    """
    max_retries = max(0, _env_int("SPAIN_VISA_HTTP_RETRIES", 4))
    net_retries = max(0, _env_int("SPAIN_VISA_NET_RESET_RETRIES", 3))
    base_wait = _env_float("SPAIN_VISA_HTTP_BACKOFF_SEC", 8.0)
    cooldown = max(1.0, _env_float("SPAIN_VISA_429_COOLDOWN_SEC", 60.0))
    start = _env_float("SPAIN_VISA_START_DELAY_SEC", 0.0)
    if start > 0:
        time.sleep(start)

    caller_allow_redirects = bool(kwargs.pop("allow_redirects", True))
    last: requests.Response | None = None
    net_failures = 0
    for attempt in range(max_retries + 1):
        _pace_requests()
        m = method.lower()
        req_url = _force_https(url)
        req_kwargs = dict(kwargs)
        try:
            if not caller_allow_redirects:
                if m == "get":
                    last = session.get(req_url, allow_redirects=False, **req_kwargs)
                elif m == "post":
                    last = session.post(req_url, allow_redirects=False, **req_kwargs)
                else:
                    raise ValueError(f"Unsupported method: {method}")
            else:
                for _ in range(10):
                    if m == "get":
                        last = session.get(req_url, allow_redirects=False, **req_kwargs)
                    elif m == "post":
                        last = session.post(req_url, allow_redirects=False, **req_kwargs)
                    else:
                        raise ValueError(f"Unsupported method: {method}")

                    if last.is_redirect or last.is_permanent_redirect:
                        loc = (last.headers.get("Location") or "").strip()
                        if not loc:
                            break
                        req_url = _force_https(urljoin(last.url, loc))
                        if last.status_code in (301, 302, 303):
                            m = "get"
                            req_kwargs.pop("data", None)
                            req_kwargs.pop("json", None)
                        continue
                    break
        except requests.RequestException as e:
            if _is_transient_network_reset_error(e) and net_failures < net_retries:
                net_failures += 1
                wait = max(cooldown, base_wait * (2**min(net_failures - 1, 6))) + random.uniform(0.5, 2.0)
                print(
                    f"Connection reset/aborted by remote host — waiting {wait:.0f}s before retry "
                    f"({net_failures}/{net_retries})...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            raise
        if not _is_rate_limited_response(last):
            return last
        if attempt < max_retries:
            wait = max(cooldown, base_wait * (2**attempt) + random.uniform(0.5, 2.0))
            print(
                f"Rate-limited (HTTP 429 / block page) — waiting {wait:.0f}s before retry "
                f"({attempt + 1}/{max_retries})...",
                file=sys.stderr,
            )
            time.sleep(wait)
    assert last is not None
    return last


def print_429_help() -> None:
    print(
        "\nThe site returned HTTP 429 (rate limit): too many requests from this IP or heavy traffic.\n"
        "This is server-side protection, not a bug in the script logic.\n"
        "Options:\n"
        "  • Wait 15–60 minutes and try again; set $env:SPAIN_VISA_START_DELAY_SEC='60' before running.\n"
        "  • Increase cooldown and retries, e.g. SPAIN_VISA_429_COOLDOWN_SEC=120 and SPAIN_VISA_HTTP_RETRIES=6.\n"
        "  • Use a different network (mobile hotspot / another ISP) to get a different IP.\n"
        "  • Slow down: avoid repeated runs, browser extensions, and other tools hitting the same site.\n"
        "  • If automation must continue, control the same session as a real browser (e.g. Playwright) — "
        "out of scope for this simple requests client.\n",
        file=sys.stderr,
    )


def _visible(tag: Any) -> bool:
    if not tag or not getattr(tag, "name", None):
        return True
    el = tag
    while el is not None and getattr(el, "name", None):
        if el.get("hidden") is not None:
            return False
        style = (el.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            return False
        classes = el.get("class") or []
        if isinstance(classes, str):
            classes = [classes]
        if "d-none" in classes:
            return False
        el = el.parent
    return True


def _submittable(tag: Any) -> bool:
    """Disabled fields are not posted by browsers; honeypots may use the disabled attribute."""
    return tag.get("disabled") is None


def _form_by_action(soup: BeautifulSoup, pattern: re.Pattern[str]) -> Any:
    for form in soup.find_all("form"):
        act = (form.get("action") or "").lower()
        if pattern.search(act):
            return form
    return None


def _absolute_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return urljoin(BASE + "/", href.lstrip("/"))


def parse_captcha_target_number(html: str) -> str | None:
    m = re.search(
        r"please\s+select\s+all\s+boxes\s+with\s+number\s+(\d+)",
        html,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(r"number\s+(\d{2,})", html, flags=re.IGNORECASE)
    return m.group(1) if m else None


def _decode_captcha_image_src(src: str) -> bytes | None:
    if not src or "base64" not in src:
        return None
    try:
        b64 = src.split(",", 1)[1]
        return base64.b64decode(b64)
    except (IndexError, ValueError):
        return None


def ocr_digits_from_image(image_bytes: bytes) -> str:
    """Return digits read from a captcha tile; empty string if OCR unavailable or failed."""
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return ""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        w, h = img.size
        if w > 0 and h > 0:
            img = img.resize((max(w * 3, 48), max(h * 3, 48)))
        raw = pytesseract.image_to_string(
            img,
            config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789",
        )
        digits = re.sub(r"\D", "", raw)
        if digits:
            return digits
    except Exception:
        pass

    # Optional fallback OCR engine (useful when Tesseract misses stylized digits).
    # Requires: easyocr, opencv-python, numpy
    try:
        import cv2  # type: ignore
        import easyocr  # type: ignore
        import numpy as np  # type: ignore

        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        src = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if src is None:
            return ""
        proc = cv2.resize(src, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        proc = cv2.GaussianBlur(proc, (3, 3), 0)
        _, proc = cv2.threshold(proc, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        found = reader.readtext(proc, detail=0, paragraph=False, allowlist="0123456789")
        text = "".join(str(x) for x in found)
        return re.sub(r"\D", "", text)
    except Exception:
        return ""


def collect_captcha_tiles(html: str) -> list[tuple[str, bytes]]:
    """Pairs of (wrapper_div_id, raw_image_bytes) for each img.captcha-img."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, bytes]] = []
    for img in soup.find_all("img", class_=lambda c: c and "captcha-img" in c):
        parent = img.parent
        if not parent or not parent.get("id"):
            continue
        raw = _decode_captcha_image_src(img.get("src") or "")
        if raw:
            out.append((parent["id"], raw))
    return out


def select_tiles_for_target(target: str, tiles: list[tuple[str, bytes]]) -> list[str]:
    chosen: list[str] = []
    for box_id, img_bytes in tiles:
        digits = ocr_digits_from_image(img_bytes)
        if not digits:
            continue
        if target in digits or digits == target:
            chosen.append(box_id)
    return chosen


def _collect_login_matrix_text_inputs(form: Any) -> list[Any]:
    """Random-named email text boxes on login (not hidden anti-forgery fields)."""
    out: list[Any] = []
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name or name.startswith("__"):
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("text", "email", "tel"):
            out.append(inp)
    return out


def _extract_active_email_field_id_from_scripts(html: str) -> str | None:
    """Parse inline scripts for the one input the page enables (removes entry-disabled / disabled)."""
    chunks: list[str] = []
    for m in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", html, re.I):
        chunks.append(m.group(1))
    blob = "\n".join(chunks)
    patterns = [
        r'\$\(\s*["\']#([a-zA-Z0-9]+)["\']\s*\)\s*\.\s*removeClass\s*\(\s*["\']entry-disabled["\']\s*\)',
        r'\$\(\s*["\']#([a-zA-Z0-9]+)["\']\s*\)\s*\.\s*prop\s*\(\s*["\']disabled["\']\s*,\s*false',
        r"getElementById\s*\(\s*['\"]([a-zA-Z0-9]+)['\"]\s*\)\s*\.\s*classList\s*\.\s*remove\s*\(\s*['\"]entry-disabled['\"]",
        r"getElementById\s*\(\s*['\"]([a-zA-Z0-9]+)['\"]\s*\)\s*\.\s*removeAttribute\s*\(\s*['\"]disabled['\"]",
    ]
    for pat in patterns:
        mm = re.search(pat, blob)
        if mm:
            return mm.group(1)
    mm = re.search(
        r'["\']#([a-zA-Z0-9]{3,48})["\']\s*\)[\s\S]{0,500}?entry-disabled',
        blob,
    )
    if mm:
        return mm.group(1)
    mm = re.search(
        r"(?:inputId|fieldName|activeField|expectedField)\s*[:=]\s*['\"]([a-zA-Z0-9]+)['\"]",
        blob,
        re.I,
    )
    if mm:
        return mm.group(1)
    return None


def _pick_active_login_email_input(form: Any, html: str) -> Any:
    candidates = _collect_login_matrix_text_inputs(form)
    if not candidates:
        raise RuntimeError("Login form has no email text inputs.")

    aid = _extract_active_email_field_id_from_scripts(html)
    if aid:
        for inp in candidates:
            if inp.get("id") == aid or inp.get("name") == aid:
                return inp

    enabled_style: list[Any] = []
    for inp in candidates:
        classes = inp.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        if "entry-disabled" not in classes and _visible(inp) and _submittable(inp):
            enabled_style.append(inp)
    if len(enabled_style) == 1:
        return enabled_style[0]
    if len(enabled_style) > 1:
        return enabled_style[0]

    raw_idx = os.environ.get("SPAIN_VISA_LOGIN_EMAIL_INPUT_INDEX", "0").strip()
    try:
        idx = int(raw_idx)
    except ValueError:
        idx = 0
    idx = max(0, min(idx, len(candidates) - 1))
    return candidates[idx]


def _build_login_response_data_json(form: Any, email: str, active: Any) -> str:
    """JSON placed in #ResponseData. Default matches servers that reject a full matrix of ''."""
    mode = os.environ.get("SPAIN_VISA_LOGIN_RESPONSE_DATA_JSON", "active_only").strip().lower()
    active_key = active.get("id") or active.get("name")
    if not active_key:
        return "{}"
    if mode in ("matrix", "full", "all"):
        obj: dict[str, str] = {}
        for inp in _collect_login_matrix_text_inputs(form):
            key = inp.get("id") or inp.get("name")
            if not key:
                continue
            obj[key] = email if inp is active else ""
        return json.dumps(obj, separators=(",", ":"))
    return json.dumps({active_key: email}, separators=(",", ":"))


def _collect_password_inputs(form: Any) -> list[Any]:
    return [
        inp
        for inp in form.find_all("input")
        if (inp.get("type") or "").lower() == "password" and inp.get("name")
    ]


def submit_login(session: requests.Session, email: str, password: str) -> requests.Response:
    r = request_with_429_retry(session, "get", LOGIN_PAGE, timeout=60)
    if _is_rate_limited_response(r):
        print_429_help()
        if os.environ.get("SPAIN_VISA_FAIL_ON_429", "1").strip() == "1":
            raise RuntimeError("Rate-limited on login page; stopping before next step.")
    r.raise_for_status()
    raw_html = r.text
    soup = BeautifulSoup(raw_html, "html.parser")
    form = _form_by_action(soup, re.compile(r"loginsubmit", re.I))
    if not form:
        raise RuntimeError("Could not find login form (action LoginSubmit).")

    action = _absolute_url(form.get("action") or "/Global/account/LoginSubmit")
    active_email = _pick_active_login_email_input(form, raw_html)
    if os.environ.get("SPAIN_VISA_LOGIN_DEBUG", "").strip() == "1":
        ak = active_email.get("id") or active_email.get("name")
        print(f"[login debug] active_email field id/name={ak!r}", file=sys.stderr)

    payload: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype == "hidden":
            payload[name] = inp.get("value") or ""

    active_only_post = (
        os.environ.get("SPAIN_VISA_LOGIN_POST_ACTIVE_EMAIL_ONLY", "").strip() == "1"
    )
    for inp in _collect_login_matrix_text_inputs(form):
        name = inp.get("name")
        if not name:
            continue
        if inp is active_email:
            payload[name] = email
        elif active_only_post:
            continue
        elif _submittable(inp):
            payload[name] = ""

    pw_inputs = _collect_password_inputs(form)
    if len(pw_inputs) == 1 and _visible(pw_inputs[0]) and _submittable(pw_inputs[0]):
        payload[pw_inputs[0]["name"]] = password
    elif len(pw_inputs) > 1:
        active_pw = next(
            (p for p in pw_inputs if _visible(p) and _submittable(p)),
            pw_inputs[0],
        )
        for inp in pw_inputs:
            n = inp.get("name")
            if not n or not _submittable(inp):
                continue
            payload[n] = password if inp is active_pw else ""

    payload["ResponseData"] = _build_login_response_data_json(form, email, active_email)
    if os.environ.get("SPAIN_VISA_LOGIN_DEBUG", "").strip() == "1":
        rd = payload.get("ResponseData", "")
        print(
            f"[login debug] ResponseData mode="
            f"{os.environ.get('SPAIN_VISA_LOGIN_RESPONSE_DATA_JSON', 'active_only')!r} len={len(rd)}",
            file=sys.stderr,
        )

    session.headers["Referer"] = LOGIN_PAGE
    session.headers["Origin"] = BASE
    session.headers["Sec-Fetch-Site"] = "same-origin"
    post_resp = request_with_429_retry(
        session, "post", action, data=payload, timeout=60, allow_redirects=True
    )
    if _is_rate_limited_response(post_resp):
        print_429_help()
        if os.environ.get("SPAIN_VISA_FAIL_ON_429", "1").strip() == "1":
            raise RuntimeError("Rate-limited on login submit; stopping before next step.")
        post_resp.raise_for_status()
    return post_resp


def build_captcha_response_data(form: Any, password: str) -> str:
    obj: dict[str, str] = {}
    pw_inputs = _collect_password_inputs(form)
    active_pw = next((p for p in pw_inputs if _visible(p) and _submittable(p)), None)
    for inp in pw_inputs:
        key = inp.get("id") or inp.get("name")
        if not key:
            continue
        obj[key] = password if inp is active_pw else ""
    return json.dumps(obj, separators=(",", ":"))


def submit_captcha(
    session: requests.Session,
    captcha_html: str,
    captcha_page_url: str,
    password: str,
    selected_ids: list[str],
) -> requests.Response:
    soup = BeautifulSoup(captcha_html, "html.parser")
    form = soup.find("form", id="captchaForm") or _form_by_action(
        soup, re.compile(r"logincaptchasubmit|newcaptcha", re.I)
    )
    if not form:
        raise RuntimeError("Could not find captcha form (id captchaForm or LoginCaptchaSubmit).")

    action = _absolute_url(form.get("action") or "")
    if not action or action.endswith("/"):
        action = urljoin(BASE + "/", "/Global/NewCaptcha/LoginCaptchaSubmit")

    payload: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype == "hidden":
            payload[name] = inp.get("value") or ""

    # Mirror browser behavior for dynamic captcha password fields:
    # one active input carries the password, honeypot/disabled ones remain empty.
    pw_inputs = _collect_password_inputs(form)
    if len(pw_inputs) == 1 and _visible(pw_inputs[0]) and _submittable(pw_inputs[0]):
        payload[pw_inputs[0]["name"]] = password
    elif len(pw_inputs) > 1:
        active_pw = next(
            (p for p in pw_inputs if _visible(p) and _submittable(p)),
            pw_inputs[0],
        )
        for inp in pw_inputs:
            n = inp.get("name")
            if not n or not _submittable(inp):
                continue
            payload[n] = password if inp is active_pw else ""

    payload["SelectedImages"] = ",".join(selected_ids)
    payload["ResponseData"] = build_captcha_response_data(form, password)

    session.headers["Referer"] = captcha_page_url
    session.headers["Origin"] = BASE
    session.headers["Sec-Fetch-Site"] = "same-origin"
    post_resp = request_with_429_retry(
        session, "post", action, data=payload, timeout=60, allow_redirects=True
    )
    if _is_rate_limited_response(post_resp):
        print_429_help()
        if os.environ.get("SPAIN_VISA_FAIL_ON_429", "1").strip() == "1":
            raise RuntimeError("Rate-limited on captcha submit; stopping before next step.")
        post_resp.raise_for_status()
    return post_resp


def find_book_new_appointment_url(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")

    # Fast path: known endpoint appears in href.
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if "/appointment/newappointment" in href.lower():
            return _absolute_url(href)

    # Fallback: discover links by visible text labels.
    wanted = ("book now", "book new appointment", "new appointment")
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True).lower()
        if any(w in txt for w in wanted):
            href = (a.get("href") or "").strip()
            if href:
                return urljoin(base_url, href)
    return None


def open_book_new_appointment(
    session: requests.Session,
    page_html: str,
    page_url: str,
    forced_target: str | None = None,
) -> requests.Response | None:
    if os.environ.get("SPAIN_VISA_STEP2_BOOK_NOW", "1").strip() == "0":
        return None

    if forced_target:
        ft = forced_target.strip()
        target = _absolute_url(ft) if not ft.startswith("http") else ft
    else:
        override = os.environ.get("SPAIN_VISA_BOOK_URL", "").strip()
        if override:
            target = _absolute_url(override) if not override.startswith("http") else override
        else:
            target = find_book_new_appointment_url(page_html, page_url)

    if not target:
        return None

    session.headers["Referer"] = page_url
    session.headers["Origin"] = BASE
    session.headers["Sec-Fetch-Site"] = "same-origin"
    r = request_with_429_retry(session, "get", target, timeout=60, allow_redirects=True)
    if r.status_code == 429:
        print_429_help()
        r.raise_for_status()
    return r


def run_flow(email: str, password: str) -> requests.Response:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    _apply_proxies_to_session(session)

    captcha_only = os.environ.get("SPAIN_VISA_CAPTCHA_URL", "").strip()
    if captcha_only:
        last = request_with_429_retry(session, "get", captcha_only, timeout=60)
        if _is_rate_limited_response(last):
            print_429_help()
            raise RuntimeError("Rate-limited while opening captcha page; stopping before next step.")
        last.raise_for_status()
        html, final_url = last.text, last.url
    else:
        last = submit_login(session, email, password)
        html, final_url = last.text, last.url

    if _is_rate_limit_html(html):
        print_429_help()
        raise RuntimeError("Rate-limit block page detected; stopping before next step.")
    if _looks_like_login_page(final_url, html):
        if _ignore_login_err_for_book_step() and _login_err_in_url(final_url):
            rel = os.environ.get("SPAIN_VISA_BOOK_URL", "").strip() or DEFAULT_BOOK_NEW_APPOINTMENT_URL
            step2 = open_book_new_appointment(session, html, final_url, forced_target=rel)
            if step2 is not None and not _looks_like_login_page(step2.url, step2.text):
                return step2
        raise RuntimeError(
            "Authentication did not establish a logged-in session (returned to login page)."
        )

    if (
        "logincaptcha" not in final_url.lower()
        and "newcaptcha" not in html.lower()
        and "captcha-img" not in html.lower()
    ):
        step2 = open_book_new_appointment(session, html, final_url)
        return step2 or last

    target = parse_captcha_target_number(html)
    tiles = collect_captcha_tiles(html)
    if not target:
        print("Warning: could not parse captcha target number from HTML.", file=sys.stderr)
    selected = select_tiles_for_target(target or "", tiles) if target else []
    if target and not selected:
        print(
            "Warning: no captcha tiles selected (install Pillow+pytesseract and Tesseract, "
            "or verify OCR). Submitting empty selection may fail.",
            file=sys.stderr,
        )

    after_captcha = submit_captcha(session, html, final_url, password, selected)
    if _looks_like_login_page(after_captcha.url, after_captcha.text):
        if _ignore_login_err_for_book_step() and _login_err_in_url(after_captcha.url):
            rel = os.environ.get("SPAIN_VISA_BOOK_URL", "").strip() or DEFAULT_BOOK_NEW_APPOINTMENT_URL
            step2 = open_book_new_appointment(
                session, after_captcha.text, after_captcha.url, forced_target=rel
            )
            if step2 is not None and not _looks_like_login_page(step2.url, step2.text):
                return step2
        raise RuntimeError(
            "Captcha/login flow returned to login page; session is not authenticated yet."
        )
    step2 = open_book_new_appointment(session, after_captcha.text, after_captcha.url)
    out = step2 or after_captcha
    if _looks_like_login_page(out.url, out.text):
        raise RuntimeError(
            "Step 2 redirected back to login page; authentication is still blocked."
        )
    return out


def _playwright_targets(page: Any) -> list[Any]:
    out = [page]
    try:
        out.extend(page.frames)
    except Exception:
        pass
    return out


def _playwright_fill_visible_input(page: Any, selector: str, value: str) -> bool:
    for target in _playwright_targets(page):
        items = target.locator(selector)
        n = items.count()
        for i in range(n):
            el = items.nth(i)
            try:
                if not el.is_visible():
                    continue
                # Some dynamic fields are initially readonly/disabled in anti-bot forms.
                # Try normal fill first, then force value via JS event dispatch.
                try:
                    el.fill(value, timeout=3000)
                    return True
                except Exception:
                    el.evaluate(
                        """(node, val) => {
                            try { node.removeAttribute('readonly'); } catch(e) {}
                            try { node.removeAttribute('disabled'); } catch(e) {}
                            node.value = val;
                            node.dispatchEvent(new Event('input', { bubbles: true }));
                            node.dispatchEvent(new Event('change', { bubbles: true }));
                        }""",
                        value,
                    )
                    return True
            except Exception:
                continue
    return False


def _playwright_fill_login_email(page: Any, email: str) -> bool:
    selectors = [
        "input[type='email']",
        "input[name*='mail' i]",
        "input[id*='mail' i]",
        "form[action*='loginsubmit' i] input[type='text']",
        "input[type='text']",
    ]
    for sel in selectors:
        if _playwright_fill_visible_input(page, sel, email):
            return True
    return False


def _playwright_fill_login_password(page: Any, password: str) -> bool:
    selectors = [
        "input[type='password']",
        "input[autocomplete='current-password']",
        "input[name*='pass' i]",
        "input[id*='pass' i]",
    ]
    for sel in selectors:
        if _playwright_fill_visible_input(page, sel, password):
            return True
    return False


def _playwright_fill_login_fields(page: Any, email: str, password: str) -> tuple[bool, bool]:
    """
    Fill email/password from the same LoginSubmit form to avoid cross-field mixups.
    Returns (email_filled, password_filled).
    """
    for target in _playwright_targets(page):
        try:
            out = target.evaluate(
                """(args) => {
                    const emailVal = args.email || "";
                    const passVal = args.password || "";
                    const form = document.querySelector('form[action*="LoginSubmit" i]') || document.querySelector("form");
                    if (!form) return { emailFilled: false, passwordFilled: false };

                    const isVisible = (el) => {
                        if (!el) return false;
                        const st = window.getComputedStyle(el);
                        if (!st) return true;
                        return st.display !== "none" && st.visibility !== "hidden";
                    };
                    const canUse = (el) => {
                        if (!el || el.disabled) return false;
                        if (!isVisible(el)) return false;
                        return true;
                    };
                    const patch = (el, val) => {
                        try { el.removeAttribute("readonly"); } catch (e) {}
                        try { el.removeAttribute("disabled"); } catch (e) {}
                        el.focus();
                        el.value = val;
                        el.dispatchEvent(new Event("input", { bubbles: true }));
                        el.dispatchEvent(new Event("change", { bubbles: true }));
                    };
                    const hasCls = (el, c) => (el.classList ? el.classList.contains(c) : false);

                    const all = Array.from(form.querySelectorAll("input"));
                    const emailCandidates = all.filter((el) => {
                        const t = (el.type || "text").toLowerCase();
                        const k = ((el.name || "") + " " + (el.id || "")).toLowerCase();
                        if (!canUse(el)) return false;
                        if (t === "hidden" || t === "password") return false;
                        if (k.includes("pass")) return false;
                        return (t === "email" || t === "text" || t === "tel");
                    });
                    const pwCandidates = all.filter((el) => {
                        const t = (el.type || "").toLowerCase();
                        const k = ((el.name || "") + " " + (el.id || "")).toLowerCase();
                        if (!canUse(el)) return false;
                        return t === "password" || k.includes("pass");
                    });

                    const norm = (s) => (s || "").trim().toLowerCase();
                    const emailNorm = norm(emailVal);

                    // Prefer the one enabled field the page exposes (not entry-disabled).
                    let pickEmail =
                        emailCandidates.find((el) => !hasCls(el, "entry-disabled")) || null;

                    // If everything is still honeypot-disabled, prefer a field that already matches
                    // the intended email (server sometimes pre-seeds the active slot).
                    if (!pickEmail && emailNorm) {
                        pickEmail =
                            emailCandidates.find((el) => norm(el.value) === emailNorm) || null;
                    }

                    // Never pick a honeypot that already contains a different email value.
                    if (pickEmail && emailNorm && norm(pickEmail.value) && norm(pickEmail.value) !== emailNorm) {
                        pickEmail = null;
                    }

                    // Last resort: first candidate (still risky, but better than nothing).
                    if (!pickEmail) {
                        pickEmail = emailCandidates[0] || null;
                    }
                    const pickPw =
                        pwCandidates.find((el) => !hasCls(el, "entry-disabled")) ||
                        pwCandidates[0] ||
                        null;

                    let emailFilled = false;
                    let passwordFilled = false;
                    if (pickEmail && emailVal) {
                        patch(pickEmail, emailVal);
                        emailFilled = true;
                    }
                    if (pickPw && passVal && pickPw !== pickEmail) {
                        patch(pickPw, passVal);
                        passwordFilled = true;
                    }
                    return { emailFilled, passwordFilled };
                }""",
                {"email": email, "password": password},
            )
            if out and (out.get("emailFilled") or out.get("passwordFilled")):
                return bool(out.get("emailFilled")), bool(out.get("passwordFilled"))
        except Exception:
            continue
    return False, False


def _playwright_submit_login(page: Any) -> str:
    # Prefer the page's own JS submit pipeline because it builds dynamic ResponseData.
    for target in _playwright_targets(page):
        try:
            has_submit_fn = target.evaluate("() => typeof OnSubmitVerify === 'function'")
            if has_submit_fn:
                ok = target.evaluate(
                    """() => {
                        const pass = OnSubmitVerify();
                        if (pass === false) return false;
                        const f = document.querySelector('form[action*="LoginSubmit" i]') || document.querySelector("form");
                        if (!f) return false;
                        if (typeof f.requestSubmit === "function") f.requestSubmit();
                        else f.submit();
                        return true;
                    }"""
                )
                if ok:
                    return "OnSubmitVerify+submit"
        except Exception:
            continue

    # Fallback 1: click known login submit controls.
    for target in _playwright_targets(page):
        btn = target.locator(
            "#btnSubmit, button#btnSubmit, button[type='submit'], input[type='submit']"
        ).first
        try:
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=10000)
                return "click_btnSubmit"
        except Exception:
            pass

    # Fallback 2: submit login form directly.
    for target in _playwright_targets(page):
        try:
            ok = target.evaluate(
                """() => {
                    const f = document.querySelector('form[action*="LoginSubmit" i]');
                    if (!f) return false;
                    if (typeof f.requestSubmit === 'function') f.requestSubmit();
                    else f.submit();
                    return true;
                }"""
            )
            if ok:
                return "form_submit"
        except Exception:
            continue
    raise RuntimeError("Could not trigger login submit in browser mode.")


def _playwright_snapshot(page: Any, attempts: int = 5) -> tuple[str, str]:
    last_err: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        try:
            return page.url, page.content()
        except Exception as e:
            last_err = e
            time.sleep(0.7)
    if last_err is not None:
        raise RuntimeError(f"Could not snapshot page content during navigation: {last_err}")
    raise RuntimeError("Could not snapshot page content during navigation.")


def run_flow_playwright(email: str, password: str) -> FlowResult:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError(
            "Playwright mode requested but dependency is missing. "
            "Install with: pip install playwright  then  py -m playwright install chromium"
        ) from e

    book_override = os.environ.get("SPAIN_VISA_BOOK_URL", "").strip()
    headless = os.environ.get("SPAIN_VISA_PLAYWRIGHT_HEADLESS", "0").strip() == "1"
    pw_debug = os.environ.get("SPAIN_VISA_PLAYWRIGHT_DEBUG", "1").strip() == "1"
    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx_opts: dict[str, Any] = {
                "user_agent": DEFAULT_HEADERS["User-Agent"],
                "locale": "en-GB",
                "timezone_id": "Asia/Karachi",
            }
            pw_proxy = playwright_proxy_from_env()
            if pw_proxy:
                ctx_opts["proxy"] = pw_proxy
                if os.environ.get("SPAIN_VISA_PROXY_DEBUG", "0").strip() == "1":
                    print(f"[proxy] Playwright {pw_proxy.get('server', '')}", file=sys.stderr)
            context = browser.new_context(**ctx_opts)
            page = context.new_page()
            net_trace: list[str] = []
            auth_trace: list[str] = []
            if pw_debug:
                def _on_response(resp: Any) -> None:
                    try:
                        u = resp.url
                        if "appointment.thespainvisa.com" in u.lower():
                            net_trace.append(f"{resp.status} {u}")
                            ul = u.lower()
                            if (
                                "loginsubmit" in ul
                                or "/global/account/login" in ul
                                or "logincaptcha" in ul
                                or "newcaptcha" in ul
                                or "err=" in ul
                            ):
                                auth_trace.append(f"{resp.status} {u}")
                    except Exception:
                        pass
                page.on("response", _on_response)
            try:
                page.goto(LOGIN_PAGE, wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                err_txt = str(e)
                if "ERR_PROXY_CONNECTION_FAILED" in err_txt or "ERR_TUNNEL_CONNECTION_FAILED" in err_txt:
                    raise RuntimeError(
                        "Browser could not use the configured proxy (connection failed). "
                        "Ensure the proxy is running and the host/port are correct, or unset SPAIN_VISA_PROXY "
                        "(and related proxy env vars). Example: http://127.0.0.1:8080 only works if a proxy listens "
                        "on port 8080. To force a direct connection: SPAIN_VISA_DISABLE_PROXY=1"
                    ) from e
                raise

            if _is_rate_limit_html(page.content()):
                raise RuntimeError("Rate-limit block page detected in browser mode.")

            em_ok, pw_ok = _playwright_fill_login_fields(page, email, password)
            if not em_ok and not _playwright_fill_login_email(page, email):
                raise RuntimeError("Could not find active email input in browser mode.")
            if not pw_ok:
                pw_ok = _playwright_fill_login_password(page, password)
            # Some login variants only require email first and request password/captcha next.
            # Do not hard-fail if password field is missing on the initial screen.

            already_opened_book = False
            submit_path = _playwright_submit_login(page)
            page.wait_for_load_state("domcontentloaded", timeout=90000)
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(2.0)

            cur, html = _playwright_snapshot(page)
            if _is_rate_limit_html(html):
                raise RuntimeError("Rate-limit block page detected after login submit.")
            if _looks_like_login_page(cur, html):
                # If no submit endpoint was called, try a second, more direct submit.
                saw_login_submit = any("loginsubmit" in x.lower() for x in net_trace)
                if not saw_login_submit:
                    try:
                        page.evaluate(
                            """() => {
                                const f = document.querySelector('form[action*="LoginSubmit" i]');
                                if (!f) return false;
                                if (typeof OnSubmitVerify === 'function') OnSubmitVerify();
                                else if (typeof f.requestSubmit === 'function') f.requestSubmit();
                                else f.submit();
                                return true;
                            }"""
                        )
                        page.wait_for_load_state("domcontentloaded", timeout=60000)
                        page.wait_for_load_state("networkidle", timeout=30000)
                        time.sleep(1.5)
                        cur, html = _playwright_snapshot(page)
                    except Exception:
                        pass
                if _looks_like_login_page(cur, html):
                    if _ignore_login_err_for_book_step() and _login_err_in_url(cur):
                        rel = book_override or DEFAULT_BOOK_NEW_APPOINTMENT_URL
                        book_url = _absolute_url(rel) if not rel.startswith("http") else rel
                        try:
                            page.goto(book_url, wait_until="domcontentloaded", timeout=90000)
                            time.sleep(2.0)
                            cur, html = _playwright_snapshot(page)
                            already_opened_book = True
                        except Exception:
                            pass
                if _looks_like_login_page(cur, html):
                    if pw_debug:
                        alert = ""
                        try:
                            alert = (
                                page.locator(".alert-danger, .validation-summary-errors, .text-danger")
                                .first.inner_text(timeout=1500)
                                .strip()
                            )
                        except Exception:
                            alert = ""
                        try:
                            dom_err = page.evaluate(
                                """() => {
                                    const v = document.querySelector('.validation-summary, .validation-summary-errors, .alert-danger');
                                    return v ? (v.innerText || '').trim() : '';
                                }"""
                            )
                            if dom_err and not alert:
                                alert = str(dom_err)
                        except Exception:
                            pass
                        print(f"[pw-debug] submit_path={submit_path}", file=sys.stderr)
                        print(f"[pw-debug] current_url={cur}", file=sys.stderr)
                        if alert:
                            print(f"[pw-debug] alert={alert}", file=sys.stderr)
                        if auth_trace:
                            print("[pw-debug] auth events:", file=sys.stderr)
                            for ln in auth_trace[-20:]:
                                print(f"[pw-debug] {ln}", file=sys.stderr)
                        if net_trace:
                            print("[pw-debug] last network events:", file=sys.stderr)
                            for ln in net_trace[-20:]:
                                print(f"[pw-debug] {ln}", file=sys.stderr)
                    raise RuntimeError("Login returned to login page in browser mode.")

            if ("logincaptcha" in cur.lower()) or ("newcaptcha" in html.lower()) or ("captcha-img" in html.lower()):
                target = parse_captcha_target_number(html)
                tiles = collect_captcha_tiles(html)
                selected = select_tiles_for_target(target or "", tiles) if target else []
                for tile_id in selected:
                    page.locator(f"#{tile_id}").click(timeout=5000)
                _playwright_fill_login_password(page, password)
                verify = page.locator("#btnVerify, button[type='submit'], input[type='submit']").first
                verify.click(timeout=15000)
                page.wait_for_load_state("domcontentloaded", timeout=90000)
                time.sleep(2.0)
                cur, html = _playwright_snapshot(page)
                if _looks_like_login_page(cur, html):
                    if _ignore_login_err_for_book_step() and _login_err_in_url(cur):
                        rel = book_override or DEFAULT_BOOK_NEW_APPOINTMENT_URL
                        book_url = _absolute_url(rel) if not rel.startswith("http") else rel
                        try:
                            page.goto(book_url, wait_until="domcontentloaded", timeout=90000)
                            time.sleep(2.0)
                            cur, html = _playwright_snapshot(page)
                            already_opened_book = True
                        except Exception:
                            pass
                    if _looks_like_login_page(cur, html):
                        raise RuntimeError("Captcha flow returned to login page in browser mode.")

            if os.environ.get("SPAIN_VISA_STEP2_BOOK_NOW", "1").strip() != "0":
                if already_opened_book and "newappointment" in cur.lower():
                    pass
                else:
                    target = _absolute_url(book_override) if book_override else find_book_new_appointment_url(html, cur)
                    if target:
                        page.goto(target, wait_until="domcontentloaded", timeout=90000)
                        time.sleep(2.0)
                        cur, html = _playwright_snapshot(page)
                        if _looks_like_login_page(cur, html):
                            raise RuntimeError("Step 2 redirected back to login in browser mode.")

            return FlowResult(cur, 200, html)
    except PlaywrightTimeoutError as e:
        raise RuntimeError(f"Playwright timeout: {e}") from e
    except RuntimeError:
        hold = _env_float("SPAIN_VISA_PLAYWRIGHT_HOLD_ON_FAIL_SEC", 5.0 if not headless else 0.0)
        if hold > 0:
            time.sleep(hold)
        raise
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass


def main() -> None:
    email = os.environ.get("SPAIN_VISA_EMAIL", "").strip()
    password = os.environ.get("SPAIN_VISA_PASSWORD", "").strip()
    if not email or not password:
        print(
            "Set SPAIN_VISA_EMAIL and SPAIN_VISA_PASSWORD. "
            "Optional: SPAIN_VISA_CAPTCHA_URL to open captcha directly.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        use_pw = os.environ.get("SPAIN_VISA_USE_PLAYWRIGHT", "0").strip() == "1"
        resp = run_flow_playwright(email, password) if use_pw else run_flow(email, password)
        print("Final URL:", resp.url)
        print("Status:", resp.status_code)
        print(resp.text[:2000])
        if (
            "newappointment" not in resp.url.lower()
            and (re.search(r"[?&]err=", resp.url, re.I) or "alert-danger" in resp.text)
        ):
            try:
                alert = BeautifulSoup(resp.text, "html.parser").select_one(".alert-danger")
                if alert and alert.get_text(strip=True):
                    print(f"Server message: {alert.get_text(strip=True)}", file=sys.stderr)
            except Exception:
                pass
            print(
                "\nLogin did not succeed (server returned an error page or ?err= in the URL).\n"
                "Common fixes:\n"
                "  • PowerShell: no space after the opening quote — use "
                '$env:SPAIN_VISA_PASSWORD="YourPasswordHere" (not "= YourPassword...").\n'
                "  • Set both variables in the same window before running: "
                "$env:SPAIN_VISA_EMAIL and $env:SPAIN_VISA_PASSWORD.\n"
                "  • Wrong honeypot email slot: try "
                '$env:SPAIN_VISA_LOGIN_EMAIL_INPUT_INDEX="0" then 1, 2, …\n'
                "  • ResponseData shape: default is active_only; try "
                '$env:SPAIN_VISA_LOGIN_RESPONSE_DATA_JSON="matrix"\n'
                "  • Try posting only the active email field: "
                '$env:SPAIN_VISA_LOGIN_POST_ACTIVE_EMAIL_ONLY="1"\n'
                '  • See which field the script picks: $env:SPAIN_VISA_LOGIN_DEBUG="1"\n',
                "  • Login redirects to ?err= but you want to try booking anyway: "
                '$env:SPAIN_VISA_IGNORE_LOGIN_ERR="1" (optional $env:SPAIN_VISA_BOOK_URL for a custom path).\n'
                "  • Confirm email/password work in a normal browser on the same site.",
                file=sys.stderr,
            )
    except RuntimeError as e:
        print("Flow stopped:", e, file=sys.stderr)
        print(
            "The site is still rejecting automated session authentication. "
            "Use a fresh network/IP and verify manual login first, then retry once.",
            file=sys.stderr,
        )
        sys.exit(1)
    except requests.RequestException as e:
        r = getattr(e, "response", None)
        if r is not None and r.status_code == 429:
            print_429_help()
        elif _is_transient_network_reset_error(e):
            print(
                "Connection was reset by remote host (likely anti-bot edge/network protection). "
                "Try later or switch network/IP.",
                file=sys.stderr,
            )
        print("HTTP error:", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
