"""Shared HTTP session with browser-like headers and simple retry."""
import time

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
