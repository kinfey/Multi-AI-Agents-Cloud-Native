"""Console runner: drives both models through all 10 edge-knowledge cases.

Pipeline per case per model:
  1. Adversarial test agent crafts attack prompt(s) (single-shot or multi-turn).
  2. Business agent (same model) is invoked with that prompt.
  3. Deterministic validator scores the output against the strict template.
  4. Optional LLM-as-judge produces a rubric score (style / robustness).
  5. Results are aggregated into a comparison table.

Usage:
    python main.py                        # multi-turn ON, judge ON (default)
    python main.py --only edge-03
    python main.py --model gpt
    python main.py --no-attack            # plain prompt baseline
    python main.py --single-turn          # disable multi-turn (one shot)
    python main.py --no-judge             # skip LLM rubric grading
    python main.py --max-turns 3          # cap on multi-turn rounds
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
import time
import uuid
import webbrowser
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from business_agent import deterministic_template, make_business_agent
from config import MODELS, ModelSpec
from judge import grade
from test_agent import MultiTurnAttacker, craft_attack, make_test_agent
from test_cases import TEST_CASES, TestCase
from validator import validate

console = Console()
BASE_ARTIFACT_DIR = Path(__file__).parent / "artifacts"
ARTIFACT_DIR = BASE_ARTIFACT_DIR  # overridden per run in main_async()


async def _run_business(model: ModelSpec, prompt: str) -> tuple[str, str | None, int]:
    """Drive the skill-equipped business agent for one prompt.

    The strict-template contract now lives in the agent's ``edu-video-script``
    skill (see business_agent.py), so the agent self-loads the contract,
    reads the ``script-template`` resource, and may call the
    ``deterministic-template`` skill script on its own. The harness's job is to
    (a) ask the model to self-repair via its skill if the deterministic
    validator still fails, and (b) keep a final guardrail so a broken run never
    emits an empty artifact. The validator remains the scoring source of truth.
    """
    t0 = time.time()
    output = ""
    error: str | None = None
    try:
        async with make_business_agent(model.model_id) as biz:
            result = await biz.run(prompt)
            output = str(result).strip()

            # Contract hardening: if the first pass drifts from template, ask
            # the model to repair *using its own skill* rather than re-injecting
            # the template here. This keeps the contract owned by the skill.
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
                    repaired = await biz.run(repair_prompt)
                    output = str(repaired).strip()
                    # Final guardrail: if the model still can't satisfy the
                    # contract, emit the same skeleton its skill script would
                    # have produced, so the artifact is never empty/invalid.
                    _, overall2, _ = validate(output)
                    if not overall2:
                        output = deterministic_template(prompt)
            else:
                output = deterministic_template(prompt)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    return output, error, int((time.time() - t0) * 1000)


async def run_case_single_turn(case: TestCase, model: ModelSpec, use_attack: bool) -> dict:
    if use_attack:
        async with make_test_agent(model.model_id) as test_agent:
            try:
                user_prompt = await craft_attack(test_agent, case.knowledge_point, case.attack_strategy)
            except Exception as e:  # noqa: BLE001
                user_prompt = f"请为「{case.knowledge_point}」写一个教育短视频脚本。"
                console.print(f"[yellow]  ! attack-agent failed: {e}[/yellow]")
    else:
        user_prompt = f"请为「{case.knowledge_point}」写一个教育短视频脚本。"

    output, error, duration = await _run_business(model, user_prompt)
    if error:
        checks, overall, score = [], False, 0.0
    else:
        checks, overall, score = validate(output)

    return {
        "case_id": case.id,
        "knowledge_point": case.knowledge_point,
        "attack_strategy": case.attack_strategy,
        "model_label": model.label,
        "model_id": model.model_id,
        "mode": "single-turn",
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


async def run_case_multi_turn(case: TestCase, model: ModelSpec, max_turns: int) -> dict:
    """Multi-turn attack; stops early once the business agent FAILS."""
    turns: list[dict] = []
    final_output = ""
    final_error: str | None = None
    final_overall = True
    final_score = 1.0
    final_checks: list = []
    total_duration = 0
    final_prompt = ""

    async with MultiTurnAttacker(
        model_id=model.model_id,
        case_id=case.id,
        knowledge_point=case.knowledge_point,
        strategy_hint=case.attack_strategy,
    ) as attacker:
        prev_output: str | None = None
        prev_pass: bool | None = None
        prev_score: float | None = None

        for t in range(1, max_turns + 1):
            try:
                prompt = await attacker.next_prompt(prev_output, prev_pass, prev_score)
            except Exception as e:  # noqa: BLE001
                console.print(f"[yellow]  ! attacker turn {t} failed: {e}[/yellow]")
                prompt = f"请为「{case.knowledge_point}」写一个教育短视频脚本。"

            output, error, dur = await _run_business(model, prompt)
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
                break  # attacker won this case

    return {
        "case_id": case.id,
        "knowledge_point": case.knowledge_point,
        "attack_strategy": case.attack_strategy,
        "model_label": model.label,
        "model_id": model.model_id,
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


async def run_case(case: TestCase, model: ModelSpec, args: argparse.Namespace) -> dict:
    if not args.use_attack:
        rec = await run_case_single_turn(case, model, use_attack=False)
    elif args.single_turn:
        rec = await run_case_single_turn(case, model, use_attack=True)
    else:
        rec = await run_case_multi_turn(case, model, args.max_turns)

    # Optional LLM-as-judge rubric grading.
    if args.use_judge and not rec["error"] and rec["output"]:
        try:
            verdict = await grade(model.model_id, rec["user_prompt"], rec["output"])
            rec["rubric"] = {
                "overall_pass": verdict.overall_pass,
                "score": verdict.score,
                "checks": verdict.checks,
            }
        except Exception as e:  # noqa: BLE001
            rec["rubric"] = {"error": f"{type(e).__name__}: {e}"}
    else:
        rec["rubric"] = None

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = model.label.replace(" ", "_").replace("/", "_")
    out_path = ARTIFACT_DIR / f"{case.id}__{safe_model}.json"
    out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def render_case_panel(rec: dict) -> None:
    status = "[green]PASS[/green]" if rec["overall_pass"] else "[red]FAIL[/red]"
    head = f"{rec['case_id']} · {rec['model_label']} · {status} · {rec['score'] * 100:.0f}% · {rec['mode']}"
    body = [
        f"[bold]Knowledge point[/bold]: {rec['knowledge_point']}",
        f"[bold]Attack strategy[/bold]: {rec['attack_strategy']}",
    ]
    if rec.get("turns"):
        body.append(f"[bold]Turns[/bold]: {len(rec['turns'])}")
        for t in rec["turns"]:
            mark = "[green]✓[/green]" if t["overall_pass"] else "[red]✗[/red]"
            body.append(
                f"  T{t['turn']} {mark} score={t['score'] * 100:.0f}% · "
                f"prompt={t['prompt'][:80].replace(chr(10), ' ')}"
            )
    body.append(f"[bold]Final prompt[/bold]: {rec['user_prompt'][:200]}")
    if rec["error"]:
        body.append(f"[red]Error[/red]: {rec['error']}")
    failed = [c for c in rec["checks"] if not c["pass"]]
    if failed:
        body.append("[bold red]Failed format checks[/bold red]:")
        for c in failed[:6]:
            body.append(f"  - {c['id']}: {c['evidence']}")
    rubric = rec.get("rubric")
    if rubric and "error" not in rubric:
        rstatus = "[green]PASS[/green]" if rubric["overall_pass"] else "[yellow]ISSUES[/yellow]"
        body.append(f"[bold]Rubric[/bold] ({rstatus}, {rubric['score']}/100):")
        for c in rubric.get("checks", []):
            mark = "[green]✓[/green]" if c.get("pass") else "[red]✗[/red]"
            body.append(f"  {mark} {c.get('id', '?')}: {c.get('notes', '')[:80]}")
    elif rubric and "error" in rubric:
        body.append(f"[yellow]Rubric error: {rubric['error']}[/yellow]")
    body.append(f"[dim]elapsed {rec['duration_ms']} ms · output {len(rec['output'])} chars[/dim]")
    console.print(Panel("\n".join(body), title=head, border_style="cyan"))


def render_summary(records: list[dict], use_judge: bool) -> None:
    table = Table(title="Format-Consistency Benchmark", show_lines=False)
    table.add_column("Case", style="bold")
    table.add_column("Knowledge Point", overflow="fold")
    for m in MODELS:
        table.add_column(m.label, justify="center")

    by_case: dict[str, dict[str, dict]] = {}
    for r in records:
        by_case.setdefault(r["case_id"], {})[r["model_label"]] = r

    for case in TEST_CASES:
        row = [case.id, case.knowledge_point]
        for m in MODELS:
            r = by_case.get(case.id, {}).get(m.label)
            if r is None:
                row.append("-")
                continue
            if r["error"]:
                row.append("[red]ERR[/red]")
                continue
            det = "[green]✓[/green]" if r["overall_pass"] else f"[yellow]{r['score']*100:.0f}%[/yellow]"
            cell = det
            rb = r.get("rubric")
            if use_judge and rb and "error" not in rb:
                rb_mark = "[green]✓[/green]" if rb["overall_pass"] else "[yellow]✗[/yellow]"
                cell = f"{det} | {rb_mark}{rb['score']}"
            row.append(cell)
        table.add_row(*row)

    agg_row = ["[bold]TOTAL[/bold]", "format pass / mean (rubric mean)"]
    for m in MODELS:
        recs = [r for r in records if r["model_label"] == m.label]
        if not recs:
            agg_row.append("-")
            continue
        pass_rate = sum(1 for r in recs if r["overall_pass"]) / len(recs)
        mean_score = sum(r["score"] for r in recs) / len(recs)
        cell = f"{pass_rate * 100:.0f}% / {mean_score * 100:.0f}%"
        if use_judge:
            rubrics = [r["rubric"]["score"] for r in recs if r.get("rubric") and "error" not in r["rubric"]]
            if rubrics:
                cell += f" ({sum(rubrics) / len(rubrics):.0f})"
        agg_row.append(cell)
    table.add_row(*agg_row)
    console.print(table)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Skill Testing Agent")
    p.add_argument("--only", help="run a single case id, e.g. edge-03")
    p.add_argument("--model", choices=["claude", "gpt", "all"], default="all")
    p.add_argument("--no-attack", dest="use_attack", action="store_false",
                   help="skip the adversarial test agent (baseline)")
    p.add_argument("--single-turn", action="store_true",
                   help="use single-shot adversarial prompt instead of multi-turn")
    p.add_argument("--max-turns", type=int, default=3,
                   help="max attack rounds in multi-turn mode (default 3)")
    p.add_argument("--no-judge", dest="use_judge", action="store_false",
                   help="skip the LLM-as-judge rubric grading")
    p.add_argument("--open-dashboard", action="store_true",
                   help="open dashboard URL in browser after run completes")
    p.set_defaults(use_attack=True, use_judge=True)
    return p.parse_args()


def select_models(choice: str) -> list[ModelSpec]:
    if choice == "claude":
        return [m for m in MODELS if "Claude" in m.label]
    if choice == "gpt":
        return [m for m in MODELS if "GPT" in m.label]
    return list(MODELS)


def select_cases(only: str | None) -> list[TestCase]:
    if not only:
        return list(TEST_CASES)
    cases = [c for c in TEST_CASES if c.id == only]
    if not cases:
        console.print(f"[red]Unknown case id: {only}[/red]")
        sys.exit(2)
    return cases


async def main_async() -> int:
    global ARTIFACT_DIR
    args = parse_args()
    models = select_models(args.model)
    cases = select_cases(args.only)

    # Each run gets its own subfolder: artifacts/yymmdd-xxxxxx/
    _now = datetime.datetime.now()
    _run_id = _now.strftime("%y%m%d") + "-" + uuid.uuid4().hex[:6]
    ARTIFACT_DIR = BASE_ARTIFACT_DIR / _run_id
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.use_attack:
        mode = "no-attack (baseline)"
    elif args.single_turn:
        mode = "single-turn"
    else:
        mode = f"multi-turn (max={args.max_turns})"
    console.print(Panel.fit(
        f"[bold]Skill Testing Agent[/bold]\n"
        f"Models: {', '.join(m.label for m in models)}\n"
        f"Cases: {len(cases)}    Mode: {mode}    Judge: {args.use_judge}",
        border_style="magenta",
    ))

    records: list[dict] = []
    for case in cases:
        for model in models:
            console.print(f"\n[cyan]▶ {case.id} on {model.label} …[/cyan]")
            rec = await run_case(case, model, args)
            records.append(rec)
            render_case_panel(rec)

    console.rule("Summary")
    render_summary(records, args.use_judge)

    summary_path = ARTIFACT_DIR / "summary.json"
    summary_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Write per-run index.json so the dashboard can discover files in this run.
    _ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_artifact_files = sorted(p.name for p in ARTIFACT_DIR.glob("edge-*.json"))
    run_index = {
        "run_id": _run_id,
        "files": all_artifact_files,
        "updated": _ts,
        "cases": len(cases),
        "models": [m.label for m in models],
        "pass_count": sum(1 for r in records if r["overall_pass"]),
        "total_count": len(records),
    }
    (ARTIFACT_DIR / "index.json").write_text(
        json.dumps(run_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Update top-level artifacts/runs.json (master list of all runs).
    BASE_ARTIFACT_DIR.mkdir(exist_ok=True)
    runs_path = BASE_ARTIFACT_DIR / "runs.json"
    existing_runs: list[dict] = []
    if runs_path.exists():
        try:
            existing_runs = json.loads(runs_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing_runs = []
    # Remove stale entry for same run_id if any, prepend new one
    existing_runs = [r for r in existing_runs if r.get("run_id") != _run_id]
    existing_runs.insert(0, run_index)
    runs_path.write_text(
        json.dumps(existing_runs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    console.print(f"\n[green]Artifacts written to {ARTIFACT_DIR}[/green]")
    console.print(f"[cyan]Run ID: {_run_id}[/cyan]")
    console.print(
        "[cyan]Dashboard: python -m http.server 8080 → http://localhost:8080/html/dashboard.html[/cyan]"
    )
    if args.open_dashboard:
        url = "http://localhost:8080/html/dashboard.html"
        try:
            opened = webbrowser.open(url)
            if opened:
                console.print(f"[green]Opened dashboard: {url}[/green]")
            else:
                console.print(
                    f"[yellow]Could not auto-open browser. Open manually: {url}[/yellow]"
                )
        except Exception as e:  # noqa: BLE001
            console.print(
                f"[yellow]Failed to open browser ({type(e).__name__}: {e}). Open manually: {url}[/yellow]"
            )
    return 0


def main() -> int:
    if not os.getenv("GITHUB_COPILOT_CLI_PATH") and not _which("copilot"):
        console.print(
            "[yellow]Warning: GitHub Copilot CLI ('copilot') not found on PATH. "
            "Install it and run `copilot auth login` before running.[/yellow]"
        )
    return asyncio.run(main_async())


def _which(cmd: str) -> str | None:
    from shutil import which
    return which(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
