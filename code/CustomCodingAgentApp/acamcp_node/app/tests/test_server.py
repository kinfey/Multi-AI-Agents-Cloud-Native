import asyncio
import unittest
from typing import Any

from app.config import Settings
from app.gateway_client import AgentTurn, TokenUsage
from app.server import build_server, _WIPE_MARKER


class FakeGatewayClient:
    def __init__(self, health: dict[str, Any] | None = None) -> None:
        self._health = health or {"ok": True, "status": "live"}
        self.calls: list[tuple[str, str, str | None]] = []
        self.raise_on_run: Exception | None = None
        self.downloads: list[tuple[str, str]] = []

    async def run_agent_turn(
        self, agent_id: str, prompt: str, session_key: str | None = None
    ) -> AgentTurn:
        if self.raise_on_run is not None:
            raise self.raise_on_run
        self.calls.append((agent_id, prompt, session_key))
        # The deterministic pre-run wipe drives the coding-agent with a
        # housekeeping prompt and confirms the WORKSPACES_WIPED marker; echo it.
        if _WIPE_MARKER in prompt:
            return AgentTurn(content=_WIPE_MARKER, usage=TokenUsage(0, 0, 0))
        content = f"[{agent_id}] handled {len(prompt)} chars"
        # The deployment-agent now runs a split build->poll flow: the build turn
        # only submits the build; the `deploy-finish` poll turn is what reports
        # the DEPLOYED_URL. Emit a URL only in response to the finish prompt.
        if agent_id == "deployment-agent":
            if "Finish the deployment" in prompt:
                content += "\nDEPLOYED_URL=https://app.example.com"
            else:
                content += "\nBUILD_SUBMITTED"
        # The testing-agent must end with a TESTS_PASSED verdict or the review
        # gate loops back to the coding-agent; emit a passing verdict by default.
        if agent_id == "testing-agent":
            content += "\nTESTS_PASSED"
        return AgentTurn(
            content=content,
            usage=TokenUsage(prompt=10, completion=5, total=15),
        )

    async def run_agent(self, agent_id: str, prompt: str, session_key: str | None = None) -> str:
        turn = await self.run_agent_turn(agent_id, prompt, session_key=session_key)
        return turn.content

    async def download_artifact(self, url: str, dest_dir: str) -> str:
        self.downloads.append((url, dest_dir))
        return f"{dest_dir}/artifact.zip"

    async def health(self) -> dict[str, Any]:
        return self._health


SETTINGS = Settings(
    gateway_base_url="https://gw.example.com",
    gateway_token="secret-token",
    request_timeout_seconds=30,
    agents=(
        "requirements-agent",
        "coding-agent",
        "testing-agent",
        "deployment-agent",
        "save-agent",
    ),
    host="127.0.0.1",
    port=8000,
    local_save_dir="/tmp/openclaw-prototypes",
)


def _run(coro):
    return asyncio.run(coro)


async def _call_tool(mcp, name: str, arguments: dict[str, Any]):
    result = await mcp.call_tool(name, arguments)
    return result[1]


class GeneratePrototypeTests(unittest.TestCase):
    def setUp(self) -> None:
        # Health-check must not hit the network in unit tests; default to healthy.
        import app.server as server_module

        self._server_module = server_module
        self._orig_url_healthy = server_module._url_healthy
        # Don't actually sleep between deploy polls in tests.
        self._orig_poll_delay = server_module._DEPLOY_POLL_DELAY_S
        server_module._DEPLOY_POLL_DELAY_S = 0  # type: ignore[assignment]

        async def _fake_healthy(url: str):
            return True, ""

        server_module._url_healthy = _fake_healthy  # type: ignore[assignment]

    def tearDown(self) -> None:
        self._server_module._url_healthy = self._orig_url_healthy  # type: ignore[assignment]
        self._server_module._DEPLOY_POLL_DELAY_S = self._orig_poll_delay  # type: ignore[assignment]

    def test_runs_all_agents_in_order(self) -> None:
        client = FakeGatewayClient()
        mcp = build_server(client, SETTINGS)

        structured = _run(_call_tool(mcp, "generate_prototype", {"requirement": "Build a CLI calculator"}))

        called_agents = [c[0] for c in client.calls]
        self.assertEqual(
            called_agents,
            [
                "coding-agent",  # deterministic pre-run workspace wipe
                "requirements-agent",
                "coding-agent",
                "testing-agent",
                "deployment-agent",  # split build turn
                "deployment-agent",  # deploy-finish poll turn (reports URL)
                "save-agent",
            ],
        )
        self.assertEqual(len(structured["artifacts"]), 5)
        self.assertEqual(structured["summary"]["requirement"], "Build a CLI calculator")
        # Per-agent and total token usage are surfaced in the summary.
        self.assertEqual(structured["summary"]["total_tokens"]["total"], 90)
        self.assertEqual(structured["summary"]["stages"][0]["tokens"]["total"], 15)
        self.assertIn("description", structured["summary"]["stages"][0])
        # A shared session key is used for all agent calls.
        session_keys = {c[2] for c in client.calls}
        self.assertEqual(len(session_keys), 1)

    def test_downloads_to_local_folder_when_save_agent_emits_url(self) -> None:
        client = FakeGatewayClient()

        async def run_agent_turn(agent_id, prompt, session_key=None):
            client.calls.append((agent_id, prompt, session_key))
            if _WIPE_MARKER in prompt:
                return AgentTurn(content=_WIPE_MARKER, usage=TokenUsage(0, 0, 0))
            content = f"[{agent_id}] ok"
            if agent_id == "deployment-agent":
                content += "\nDEPLOYED_URL=https://app.example.com"
            if agent_id == "testing-agent":
                content += "\nTESTS_PASSED"
            if agent_id == "save-agent":
                content = (
                    "DOWNLOAD_URL=https://gw.example.com/api/saveagent/artifacts/"
                    + "a" * 32
                    + ".zip"
                )
            return AgentTurn(content=content, usage=TokenUsage(1, 1, 2))

        client.run_agent_turn = run_agent_turn  # type: ignore[assignment]
        mcp = build_server(client, SETTINGS)

        structured = _run(_call_tool(mcp, "generate_prototype", {"requirement": "Build an app"}))

        self.assertTrue(structured["summary"]["download_url"].endswith(".zip"))
        # No output_dir given -> falls back to the configured local save dir.
        self.assertEqual(len(client.downloads), 1)
        self.assertEqual(client.downloads[0][1], "/tmp/openclaw-prototypes")
        self.assertEqual(structured["summary"]["output_dir"], "/tmp/openclaw-prototypes")

    def test_prior_artifacts_flow_into_later_prompts(self) -> None:
        client = FakeGatewayClient()
        mcp = build_server(client, SETTINGS)

        _run(_call_tool(mcp, "generate_prototype", {"requirement": "Build an API"}))

        # calls[0] is the deterministic wipe (coding-agent); real stages follow.
        coding_prompt = client.calls[2][1]
        self.assertIn("Approved prior artifacts", coding_prompt)
        self.assertIn("requirements-agent", coding_prompt)

        # calls[4] is the deployment build turn, calls[5] the deploy-finish poll.
        deployment_prompt = client.calls[4][1]
        self.assertIn("Deployment Agent", deployment_prompt)
        self.assertIn("deploy-build", deployment_prompt)
        self.assertIn("BUILD_SUBMITTED", deployment_prompt)

        save_prompt = client.calls[6][1]
        self.assertIn("save-pack", save_prompt)
        self.assertIn("DOWNLOAD_URL", save_prompt)
        self.assertIn("deployment-agent", save_prompt)

    def test_rejects_empty_requirement(self) -> None:
        client = FakeGatewayClient()
        mcp = build_server(client, SETTINGS)

        with self.assertRaises(Exception):
            _run(_call_tool(mcp, "generate_prototype", {"requirement": "   "}))
        self.assertEqual(client.calls, [])

    def test_deploy_polls_until_url_appears(self) -> None:
        client = FakeGatewayClient()
        # The deploy-finish poll reports STILL_DEPLOYING twice, then a URL.
        state = {"finish_calls": 0}

        async def run_agent_turn(agent_id, prompt, session_key=None):
            client.calls.append((agent_id, prompt, session_key))
            if _WIPE_MARKER in prompt:
                return AgentTurn(content=_WIPE_MARKER, usage=TokenUsage(0, 0, 0))
            content = f"[{agent_id}] ok"
            if agent_id == "testing-agent":
                content += "\nTESTS_PASSED"
            if agent_id == "deployment-agent":
                if "Finish the deployment" in prompt:
                    state["finish_calls"] += 1
                    if state["finish_calls"] >= 3:
                        content += "\nDEPLOYED_URL=https://recovered.example.com"
                    else:
                        content += "\nSTILL_DEPLOYING"
                else:
                    content += "\nBUILD_SUBMITTED"
            return AgentTurn(content=content, usage=TokenUsage(1, 1, 2))

        client.run_agent_turn = run_agent_turn  # type: ignore[assignment]
        mcp = build_server(client, SETTINGS)

        structured = _run(_call_tool(mcp, "generate_prototype", {"requirement": "Build an app"}))

        # 1 build turn + 3 finish polls (the third reports the URL).
        deploy_calls = [c for c in client.calls if c[0] == "deployment-agent"]
        self.assertEqual(len(deploy_calls), 4)
        self.assertIn("deploy-build", deploy_calls[0][1])
        self.assertIn("deploy-finish", deploy_calls[1][1])
        self.assertEqual(
            structured["summary"]["deployed_url"], "https://recovered.example.com"
        )

    def test_deploy_reports_no_url_when_polls_exhaust(self) -> None:
        from app.server import _DEPLOY_POLL_ATTEMPTS

        client = FakeGatewayClient()

        async def run_agent_turn(agent_id, prompt, session_key=None):
            client.calls.append((agent_id, prompt, session_key))
            if _WIPE_MARKER in prompt:
                return AgentTurn(content=_WIPE_MARKER, usage=TokenUsage(0, 0, 0))
            content = f"[{agent_id}] ok"
            if agent_id == "testing-agent":
                content += "\nTESTS_PASSED"
            if agent_id == "deployment-agent":
                # Never reports a URL: build submits, every finish poll stalls.
                content += (
                    "\nSTILL_DEPLOYING" if "Finish the deployment" in prompt else "\nBUILD_SUBMITTED"
                )
            return AgentTurn(content=content, usage=TokenUsage(1, 1, 2))

        client.run_agent_turn = run_agent_turn  # type: ignore[assignment]
        mcp = build_server(client, SETTINGS)

        structured = _run(_call_tool(mcp, "generate_prototype", {"requirement": "Build an app"}))

        # 1 build turn + all finish polls, none producing a URL.
        deploy_calls = [c for c in client.calls if c[0] == "deployment-agent"]
        self.assertEqual(len(deploy_calls), 1 + _DEPLOY_POLL_ATTEMPTS)
        self.assertIsNone(structured["summary"]["deployed_url"])

    def test_failing_tests_loop_back_to_coding_then_pass(self) -> None:
        client = FakeGatewayClient()
        state = {"test_calls": 0}

        async def run_agent_turn(agent_id, prompt, session_key=None):
            client.calls.append((agent_id, prompt, session_key))
            if _WIPE_MARKER in prompt:
                return AgentTurn(content=_WIPE_MARKER, usage=TokenUsage(0, 0, 0))
            content = f"[{agent_id}] ok"
            if agent_id == "deployment-agent":
                content += (
                    "\nDEPLOYED_URL=https://app.example.com"
                    if "Finish the deployment" in prompt
                    else "\nBUILD_SUBMITTED"
                )
            if agent_id == "testing-agent":
                state["test_calls"] += 1
                # Fail the first round, pass on the second.
                if state["test_calls"] >= 2:
                    content += "\nTESTS_PASSED"
                else:
                    content += "\nTESTS_FAILED: backend /api/groups returns 500"
            return AgentTurn(content=content, usage=TokenUsage(1, 1, 2))

        client.run_agent_turn = run_agent_turn  # type: ignore[assignment]
        mcp = build_server(client, SETTINGS)

        structured = _run(_call_tool(mcp, "generate_prototype", {"requirement": "Build an app"}))

        agents_called = [c[0] for c in client.calls]
        # coding runs: 1 wipe + 1 initial build + 1 fix; testing runs twice.
        self.assertEqual(agents_called.count("coding-agent"), 3)
        self.assertEqual(agents_called.count("testing-agent"), 2)
        # The failure text is fed back into the fix coding turn (index 2: after
        # the wipe turn [0] and the initial build turn [1]).
        second_coding_prompt = [c for c in client.calls if c[0] == "coding-agent"][2][1]
        self.assertIn("TESTS_FAILED", second_coding_prompt)
        self.assertTrue(structured["summary"]["tests_passed"])
        # Order: after the loop, deployment (build + finish poll) then save run.
        self.assertEqual(agents_called.count("deployment-agent"), 2)
        self.assertEqual(agents_called.count("save-agent"), 1)

    def test_unhealthy_deploy_loops_back_to_deployment_then_recovers(self) -> None:
        client = FakeGatewayClient()
        # deployment-agent always emits a URL; the health-check is unhealthy once.
        health_state = {"checks": 0}

        async def fake_healthy(url: str):
            health_state["checks"] += 1
            if health_state["checks"] >= 2:
                return True, ""
            return False, "ConnectError: refused"

        self._server_module._url_healthy = fake_healthy  # type: ignore[assignment]
        mcp = build_server(client, SETTINGS)

        structured = _run(_call_tool(mcp, "generate_prototype", {"requirement": "Build an app"}))

        deploy_calls = [c for c in client.calls if c[0] == "deployment-agent"]
        # build + finish (URL), then after the unhealthy check a redeploy build +
        # finish (URL) before the second health check passes.
        self.assertEqual(len(deploy_calls), 4)
        self.assertIn("NOT reachable", deploy_calls[2][1])
        self.assertEqual(
            structured["summary"]["deployed_url"], "https://app.example.com"
        )

    def test_deployment_refusal_recovers_with_soft_prompt(self) -> None:
        # Obsolete: the forced/soft-prompt retry mechanism was replaced by the
        # split build->poll deploy flow. Retained as a no-op placeholder name so
        # historical references don't dangle; the behaviour is covered by
        # test_deploy_polls_until_url_appears.
        self.skipTest("forced/soft-prompt deploy retry removed in split-deploy flow")


class RunAgentTests(unittest.TestCase):
    def test_run_agent_returns_reply(self) -> None:
        client = FakeGatewayClient()
        mcp = build_server(client, SETTINGS)

        structured = _run(_call_tool(mcp, "run_agent", {"agent_id": "coding-agent", "message": "hi"}))

        self.assertEqual(structured["agent"], "coding-agent")
        self.assertEqual(structured["tokens"]["total"], 15)
        self.assertEqual(client.calls[0][0], "coding-agent")

    def test_run_agent_rejects_unknown_agent(self) -> None:
        client = FakeGatewayClient()
        mcp = build_server(client, SETTINGS)

        with self.assertRaises(Exception):
            _run(_call_tool(mcp, "run_agent", {"agent_id": "nope", "message": "hi"}))


class HealthAndRegistrationTests(unittest.TestCase):
    def test_check_gateway_health(self) -> None:
        client = FakeGatewayClient(health={"ok": True, "status": "live"})
        mcp = build_server(client, SETTINGS)

        structured = _run(_call_tool(mcp, "check_gateway_health", {}))

        self.assertEqual(structured["gateway"], "https://gw.example.com")
        self.assertEqual(structured["health"], {"ok": True, "status": "live"})

    def test_tools_registered(self) -> None:
        mcp = build_server(FakeGatewayClient(), SETTINGS)
        tool_names = {tool.name for tool in _run(mcp.list_tools())}
        self.assertIn("generate_prototype", tool_names)
        self.assertIn("run_agent", tool_names)
        self.assertIn("check_gateway_health", tool_names)


class ArchiveReportTests(unittest.TestCase):
    def test_report_files_include_requirements_and_testing(self) -> None:
        from app.server import _build_report_files

        results = [
            {"agent": "requirements-agent", "description": "criteria", "content": "AC body", "tokens": {"prompt": 1, "completion": 2, "total": 3}},
            {"agent": "coding-agent", "description": "code", "content": "files", "tokens": {"prompt": 4, "completion": 5, "total": 9}},
            {"agent": "testing-agent", "description": "tests", "content": "TEST body", "tokens": {"prompt": 6, "completion": 7, "total": 13}},
        ]
        files = _build_report_files(results, "Build X", {"prompt": 11, "completion": 14, "total": 25}, "http://gw/x.zip")

        self.assertIn("requirements-agent/ACCEPTANCE_CRITERIA.md", files)
        self.assertIn("testing-agent/TEST_REPORT.md", files)
        self.assertIn("AC body", files["requirements-agent/ACCEPTANCE_CRITERIA.md"])
        self.assertIn("TEST body", files["testing-agent/TEST_REPORT.md"])
        self.assertIn("WORKFLOW_SUMMARY.md", files)
        # coding-agent output is not duplicated as a markdown report.
        self.assertNotIn("coding-agent/CODING.md", files)

    def test_augment_archive_appends_files(self) -> None:
        import os
        import tempfile
        import zipfile
        from app.server import _augment_archive

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "a.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("coding-agent/app.py", "print('hi')")
            _augment_archive(zip_path, {"testing-agent/TEST_REPORT.md": "report"})
            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
                self.assertIn("coding-agent/app.py", names)
                self.assertIn("testing-agent/TEST_REPORT.md", names)
                self.assertEqual(zf.read("testing-agent/TEST_REPORT.md").decode(), "report")


class UrlHealthTests(unittest.TestCase):
    def _run_healthy(self, status: int, body: str):
        import app.server as server_module

        class _Resp:
            status_code = status
            text = body

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return _Resp()

        orig = server_module.httpx.AsyncClient
        server_module.httpx.AsyncClient = _FakeClient  # type: ignore[assignment]
        try:
            return _run(server_module._url_healthy("https://x.example.com"))
        finally:
            server_module.httpx.AsyncClient = orig  # type: ignore[assignment]

    def test_resource_not_found_body_is_unhealthy_even_on_200(self) -> None:
        ok, detail = self._run_healthy(
            200, '{"code":"ResourceNotFound","message":"/ does not exist"}'
        )
        self.assertFalse(ok)
        self.assertIn("ResourceNotFound", detail)

    def test_ordinary_page_is_healthy(self) -> None:
        ok, detail = self._run_healthy(200, "<!doctype html><title>App</title>")
        self.assertTrue(ok)
        self.assertEqual(detail, "")

    def test_error_status_is_unhealthy(self) -> None:
        ok, detail = self._run_healthy(503, "unavailable")
        self.assertFalse(ok)
        self.assertIn("503", detail)


if __name__ == "__main__":
    unittest.main()

