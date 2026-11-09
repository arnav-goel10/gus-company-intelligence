"""URL helpers shared by planning and discovery."""

from __future__ import annotations

import urllib.parse


def build_search_url(query: str) -> str:
    return f"https://www.bing.com/search?q={urllib.parse.quote_plus(query)}"


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication: lower-case scheme and host, no fragment."""
    parsed = urllib.parse.urlsplit(url.strip())
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, "")
    )


def hostname_of(url: str) -> str:
    stripped = url.strip()
    candidate = stripped if "://" in stripped else f"https://{stripped.lstrip('/')}"
    return (urllib.parse.urlparse(candidate).hostname or "").lower()


def domain_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")
