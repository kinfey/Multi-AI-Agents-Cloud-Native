"""
trend_scout.py  —  Trend scouting module.
Calls the OpenClaw Gateway /v1/chat/completions endpoint,
asks the trend-scout agent to use web_search tools to find the
current most popular AI/tech topics, and returns a structured Topic list.
"""

import datetime
import os
from pathlib import Path
import re
import json
import time
import requests
from dataclasses import dataclass
from typing import Optional

OPENCLAW_GATEWAY = os.getenv("OPENCLAW_GATEWAY", "http://openclaw:18789")

def _load_token() -> str:
    """
    Load the Gateway Token from a file or environment variable.
    Prefers OPENCLAW_TOKEN_FILE (rotated by secrets-init on each startup).
    """
    token_file = os.getenv("OPENCLAW_TOKEN_FILE", "/run/secrets/gateway-token")
    try:
        token = Path(token_file).read_text().strip()
        if token:
            return token
    except (FileNotFoundError, PermissionError):
        pass
    return os.getenv("OPENCLAW_TOKEN", "")

OPENCLAW_TOKEN = _load_token()


@dataclass
class TrendTopic:
    topic: str
    summary: str
    trend_score: int
    keywords: list[str]
    angle: str

    def __str__(self):
        return (
            f"[Score {self.trend_score}/10] {self.topic}\n"
            f"  Reason: {self.summary}\n"
            f"  Angle: {self.angle}\n"
            f"  Keywords: {', '.join(self.keywords)}"
        )


# ─────────────────────────────────────────────
#  OpenClaw Agent call (with retries + stream parsing)
# ─────────────────────────────────────────────

def _build_scout_prompt() -> str:
    now = datetime.datetime.now()
    today      = now.strftime("%Y-%m-%d")          # e.g. 2026-03-09
    month_year = now.strftime("%B %Y")             # e.g. March 2026
    year       = now.strftime("%Y")                # e.g. 2026
    iso_week   = now.strftime("week %W of %Y")     # e.g. week 10 of 2026
    return f"""Today's date: {today} ({iso_week}).
Use the web_search tool to run the following 2 searches immediately, then synthesise the results into JSON:

1. web_search("AI LLM large language model trending news {month_year}")
2. web_search("artificial intelligence major news {today}")

Once all searches are done, output the top 3 hottest topics strictly in the JSON array format below — no other text:
[
  {{
    "topic": "topic name (≤10 words)",
    "summary": "reason it is trending (≤50 words)",
    "trend_score": 9,
    "keywords": ["word1", "word2", "word3"],
    "angle": "podcast entry angle (≤20 words)"
  }}
]"""

SCOUT_PROMPT = _build_scout_prompt()


def _call_openclaw_agent(
    prompt: str,
    agent_id: str = "trend-scout",
    timeout: int = 600,
    max_retries: int = 2,
) -> str:
    """
    Call the OpenClaw Gateway /v1/chat/completions endpoint.
    Uses model: "openclaw:<agentId>" to target a specific agent.
    The agent invokes web_search tools then returns the final text.

    Ref: https://fossies.org/linux/openclaw/docs/gateway/openai-http-api.md
    """
    url = f"{OPENCLAW_GATEWAY}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENCLAW_TOKEN}",
        "Content-Type": "application/json",
        "x-openclaw-agent-id": agent_id,
    }
    payload = {
        "model": f"openclaw:{agent_id}",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 800,
    }

    for attempt in range(1, max_retries + 1):
        try:
            print(f"  [TrendScout] Calling OpenClaw agent '{agent_id}' (attempt {attempt})...")
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            print(f"  [TrendScout] Agent response length: {len(content)} chars")
            return content
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to OpenClaw Gateway ({OPENCLAW_GATEWAY})\n"
                "Please verify the OpenClaw container is running: docker compose up -d openclaw"
            )
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise RuntimeError(
                    "OpenClaw /v1/chat/completions returned 404.\n"
                    "Please verify openclaw.json contains:\n"
                    '  "gateway.http.endpoints.chatCompletions.enabled": true\n'
                    "and restart OpenClaw: docker compose restart openclaw"
                )
            elif e.response.status_code == 401:
                raise RuntimeError(
                    "OpenClaw authentication failed (401). Check OPENCLAW_TOKEN env var."
                )
            if attempt < max_retries:
                print(f"  [TrendScout] HTTP error {e.response.status_code}, retrying in 3s...")
                time.sleep(3)
            else:
                raise
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                print(f"  [TrendScout] Timeout, retrying in 5s...")
                time.sleep(5)
            else:
                raise RuntimeError(f"OpenClaw agent call timed out (>{timeout}s)")


# ─────────────────────────────────────────────
#  JSON parsing with error tolerance
# ─────────────────────────────────────────────

def _parse_topics(raw: str) -> list[TrendTopic]:
    """Extract JSON Topic list from agent response (tolerates extra text and <think> blocks)"""
    # Strip <think>...</think> blocks emitted by reasoning models (e.g. qwen3)
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Try direct JSON parse first (clean response)
    try:
        items = json.loads(cleaned)
        if isinstance(items, list):
            pass  # use items directly below
        else:
            raise ValueError("Top-level JSON is not an array")
    except (json.JSONDecodeError, ValueError):
        # Fall back to regex extraction
        m = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if not m:
            raise ValueError(
                f"No JSON array found in agent response.\nRaw response (first 500 chars):\n{raw[:500]}"
            )
        try:
            items = json.loads(m.group())
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON parse error: {e}\nContent: {m.group()[:300]}")

    topics = []
    for item in items:
        if not isinstance(item, dict):
            continue
        topics.append(TrendTopic(
            topic=str(item.get("topic", "Unknown topic")),
            summary=str(item.get("summary", "")),
            trend_score=int(item.get("trend_score", 5)),
            keywords=list(item.get("keywords", [])),
            angle=str(item.get("angle", "")),
        ))

    if not topics:
        raise ValueError("Parsed result is empty; agent may not have returned the expected format")

    # Sort by trend score descending
    topics.sort(key=lambda t: t.trend_score, reverse=True)
    return topics


# ─────────────────────────────────────────────
#  Main entry point
# ─────────────────────────────────────────────

def get_trending_topics(top_n: int = 3) -> list[TrendTopic]:
    """
    Call the OpenClaw trend-scout agent and return the current top AI/tech topics.

    Flow:
    1. POST /v1/chat/completions → OpenClaw Gateway
    2. trend-scout agent runs web_search queries
    3. Agent synthesises results and outputs structured JSON
    4. Parse and return TrendTopic list

    Args:
        top_n: Number of topics to return (default 3)
    Returns:
        List[TrendTopic] sorted by trend score descending
    """
    print(f"\n{'='*50}")
    print(f"  Trend Scout: calling OpenClaw agent for trending topics")
    print(f"  Gateway: {OPENCLAW_GATEWAY}")
    print(f"{'='*50}")

    raw = _call_openclaw_agent(SCOUT_PROMPT, agent_id="trend-scout")
    topics = _parse_topics(raw)

    print(f"\n  Found {len(topics)} trending topics:")
    for i, t in enumerate(topics[:top_n], 1):
        print(f"\n  {i}. {t}")

    return topics[:top_n]


def get_trending_topics_fallback(top_n: int = 3) -> list[TrendTopic]:
    """
    Fallback: when OpenClaw is unavailable, search trends directly via SerpAPI.
    Bypasses the OpenClaw agent as a degraded fallback.
    """
    import serpapi  # type: ignore

    SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
    if not SERPAPI_KEY:
        raise ValueError("SERPAPI_KEY is not set")

    print("  [TrendScout] Switching to direct SerpAPI search...")
    client = serpapi.Client(api_key=SERPAPI_KEY)

    now = datetime.datetime.now()
    queries = [
        "AI artificial intelligence latest breakthroughs trending this week " + now.strftime("%B %Y"),
        "large language model LLM agent news today " + now.strftime("%Y-%m-%d"),
        "artificial intelligence major announcements " + now.strftime("%Y"),
    ]

    all_snippets = []
    for q in queries:
        try:
            raw = client.search({"engine": "google", "q": q, "hl": "en", "gl": "us", "num": 5})
            for r in raw.get("organic_results", [])[:3]:
                all_snippets.append(f"- {r.get('title', '')}: {r.get('snippet', '')}")
        except Exception:
            pass
        time.sleep(0.5)

    # distil directly via Ollama (bypassing OpenClaw)
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434/v1")
    # fallback mode uses lightweight model
    MODEL = os.getenv("FALLBACK_MODEL", "qwen3:0.6b")

    snippets_text = "\n".join(all_snippets[:20])
    today_str = now.strftime("%Y-%m-%d")
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": (
                f"Today's date is {today_str}. "
                "From the search results below, extract the 3 hottest AI/tech topics trending TODAY. "
                "Prefer the most recent articles (closest to today's date). "
                "Output strictly as a JSON array (no other text):\n"
                'format: [{"topic":"...", "summary":"...", "trend_score":9, "keywords":["..."], "angle":"..."}]\n\n'
                + snippets_text
            ),
        }],
        "temperature": 0.3,
        "max_tokens": 2048,
    }
    resp = requests.post(f"{OLLAMA_BASE_URL}/chat/completions", json=payload, timeout=600)
    resp.raise_for_status()
    raw_json = resp.json()["choices"][0]["message"]["content"].strip()
    return _parse_topics(raw_json)[:top_n]


def scout_with_fallback(top_n: int = 3) -> list[TrendTopic]:
    """
    Primary entry point: uses SerpAPI directly for trend discovery.
    OpenClaw agent path is available via get_trending_topics() if needed.
    """
    return get_trending_topics_fallback(top_n)


# ─── Standalone test ────────────────────────────────
if __name__ == "__main__":
    topics = scout_with_fallback(top_n=3)
    print("\n" + "="*50)
    print("Final trending topic list:")
    for i, t in enumerate(topics, 1):
        print(f"\n{i}. {t}")
