---
name: genscript-podcast
description: Turn the research brief into the on-air script for 《FIFA 2026 世界杯 5 分钟》, hosted by Kinfey Lo. Produce a zh-CN (mainland Mandarin commentary) and a zh-TW (HK Cantonese broadcast) version inside two fenced Markdown blocks, with a mandatory in-sandbox length check. Load the hyperlight-sandbox skill first for execute_code rules.
license: MIT
allowed-tools: execute_code
metadata:
  author: harnessagent_sandbox_demo
  version: "1.0"
---

## Goal

You receive an outline + DeepSearch brief. Write the on-air script
for a single-host podcast called **《FIFA 2026 世界杯 5 分钟》**.
Host: **Kinfey Lo** (只有一个主持人).

Do **not** ask the user to paste the brief again — the brief is
already in the message you received. If a section is thin, work
with what you have.

## Style guide

- Conversational, energetic, fan-friendly. The host is *not* a
  stiff news anchor — think of an enthusiastic football podcaster.
- Total length ≈ 10 minutes of speech (≈ 1500–1800 Chinese
  characters per language version).
- **zh-CN** version: use an intense, rhythm-driven mainland
  football-commentary style inspired by Zhan Jun's match
  narration approach (high information density, crisp tempo,
  sharp transitions).
- **zh-TW** version: use a classic TVB-style Cantonese Chinese
  football-news narration flavor inspired by Ng Fong-wing's
  broadcast approach (穩定節奏、港式體育播報語感、情境感強). Must
  read as Hong Kong Cantonese written broadcast language, not
  Mandarin rewritten into traditional characters. Prefer Hong
  Kong expressions such as: 我哋、你哋、今集、呢場、呢個、入波、踢法、
  走勢、跟住、收工前、臨尾、開波、完場、再見.
- Sound like live radio, not a written report: short punchy
  clauses, spoken connectors, audible transitions.
- Avoid rigid template wording and avoid repetitive source
  naming.
- Structure:
  1. 开场白 (cold open + 30–45 s self-introduction by Kinfey Lo)
  2. 5 sections, one per story, in ranked order
  3. 结尾 (call to action: follow / subscribe, tease tomorrow's
     episode)
- Cite sources naturally in speech (e.g. "根据 FIFA 官网的报道……").
- Do NOT invent facts — only use what's in the brief.

## Output format — EXACT two-block Markdown, nothing else

````
```zh-CN
host : ...

host : ...
```

```zh-TW
host : ...

host : ...
```
````

Rules:

- Every non-empty line must start with `host : ` exactly (lowercase
  `host`, space-colon-space).
- Keep blank lines between adjacent `host : ...` lines.
- Target 12–20 `host : ...` lines per language.
- Include a clear ending that says goodbye to listeners and
  explicitly asks them to follow/subscribe to **《FIFA 2026 世界杯
  5 分钟》** (zh-TW: «FIFA 2026 世界盃 5 分鐘»). For zh-TW closing
  CTA, use Hong Kong Cantonese phrasing.
- Do not output anything outside the two fenced blocks.

## Tools — sandbox only

You only see one tool: `execute_code`. There are NO `call_tool`
helpers in this agent — no host bridges. All checks run in the
guest.

## MANDATORY length verification

Before you emit the two fenced blocks, you MUST run `execute_code`
at least once to verify each script's Chinese-character count is
in the **1500–1800** range. Example:

```python
script_zh_cn = '''host : ...\n\nhost : ...'''
script_zh_tw = '''host : ...\n\nhost : ...'''
cn = sum(1 for c in script_zh_cn if '\u4e00' <= c <= '\u9fff')
tw = sum(1 for c in script_zh_tw if '\u4e00' <= c <= '\u9fff')
print("cn_hanzi=", cn, "tw_hanzi=", tw)
assert 1500 <= cn <= 1900, cn
assert 1500 <= tw <= 1900, tw
```

If either count is out of range, REWRITE the offending script and
run `execute_code` again until both pass. Only then emit the two
fenced blocks as your final reply.

Do NOT try to write files — a downstream host step persists both
scripts automatically. Your only deliverable is the two fenced
Markdown blocks in the response text.
