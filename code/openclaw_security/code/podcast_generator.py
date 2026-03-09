#!/usr/bin/env python3
"""
podcast_generator.py  —  Podcast dialogue generation library.
Called by auto_run.py; accepts a TrendTopic object and writes a TXT file.
No interactive CLI — all parameters are passed by auto_run.py.
"""

import os
import re
import time
import datetime
import requests
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trend_scout import TrendTopic

# ─────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────

OLLAMA_BASE_URL  = os.getenv("OLLAMA_BASE_URL",  "http://ollama:11434/v1")
OPENCLAW_GATEWAY = os.getenv("OPENCLAW_GATEWAY", "http://openclaw:18789")
OUTPUT_DIR       = Path(os.getenv("PODCAST_OUTPUT_DIR", "./output"))
MODEL            = os.getenv("PODCAST_MODEL",    "qwen3:0.6b")

def _load_token() -> str:
    """
    Load the Gateway Token from a file or environment variable.
    Prefers OPENCLAW_TOKEN_FILE (rotated by secrets-init on each startup).
    Corresponds to the ryoooo/microvm-openclaw.nix virtiofs secrets pattern.
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

PODCAST_NAME = "AI Tech Chat"
HOST_NAME    = "Lucy"
GUEST_NAME   = "Ken"
MIN_ROUNDS   = 20

# ─────────────────────────────────────────────
#  Prompt templates
# ─────────────────────────────────────────────

PODCAST_SYSTEM = f"""\
You are a professional podcast scriptwriter. Write a word-for-word script for the Chinese tech podcast {PODCAST_NAME}.

**IMPORTANT: The entire script MUST be written in Simplified Chinese (简体中文). Every line of dialogue must be in Simplified Chinese.**

Show roles:
- {HOST_NAME} (host): sharp thinker, great at follow-up questions, concise language, occasionally lightens the mood with humour
- {GUEST_NAME} (guest): deep technical background, strong opinions, skilled at using analogies to explain complex concepts

Writing requirements:
1. Natural, flowing dialogue in the style of a spoken Chinese tech podcast
2. {HOST_NAME}'s questions build progressively, from accessible to in-depth
3. {GUEST_NAME}'s answers must be substantive — cite data, case studies, and comparisons
4. Weave in clashing viewpoints and reasonable disagreements
5. Keep each exchange to 3–6 sentences
6. Strictly follow the format: each line starts with "{HOST_NAME}:" or "{GUEST_NAME}:"

Output format (strictly follow):
{HOST_NAME}: [对话内容（简体中文）]
{GUEST_NAME}: [对话内容（简体中文）]
... (at least {MIN_ROUNDS} complete exchanges)
"""

DIALOGUE_PROMPT = """\
Based on the knowledge below, generate a complete podcast dialogue script about "{topic}".

**Output language: Simplified Chinese (简体中文) only. Every single line must be in Simplified Chinese.**

[OpenClaw Trend Scout Results]
Trend score: {trend_score}/10
Trend reason: {summary}
Keywords: {keywords}
Podcast angle: {angle}

[Deep Search Knowledge Background]
{knowledge}
[Knowledge Background End]

Requirements:
- Open with {host} introducing the show {podcast_name} and today's trending topic
- At least {min_rounds} complete Q&A exchanges
- Cover: topic background → technical breakdown → real-world impact → industry outlook → controversies & challenges
- Close with {host} thanking the guest and teasing the next episode
- Output dialogue directly, no preamble
"""

# ─────────────────────────────────────────────
#  LLM call
# ─────────────────────────────────────────────

def call_llm(messages: list[dict], temperature: float = 0.85, max_tokens: int = 6000) -> str:
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": 0.9,
    }
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/chat/completions",
        json=payload, timeout=600
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ─────────────────────────────────────────────
#  Dialogue parsing & quality check
# ─────────────────────────────────────────────

def parse_dialogue(raw: str) -> list[tuple[str, str]]:
    lines = raw.strip().splitlines()
    turns = []
    current_speaker = None
    current_lines: list[str] = []
    patterns = [
        (HOST_NAME,  re.compile(rf"^{re.escape(HOST_NAME)}[：:]\s*")),
        (GUEST_NAME, re.compile(rf"^{re.escape(GUEST_NAME)}[：:]\s*")),
    ]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        matched = False
        for speaker, pat in patterns:
            if pat.match(line):
                if current_speaker and current_lines:
                    turns.append((current_speaker, " ".join(current_lines)))
                current_speaker = speaker
                current_lines = [pat.sub("", line)]
                matched = True
                break
        if not matched and current_speaker:
            current_lines.append(line)
    if current_speaker and current_lines:
        turns.append((current_speaker, " ".join(current_lines)))
    return turns


def count_rounds(turns: list[tuple[str, str]]) -> int:
    return min(
        sum(1 for s, _ in turns if s == HOST_NAME),
        sum(1 for s, _ in turns if s == GUEST_NAME),
    )


def extend_dialogue(turns, knowledge, topic, needed):
    print(f"  ⚠  {count_rounds(turns)} rounds, extending by ~{needed} more...")
    existing = "\n".join(f"{s}：{t}" for s, t in turns[-10:])
    messages = [
        {"role": "system", "content": PODCAST_SYSTEM},
        {"role": "user", "content": (
            f"Here are the last exchanges from the '{topic}' podcast so far:\n\n{existing}\n\n"
            f"Continue with {needed} more exchanges. Keep the same style. Do not write a closing line — just continue immediately:"
        )},
    ]
    raw = call_llm(messages, temperature=0.88)
    return turns + parse_dialogue(raw)


# ─────────────────────────────────────────────
#  Formatted output
# ─────────────────────────────────────────────

def format_podcast_txt(
    trend: "TrendTopic",
    turns: list[tuple[str, str]],
    knowledge: str,
    date_str: str,
) -> str:
    div = "─" * 60
    header = (
        f"{div}\n{PODCAST_NAME}\n{div}\n"
        f"Topic: {trend.topic}\n"
        f"Recording date: {date_str}\n"
        f"Host: {HOST_NAME}  |  Guest: {GUEST_NAME}\n"
        f"Total rounds: {count_rounds(turns)}  |  Total lines: {len(turns)}\n"
        f"\n[OpenClaw Trend Source]\n"
        f"  Trend score: {trend.trend_score}/10\n"
        f"  Trend reason: {trend.summary}\n"
        f"  Keywords: {', '.join(trend.keywords)}\n"
        f"{div}\n\n"
    )
    body_lines = []
    for i, (speaker, text) in enumerate(turns):
        if i > 0 and i % 10 == 0:
            body_lines.append(f"\n{'·' * 40}\n")
        body_lines.append(f"{speaker}：{text}\n")

    footer = (
        f"\n{div}\n"
        f"[Deep Search Summary]\n"
        f"{div}\n{knowledge}\n{div}\n"
        f"This script was auto-generated by {PODCAST_NAME} AI\n"
        f"Pipeline: trend scout -> deep search -> LLM dialogue\n"
        f"{div}\n"
    )
    return header + "\n".join(body_lines) + footer


# ─────────────────────────────────────────────
#  Service health check
# ─────────────────────────────────────────────

def check_ollama_health() -> bool:
    try:
        r = requests.get(OLLAMA_BASE_URL.replace("/v1", "/api/tags"), timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def check_openclaw_health() -> bool:
    try:
        r = requests.get(f"{OPENCLAW_GATEWAY}/healthz", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def wait_for_services(max_wait: int = 120) -> None:
    print("⏳ Waiting for services to be ready...")
    start = time.time()
    while time.time() - start < max_wait:
        ollama_ok    = check_ollama_health()
        openclaw_ok  = check_openclaw_health()
        ok = "\u2713"
        ng = "\u2717"
        print(
            f"\r  Ollama: {ok if ollama_ok else ng}  "
            f"OpenClaw: {ok if openclaw_ok else ng}",
            end="", flush=True
        )
        if ollama_ok:
            print(f"\n✅ Services ready (Ollama is up)")
            return
        time.sleep(3)
    raise RuntimeError("Services failed to start within timeout; check docker compose logs")


# ─────────────────────────────────────────────
#  Core generation function (called by auto_run.py)
# ─────────────────────────────────────────────

def generate_podcast_from_topic(
    trend: "TrendTopic",
    skip_search: bool = False,
) -> Path:
    """
    Accepts a TrendTopic, generates a complete podcast script, saves as TXT.

    Args:
        trend: Trending topic from OpenClaw TrendScout
        skip_search: Skip SerpAPI DeepSearch
    Returns:
        Output file path
    """
    from deepsearch import build_knowledge_base

    print(f"\n{'─'*55}")
    print(f"  🎙  Generating: {trend.topic}")
    print(f"  Model: {MODEL}  |  Skip search: {'Yes' if skip_search else 'No'}")

    # Step 1: DeepSearch
    if skip_search:
        print("  ⏭  Skipping deep search (--no-search mode)")
        knowledge = (
            f"Topic: {trend.topic}\n"
            f"Trend reason: {trend.summary}\n"
            f"Keywords: {', '.join(trend.keywords)}\n"
            f"(offline mode, SerpAPI search skipped)"
        )
    else:
        print("\n  🔍 Deep search: collecting resources via SerpAPI Google...")
        knowledge = build_knowledge_base(
            topic=trend.topic,
            ollama_base_url=OLLAMA_BASE_URL,
            model=MODEL,
        )
        print("  ✅ DeepSearch complete")

    # Step 2: Generate dialogue
    print("\n  💬 Calling LLM to generate dialogue script (approx. 1-3 min)...")
    user_prompt = DIALOGUE_PROMPT.format(
        topic=trend.topic,
        trend_score=trend.trend_score,
        summary=trend.summary,
        keywords=", ".join(trend.keywords),
        angle=trend.angle,
        knowledge=knowledge,
        host=HOST_NAME,
        podcast_name=PODCAST_NAME,
        min_rounds=MIN_ROUNDS,
    )
    messages = [
        {"role": "system", "content": PODCAST_SYSTEM},
        {"role": "user",   "content": user_prompt},
    ]
    raw = call_llm(messages, temperature=0.85, max_tokens=6000)
    turns = parse_dialogue(raw)
    print(f"  Initial generation: {count_rounds(turns)} rounds / {len(turns)} lines")

    # Step 3: Quality assurance
    attempts = 0
    while count_rounds(turns) < MIN_ROUNDS and attempts < 3:
        turns = extend_dialogue(turns, knowledge, trend.topic, MIN_ROUNDS - count_rounds(turns) + 3)
        attempts += 1

    final_rounds = count_rounds(turns)
    print(f"  ✅ Final: {final_rounds} rounds of dialogue")

    # Step 4: Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_topic = re.sub(r'[\\/:*?"<>|]', "_", trend.topic)[:40]
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"podcast_{safe_topic}_{ts}.txt"

    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    content = format_podcast_txt(trend, turns, knowledge, date_str)
    out_path.write_text(content, encoding="utf-8")

    print(f"  📄 Saved: {out_path}  ({out_path.stat().st_size/1024:.1f} KB)")
    return out_path
