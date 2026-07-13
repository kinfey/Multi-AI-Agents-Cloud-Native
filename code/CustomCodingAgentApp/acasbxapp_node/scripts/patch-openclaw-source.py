#!/usr/bin/env python3
"""Idempotently apply local OpenClaw source patches to the build work dir.

Currently injects an SSE keepalive into the OpenAI-compatible streaming handler
so long agent turns survive ingress idle timeouts (e.g. Azure Container Apps
closes idle connections after 240s). SSE comment lines keep the connection
active during tool-execution gaps that emit no assistant tokens.
"""
from __future__ import annotations

import sys
from pathlib import Path

KEEPALIVE_MARKER = "sseKeepAlive"

KEEPALIVE_ANCHOR = "  setSseHeaders(res);\n"

KEEPALIVE_BLOCK = """
  // Keep the ingress connection alive during long tool-execution gaps that
  // produce no assistant tokens. Some ingress proxies (e.g. Azure Container
  // Apps) close idle connections after a fixed timeout (240s). SSE comment
  // lines (starting with ":") are ignored by clients but count as connection
  // activity, resetting the idle timer so multi-minute agent turns survive.
  const sseKeepAlive = setInterval(() => {
    if (res.writableEnded || res.destroyed) {
      clearInterval(sseKeepAlive);
      return;
    }
    try {
      res.write(": keepalive\\n\\n");
    } catch {
      clearInterval(sseKeepAlive);
    }
  }, 15000);
  if (typeof sseKeepAlive.unref === "function") {
    sseKeepAlive.unref();
  }
"""


def patch_openai_http(work_dir: Path) -> bool:
    target = work_dir / "src" / "gateway" / "openai-http.ts"
    if not target.exists():
        print(f"patch-openclaw-source: {target} not found; skipping", file=sys.stderr)
        return False
    text = target.read_text()
    if KEEPALIVE_MARKER in text:
        print("patch-openclaw-source: SSE keepalive already present; skipping")
        return True
    if KEEPALIVE_ANCHOR not in text:
        print(
            "patch-openclaw-source: anchor 'setSseHeaders(res);' not found; "
            "upstream source may have changed",
            file=sys.stderr,
        )
        return False
    patched = text.replace(
        KEEPALIVE_ANCHOR,
        KEEPALIVE_ANCHOR + KEEPALIVE_BLOCK,
        1,
    )
    target.write_text(patched)
    print("patch-openclaw-source: injected SSE keepalive into openai-http.ts")
    return True


SKILLS_MARKER = "skillsSyncSignature"

SKILLS_FIELD_ANCHOR = (
    "  private refreshedSkillsForNextExecWorkdir: string | null = null;\n"
)
SKILLS_FIELD_BLOCK = (
    "  private skillsSyncSignature: string | null = null;\n"
)

SKILLS_METHOD_ORIGINAL = """  private async refreshRemoteSkillsWorkspace(): Promise<void> {
    if (
      this.params.createParams.cfg.workspaceAccess !== "rw" ||
      !this.params.createParams.skillsWorkspaceDir
    ) {
      return;
    }
    await this.clearRemoteDirectory(this.params.runtimePaths.remoteSkillsWorkspaceDir);
    if (!(await isExistingDirectory(this.params.createParams.skillsWorkspaceDir))) {
      return;
    }
    await this.syncLocalDirectoryToRemote(
      this.params.createParams.skillsWorkspaceDir,
      this.params.runtimePaths.remoteSkillsWorkspaceDir,
    );
  }"""

SKILLS_METHOD_REPLACEMENT = """  private async refreshRemoteSkillsWorkspace(): Promise<void> {
    if (
      this.params.createParams.cfg.workspaceAccess !== "rw" ||
      !this.params.createParams.skillsWorkspaceDir
    ) {
      return;
    }
    const skillsDir = this.params.createParams.skillsWorkspaceDir;
    const exists = await isExistingDirectory(skillsDir);
    // Skills are static during a run. Re-uploading the entire skills workspace
    // on every tool call turns each read/write/edit/exec into an O(skills)
    // sequence of `aca fs cp` round-trips (observed 130-260s per tool call).
    // Cache a content signature and skip the clear+resync when unchanged so the
    // skills sync happens once instead of on every tool invocation.
    const signature = exists ? await this.computeSkillsSignature(skillsDir) : "<absent>";
    if (this.skillsSyncSignature === signature) {
      return;
    }
    await this.clearRemoteDirectory(this.params.runtimePaths.remoteSkillsWorkspaceDir);
    if (exists) {
      await this.syncLocalDirectoryToRemote(
        skillsDir,
        this.params.runtimePaths.remoteSkillsWorkspaceDir,
      );
    }
    this.skillsSyncSignature = signature;
  }

  private async computeSkillsSignature(dir: string): Promise<string> {
    const parts: string[] = [];
    const walk = async (current: string, rel: string): Promise<void> => {
      const entries = await fs.readdir(current, { withFileTypes: true });
      for (const entry of entries.toSorted((a, b) => a.name.localeCompare(b.name))) {
        const childRel = rel ? `${rel}/${entry.name}` : entry.name;
        const childPath = path.join(current, entry.name);
        if (entry.isDirectory()) {
          parts.push(`d:${childRel}`);
          await walk(childPath, childRel);
        } else if (entry.isFile()) {
          const stat = await fs.stat(childPath);
          parts.push(`f:${childRel}:${stat.size}:${stat.mtimeMs}`);
        }
      }
    };
    await walk(dir, "");
    return parts.join("\\n");
  }"""


def patch_aca_backend(work_dir: Path) -> bool:
    target = work_dir / "src" / "agents" / "sandbox" / "aca-backend.ts"
    if not target.exists():
        print(f"patch-openclaw-source: {target} not found; skipping", file=sys.stderr)
        return False
    text = target.read_text()
    if SKILLS_MARKER in text:
        print("patch-openclaw-source: skills-sync cache already present; skipping")
        return True
    if SKILLS_FIELD_ANCHOR not in text or SKILLS_METHOD_ORIGINAL not in text:
        print(
            "patch-openclaw-source: aca-backend anchors not found; upstream source "
            "may have changed",
            file=sys.stderr,
        )
        return False
    text = text.replace(
        SKILLS_FIELD_ANCHOR,
        SKILLS_FIELD_ANCHOR + SKILLS_FIELD_BLOCK,
        1,
    )
    text = text.replace(SKILLS_METHOD_ORIGINAL, SKILLS_METHOD_REPLACEMENT, 1)
    target.write_text(text)
    print("patch-openclaw-source: cached skills sync in aca-backend.ts")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch-openclaw-source.py <work_dir>", file=sys.stderr)
        return 2
    work_dir = Path(sys.argv[1])
    ok_http = patch_openai_http(work_dir)
    ok_aca = patch_aca_backend(work_dir)
    return 0 if (ok_http and ok_aca) else 1


if __name__ == "__main__":
    raise SystemExit(main())
