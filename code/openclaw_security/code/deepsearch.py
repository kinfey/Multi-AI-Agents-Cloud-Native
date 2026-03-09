"""
deepsearch.py  —  DeepSearch module.
Uses SerpAPI Google Search + page scraping + LLM summarisation
to build a knowledge background for a podcast topic.

Dependencies: pip install serpapi requests beautifulsoup4
SerpAPI Key: https://serpapi.com/manage-api-key
"""

import os
import re
import time
import datetime
import urllib.parse
import urllib.request
import json
import html

try:
    import serpapi          # pip install serpapi  (new official package)
    HAS_SERPAPI = True
except ImportError:
    HAS_SERPAPI = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# SerpAPI Key read from environment variable
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")


# ─────────────────────────────────────────────
#  1. Search layer  —  SerpAPI Google Search
# ─────────────────────────────────────────────

def search_serpapi(query: str, max_results: int = 8) -> list[dict]:
    """
    Use the serpapi official package to call the Google Search API.
    Returns a normalised [{"title", "url", "snippet"}, ...] list.

    Docs: https://serpapi.com/search-api
    Package: pip install serpapi
    """
    if not HAS_SERPAPI:
        raise ImportError(
            "serpapi package not installed. Run: pip install serpapi\n"
            "or add serpapi to the dependencies in Dockerfile.app"
        )
    if not SERPAPI_KEY:
        raise ValueError(
            "SERPAPI_KEY environment variable is not set.\n"
            "Add it to your .env file: SERPAPI_KEY=your_api_key_here\n"
            "Get a key at: https://serpapi.com/manage-api-key"
        )

    client = serpapi.Client(api_key=SERPAPI_KEY)
    raw = client.search({
        "engine":  "google",
        "q":       query,
        "hl":      "zh-cn",        # interface language: Chinese
        "gl":      "cn",           # region: China (change to 'us' for English results)
        "num":     max_results,    # results per page (max 100)
        "safe":    "off",
    })

    results = []
    for item in raw.get("organic_results", []):
        results.append({
            "title":   item.get("title", ""),
            "url":     item.get("link", ""),
            "snippet": item.get("snippet", ""),
        })
    return results[:max_results]


def search_serpapi_http(query: str, max_results: int = 8) -> list[dict]:
    """
    Pure HTTP fallback: calls the SerpAPI REST endpoint directly (no serpapi package needed).
    Use this when the serpapi package cannot be installed.
    """
    if not SERPAPI_KEY:
        raise ValueError("SERPAPI_KEY environment variable is not set")

    import requests  # type: ignore
    params = {
        "engine":  "google",
        "q":       query,
        "hl":      "zh-cn",
        "gl":      "cn",
        "num":     max_results,
        "api_key": SERPAPI_KEY,
    }
    resp = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    results = []
    for item in data.get("organic_results", []):
        results.append({
            "title":   item.get("title", ""),
            "url":     item.get("link", ""),
            "snippet": item.get("snippet", ""),
        })
    return results[:max_results]


def web_search(query: str, max_results: int = 8) -> list[dict]:
    """
    Unified search entry point:
      1. Prefer the serpapi official package
      2. Fall back to plain HTTP REST call
    Both methods require a valid SERPAPI_KEY.
    """
    print(f"  [DeepSearch] Google search: {query!r}")
    try:
        if HAS_SERPAPI:
            results = search_serpapi(query, max_results)
        else:
            print("  [DeepSearch] serpapi package not installed, using HTTP fallback")
            results = search_serpapi_http(query, max_results)
        print(f"  [DeepSearch] Got {len(results)} results")
        return results
    except ValueError as e:
        raise
    except Exception as e:
        print(f"  [DeepSearch] serpapi call failed: {e}, trying HTTP fallback")
        try:
            return search_serpapi_http(query, max_results)
        except Exception as e2:
            print(f"  [DeepSearch] HTTP fallback also failed: {e2}")
            return []


# ─────────────────────────────────────────────
#  2. Webpage body scraping
# ─────────────────────────────────────────────

def fetch_page_text(url: str, max_chars: int = 3000) -> str:
    """Fetch page body text (prefers BeautifulSoup, falls back to regex)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PodcastBot/1.0)",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""

    if HAS_BS4:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
    else:
        text = re.sub(r"<[^>]+>", " ", raw)
        text = html.unescape(text)
        text = re.sub(r"\s{3,}", "\n", text).strip()

    return text[:max_chars]


# ─────────────────────────────────────────────
#  3. Multi-query expansion (DeepSearch core)
# ─────────────────────────────────────────────

SEARCH_ANGLE_PROMPT = """\
You are a search strategy expert. Given a podcast topic, generate 4 complementary search queries (mix of Chinese and English)
covering: technical principles, latest developments, industry applications, controversies/challenges.
Output only a JSON array in the format: ["query1","query2","query3","query4"]
Do not include any other text.
"""


def expand_queries(topic: str, ollama_base_url: str = "", model: str = "") -> list[str]:
    """Build complementary search queries for the topic (no LLM call needed)."""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    return [
        f"{topic} latest news {today}",
        f"{topic} technical principles how it works",
        f"{topic} industry applications use cases",
        f"{topic} challenges future trends",
    ]


# ─────────────────────────────────────────────
#  4. Knowledge summary (for podcast script)
# ─────────────────────────────────────────────

SUMMARY_SYSTEM = """\
You are a research assistant who organises source material for podcast episodes.
Based on the provided search summaries and webpage content, distil the core knowledge points needed for the podcast guest's discussion.
Requirements:
- Divide into 5–8 key knowledge points
- Each knowledge point includes: a title + 2–3 explanatory sentences
- Language: Chinese, concise and professional, suitable for spoken discussion
- Include specific data, case studies, or timelines where available
"""


def build_knowledge_base(
    topic: str,
    ollama_base_url: str,
    model: str,
    max_sources: int = 6,
) -> str:
    """
    Full DeepSearch pipeline:
    1. LLM expands search angles
    2. SerpAPI Google Search across multiple queries
    3. Fetch top N page bodies
    4. LLM distils into structured knowledge points
    """
    import requests  # type: ignore

    print(f"\n{'='*50}")
    print(f"  Deep Search: topic = {topic!r}")
    print(f"  Search engine: SerpAPI Google Search")
    print(f"{'='*50}")

    queries = expand_queries(topic, ollama_base_url, model)
    print(f"  Expanded queries: {queries}")

    all_results: list[dict] = []
    seen_urls: set[str] = set()
    for q in queries:
        try:
            for r in web_search(q, max_results=5):
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    all_results.append(r)
        except ValueError as e:
            print(f"  [DeepSearch] ⚠ skipping search: {e}")
            break
        time.sleep(0.5)

    print(f"  Total unique results collected: {len(all_results)}")

    enriched: list[str] = []
    for i, r in enumerate(all_results[:max_sources]):
        enriched.append(f"[Source {i+1}] {r['title']}\nURL: {r['url']}")
        enriched.append(f"Snippet: {r['snippet']}")
        full_text = fetch_page_text(r["url"], max_chars=2000)
        if full_text:
            enriched.append(f"Body excerpt:\n{full_text[:800]}")
        enriched.append("")
        time.sleep(0.3)

    raw_content = "\n".join(enriched)
    print(f"  Raw content length: {len(raw_content)} chars")

    print("  Distilling knowledge points...")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": (
                f"Podcast topic: {topic}\n\n"
                f"Source material collected:\n\n{raw_content[:8000]}"
            )},
        ],
        "temperature": 0.4,
        "max_tokens": 2000,
    }
    try:
        resp = requests.post(
            f"{ollama_base_url}/chat/completions",
            json=payload, timeout=600
        )
        resp.raise_for_status()
        knowledge = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  [DeepSearch] Knowledge distillation failed: {e}")
        knowledge = f"Topic: {topic}\n\nSearch snippets:\n" + "\n".join(
            f"- {r['title']}: {r['snippet']}" for r in all_results[:8]
        )

    print(f"  Knowledge base built ({len(knowledge)} chars)")
    return knowledge


# ─── Standalone test ─────────────────────────────
if __name__ == "__main__":
    import sys
    if not SERPAPI_KEY:
        print("Please set the environment variable first: export SERPAPI_KEY=your_key_here")
        sys.exit(1)
    topic = sys.argv[1] if len(sys.argv) > 1 else "AI Agent technology development"
    kb = build_knowledge_base(
        topic,
        ollama_base_url="http://localhost:11434/v1",
        model="qwen2.5:3b",
    )
    print("\n" + "="*50)
    print("Knowledge base contents:")
    print(kb)
