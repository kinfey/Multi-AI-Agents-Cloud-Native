---
name: search-bbc-worldcup
description: Find the 5 most important FIFA World Cup 2026 stories of the day from BBC Sport's World Cup listing page and return them as JSON with verified source URLs. Load the hyperlight-sandbox skill first for sandbox/fetch_url rules.
license: MIT
allowed-tools: execute_code
metadata:
  author: harnessagent_sandbox_demo
  version: "1.0"
---

## Goal

Surface the 5 most important FIFA World Cup 2026 stories of the day
and return them as a single JSON block.

## Source policy — STRICT

The single source of truth is
`https://www.bbc.com/sport/football/world-cup`. Do not fetch any
other site. (The sandbox allow-list is BBC-only anyway.)

## Required workflow

Run all steps via `execute_code` per the `hyperlight-sandbox` skill.

### 1. Fetch the listing page

```python
page = call_tool("fetch_url", url="https://www.bbc.com/sport/football/world-cup")
print(page[:6000])
```

### 2. Extract candidate URLs from the `LINKS:` section

Every URL you ever output MUST come from this list verbatim. **You
are forbidden from constructing a BBC URL from a headline, slug
guess, or memory.** BBC slugs are opaque hashes (e.g.
`c1234abcdef`), not human-readable phrases. If a URL is not in the
`LINKS:` list, it does not exist as far as you are concerned.

```python
import re
m = re.search(r"^LINKS:\n((?:  - .+\n?)+)", page, flags=re.MULTILINE)
urls = []
if m:
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("- "):
            u = line[2:].strip()
            if u and u not in urls:
                urls.append(u)
print(len(urls), urls[:15])
```

If `urls` is empty, refetch the listing page once and retry. Do
not proceed to step 3 until `urls` has at least one entry.

### 3. Verify each URL

Walk `urls` in order. For each one, in a SEPARATE `execute_code`
block, run `call_tool("fetch_url", url=u)` and inspect the first
line.

- If the header starts with `STATUS: 200`, keep it as VERIFIED.
  Read the story's title from the `TITLE:` header line and its
  summary from the `DESCRIPTION:` header line of THAT fetch.
- If it returns 4xx / 5xx / `ERROR`, skip it. Do not try to fix
  the URL — the URL is whatever the listing page said it was.

Stop once you have 5 VERIFIED URLs, or once you have tried at
least 15 candidates.

### 4. Fallback rules

If after step 3 you still have fewer than 5 verified stories:

- You MAY fill the remaining slots from `urls` using the listing
  page's nearby anchor text + `<p>` snippet for title / summary.
  These slots do not need a 200 verification.
- The `source_url` in those fallback slots MUST still be a literal
  entry from `urls`. NEVER fabricate a URL.
- If `urls` itself has fewer than 5 entries, output as many
  stories as you have URLs for (could be 1–4). Do not pad with
  invented URLs. Returning 3 real stories is correct; returning 5
  with 2 hallucinated URLs is wrong.

## Hard rules on `source_url`

- Each `source_url` MUST be a verbatim entry from the `LINKS:` list
  of the step-1 listing fetch. NEVER construct a URL from a title
  or slug guess.
- `source_url` values MUST be unique across stories.
- None may equal `https://www.bbc.com/sport/football/world-cup`
  itself.
- Prefer URLs that returned `STATUS: 200` in step 3.

## Output format — return THIS exact JSON shape, nothing else

````
```json
{
  "date": "YYYY-MM-DD",
  "stories": [
    {
      "rank": 1,
      "title": "...",
      "category": "match | friendly | team_news | colour | fifa_org",
      "summary": "1–2 sentences in English",
      "source_name": "BBC Sport",
      "source_url": "https://www.bbc.com/sport/football/..."
    }
  ]
}
```
````

Wrap the JSON in a fenced ```json block. Do not add prose around it.
