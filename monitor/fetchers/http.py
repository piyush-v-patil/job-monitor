"""Shared HTTP session, retry logic, and field-normalization helpers."""
import html
import re
import time
from datetime import datetime, timedelta, timezone

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    return s


def get_json(s, url, retries=2, **kw):
    return _req(s, "GET", url, retries, **kw)


def post_json(s, url, retries=2, **kw):
    return _req(s, "POST", url, retries, **kw)


def _req(s, method, url, retries, **kw):
    kw.setdefault("timeout", 30)
    last = None
    for attempt in range(retries + 1):
        try:
            r = s.request(method, url, **kw)
            if r.status_code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + attempt)
    raise last


# --- shared normalization helpers -------------------------------------------

def iso_date(value) -> str:
    """Normalize an epoch (s or ms), ISO string, or None to 'YYYY-MM-DD'."""
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            n = float(value)
            if n > 1e11:      # milliseconds
                n /= 1000.0
            return datetime.fromtimestamp(n, timezone.utc).strftime("%Y-%m-%d")
        text = str(value).strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).strftime("%Y-%m-%d")
        except ValueError:
            for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y/%m/%d", "%m/%d/%Y"):
                try:
                    return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
            return ""
    except Exception:  # noqa: BLE001
        return ""


def rel_date(text: str) -> str:
    """Workday-style 'Posted 3 Days Ago' / 'Posted Today' -> 'YYYY-MM-DD'."""
    if not text:
        return ""
    t = text.lower()
    today = datetime.now(timezone.utc)
    if "today" in t:
        return today.strftime("%Y-%m-%d")
    if "yesterday" in t:
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return (today - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\+?\s*month", t)
    if m:
        return (today - timedelta(days=30 * int(m.group(1)))).strftime("%Y-%m-%d")
    return ""


def clean_text(html_or_text: str, limit: int = 180) -> str:
    """Strip tags/entities and collapse whitespace into a short plain snippet."""
    if not html_or_text:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(html_or_text))
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit].rstrip() + ("…" if len(s) > limit else "")
