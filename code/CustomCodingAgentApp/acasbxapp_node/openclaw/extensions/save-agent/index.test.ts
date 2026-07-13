import { describe, expect, it } from "vitest";
import plugin, { resolveArtifactPath } from "./index.js";

describe("save-agent plugin", () => {
  it("resolves only canonical artifact identifiers under the artifact root", () => {
    expect(
      resolveArtifactPath(
        "/api/saveagent/artifacts/0123456789abcdef0123456789abcdef.zip",
        "/state/openclaw/artifacts",
      ),
    ).toBe("/state/openclaw/artifacts/0123456789abcdef0123456789abcdef.zip");
    expect(resolveArtifactPath("/api/saveagent/artifacts/../secret.zip", "/tmp/artifacts")).toBeUndefined();
    expect(resolveArtifactPath("/api/saveagent/artifacts/not-an-id.zip", "/tmp/artifacts")).toBeUndefined();
  });

  it("registers a gateway-authenticated prefix route", () => {
    const routes: Array<Record<string, unknown>> = [];
    const tools: Array<unknown> = [];
    plugin.register({
      registerHttpRoute(route) {
        routes.push(route as unknown as Record<string, unknown>);
      },
      registerTool(tool) {
        tools.push(tool);
      },
    } as Parameters<typeof plugin.register>[0]);

    expect(routes).toHaveLength(1);
    expect(tools).toHaveLength(2);
    expect(routes[0]).toMatchObject({
      path: "/api/saveagent/artifacts/",
      auth: "gateway",
      match: "prefix",
    });
  });

  it("exposes privileged tools only to their owning agents", () => {
    const tools: Array<(ctx: { agentId?: string }) => unknown> = [];
    plugin.register({
      registerHttpRoute() {},
      registerTool(tool) {
        tools.push(tool as (ctx: { agentId?: string }) => unknown);
      },
    } as Parameters<typeof plugin.register>[0]);

    expect(tools[0]({ agentId: "deployment-agent" })).toMatchObject({ name: "deploy_to_aca" });
    expect(tools[0]({ agentId: "coding-agent" })).toBeNull();
    expect(tools[1]({ agentId: "save-agent" })).toMatchObject({
      name: "package_agent_workspaces",
    });
    expect(tools[1]({ agentId: "testing-agent" })).toBeNull();
  });
});