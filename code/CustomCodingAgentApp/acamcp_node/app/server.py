"""FastMCP server exposing the OpenClaw programming workflow as MCP tools.

The server speaks the MCP streamable HTTP transport and mounts its endpoint at
``/mcp`` (see the Azure Container Apps standalone MCP hosting model:
https://learn.microsoft.com/azure/container-apps/mcp-overview).

It drives the OpenClaw multi-agent workflow by calling the gateway's
OpenAI-compatible ``/v1/chat/completions`` endpoint once per agent, in order,
passing each approved artifact forward as context.

Every agent turn is announced (what it is doing) and reports its token
consumption, both as MCP progress logs and in the returned summary. When the
save-agent emits a download URL and no explicit destination is given, the
archive is downloaded to the local save directory (``~/Downloads`` by default).
"""

import asyncio
import os
import re
import time
import uuid
import zipfile
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

from .config import Settings
from .gateway_client import AgentTurn, GatewayClientError, OpenClawGatewayClient

_AGENT_INSTRUCTIONS: dict[str, str] = {
    "requirements-agent": (
        "Act as the Requirement Agent. Turn the requirement into clear, testable "
        "acceptance criteria for a project prototype."
    ),
    "coding-agent": (
        "Act as the Coding Agent. You share ONE workspace with the testing, "
        "deployment, and save agents, so put ALL project files inside a single "
        "subdirectory named `app/` at the workspace root (backend + frontend code and "
        "a production Dockerfile with an `EXPOSE` line). This is a FRESH build and your "
        "sandbox workspace may still hold files from a PREVIOUS run that must NOT leak "
        "into this one. On your FIRST turn, before writing anything, wipe the ENTIRE "
        "workspace with exactly this single-line command: "
        "`rm -rf ./* ./.[!.]* 2>/dev/null || true` — then recreate one empty `app/` "
        "directory so ONLY the files you write in this run remain. Write complete, "
        "runnable code — no "
        "placeholders or TODOs — so the app starts and every endpoint/page works, and "
        "make the REST paths match the requirement EXACTLY. Also write a "
        "`.dockerignore` inside `app/` (single-line: "
        "`printf '%s\\n' node_modules .npm .git dist build .next coverage "
        "__pycache__ .venv '*.log' > app/.dockerignore`) so the deployment build "
        "context stays small and the image build does not time out. If you receive "
        "test "
        "failures from the testing-agent, do NOT delete the workspace; edit the "
        "existing files under `app/` to fix the exact reported problems, then list "
        "every file changed. Only read/write inside your workspace; use single-line "
        "shell commands (no multi-line here-docs). At the very END of your reply, "
        "include a markdown section titled exactly `## PROJECT ARCHITECTURE` that "
        "shows the full file tree under `app/` inside a ```text fenced code block, "
        "followed by a short bullet list describing each major component (backend, "
        "frontend, and any key modules)."
    ),
    "testing-agent": (
        "Act as the Testing Agent. The coding-agent's project lives in the `app/` "
        "subdirectory of this SHARED workspace — `cd app` first; the files ARE there. "
        "Run tests in TWO phases, BACKEND FIRST, then FRONTEND. "
        "PHASE 1 (backend, pytest): install the backend deps and `pytest`, write "
        "pytest test cases that exercise every REST endpoint (status codes, JSON "
        "shape, sample data), and run them with `pytest`. "
        "PHASE 2 (frontend, jest): ONLY after the backend phase finishes, install "
        "`jest` and write jest test cases that check the served HTML/JS wires up to "
        "the API (expected DOM elements, fetch calls, no obvious JS errors), and run "
        "them with `jest`. "
        "Collect the REAL pass/fail result of EACH individual test case from the "
        "pytest and jest output — do not invent results. "
        "Sandbox rules: only read/write inside your workspace — never /tmp or other "
        "absolute paths; run single-line commands only, never multi-line here-docs "
        "(<<EOF/<<PY). "
        "Your reply MUST include a markdown section titled exactly `## TEST RESULTS` "
        "containing ONE markdown table with the columns `| Phase | Test Case | "
        "Result |`, where Phase is `backend` or `frontend`, Test Case is the "
        "pytest/jest test name, and Result is `✅ PASS` or `❌ FAIL` (append a short "
        "reason after a failure). List all backend rows first, then all frontend "
        "rows. "
        "End your reply with a verdict on its own line: exactly `TESTS_PASSED` if ALL "
        "backend and frontend test cases pass, or `TESTS_FAILED` followed by a "
        "concise numbered list of each failing case (name, expected vs actual) so "
        "the coding-agent can fix them. Never write `TESTS_PASSED` unless you "
        "actually ran pytest AND jest and every case passed. If `app/` is missing or "
        "empty, report `TESTS_FAILED` with that fact."
    ),
    "deployment-agent": (
        "Act as the Deployment Agent. The testing-agent has ALREADY validated the "
        "app, so do NOT re-run, re-test, or re-validate it: do not start uvicorn, do "
        "not curl endpoints, and do not write validation scripts. Your one job is to "
        "deploy. Run exactly two commands, in order, from your sandbox: first "
        "`deploy-login` (authenticates az as the sandbox managed identity), then "
        "`deploy-app <project-dir> <app-name> <port>` (runs `az acr build` and "
        "creates/updates the Container App). The coding-agent built the project in the "
        "`app/` subdirectory of this SHARED workspace, so use `app` (or the exact "
        "directory that contains the Dockerfile — run `find . -name Dockerfile` to "
        "confirm) as <project-dir>; do NOT assume `.` "
        "(a bare `.` fails with `Unable to find './Dockerfile'` when the app is nested). "
        "Before deploying, make sure the build context is small so `az acr build` does "
        "not time out: if `app/.dockerignore` is missing, create it with this single "
        "line — `printf '%s\\n' node_modules .npm .git dist build .next coverage "
        "__pycache__ .venv '*.log' > app/.dockerignore`. "
        "Use a lowercase, hyphenated app name "
        "derived from the project (<=32 chars total, no leading/trailing hyphen) and "
        "the port the Dockerfile EXPOSEs (default 8000). "
        "Sandbox rules you MUST follow: only read/write inside your assigned workspace "
        "directory — never write to /tmp or any absolute path outside it. Run "
        "single-line shell commands only; never use multi-line here-docs (<<EOF/<<PY) "
        "or embedded newlines, which the exec transport rejects. "
        "Report the succeeded status and the exact HTTPS URL printed as `DEPLOYED_URL=...` "
        "(the app FQDN), plus the image tag and revision. Never predict or fabricate a "
        "hostname; only report the URL emitted by `deploy-app`. If `deploy-app` fails or "
        "prints no `DEPLOYED_URL=`, report `[blocked]` with the exact error instead of "
        "claiming success."
    ),
    "save-agent": (
        "Act as the Save Agent. After successful deployment, package the coding-agent "
        "and testing-agent generated project files by running `save-pack` from inside "
        "your sandbox. It zips the workspaces, uploads the archive to the gateway "
        "artifacts store via the managed identity, and prints a JSON line containing "
        "`downloadUrl`. Report that exact authenticated Download URL as `DOWNLOAD_URL=...` "
        "(an `/api/saveagent/artifacts/<id>.zip` link). Never invent the URL; only report "
        "the one emitted by `save-pack`."
    ),
}

# Short, human-readable description of what each stage is doing, surfaced as a
# progress message before the agent runs.
_AGENT_STAGE_DESC: dict[str, str] = {
    "requirements-agent": "Turning the requirement into testable acceptance criteria",
    "coding-agent": "Writing the project files and a production Dockerfile",
    "testing-agent": "Building a test plan and validating the implementation",
    "deployment-agent": "Deploying the app to Azure Container Apps from the sandbox",
    "save-agent": "Packaging the workspaces and producing an authenticated download URL",
}

# Matches the authenticated artifact link the save-agent reports.
_DOWNLOAD_URL_RE = re.compile(
    r"https?://[^\s\"']+/api/saveagent/artifacts/[a-f0-9]{32}\.zip"
)

# Matches the deployed app FQDN the deployment-agent reports as DEPLOYED_URL=...
_DEPLOYED_URL_RE = re.compile(
    r"DEPLOYED_URL=\s*(https?://[^\s\"'`]+)"
)

# Deterministic pre-run cleanup. Agents share ONE reused ACA sandbox and write
# their project files under /tmp/openclaw-sandboxes/<...agent...>/workspace.
# OpenClaw never syncs those back, and `save-pack` packages the most recently
# modified workspace with files — so stale files/dirs from a PREVIOUS run linger
# in the reused sandbox and can leak into (or be packaged as) the next result.
# Before generating anything we drive the coding-agent (the one with sandbox
# shell access) with this single-purpose prompt to WIPE every lingering agent
# workspace, preserving only the OpenClaw meta files. It prints WORKSPACES_WIPED
# so the orchestrator can confirm the wipe actually ran.
_WIPE_MARKER = "WORKSPACES_WIPED"
_WIPE_PROMPT = (
    "Housekeeping only — do NOT write any project code this turn. Your sandbox is "
    "reused across runs and may still hold files from a PREVIOUS, unrelated task "
    "that must be removed before a fresh build. Run EXACTLY these two single-line "
    "commands (no multi-line here-docs), in order, then report their output:\n"
    "1. `find /tmp/openclaw-sandboxes -mindepth 1 -path '*/workspace/*' -type f "
    "! -name AGENTS.md ! -name SOUL.md ! -name USER.md ! -name TOOLS.md "
    "! -name IDENTITY.md ! -name BOOTSTRAP.md ! -name HEARTBEAT.md "
    "! -name openclaw-workspace-state.json -delete 2>/dev/null; find "
    "/tmp/openclaw-sandboxes -mindepth 1 -path '*/workspace/*' -depth -type d "
    "-empty -delete 2>/dev/null; rm -rf ./* ./.[!.]* 2>/dev/null; echo "
    f"{_WIPE_MARKER}`\n"
    "2. `ls -la` to show the now-empty workspace.\n"
    f"End your reply with the line `{_WIPE_MARKER}` once the deletion command has run."
)

# Review gate: the testing-agent must end with a TESTS_PASSED verdict; otherwise
# the failures are fed back to the coding-agent and the code<->test loop repeats.
_MAX_TEST_ROUNDS = 3
_TESTS_PASSED_RE = re.compile(r"\bTESTS_PASSED\b")

# Review gate: after a DEPLOYED_URL is obtained, the orchestrator health-checks it
# and, if unreachable, feeds the failure back to the deployment-agent to fix.
_MAX_DEPLOY_REVIEW = 2

# ---------------------------------------------------------------------------
# Split, poll-based deploy (works around the ~120s ACA `sandbox exec` cap).
#
# The ACA sandbox `exec` data-plane call is hard-capped at ~120s, but a real
# `az acr build` + `az containerapp create` easily exceeds that and the single
# bundled `deploy-app` wrapper always blew the cap ("Network issue — retry
# policy expired"). Crucially, BOTH `az acr build` and `az containerapp
# create` continue running SERVER-SIDE after the client exec times out. So we
# split deployment into two idempotent, individually-fast helpers and POLL:
#   * deploy-build <ctx> <app>  — pushes a fixed `<app>:latest` image (detects &
#     saves the Dockerfile EXPOSE port); tolerates the ~120s disconnect since
#     ACR finishes the build regardless.
#   * deploy-finish <app>       — idempotent: STILL_BUILDING until the image is
#     in ACR, then kicks a `--no-wait` container-app create and, once the app
#     reports Succeeded, prints the real `DEPLOYED_URL=`.
# The helpers are installed into the reused sandbox on-demand (base64, single
# line) so a freshly recreated sandbox self-heals without an image rebuild.
_DEPLOY_BUILD_B64 = (
    "IyEvYmluL2Jhc2gKc2V0IC1lCi4gL2V0Yy9vcGVuY2xhdy1kZXBsb3kuZW52CkNUWD0iJHsxOj91"
    "c2FnZTogZGVwbG95LWJ1aWxkIDxjdHgtZGlyPiA8YXBwLW5hbWU+fSI7IEFQUD0iJHsyOj91c2Fn"
    "ZTogZGVwbG95LWJ1aWxkIDxjdHgtZGlyPiA8YXBwLW5hbWU+fSIKbWtkaXIgLXAgL3Jvb3QvLmRl"
    "cGxveS1tZXRhClBPUlQ9JChncmVwIC1pRSAiXkVYUE9TRSIgIiRDVFgvRG9ja2VyZmlsZSIgMj4v"
    "ZGV2L251bGwgfCBncmVwIC1vRSAiWzAtOV0rIiB8IGhlYWQgLTEpClsgLXogIiRQT1JUIiBdICYm"
    "IFBPUlQ9ODAwMAplY2hvICIkUE9SVCIgPiAiL3Jvb3QvLmRlcGxveS1tZXRhLyRBUFAucG9ydCIK"
    "ZGVwbG95LWxvZ2luID4vZGV2L251bGwgMj4mMSB8fCB0cnVlCmVjaG8gIkJVSUxEX1NVQk1JVCBh"
    "cHA9JEFQUCBjdHg9JENUWCBwb3J0PSRQT1JUIChtYXkgZGlzY29ubmVjdCB+Mm1pbjsgQUNSIGtl"
    "ZXBzIGJ1aWxkaW5nKSIKYXogYWNyIGJ1aWxkIC0tcmVnaXN0cnkgIiRPUEVOQ0xBV19ERVBMT1lf"
    "QUNSIiAtLWltYWdlICIkQVBQOmxhdGVzdCIgLS1wbGF0Zm9ybSBsaW51eC9hbWQ2NCAiJENUWCIg"
    "LW8gbm9uZSAmJiBlY2hvIEJVSUxEX0RPTkVfSU5MSU5FCg=="
)
_DEPLOY_FINISH_B64 = (
    "IyEvYmluL2Jhc2gKc2V0IC1lCi4gL2V0Yy9vcGVuY2xhdy1kZXBsb3kuZW52CkFQUD0iJHsxOj91"
    "c2FnZTogZGVwbG95LWZpbmlzaCA8YXBwLW5hbWU+IFtwb3J0XX0iOyBQT1JUPSIkezI6LX0iClsg"
    "LXogIiRQT1JUIiBdICYmIFBPUlQ9JChjYXQgIi9yb290Ly5kZXBsb3ktbWV0YS8kQVBQLnBvcnQi"
    "IDI+L2Rldi9udWxsIHx8IGVjaG8gODAwMCkKSU1HPSIke09QRU5DTEFXX0RFUExPWV9BQ1J9LmF6"
    "dXJlY3IuaW8vJHtBUFB9OmxhdGVzdCIKaWYgISBheiBhY3IgcmVwb3NpdG9yeSBzaG93LXRhZ3Mg"
    "LS1uYW1lICIkT1BFTkNMQVdfREVQTE9ZX0FDUiIgLS1yZXBvc2l0b3J5ICIkQVBQIiAtbyB0c3Yg"
    "Mj4vZGV2L251bGwgfCBncmVwIC1xeCBsYXRlc3Q7IHRoZW4KICBlY2hvIFNUSUxMX0JVSUxESU5H"
    "OyBleGl0IDAKZmkKU1RBVEU9JChheiBjb250YWluZXJhcHAgc2hvdyAtZyAiJE9QRU5DTEFXX0RF"
    "UExPWV9SRVNPVVJDRV9HUk9VUCIgLW4gIiRBUFAiIC0tcXVlcnkgcHJvcGVydGllcy5wcm92aXNp"
    "b25pbmdTdGF0ZSAtbyB0c3YgMj4vZGV2L251bGwgfHwgdHJ1ZSkKaWYgWyAteiAiJFNUQVRFIiBd"
    "OyB0aGVuCiAgYXogY29udGFpbmVyYXBwIGNyZWF0ZSAtZyAiJE9QRU5DTEFXX0RFUExPWV9SRVNP"
    "VVJDRV9HUk9VUCIgLW4gIiRBUFAiIC0tZW52aXJvbm1lbnQgIiRPUEVOQ0xBV19ERVBMT1lfQUNB"
    "X0VOViIgLS1pbWFnZSAiJElNRyIgLS1yZWdpc3RyeS1zZXJ2ZXIgIiR7T1BFTkNMQVdfREVQTE9Z"
    "X0FDUn0uYXp1cmVjci5pbyIgLS1yZWdpc3RyeS1pZGVudGl0eSAiJE9QRU5DTEFXX0RFUExPWV9B"
    "Q1JfUFVMTF9NSSIgLS11c2VyLWFzc2lnbmVkICIkT1BFTkNMQVdfREVQTE9ZX0FDUl9QVUxMX01J"
    "IiAtLXRhcmdldC1wb3J0ICIkUE9SVCIgLS1pbmdyZXNzIGV4dGVybmFsIC0tbWluLXJlcGxpY2Fz"
    "IDEgLS1tYXgtcmVwbGljYXMgMSAtLWNwdSAwLjUgLS1tZW1vcnkgMS4wR2kgLS1uby13YWl0IC1v"
    "IG5vbmUgMj4vZGV2L251bGwgfHwgdHJ1ZQogIGVjaG8gIkRFUExPWV9TVEFSVEVEIHBvcnQ9JFBP"
    "UlQiOyBleGl0IDAKZmkKaWYgWyAiJFNUQVRFIiA9ICJTdWNjZWVkZWQiIF07IHRoZW4KICBGUURO"
    "PSQoYXogY29udGFpbmVyYXBwIHNob3cgLWcgIiRPUEVOQ0xBV19ERVBMT1lfUkVTT1VSQ0VfR1JP"
    "VVAiIC1uICIkQVBQIiAtLXF1ZXJ5IHByb3BlcnRpZXMuY29uZmlndXJhdGlvbi5pbmdyZXNzLmZx"
    "ZG4gLW8gdHN2IDI+L2Rldi9udWxsIHx8IHRydWUpCiAgaWYgWyAtbiAiJEZRRE4iIF07IHRoZW4g"
    "ZWNobyAiREVQTE9ZRURfVVJMPWh0dHBzOi8vJEZRRE4iOyBleGl0IDA7IGZpCmZpCmVjaG8gU1RJ"
    "TExfREVQTE9ZSU5HOyBleGl0IDAK"
)


def _helper_install_cmd() -> str:
    """A single-line command that (re)installs the deploy helpers in the sandbox."""
    return (
        "mkdir -p /usr/local/bin && "
        f"printf %s '{_DEPLOY_BUILD_B64}' | base64 -d > /usr/local/bin/deploy-build && "
        f"printf %s '{_DEPLOY_FINISH_B64}' | base64 -d > /usr/local/bin/deploy-finish && "
        "chmod +x /usr/local/bin/deploy-build /usr/local/bin/deploy-finish && "
        "echo HELPERS_READY"
    )


def _deploy_build_prompt(app_name: str) -> str:
    return (
        "Act as the Deployment Agent. The testing-agent already validated the app, "
        "so do NOT re-run, re-test, curl, or start a local server. Deploy it now by "
        "running these steps, each as ONE single-line shell command (never a "
        "multi-line here-doc):\n"
        f"1. Install the deploy helpers: {_helper_install_cmd()}\n"
        "2. Locate the project directory that holds the Dockerfile: "
        "`find . -name Dockerfile` (it is usually the `app/` SUBDIRECTORY, not `.`).\n"
        "3. If that directory has no `.dockerignore`, create one so the build "
        "context stays small: `printf '%s\\n' node_modules .npm .git dist build "
        ".next coverage __pycache__ .venv '*.log' > <project-dir>/.dockerignore`.\n"
        f"4. Start the build: `deploy-build <project-dir> {app_name}` — pass EXACTLY "
        f"`{app_name}` as the app name and the directory from step 2 as <project-dir>. "
        "This uploads the context and streams the ACR build. It MAY disconnect after "
        "about 2 minutes with `Network issue — retry policy expired`; that is "
        "EXPECTED and NOT a failure — the image keeps building in ACR, so do NOT "
        "re-run deploy-build. "
        "After running deploy-build once, end your reply with the line "
        "`BUILD_SUBMITTED`. Do NOT emit any DEPLOYED_URL line in this turn."
    )


def _deploy_finish_prompt(app_name: str) -> str:
    return (
        "Act as the Deployment Agent. Finish the deployment by running EXACTLY this "
        "one single-line command and reporting its output verbatim:\n"
        f"`deploy-finish {app_name}`\n"
        "Report the command's last output line exactly as printed. If it prints a "
        "`DEPLOYED_URL=...` line, report that exact line. If it prints "
        "`STILL_BUILDING`, `DEPLOY_STARTED`, or `STILL_DEPLOYING`, report that single "
        "word and DO NOT emit any URL. Never invent, guess, or construct a URL."
    )


# How many times the orchestrator polls `deploy-finish` and how long it waits
# between polls (build + container-app create typically finish within a few
# minutes; each poll turn is itself well under the 120s exec cap).
_DEPLOY_POLL_ATTEMPTS = 12
_DEPLOY_POLL_DELAY_S = 20


def _tests_passed(text: str) -> bool:
    return bool(_TESTS_PASSED_RE.search(text or ""))


def _coding_fix_prompt(requirement: str, test_failures: str) -> str:
    return (
        _AGENT_INSTRUCTIONS["coding-agent"]
        + f"\n\nRequirement:\n{requirement}\n\n"
        "The testing-agent ran your code and it FAILED the tests below. Do NOT wipe "
        "the workspace or run `rm -rf app`; edit the existing files under `app/` to "
        "fix the exact problems, then list the files you changed:\n"
        + test_failures
    )


def _run_app_name() -> str:
    """A unique, ACA-valid Container App name for this run.

    Using a fresh name every run prevents a stale revision from a PRIOR run (which
    reuses a generic name like ``app``) from shadowing this run's URL — that
    shadowing is what made fabricated/stale ``DEPLOYED_URL`` values look reachable.
    Format: ``proto-<10 hex>`` (16 chars, lowercase, hyphen-safe, <=32 limit).
    """
    return f"proto-{uuid.uuid4().hex[:10]}"


def _redeploy_fix_prompt(app_name: str, deployed_url: str, detail: str) -> str:
    return (
        "Act as the Deployment Agent. The app you deployed at "
        f"{deployed_url} is NOT reachable ({detail}). The likely causes are a "
        "wrong EXPOSE/target port or a crashing container. Fix the Dockerfile or "
        "app under the project directory (single-line commands only, no here-docs, "
        "stay inside your workspace), then REBUILD and REDEPLOY the SAME app by "
        f"running: `deploy-build <project-dir> {app_name}` (pass EXACTLY "
        f"`{app_name}`). This may disconnect after ~2 minutes — that is expected, "
        "the build continues in ACR. After running deploy-build once, end your "
        "reply with `BUILD_SUBMITTED`; do NOT emit a URL in this turn."
    )


def _build_prompt(agent_id: str, requirement: str, prior: list[dict[str, str]]) -> str:
    instruction = _AGENT_INSTRUCTIONS.get(agent_id, f"Act as {agent_id}.")
    lines = [instruction, "", f"Requirement:\n{requirement}"]
    if prior:
        artifacts = "\n\n".join(
            f"## {item['agent']}\n{item['content']}" for item in prior
        )
        lines.append("\nApproved prior artifacts:\n" + artifacts)
    return "\n".join(lines)


def _extract_download_url(text: str) -> str | None:
    match = _DOWNLOAD_URL_RE.search(text or "")
    return match.group(0) if match else None


def _extract_deployed_url(text: str) -> str | None:
    match = _DEPLOYED_URL_RE.search(text or "")
    return match.group(1) if match else None


def _extract_md_section(text: str, heading: str) -> str | None:
    """Return a markdown section (from ``## heading`` up to the next ``## ``).

    Used to surface the coding-agent's ``## PROJECT ARCHITECTURE`` and the
    testing-agent's ``## TEST RESULTS`` sections directly into Teams. Returns
    ``None`` when the heading is absent.
    """
    if not text:
        return None
    pattern = re.compile(
        r"^\s{0,3}#{1,6}\s*" + re.escape(heading) + r"\s*$", re.IGNORECASE | re.MULTILINE
    )
    m = pattern.search(text)
    if not m:
        return None
    start = m.start()
    # Find the next top-level heading after this one to bound the section.
    nxt = re.compile(r"^\s{0,3}#{1,3}\s+\S", re.MULTILINE).search(text, m.end())
    end = nxt.start() if nxt else len(text)
    return text[start:end].strip()


async def _url_healthy(url: str) -> tuple[bool, str]:
    """Health-check a deployed app URL.

    Returns ``(True, "")`` when the URL responds with an HTTP status < 400,
    otherwise ``(False, detail)`` where ``detail`` describes the failure. Follows
    redirects and never raises.
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0), follow_redirects=True
        ) as http:
            resp = await http.get(url)
        # Azure Container Apps' ingress returns a JSON body like
        # {"code":"ResourceNotFound","message":"/ does not exist"} when the FQDN
        # resolves to the environment but no Container App with that name has an
        # active revision (e.g. a fabricated/stale URL, or a deploy that never
        # succeeded). This can come back with a 2xx status, so status alone is not
        # enough — inspect the body and treat that shape as unreachable.
        body = (resp.text or "")[:1000]
        if "ResourceNotFound" in body or "does not exist" in body:
            return False, "ACA ResourceNotFound (no active revision at this URL)"
        if resp.status_code < 400:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001 - report any connectivity failure
        return False, f"{type(exc).__name__}: {exc}"


def _extract_archive(zip_path: str, dest_dir: str) -> str:
    """Extract ``zip_path`` into a sibling folder and return that folder path."""
    base = os.path.splitext(os.path.basename(zip_path))[0]
    out_dir = os.path.join(dest_dir, base)
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        # Guard against path traversal in archive members.
        for member in archive.namelist():
            target = os.path.normpath(os.path.join(out_dir, member))
            if not target.startswith(os.path.abspath(out_dir) + os.sep) and target != os.path.abspath(out_dir):
                raise ValueError(f"unsafe archive member path: {member}")
        archive.extractall(out_dir)
    return out_dir


# Text outputs (agent replies) that should be captured as markdown files inside
# the archive. The coding-agent contributes real project files (already in the
# archive), so only the analysis/report stages are added here.
_REPORT_FILENAMES: dict[str, str] = {
    "requirements-agent": "requirements-agent/ACCEPTANCE_CRITERIA.md",
    "testing-agent": "testing-agent/TEST_REPORT.md",
    "deployment-agent": "deployment-agent/DEPLOYMENT.md",
}


def _build_report_files(
    results: list[dict[str, Any]],
    requirement: str,
    totals: dict[str, int],
    download_url: str | None,
) -> dict[str, str]:
    """Build ``{archive_path: text}`` for the agent text outputs to embed."""
    files: dict[str, str] = {}
    for item in results:
        arcname = _REPORT_FILENAMES.get(item["agent"])
        if arcname and item.get("content"):
            title = item.get("description") or item["agent"]
            files[arcname] = f"# {item['agent']}\n\n_{title}_\n\n{item['content']}\n"

    lines = [
        "# Workflow Summary",
        "",
        f"**Requirement:** {requirement}",
        "",
        "## Token usage",
        "",
        "| Agent | Prompt | Completion | Total |",
        "| --- | ---: | ---: | ---: |",
    ]
    for item in results:
        tok = item.get("tokens") or {}
        lines.append(
            f"| {item['agent']} | {tok.get('prompt', 0)} | "
            f"{tok.get('completion', 0)} | {tok.get('total', 0)} |"
        )
    lines.append(
        f"| **TOTAL** | **{totals.get('prompt', 0)}** | "
        f"**{totals.get('completion', 0)}** | **{totals.get('total', 0)}** |"
    )
    if download_url:
        lines += ["", f"**Download URL:** {download_url}"]
    files["WORKFLOW_SUMMARY.md"] = "\n".join(lines) + "\n"
    return files


def _augment_archive(zip_path: str, files: dict[str, str]) -> None:
    """Append ``files`` (arcname -> text) to an existing zip, skipping dupes."""
    with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as archive:
        existing = set(archive.namelist())
        for arcname, text in files.items():
            if arcname in existing:
                continue
            archive.writestr(arcname, text)


def build_server(client: OpenClawGatewayClient, settings: Settings) -> FastMCP:
    """Create a configured :class:`FastMCP` server bound to ``client``."""
    mcp = FastMCP(
        name="openclaw-workflow-mcp",
        instructions=(
            "Tools for turning a plain-language programming requirement into a "
            "reviewed project prototype using the OpenClaw multi-agent workflow "
            "(requirements -> coding -> testing -> deployment -> save), backed by the "
            "OpenClaw gateway on Azure Container Apps. Each stage announces what it is "
            "doing and reports token consumption; the finished prototype is downloaded "
            "to a local folder (~/Downloads by default) when no destination is given."
        ),
        host=settings.host,
        port=settings.port,
    )

    async def _announce(ctx: Context | None, message: str) -> None:
        if ctx is None:
            return
        # Logging requires an active MCP request; tolerate its absence (e.g.
        # when the tool is invoked outside a request in tests).
        try:
            await ctx.info(message)
        except Exception:
            pass
        # Also fire a progress notification (no text — the log above is the
        # single text channel) so the MCP client's `resetTimeoutOnProgress`
        # keeps this long-running call alive throughout the workflow. Log
        # notifications alone do NOT reset the client timer, which is what
        # caused "Maximum total timeout exceeded" on long runs.
        try:
            await ctx.report_progress(progress=time.monotonic())
        except Exception:
            pass

    async def _progress(ctx: Context | None, current: int, total: int) -> None:
        if ctx is None:
            return
        try:
            await ctx.report_progress(current, total)
        except Exception:
            pass

    async def _run_turn(agent_id: str, prompt: str, session_key: str | None) -> AgentTurn:
        turn = await client.run_agent_turn(agent_id, prompt, session_key=session_key)
        # A plain string may come back from stub clients used in tests.
        if isinstance(turn, str):
            return AgentTurn(content=turn)
        return turn

    @mcp.tool(
        title="Generate project prototype",
        description=(
            "Run the full OpenClaw multi-agent workflow (requirements -> coding -> "
            "testing -> deployment -> save) against the gateway to turn a programming "
            "requirement into a project prototype. Announces each stage and reports "
            "per-agent and total token usage. When the save-agent produces a download "
            "URL, the archive is downloaded to `output_dir` (defaults to the local save "
            "directory, ~/Downloads) and extracted. Returns a per-stage summary, "
            "token totals, the download URL, and the local path where the prototype was "
            "saved."
        ),
    )
    async def generate_prototype(
        requirement: str,
        output_dir: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        requirement = requirement.strip()
        if not requirement:
            raise ValueError("requirement must not be empty")
        # If no destination is specified, save to the local folder by default.
        destination = os.path.abspath(os.path.expanduser(output_dir.strip())) if (
            output_dir and output_dir.strip()
        ) else settings.local_save_dir

        # A unique per-run session key so every new prototype request starts
        # from a clean slate — no conversation history or generated content
        # carried over from any previous run of the 5 agents. It stays constant
        # within THIS run so the deployment/save agents can re-drive the same
        # sandbox session.
        session_key = f"mcp-prototype:{uuid.uuid4().hex}"
        agents = list(settings.agents)
        n = len(agents)
        # Final content + summed tokens per agent (agents may run several rounds).
        latest: dict[str, str] = {}
        tokens_by_agent: dict[str, dict[str, int]] = {}
        total_prompt = total_completion = total_total = 0

        def _idx(agent_id: str) -> int:
            return agents.index(agent_id) + 1 if agent_id in agents else 0

        def _prior() -> list[dict[str, str]]:
            # Completed agents, in canonical order, as prior artifacts context.
            return [{"agent": a, "content": latest[a]} for a in agents if a in latest]

        async def _stage(agent_id: str, prompt: str, *, announce: bool = True) -> AgentTurn:
            nonlocal total_prompt, total_completion, total_total
            if announce:
                stage = _AGENT_STAGE_DESC.get(agent_id, f"Running {agent_id}")
                await _announce(ctx, f"[{_idx(agent_id)}/{n}] ▶ {agent_id}: {stage}")
                await _progress(ctx, max(_idx(agent_id) - 1, 0), n)
            turn = await _run_turn(agent_id, prompt, session_key)
            u = turn.usage
            total_prompt += u.prompt
            total_completion += u.completion
            total_total += u.total
            prev = tokens_by_agent.get(
                agent_id, {"prompt": 0, "completion": 0, "total": 0}
            )
            tokens_by_agent[agent_id] = {
                "prompt": prev["prompt"] + u.prompt,
                "completion": prev["completion"] + u.completion,
                "total": prev["total"] + u.total,
            }
            if announce:
                await _announce(
                    ctx,
                    f"[{_idx(agent_id)}/{n}] ✓ {agent_id} done — tokens: "
                    f"prompt={u.prompt}, completion={u.completion}, total={u.total}",
                )
            return turn

        try:
            # 0) Deterministic pre-run wipe --------------------------------------
            # Clear every lingering agent workspace in the reused sandbox so no
            # files from a previous run leak into or get packaged with this one.
            await _announce(
                ctx, f"[0/{n}] 🧹 Clearing previous run's generated files from the sandbox"
            )
            wiped = False
            for wipe_try in range(1, 3):
                wipe_turn = await _run_turn("coding-agent", _WIPE_PROMPT, session_key)
                if _WIPE_MARKER in (wipe_turn.content or ""):
                    wiped = True
                    await _announce(ctx, f"[0/{n}] ✓ Sandbox workspace cleared")
                    break
            if not wiped:
                await _announce(
                    ctx,
                    f"[0/{n}] ⚠ Could not confirm the workspace was cleared — the "
                    "coding-agent will still wipe it on its first build turn.",
                )

            # 1) Requirements -----------------------------------------------------
            turn = await _stage(
                "requirements-agent",
                _build_prompt("requirements-agent", requirement, _prior()),
            )
            latest["requirements-agent"] = turn.content

            # 2) Coding <-> Testing review loop -----------------------------------
            # The testing-agent actually runs backend + frontend tests and emits a
            # TESTS_PASSED / TESTS_FAILED verdict. On failure the failures are fed
            # back to the coding-agent to fix, then tests re-run, up to N rounds.
            code_turn = await _stage(
                "coding-agent", _build_prompt("coding-agent", requirement, _prior())
            )
            latest["coding-agent"] = code_turn.content
            arch = _extract_md_section(code_turn.content, "PROJECT ARCHITECTURE")
            if arch:
                await _announce(ctx, f"[2/{n}] 🏗 Project architecture:\n\n{arch}")

            tests_ok = False
            for round_no in range(1, _MAX_TEST_ROUNDS + 1):
                test_turn = await _stage(
                    "testing-agent",
                    _build_prompt("testing-agent", requirement, _prior()),
                )
                latest["testing-agent"] = test_turn.content
                results_md = _extract_md_section(test_turn.content, "TEST RESULTS")
                if results_md:
                    await _announce(
                        ctx, f"[3/{n}] 🧪 Test results (round {round_no}):\n\n{results_md}"
                    )
                if _tests_passed(test_turn.content):
                    tests_ok = True
                    await _announce(
                        ctx, f"[3/{n}] ✓ tests passed (round {round_no})"
                    )
                    break
                if round_no >= _MAX_TEST_ROUNDS:
                    await _announce(
                        ctx,
                        f"[3/{n}] ⚠ tests still failing after {round_no} rounds — "
                        "continuing, but the app may be broken. Review the report.",
                    )
                    break
                await _announce(
                    ctx,
                    f"[2/{n}] ↩ tests failed (round {round_no}) — sending failures "
                    "back to coding-agent to fix",
                )
                fix_turn = await _stage(
                    "coding-agent",
                    _coding_fix_prompt(requirement, test_turn.content),
                    announce=False,
                )
                # Keep the most recent code state as the coding-agent artifact.
                latest["coding-agent"] = fix_turn.content

            # 3) Deployment: split build + poll-based finish ---------------------
            # A fresh app name per run avoids stale-revision shadowing (which made
            # bogus URLs look reachable) and makes the URL deterministic. We build
            # first (tolerating the ~120s exec disconnect — ACR finishes the build
            # server-side), then POLL an idempotent `deploy-finish` until the
            # container app reports a real DEPLOYED_URL.
            app_name = _run_app_name()
            await _announce(ctx, f"[4/{n}] 🚀 deploying as `{app_name}`")
            build_turn = await _stage(
                "deployment-agent", _deploy_build_prompt(app_name)
            )
            content = build_turn.content
            await _announce(
                ctx,
                f"[4/{n}] 🏗️ build submitted for `{app_name}` — polling for the "
                "deployed URL (build + container-app create run server-side)",
            )
            for attempt in range(1, _DEPLOY_POLL_ATTEMPTS + 1):
                finish_turn = await _stage(
                    "deployment-agent", _deploy_finish_prompt(app_name), announce=False
                )
                content = (
                    f"{content}\n\n---\n[deploy-finish poll {attempt}]\n"
                    f"{finish_turn.content}"
                )
                if _extract_deployed_url(finish_turn.content):
                    await _announce(
                        ctx,
                        f"[4/{n}] ✓ deployment reported a URL on poll {attempt}",
                    )
                    break
                await _announce(
                    ctx,
                    f"[4/{n}] ⏳ still deploying (poll {attempt}/"
                    f"{_DEPLOY_POLL_ATTEMPTS}) — build/create in progress",
                )
                if attempt < _DEPLOY_POLL_ATTEMPTS:
                    await asyncio.sleep(_DEPLOY_POLL_DELAY_S)

            # Review the deployed app: it must actually be reachable. If not,
            # rebuild+redeploy under a FRESH app name (a clean create avoids the
            # same-tag image-not-updated problem) and re-poll, up to N times.
            for review_no in range(1, _MAX_DEPLOY_REVIEW + 1):
                url = _extract_deployed_url(content)
                if not url:
                    break
                healthy, detail = await _url_healthy(url)
                if healthy:
                    await _announce(ctx, f"[4/{n}] ✓ deployed app reachable at {url}")
                    break
                if review_no >= _MAX_DEPLOY_REVIEW:
                    await _announce(
                        ctx,
                        f"[4/{n}] ⚠ deployed app at {url} not reachable ({detail}) "
                        "after review — check the container app.",
                    )
                    break
                await _announce(
                    ctx,
                    f"[4/{n}] ↩ deployed app not reachable ({detail}) — fixing and "
                    "redeploying",
                )
                app_name = _run_app_name()
                fix = await _stage(
                    "deployment-agent",
                    _redeploy_fix_prompt(app_name, url, detail),
                    announce=False,
                )
                content = f"{content}\n\n---\n[redeploy-review {review_no}]\n{fix.content}"
                # Poll the fresh app for its URL before re-checking health.
                found_url = None
                for attempt in range(1, _DEPLOY_POLL_ATTEMPTS + 1):
                    finish_turn = await _stage(
                        "deployment-agent",
                        _deploy_finish_prompt(app_name),
                        announce=False,
                    )
                    content = (
                        f"{content}\n\n---\n[redeploy-finish poll {attempt}]\n"
                        f"{finish_turn.content}"
                    )
                    found_url = _extract_deployed_url(finish_turn.content)
                    if found_url:
                        await _announce(
                            ctx,
                            f"[4/{n}] ✓ redeploy reported a URL on poll {attempt}",
                        )
                        break
                    if attempt < _DEPLOY_POLL_ATTEMPTS:
                        await asyncio.sleep(_DEPLOY_POLL_DELAY_S)
                if not found_url:
                    await _announce(
                        ctx,
                        f"[4/{n}] ⚠ redeploy did not report a URL after "
                        f"{_DEPLOY_POLL_ATTEMPTS} polls.",
                    )
                    break

            if not _extract_deployed_url(content):
                await _announce(
                    ctx,
                    f"[4/{n}] ⚠ deployment-agent produced no DEPLOYED_URL= after "
                    f"{_DEPLOY_POLL_ATTEMPTS} polls — the app was NOT deployed. "
                    "Review its output.",
                )
            latest["deployment-agent"] = content

            # 4) Save -------------------------------------------------------------
            save_turn = await _stage(
                "save-agent", _build_prompt("save-agent", requirement, _prior())
            )
            latest["save-agent"] = save_turn.content
        except GatewayClientError as exc:
            raise RuntimeError(str(exc)) from exc

        # Assemble per-agent results in canonical order (one entry per agent).
        results: list[dict[str, Any]] = [
            {
                "agent": a,
                "description": _AGENT_STAGE_DESC.get(a, f"Running {a}"),
                "content": latest[a],
                "tokens": tokens_by_agent.get(
                    a, {"prompt": 0, "completion": 0, "total": 0}
                ),
            }
            for a in agents
            if a in latest
        ]
        tests_passed = _tests_passed(latest.get("testing-agent", ""))

        # Save-agent is last: pull the authenticated download URL and fetch it
        # into the local destination folder.
        download_url = _extract_download_url(latest.get("save-agent", "")) or None
        # Deployment-agent's real app URL (None if the deploy never happened).
        deployed_url = _extract_deployed_url(latest.get("deployment-agent", "")) or None
        saved_path: str | None = None
        extracted_dir: str | None = None
        if download_url:
            await _announce(ctx, f"Downloading prototype archive to {destination}")
            try:
                saved_path = await client.download_artifact(download_url, destination)
                # Embed the requirements/testing/deployment agent text outputs
                # (agent replies, not workspace files) into the archive so the
                # zip is self-contained.
                totals = {
                    "prompt": total_prompt,
                    "completion": total_completion,
                    "total": total_total,
                }
                try:
                    _augment_archive(
                        saved_path,
                        _build_report_files(results, requirement, totals, download_url),
                    )
                except (zipfile.BadZipFile, OSError):
                    pass
                try:
                    extracted_dir = _extract_archive(saved_path, destination)
                except (zipfile.BadZipFile, ValueError, OSError):
                    extracted_dir = None
                await _announce(ctx, f"Saved prototype to {saved_path}")
            except GatewayClientError as exc:
                await _announce(ctx, f"Download failed: {exc}")

        return {
            "summary": {
                "requirement": requirement,
                "stages": [
                    {
                        "agent": item["agent"],
                        "description": item["description"],
                        "chars": len(item["content"]),
                        "tokens": item["tokens"],
                    }
                    for item in results
                ],
                "total_tokens": {
                    "prompt": total_prompt,
                    "completion": total_completion,
                    "total": total_total,
                },
                "download_url": download_url,
                "deployed_url": deployed_url,
                "tests_passed": tests_passed,
                "saved_zip": saved_path,
                "saved_dir": extracted_dir,
                "output_dir": destination,
            },
            "artifacts": results,
        }

    @mcp.tool(
        title="Run a single agent",
        description=(
            "Send a message to one OpenClaw agent (requirements-agent, "
            "coding-agent, testing-agent, deployment-agent, or save-agent) and return "
            "its reply plus token usage."
        ),
    )
    async def run_agent(
        agent_id: str, message: str, ctx: Context | None = None
    ) -> dict[str, Any]:
        agent_id = agent_id.strip()
        message = message.strip()
        if agent_id not in settings.agents:
            raise ValueError(
                f"unknown agent '{agent_id}'; expected one of {list(settings.agents)}"
            )
        if not message:
            raise ValueError("message must not be empty")
        stage = _AGENT_STAGE_DESC.get(agent_id, f"Running {agent_id}")
        await _announce(ctx, f"▶ {agent_id}: {stage}")
        try:
            turn = await _run_turn(agent_id, message, None)
        except GatewayClientError as exc:
            raise RuntimeError(str(exc)) from exc
        await _announce(
            ctx,
            f"✓ {agent_id} done — tokens: prompt={turn.usage.prompt}, "
            f"completion={turn.usage.completion}, total={turn.usage.total}",
        )
        return {
            "agent": agent_id,
            "content": turn.content,
            "tokens": turn.usage.as_dict(),
        }

    @mcp.tool(
        title="Check gateway health",
        description="Report the health status of the backing OpenClaw gateway.",
    )
    async def check_gateway_health() -> dict[str, Any]:
        try:
            status = await client.health()
        except GatewayClientError as exc:
            raise RuntimeError(str(exc)) from exc
        return {"gateway": settings.gateway_base_url, "health": status}

    @mcp.resource(
        "workflow://info",
        title="Workflow overview",
        description="Ordered stages of the OpenClaw programming workflow.",
        mime_type="application/json",
    )
    def workflow_info() -> dict[str, Any]:
        return {
            "name": "OpenClaw programming workflow",
            "gateway": settings.gateway_base_url,
            "stages": list(settings.agents),
            "transport": "OpenAI-compatible /v1/chat/completions (model=openclaw/<agent>)",
            "local_save_dir": settings.local_save_dir,
        }

    @mcp.prompt(
        title="Prototype request",
        description="Template for requesting a project prototype from the workflow.",
    )
    def prototype_request(requirement: str) -> str:
        return (
            "Use the generate_prototype tool to produce a reviewed project "
            f"prototype for this requirement:\n\n{requirement}\n\n"
            "Then summarize the artifact and token usage from each workflow stage."
        )

    return mcp


def create_default_server() -> FastMCP:
    """Build a server using configuration from environment variables."""
    settings = Settings.from_env()
    client = OpenClawGatewayClient(
        base_url=settings.gateway_base_url,
        token=settings.gateway_token,
        timeout_seconds=settings.request_timeout_seconds,
    )
    return build_server(client, settings)
