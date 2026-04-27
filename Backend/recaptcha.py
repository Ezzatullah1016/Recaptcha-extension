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
  SPAIN_VISA_START_DELAY_SEC — optional extra delay in seconds before the first request (e.g. 30).

Dependencies: pip install requests beautifulsoup4 pillow
OCR (recommended): install Tesseract, then pip install pytesseract
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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://appointment.thespainvisa.com"
LOGIN_PAGE = f"{BASE}/Global/account/login"
# Reference captcha URL shape (after login); `data` query is server-specific.
CAPTCHA_PAGE_TEMPLATE = f"{BASE}/Global/newcaptcha/logincaptcha?data="

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
    Retry on HTTP 429 (rate limit) with exponential backoff. Returns last response
    (possibly still 429) if all retries are exhausted.
    """
    max_retries = max(0, _env_int("SPAIN_VISA_HTTP_RETRIES", 4))
    base_wait = _env_float("SPAIN_VISA_HTTP_BACKOFF_SEC", 8.0)
    start = _env_float("SPAIN_VISA_START_DELAY_SEC", 0.0)
    if start > 0:
        time.sleep(start)

    last: requests.Response | None = None
    for attempt in range(max_retries + 1):
        m = method.lower()
        if m == "get":
            last = session.get(url, **kwargs)
        elif m == "post":
            last = session.post(url, **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")
        if last.status_code != 429:
            return last
        if attempt < max_retries:
            wait = base_wait * (2**attempt) + random.uniform(0.5, 2.0)
            print(
                f"HTTP 429 Too Many Requests — waiting {wait:.0f}s before retry "
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
        "  • Wait 15–60 minutes and try again; set $env:SPAIN_VISA_START_DELAY_SEC='30' before running.\n"
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
        return re.sub(r"\D", "", raw)
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
    if r.status_code == 429:
        print_429_help()
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
    if post_resp.status_code == 429:
        print_429_help()
        post_resp.raise_for_status()
    return post_resp


def build_captcha_response_data(form: Any, password: str) -> str:
    obj: dict[str, str] = {}
    for inp in form.find_all("input", type=lambda t: (t or "").lower() == "password"):
        key = inp.get("id") or inp.get("name")
        if not key:
            continue
        obj[key] = password if _visible(inp) else ""
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

    payload["SelectedImages"] = ",".join(selected_ids)
    payload["ResponseData"] = build_captcha_response_data(form, password)

    session.headers["Referer"] = captcha_page_url
    session.headers["Origin"] = BASE
    session.headers["Sec-Fetch-Site"] = "same-origin"
    post_resp = request_with_429_retry(
        session, "post", action, data=payload, timeout=60, allow_redirects=True
    )
    if post_resp.status_code == 429:
        print_429_help()
        post_resp.raise_for_status()
    return post_resp


def run_flow(email: str, password: str) -> requests.Response:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    captcha_only = os.environ.get("SPAIN_VISA_CAPTCHA_URL", "").strip()
    if captcha_only:
        last = request_with_429_retry(session, "get", captcha_only, timeout=60)
        if last.status_code == 429:
            print_429_help()
        last.raise_for_status()
        html, final_url = last.text, last.url
    else:
        last = submit_login(session, email, password)
        html, final_url = last.text, last.url

    if (
        "logincaptcha" not in final_url.lower()
        and "newcaptcha" not in html.lower()
        and "captcha-img" not in html.lower()
    ):
        return last

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

    return submit_captcha(session, html, final_url, password, selected)


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
        resp = run_flow(email, password)
        print("Final URL:", resp.url)
        print("Status:", resp.status_code)
        print(resp.text[:2000])
        if re.search(r"[?&]err=", resp.url, re.I) or "alert-danger" in resp.text:
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
                "  • Confirm email/password work in a normal browser on the same site.",
                file=sys.stderr,
            )
    except requests.RequestException as e:
        r = getattr(e, "response", None)
        if r is not None and r.status_code == 429:
            print_429_help()
        print("HTTP error:", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
