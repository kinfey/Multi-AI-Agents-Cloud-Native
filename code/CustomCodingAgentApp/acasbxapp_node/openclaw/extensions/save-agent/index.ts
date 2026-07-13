import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { Type } from "typebox";

const ROUTE_PREFIX = "/api/saveagent/artifacts/";
const ARTIFACT_NAME_PATTERN = /^[a-f0-9]{32}\.zip$/;
const execFileAsync = promisify(execFile);

async function runControlledCommand(command: string, args: string[] = []): Promise<string> {
  const { stdout } = await execFileAsync(command, args, {
    encoding: "utf8",
    maxBuffer: 1024 * 1024,
    timeout: 30 * 60 * 1000,
  });
  return stdout.trim();
}

function textResult(text: string) {
  return { content: [{ type: "text" as const, text }], details: JSON.parse(text) };
}

export function resolveArtifactPath(requestUrl: string, artifactsRoot: string): string | undefined {
  const pathname = new URL(requestUrl, "http://localhost").pathname;
  if (!pathname.startsWith(ROUTE_PREFIX)) {
    return undefined;
  }
  const artifactName = pathname.slice(ROUTE_PREFIX.length);
  if (!ARTIFACT_NAME_PATTERN.test(artifactName)) {
    return undefined;
  }
  return path.join(path.resolve(artifactsRoot), artifactName);
}

export default definePluginEntry({
  id: "save-agent",
  name: "Save Agent",
  description: "Serve coding and testing workspace archives to authenticated operators",
  register(api) {
    api.registerTool(
      (ctx) =>
        ctx.agentId === "deployment-agent"
          ? {
              name: "deploy_to_aca",
              label: "Deploy to Azure Container Apps",
              description: "Build and deploy an approved coding-agent project to ACA.",
              parameters: Type.Object(
                {
                  projectPath: Type.String({ description: "Path below the coding-agent workspace" }),
                  appName: Type.String({ description: "Lowercase Azure Container App name" }),
                  targetPort: Type.Optional(Type.Integer({ minimum: 1, maximum: 65535 })),
                  healthPath: Type.Optional(Type.String()),
                },
                { additionalProperties: false },
              ),
              execute: async (_toolCallId: string, params: Record<string, unknown>) => {
                const projectPath = String(params.projectPath ?? "");
                const appName = String(params.appName ?? "");
                const targetPort = Number(params.targetPort ?? 8080);
                const healthPath = String(params.healthPath ?? "/");
                const sourceRoot =
                  process.env.OPENCLAW_CODING_WORKSPACE ??
                  "/state/openclaw/workspaces/coding-agent";
                const sourcePath = path.resolve(sourceRoot, projectPath);
                const output = await runControlledCommand("/usr/local/bin/deploy-agent-to-aca", [
                  "--source",
                  sourcePath,
                  "--name",
                  appName,
                  "--target-port",
                  String(targetPort),
                  "--health-path",
                  healthPath,
                ]);
                return textResult(output);
              },
            }
          : null,
      { name: "deploy_to_aca", optional: true },
    );
    api.registerTool(
      (ctx) =>
        ctx.agentId === "save-agent"
          ? {
              name: "package_agent_workspaces",
              label: "Package Agent Workspaces",
              description: "Create a downloadable ZIP of coding-agent and testing-agent files.",
              parameters: Type.Object({}, { additionalProperties: false }),
              execute: async () =>
                textResult(await runControlledCommand("/usr/local/bin/save-agent-pack")),
            }
          : null,
      { name: "package_agent_workspaces", optional: true },
    );
    api.registerHttpRoute({
      path: ROUTE_PREFIX,
      auth: "gateway",
      match: "prefix",
      handler: async (req, res) => {
        if (req.method !== "GET") {
          res.statusCode = 405;
          res.setHeader("Allow", "GET");
          res.end();
          return true;
        }

        const artifactsRoot = process.env.OPENCLAW_ARTIFACTS_ROOT ?? "/state/openclaw/artifacts";
        const artifactPath = resolveArtifactPath(req.url ?? "", artifactsRoot);
        if (!artifactPath) {
          res.statusCode = 404;
          res.end();
          return true;
        }

        let stats: fs.Stats;
        try {
          stats = await fs.promises.stat(artifactPath);
        } catch {
          res.statusCode = 404;
          res.end();
          return true;
        }
        if (!stats.isFile()) {
          res.statusCode = 404;
          res.end();
          return true;
        }

        res.statusCode = 200;
        res.setHeader("Content-Type", "application/zip");
        res.setHeader("Content-Length", String(stats.size));
        res.setHeader("Content-Disposition", `attachment; filename="${path.basename(artifactPath)}"`);
        res.setHeader("Cache-Control", "private, no-store");
        await new Promise<void>((resolve, reject) => {
          const stream = fs.createReadStream(artifactPath);
          stream.on("error", reject);
          res.on("finish", resolve);
          stream.pipe(res);
        });
        return true;
      },
    });
  },
});