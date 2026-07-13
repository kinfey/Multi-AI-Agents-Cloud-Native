import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_download_module():
    script = PROJECT_ROOT / "scripts" / "download-agent-artifact.py"
    spec = importlib.util.spec_from_file_location("download_agent_artifact", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_save_agent_pack_includes_both_workspaces_and_excludes_secrets(tmp_path):
    workspaces = tmp_path / "workspaces"
    artifacts = tmp_path / "artifacts"
    (workspaces / "coding-agent" / "src").mkdir(parents=True)
    (workspaces / "testing-agent" / "tests").mkdir(parents=True)
    (workspaces / "coding-agent" / "src" / "main.py").write_text("print('ok')\n")
    (workspaces / "coding-agent" / ".env").write_text("SECRET=hidden\n")
    (workspaces / "testing-agent" / "tests" / "test_main.py").write_text("def test_ok(): pass\n")

    env = os.environ | {
        "OPENCLAW_WORKSPACES_ROOT": str(workspaces),
        "OPENCLAW_ARTIFACTS_ROOT": str(artifacts),
        "OPENCLAW_GATEWAY_URL": "https://gateway.example",
    }
    result = subprocess.run(
        ["bash", PROJECT_ROOT / "scripts" / "save-agent-pack.sh"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(result.stdout)

    assert payload["downloadUrl"].startswith(
        "https://gateway.example/api/saveagent/artifacts/"
    )
    with ZipFile(payload["archive"]) as archive:
        names = set(archive.namelist())
    assert "coding-agent/src/main.py" in names
    assert "testing-agent/tests/test_main.py" in names
    assert "coding-agent/.env" not in names


def test_download_helper_rejects_unsafe_members_and_opens_insiders(tmp_path):
    module = _load_download_module()

    assert module._safe_member(ZipInfo("coding-agent/main.py"))
    assert not module._safe_member(ZipInfo("../outside.txt"))

    with patch.object(module.shutil, "which", return_value="/usr/local/bin/code-insiders"), patch.object(
        module.subprocess, "run"
    ) as run:
        module._open_in_editor(tmp_path)

    run.assert_called_once_with(
        ["/usr/local/bin/code-insiders", str(tmp_path)], check=True
    )


def test_download_helper_falls_back_to_stable_vscode(tmp_path):
    module = _load_download_module()

    # Insiders CLI missing; stable `code` present.
    def which(name):
        return "/usr/local/bin/code" if name == "code" else None

    with patch.object(module.shutil, "which", side_effect=which), patch.object(
        module.subprocess, "run"
    ) as run:
        module._open_in_editor(tmp_path)

    run.assert_called_once_with(["/usr/local/bin/code", str(tmp_path)], check=True)