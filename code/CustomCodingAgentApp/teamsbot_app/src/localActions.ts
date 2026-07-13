import { spawn } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as https from "https";
import * as http from "http";
import { config } from "./config";

/**
 * Client-side "open the result" actions. These only affect the machine the bot
 * process runs on, so they are meaningful when running the bot locally
 * (`npm start` on your Mac) — NOT when hosted in ACA. Guarded by
 * `AUTO_OPEN_LOCAL=true`.
 *
 * Given a finished workflow it will:
 *   1. open `deployed_url` in the default browser,
 *   2. download `download_url` (gateway-authenticated ZIP) into DOWNLOAD_DIR,
 *   3. extract it, and
 *   4. open the extracted folder in VS Code / VS Code Insiders.
 *
 * Returns a short human-readable status line for each step (to echo into Teams).
 */

function expandDir(dir: string): string {
  let d = dir;
  if (d.startsWith("~")) d = path.join(os.homedir(), d.slice(1));
  return path.resolve(d);
}

/** Open a URL / path with the OS default handler. */
function openWithOS(target: string): void {
  const platform = process.platform;
  const [cmd, args] =
    platform === "darwin"
      ? ["open", [target]]
      : platform === "win32"
        ? ["cmd", ["/c", "start", "", target]]
        : ["xdg-open", [target]];
  const child = spawn(cmd as string, args as string[], {
    stdio: "ignore",
    detached: true,
  });
  child.on("error", () => undefined); // avoid unhandled async 'error'
  child.unref();
}

/** Try to spawn one command; resolves true on successful spawn, false on error. */
function trySpawn(cmd: string, args: string[]): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const done = (ok: boolean) => {
      if (!settled) {
        settled = true;
        resolve(ok);
      }
    };
    try {
      const child = spawn(cmd, args, { stdio: "ignore", detached: true });
      child.on("error", () => done(false));
      child.on("spawn", () => {
        child.unref();
        done(true);
      });
      // Safety timeout in case neither event fires.
      setTimeout(() => done(true), 1500);
    } catch {
      done(false);
    }
  });
}

/** Resolve which editor CLI/app to use based on EDITOR_PREFERENCE. */
async function openInEditor(target: string): Promise<string> {
  const pref = config.editor; // auto | insiders | code
  const insidersBundle =
    "/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code";
  const codeBundle = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code";

  const candidates: Array<[string, string[]]> = [];
  if (pref === "insiders" || pref === "auto") {
    candidates.push(["code-insiders", [target]]);
    if (fs.existsSync(insidersBundle)) candidates.push([insidersBundle, [target]]);
    candidates.push(["open", ["-a", "Visual Studio Code - Insiders", target]]);
  }
  if (pref === "code" || pref === "auto") {
    candidates.push(["code", [target]]);
    if (fs.existsSync(codeBundle)) candidates.push([codeBundle, [target]]);
    candidates.push(["open", ["-a", "Visual Studio Code", target]]);
  }

  for (const [cmd, args] of candidates) {
    if (await trySpawn(cmd, args)) {
      const label = cmd.length > 40 ? "VS Code" : cmd;
      return `Opened ${target} with \`${label}\``;
    }
  }
  return `VS Code not found — please open manually: ${target}`;
}

/** Download a URL to a file, following the gateway bearer-auth requirement. */
function downloadFile(url: string, dest: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith("https") ? https : http;
    const headers: Record<string, string> = {};
    if (config.gatewayToken) headers["Authorization"] = `Bearer ${config.gatewayToken}`;
    const req = lib.get(url, { headers }, (res) => {
      if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        downloadFile(res.headers.location, dest).then(resolve, reject);
        return;
      }
      if (res.statusCode !== 200) {
        res.resume();
        reject(new Error(`download failed HTTP ${res.statusCode}`));
        return;
      }
      const file = fs.createWriteStream(dest);
      res.pipe(file);
      file.on("finish", () => file.close(() => resolve()));
      file.on("error", reject);
    });
    req.on("error", reject);
  });
}

/** Extract a zip using the system `unzip` (present on macOS/Linux). */
function unzip(zipPath: string, destDir: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn("unzip", ["-q", "-o", zipPath, "-d", destDir], { stdio: "ignore" });
    child.on("error", reject);
    child.on("close", (code) =>
      code === 0 ? resolve() : reject(new Error(`unzip exited ${code}`))
    );
  });
}

export interface OpenResult {
  lines: string[];
}

export async function openArtifactLocally(
  deployedUrl: string | null,
  downloadUrl: string | null
): Promise<OpenResult> {
  const lines: string[] = [];

  // 1) Open the deployed app in the default browser.
  if (deployedUrl) {
    try {
      openWithOS(deployedUrl);
      lines.push(`🌐 Opened the deployed URL in the default browser: ${deployedUrl}`);
    } catch (e) {
      lines.push(`⚠️ Failed to open the browser: ${String(e)}`);
    }
  } else {
    lines.push("ℹ️ No deployed_url — skipping browser open.");
  }

  // 2) Download + 3) extract + 4) open in VS Code.
  if (downloadUrl) {
    try {
      const dir = expandDir(config.downloadDir);
      fs.mkdirSync(dir, { recursive: true });
      const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
      const zipPath = path.join(dir, `project-${stamp}.zip`);
      await downloadFile(downloadUrl, zipPath);
      lines.push(`⬇️ Downloaded source ZIP: ${zipPath}`);

      const destDir = path.join(dir, `project-${stamp}`);
      fs.mkdirSync(destDir, { recursive: true });
      await unzip(zipPath, destDir);
      lines.push(`📦 Extracted to: ${destDir}`);

      lines.push(await openInEditor(destDir));
    } catch (e) {
      lines.push(`⚠️ Download/extract/open failed: ${String(e)}`);
    }
  } else {
    lines.push("ℹ️ No download_url — skipping source download.");
  }

  return { lines };
}
