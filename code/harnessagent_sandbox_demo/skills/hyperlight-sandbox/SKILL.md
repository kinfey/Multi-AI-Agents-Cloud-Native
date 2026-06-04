---
name: hyperlight-sandbox
description: Rules for running Python inside the Hyperlight Wasm guest via execute_code, including the call_tool("fetch_url", ...) host bridge, output-buffer limits, and forbidden imports. Load this whenever you plan to run code in the sandbox.
license: MIT
allowed-tools: execute_code
metadata:
  author: harnessagent_sandbox_demo
  version: "1.0"
---

## Sandbox model

You run Python through a single tool: `execute_code`. The interpreter
lives inside a Hyperlight Wasm guest. Each call gets a fresh snapshot,
so state never leaks between blocks.

## The single host bridge — `call_tool("fetch_url", ...)`

The guest cannot reach the network on its own (`urllib` stalls). HTTP
GETs go through one host bridge, callable from inside `execute_code`:

```python
result = call_tool("fetch_url", url="https://www.bbc.com/...")
print(result[:6000])
```

The host returns a compact (≤ ~8 KB) text record:

```
STATUS: <code>
URL: <fetched url>
TITLE: <og:title>
DESCRIPTION: <og:description>
LINKS:
  - <absolute bbc article/video URL>
  - ...
BODY:
<plain text, all tags stripped>
```

URLs only appear under `LINKS:`. The host pre-extracts BBC sport
article/video links from the raw HTML before stripping tags.

The allow-list is **BBC-only**: `www.bbc.com`, `bbc.com`. Any other
host returns an `ERROR` line — do not try other domains.

## Hard guardrails

- **One** `call_tool("fetch_url", ...)` per `execute_code` block.
  Never concatenate two page bodies in the same block — the guest
  output buffer is only ~16 KB and over-running it aborts the guest.
- Print at most ~8 KB per block. When you only need metadata, slice
  the record (`print(result[:4000])`).
- Do **not** `import html`. It stalls the guest.
- Do **not** call `urllib.request.urlopen` from inside the guest.
  The only network path is `call_tool("fetch_url", ...)`.
- Use plain string ops and stdlib `re` for parsing.

## Execution mode

You are a single-shot worker in an automated pipeline. Run
autonomously — **do not** ask for approval, **do not** propose a
plan and wait, **do not** request "execute mode". Take action
immediately by running `execute_code`, then return your final
answer in the same turn.
