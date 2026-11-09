"""URL helpers shared by planning and discovery."""

from __future__ import annotations

import urllib.parse


def _build_search_url(query: str) -> str:
    return f"https://www.bing.com/search?q={urllib.parse.quote_plus(query)}"


def _normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.query,
            "",
        )
    )


def _extract_hostname(url: str) -> str:
    stripped = url.strip()
    candidate = stripped if "://" in stripped else f"https://{stripped.lstrip('/')}"
    return (urllib.parse.urlparse(candidate).hostname or "").lower()


def _domain_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")
