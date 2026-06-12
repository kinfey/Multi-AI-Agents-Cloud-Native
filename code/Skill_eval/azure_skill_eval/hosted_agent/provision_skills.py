"""Upload hosted-agent skills to Foundry Skills.

Run from ``hosted_agent/`` or repo root after ``az login``:

    python -m hosted_agent.provision_skills

Required env:
    FOUNDRY_PROJECT_ENDPOINT
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import zipfile
from pathlib import Path

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import CreateSkillVersionFromFilesBody
from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _zip_skill_md(skill_md: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", skill_md.read_text(encoding="utf-8"))
    return buffer.getvalue()


async def _delete_skill_if_exists(project: AIProjectClient, name: str) -> None:
    try:
        await project.beta.skills.delete(name)
    except ResourceNotFoundError:
        return
    except UnicodeDecodeError as e:
        print(f"Warning: delete skill {name!r} returned undecodable response; continuing: {e}")
        return
    print(f"Deleted existing skill {name!r}.")


async def main() -> None:
    load_dotenv()
    root_env = Path(__file__).resolve().parent.parent / ".env"
    web_env = Path(__file__).resolve().parent.parent / "webapp" / ".env"
    if root_env.exists():
        load_dotenv(root_env, override=False)
    if web_env.exists():
        load_dotenv(web_env, override=False)

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not skill_files:
        raise RuntimeError(f"No SKILL.md files found under {SKILLS_DIR}.")

    async with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project,
    ):
        for skill_md in skill_files:
            name = skill_md.parent.name
            print(f"Provisioning skill {name!r} from {skill_md}...")
            await _delete_skill_if_exists(project, name)
            try:
                imported = await project.beta.skills.create_from_files(
                    name,
                    content=CreateSkillVersionFromFilesBody(
                        files=[(f"{name}.zip", _zip_skill_md(skill_md), "application/zip")]
                    ),
                )
                print(f"Imported skill {imported.name!r} (id={imported.skill_id}, version={imported.version}).")
            except UnicodeDecodeError as e:
                print(f"Warning: import skill {name!r} returned undecodable response; continuing: {e}")

        try:
            listed = {skill.name: skill async for skill in project.beta.skills.list()}
        except UnicodeDecodeError as e:
            print(f"Warning: list skills returned undecodable response; skipping verification: {e}")
            return

        for skill_md in skill_files:
            name = skill_md.parent.name
            if name not in listed:
                raise RuntimeError(f"Skill {name!r} was imported but not found in list().")
            print(f"Verified skill {name!r}.")


if __name__ == "__main__":
    asyncio.run(main())
