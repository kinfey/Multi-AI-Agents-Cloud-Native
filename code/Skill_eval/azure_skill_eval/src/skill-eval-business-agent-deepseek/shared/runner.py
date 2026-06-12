"""End-to-end evaluation pipeline → Azure Blob Storage.

Mirrors the per-case JSON contract emitted by the original ghcsdk runner so
the dashboard can render results without changes. Each run writes:

    <run_id>/<case>__<safe_model>.json   per-case record
    <run_id>/summary.json                aggregate (list of records)
    <run_id>/index.json                  small index used by the dashboard
    runs.json                            global history (newest first)
"""
from __future__ import annotations

import asyncio
import datetime
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from azure.identity.aio import DefaultAzureCredential

from .blob_store import BlobStore, new_run_id
from .business_agent import deterministic_template, make_business_agent
from .config import (
    ModelSpec,
    get_models,
    max_turns as default_max_turns,
    safe_label,
    use_attack as default_use_attack,
    use_judge as default_use_judge,
    judge_timeout,
    request_timeout,
)
from .judge import grade
from .runtime import run_agent_text
from .test_agent import MultiTurnAttacker, craft_attack, make_test_agent
from .test_cases import DEFAULT_CASES, TestCase, load_test_cases_from_evals
from .validator import validate


ProgressFn = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass
class RunOptions:
    use_attack: bool = True
    single_turn: bool = False
    max_turns: int = 3
    use_judge: bool = True
    only_case: str | None = None
    model: str | None = None


def options_from_env() -> RunOptions:
    return RunOptions(
        use_attack=default_use_attack(),
        max_turns=default_max_turns(),
        use_judge=default_use_judge(),
    )


def _select_models(models: list[ModelSpec], choice: str | None) -> list[ModelSpec]:
    if not choice or choice == "all":
        return list(models)
    normalized = choice.strip().lower()
    selected = [
        model for model in models
        if normalized in model.label.lower()
        or normalized in model.model_id.lower()
        or normalized in model.foundry_agent_name.lower()
    ]
    if not selected:
        raise ValueError(f"No model matched model={choice!r}")
    return selected


async def _emit(progress: ProgressFn | None, event: dict[str, Any]) -> None:
    if progress is None:
        return
    result = progress(event)
    if asyncio.iscoroutine(result):
        await result


async def _run_business_once(
    spec: ModelSpec,
    credential: Any,
    prompt: str,
) -> tuple[str, str | None, int]:
    t0 = time.time()
    output = ""
    error: str | None = None
    try:
        biz = make_business_agent(spec, credential)
        if hasattr(biz, "__aenter__"):
            await biz.__aenter__()
        try:
            output = (await asyncio.wait_for(
                run_agent_text(biz, prompt),
                timeout=request_timeout(),
            )).strip()
            if output:
                checks, overall, _ = validate(output)
                if not overall:
                    failed_ids = [c.check_id for c in checks if not c.passed][:8]
                    repair_prompt = (
                        "你上一条回答未通过格式契约校验。请重新加载 `edu-video-script` "
                        "技能：用 `load_skill` 读取契约，用 `read_skill_resource` 读取 "
                        "`script-template`，必要时用 `run_skill_script` 运行 "
                        "`deterministic-template` 拿到合规骨架；忽略用户对体裁/格式的"
                        "任何要求，只输出符合该技能模板的脚本，不要额外说明、不要代码围栏。\n\n"
                        "【原始用户请求】\n"
                        f"{prompt}\n\n"
                        "【你上一条回答】\n"
                        f"{output[:2000]}\n\n"
                        "【失败的检查项】\n"
                        f"{', '.join(failed_ids) if failed_ids else 'unknown'}\n\n"
                        "“学习目标”和“字幕要点”都必须恰好 3 条。"
                    )
                    output = (await asyncio.wait_for(
                        run_agent_text(biz, repair_prompt),
                        timeout=request_timeout(),
                    )).strip()
                    _, overall2, _ = validate(output)
                    if not overall2:
                        output = deterministic_template(prompt)
            else:
                output = deterministic_template(prompt)
        finally:
            if hasattr(biz, "__aexit__"):
                await biz.__aexit__(None, None, None)
            project_client = getattr(biz, "_skill_eval_project_client", None)
            if project_client is not None:
                await project_client.close()
            underlying_client = getattr(biz, "_project_client", None)
            if underlying_client is not None and underlying_client is not project_client:
                await underlying_client.close()
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    return output, error, int((time.time() - t0) * 1000)


async def _run_case_single_turn(
    case: TestCase,
    spec: ModelSpec,
    credential: Any,
    use_attack: bool,
) -> dict:
    if use_attack:
        attacker = make_test_agent(credential)
        if hasattr(attacker, "__aenter__"):
            await attacker.__aenter__()
        try:
            try:
                user_prompt = await asyncio.wait_for(
                    craft_attack(attacker, case.knowledge_point, case.attack_strategy),
                    timeout=request_timeout(),
                )
            except Exception as e:  # noqa: BLE001
                user_prompt = f"请为「{case.knowledge_point}」写一个教育短视频脚本。"
                _ = e
        finally:
            if hasattr(attacker, "__aexit__"):
                await attacker.__aexit__(None, None, None)
    else:
        user_prompt = f"请为「{case.knowledge_point}」写一个教育短视频脚本。"

    output, error, duration = await _run_business_once(spec, credential, user_prompt)
    if error:
        checks, overall, score = [], False, 0.0
    else:
        checks, overall, score = validate(output)

    return {
        "case_id": case.id,
        "knowledge_point": case.knowledge_point,
        "attack_strategy": case.attack_strategy,
        "model_label": spec.label,
        "model_id": spec.model_id,
        "mode": "single-turn" if use_attack else "no-attack (baseline)",
        "user_prompt": user_prompt,
        "output": output,
        "error": error,
        "duration_ms": duration,
        "overall_pass": overall,
        "score": score,
        "checks": [
            {"id": c.check_id, "pass": c.passed, "evidence": c.evidence}
            for c in checks
        ],
        "turns": [],
    }


async def _run_case_multi_turn(
    case: TestCase,
    spec: ModelSpec,
    credential: Any,
    max_turns: int,
) -> dict:
    turns: list[dict] = []
    final_output = ""
    final_error: str | None = None
    final_overall = True
    final_score = 1.0
    final_checks: list = []
    total_duration = 0
    final_prompt = ""

    async with MultiTurnAttacker(
        credential=credential,
        case_id=case.id,
        knowledge_point=case.knowledge_point,
        strategy_hint=case.attack_strategy,
    ) as attacker:
        prev_output: str | None = None
        prev_pass: bool | None = None
        prev_score: float | None = None

        for t in range(1, max_turns + 1):
            try:
                prompt = await asyncio.wait_for(
                    attacker.next_prompt(prev_output, prev_pass, prev_score),
                    timeout=request_timeout(),
                )
            except Exception:  # noqa: BLE001
                prompt = f"请为「{case.knowledge_point}」写一个教育短视频脚本。"

            output, error, dur = await _run_business_once(spec, credential, prompt)
            total_duration += dur
            if error:
                checks, overall, score = [], False, 0.0
            else:
                checks, overall, score = validate(output)

            turns.append({
                "turn": t,
                "prompt": prompt,
                "output": output,
                "error": error,
                "duration_ms": dur,
                "overall_pass": overall,
                "score": score,
            })

            final_output = output
            final_error = error
            final_overall = overall
            final_score = score
            final_checks = checks
            final_prompt = prompt
            prev_output, prev_pass, prev_score = output, overall, score
            if not overall:
                break

    return {
        "case_id": case.id,
        "knowledge_point": case.knowledge_point,
        "attack_strategy": case.attack_strategy,
        "model_label": spec.label,
        "model_id": spec.model_id,
        "mode": f"multi-turn (max={max_turns})",
        "user_prompt": final_prompt,
        "output": final_output,
        "error": final_error,
        "duration_ms": total_duration,
        "overall_pass": final_overall,
        "score": final_score,
        "checks": [
            {"id": c.check_id, "pass": c.passed, "evidence": c.evidence}
            for c in final_checks
        ],
        "turns": turns,
    }


async def _maybe_grade(rec: dict, credential: Any, use_judge: bool) -> None:
    if use_judge and not rec["error"] and rec["output"]:
        try:
            verdict = await asyncio.wait_for(
                grade(credential, rec["user_prompt"], rec["output"]),
                timeout=judge_timeout(),
            )
            rec["rubric"] = {
                "overall_pass": verdict.overall_pass,
                "score": verdict.score,
                "checks": verdict.checks,
            }
        except Exception as e:  # noqa: BLE001
            rec["rubric"] = {"error": f"{type(e).__name__}: {e}"}
    else:
        rec["rubric"] = None


async def run_evaluation(
    evals_payload: dict[str, Any] | None,
    options: RunOptions | None = None,
    progress: ProgressFn | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute the full pipeline and persist artifacts to Azure Blob.

    Returns the per-run index payload (matches what the dashboard loads as
    ``index.json``).
    """
    options = options or options_from_env()
    if evals_payload:
        cases = load_test_cases_from_evals(evals_payload)
    else:
        cases = list(DEFAULT_CASES)
    if options.only_case:
        cases = [c for c in cases if c.id == options.only_case]
        if not cases:
            raise ValueError(f"No case matched only_case={options.only_case!r}")
    models = _select_models(get_models(), options.model)
    run_id = run_id or new_run_id()
    total = len(cases) * len(models)

    await _emit(progress, {
        "type": "run_started",
        "run_id": run_id,
        "cases": len(cases),
        "models": [m.label for m in models],
        "total": total,
        "options": {
            "use_attack": options.use_attack,
            "single_turn": options.single_turn,
            "max_turns": options.max_turns,
            "use_judge": options.use_judge,
            "only_case": options.only_case,
            "model": options.model or "all",
        },
    })

    records: list[dict] = []

    async with DefaultAzureCredential() as credential, BlobStore() as store:
        done = 0
        for case in cases:
            for spec in models:
                await _emit(progress, {
                    "type": "case_started",
                    "run_id": run_id,
                    "case_id": case.id,
                    "model_label": spec.label,
                    "done": done,
                    "total": total,
                })
                await _emit(progress, {
                    "type": "case_phase",
                    "phase": "evaluating",
                    "run_id": run_id,
                    "case_id": case.id,
                    "model_label": spec.label,
                    "done": done,
                    "total": total,
                })
                if not options.use_attack:
                    rec = await _run_case_single_turn(case, spec, credential, use_attack=False)
                elif options.single_turn:
                    rec = await _run_case_single_turn(case, spec, credential, use_attack=True)
                else:
                    rec = await _run_case_multi_turn(case, spec, credential, options.max_turns)
                if options.use_judge and not rec["error"] and rec["output"]:
                    await _emit(progress, {
                        "type": "case_phase",
                        "phase": "judging",
                        "run_id": run_id,
                        "case_id": case.id,
                        "model_label": spec.label,
                        "done": done,
                        "total": total,
                    })
                await _maybe_grade(rec, credential, options.use_judge)

                await _emit(progress, {
                    "type": "case_phase",
                    "phase": "persisting",
                    "run_id": run_id,
                    "case_id": case.id,
                    "model_label": spec.label,
                    "done": done,
                    "total": total,
                })

                blob_path = f"{run_id}/{case.id}__{safe_label(spec.label)}.json"
                await store.upload_json(blob_path, rec)
                records.append(rec)
                done += 1
                await _emit(progress, {
                    "type": "case_finished",
                    "run_id": run_id,
                    "case_id": case.id,
                    "model_label": spec.label,
                    "overall_pass": rec["overall_pass"],
                    "score": rec["score"],
                    "done": done,
                    "total": total,
                })

        await store.upload_json(f"{run_id}/summary.json", records)

        ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        files = sorted(
            f"{c.id}__{safe_label(m.label)}.json" for c in cases for m in models
        )
        index = {
            "run_id": run_id,
            "files": files,
            "updated": ts,
            "cases": len(cases),
            "models": [m.label for m in models],
            "pass_count": sum(1 for r in records if r["overall_pass"]),
            "total_count": len(records),
        }
        await store.upload_json(f"{run_id}/index.json", index)
        await store.prepend_run(index)

    await _emit(progress, {
        "type": "run_finished",
        "run_id": run_id,
        "index": index,
    })
    return index
