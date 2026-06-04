"""Sandbox-callable tools for the podcast workflow.

Only one tool is exposed to the agents: `fetch_url`. It runs on the host
(urllib) because the Hyperlight guest's stdlib networking stalls, and is
locked to BBC Sport — the single source of truth for this pipeline.

The `make_*_tool(...)` factory returns a fresh callable so the same tool
can be attached to multiple agents without sharing decorator state.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from agent_framework import tool
from pydantic import Field

from .hyperlight_runtime import HyperlightRuntime


# --------------------------- fetch_url ------------------------------------


_FETCH_URL_DESCRIPTION = (
    "Fetch a BBC Sport URL and return clean article text (HTML stripped, "
    "whitespace collapsed, capped at ~8 KB to fit the sandbox shared output "
    "buffer). Only bbc.com / www.bbc.com are allow-listed."
)

# BBC-only. The pipeline's single source of truth is BBC Sport's World Cup
# coverage; any other domain is rejected to keep provenance unambiguous.
_ALLOWED_FETCH_HOSTS: frozenset[str] = frozenset({"www.bbc.com", "bbc.com"})

_FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# The Hyperlight guest's shared output buffer is 16,376 bytes. Host tool
# results are serialized back through that buffer, so cap the body well
# below that to leave headroom for JSON/framing overhead.
_FETCH_BODY_LIMIT = 8000


def _extract_meta(html: str, key: str, attr: str = "property") -> str:
    pattern = (
        rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']'
    )
    m = re.search(pattern, html, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _html_to_text(html: str) -> str:
    # Drop scripts, styles, and HTML comments.
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
    # Replace block-level tags with newlines so paragraphs survive collapse.
    html = re.sub(r"</(p|div|li|h[1-6]|article|section|br)\s*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    # Strip all remaining tags.
    text = re.sub(r"<[^>]+>", " ", html)
    # Decode the most common entities by hand (avoid `html` module — guest stalls on it).
    for entity, repl in (
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&apos;", "'"), ("&#39;", "'"), ("&nbsp;", " "),
        ("&ndash;", "-"), ("&mdash;", "—"), ("&hellip;", "…"),
    ):
        text = text.replace(entity, repl)
    # Collapse whitespace per line, drop empty lines.
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _host_fetch_url_sync(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"ERROR: unsupported scheme: {parsed.scheme}"
    if parsed.netloc not in _ALLOWED_FETCH_HOSTS:
        return f"ERROR: domain not allow-listed: {parsed.netloc}"
    req = Request(url, headers={"User-Agent": _FETCH_USER_AGENT})
    try:
        with urlopen(req, timeout=20) as resp:  # noqa: S310 - allow-listed
            status = getattr(resp, "status", None)
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - surface to model
        return f"ERROR: fetch failed: {exc!r}"

    html = raw.decode("utf-8", errors="ignore")
    title = _extract_meta(html, "og:title") or _extract_meta(html, "title", attr="name")
    description = _extract_meta(html, "og:description") or _extract_meta(
        html, "description", attr="name"
    )
    # Extract BBC sport article/video links from the raw HTML BEFORE we strip
    # tags; otherwise downstream regex on the cleaned BODY finds nothing.
    link_patterns = (
        r'href="(/sport/football/(?:articles|videos)/[A-Za-z0-9_-]+)"',
        r'href="(/sport/articles/[A-Za-z0-9_-]+)"',
        r'href="(https?://www\.bbc\.com/sport/(?:football/)?(?:articles|videos)/[A-Za-z0-9_-]+)"',
    )
    seen_links: set[str] = set()
    extracted_links: list[str] = []
    for pat in link_patterns:
        for h in re.findall(pat, html):
            u = h if h.startswith("http") else "https://www.bbc.com" + h
            if u in seen_links:
                continue
            if u.rstrip("/").endswith("/world-cup"):
                continue
            seen_links.add(u)
            extracted_links.append(u)
    body_text = _html_to_text(html)

    # Build a compact, model-friendly result. Header surfaces the most useful
    # fields (title/description/links) even if the body has to be truncated.
    header_parts = [f"STATUS: {status}", f"URL: {url}"]
    if title:
        header_parts.append(f"TITLE: {title}")
    if description:
        header_parts.append(f"DESCRIPTION: {description}")
    if extracted_links:
        # Cap link list so it never dominates the 8 KB budget.
        link_lines = "\n".join(f"  - {u}" for u in extracted_links[:30])
        header_parts.append(f"LINKS:\n{link_lines}")
    header = "\n".join(header_parts)

    available = max(_FETCH_BODY_LIMIT - len(header) - 16, 1024)  # 16 = newlines + label
    if len(body_text) > available:
        body_text = body_text[:available] + "\n...[truncated]"
    return f"{header}\n\nBODY:\n{body_text}"


def make_fetch_url_tool(runtime: HyperlightRuntime, on_call=None):
    """Fetch an allow-listed URL.

    Implementation note: The currently shipped Hyperlight Python guest
    module does not always expose `http_get`. Rather than ping-pong on
    feature detection, we run the fetch on the host (urllib) with a strict
    domain allow-list. The sandbox `runtime` argument is kept for API
    compatibility and for future re-routing through the guest if needed.

    `on_call` (optional): zero-arg callable invoked every time the tool
    runs. Used to count invocations originating from inside the sandbox
    via `call_tool(...)`, which bypass the agent's function middleware.
    """
    del runtime  # currently unused; kept for signature stability

    @tool(
        name="fetch_url",
        description=_FETCH_URL_DESCRIPTION,
        approval_mode="never_require",
    )
    async def fetch_url(
        url: Annotated[str, Field(description="Absolute URL to fetch (must be allow-listed).")],
    ) -> str:
        if on_call is not None:
            try:
                on_call()
            except Exception:  # noqa: BLE001 — counters must never break the tool
                pass
        return await asyncio.to_thread(_host_fetch_url_sync, url)

    return fetch_url


__all__ = ["make_fetch_url_tool"]
