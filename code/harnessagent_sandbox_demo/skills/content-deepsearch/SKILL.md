---
name: content-deepsearch
description: Take the SearchAgent's top-5 BBC World Cup stories and produce a podcast-ready research brief — outline plus DeepSearch findings — by re-fetching each story's BBC URL and extracting facts/quotes. Load the hyperlight-sandbox skill first for sandbox/fetch_url rules.
license: MIT
allowed-tools: execute_code
metadata:
  author: harnessagent_sandbox_demo
  version: "1.0"
---

## Goal

You receive a JSON list of the top 5 FIFA World Cup 2026 stories from
the SearchAgent. Your job is to **deepen** those stories — not to
re-discover them. Produce a podcast-ready research brief.

You do NOT have a news-listing tool here — never invent URLs, only
deepen the stories that the SearchAgent already produced. All fetches
must target a `source_url` from the input JSON.

## Step 1 — Outline (no new sources)

Read the SearchAgent JSON. Draft a 5-section outline for a ~10-minute
podcast episode of *FIFA 2026 世界杯 5 分钟* hosted by 主持人 Kinfey Lo.
Each section maps to one of the 5 stories, in the ranked order
received. Per section:

- Hook (1 sentence)
- 2–3 key facts you still need to verify or expand
- Suggested transition into the next section

## Step 2 — DeepSearch (REQUIRED — one fetch per `execute_code` block)

For every story, run a SEPARATE `execute_code` block (5 blocks, one
per story). Inside each block:

```python
result = call_tool("fetch_url", url="<story.source_url>")
print(result[:4000])  # title/description always at the top; trim body
```

Read the printed record and extract concrete facts/quotes you'll use.
Budget 1 fetch per story; do not fan out to other domains.

## Step 3 — Return brief in this Markdown shape (and only this)

```
# 节目大纲 / Show Outline
## Section 1 — <title>
- Hook: ...
- Key facts: ...
- Transition: ...
## Section 2 — ...
...

# DeepSearch Findings
## Section 1 — <title>
**Source:** <name> — <url>
**Facts:**
- ...
- ...
**Quotes (verbatim, if any):**
- "..."
## Section 2 — ...
...
```

Keep the brief tight — every bullet should be something the host can
actually say on air. Always include the source URL you actually
fetched.
