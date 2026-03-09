#!/usr/bin/env python3
"""
auto_run.py  —  Fully automated podcast generation entry point.
No user input required. Full pipeline:
  OpenClaw TrendScout → DeepSearch (SerpAPI) → LLM dialogue generation → TXT output

Run modes:
  1. Single run (default): python auto_run.py
  2. Scheduled mode:       python auto_run.py --schedule 6   (every 6 hours)
  3. Specify episode count: python auto_run.py --count 2    (generate podcasts for top 2 topics)
"""

import os
import sys
import time
import signal
import argparse
import datetime
import traceback
from pathlib import Path

# ── Internal modules ──────────────────────────────────
from trend_scout    import scout_with_fallback, TrendTopic
from deepsearch     import build_knowledge_base
from podcast_generator import (
    generate_podcast_from_topic,
    wait_for_services,
    OLLAMA_BASE_URL,
    MODEL,
)

# ─────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────

OUTPUT_DIR      = Path(os.getenv("PODCAST_OUTPUT_DIR", "./output"))
SCHEDULE_HOURS  = int(os.getenv("SCHEDULE_HOURS", "6"))      # interval in scheduled mode
TOPICS_PER_RUN  = int(os.getenv("TOPICS_PER_RUN", "1"))      # episodes per run (default: top 1)
SKIP_SEARCH     = os.getenv("SKIP_SEARCH", "").lower() in ("1", "true", "yes")

# ─────────────────────────────────────────────
#  Run Report
# ─────────────────────────────────────────────

class RunReport:
    def __init__(self):
        self.start_time = datetime.datetime.now()
        self.succeeded: list[tuple[str, Path]] = []
        self.failed:    list[tuple[str, str]]  = []

    def add_success(self, topic: str, path: Path):
        self.succeeded.append((topic, path))

    def add_failure(self, topic: str, reason: str):
        self.failed.append((topic, reason))

    def summary(self) -> str:
        elapsed = (datetime.datetime.now() - self.start_time).seconds
        lines = [
            "",
            "=" * 55,
            f"  Run Report  ({self.start_time.strftime('%Y-%m-%d %H:%M')})",
            "=" * 55,
            f"  Elapsed: {elapsed // 60}m {elapsed % 60}s",
            f"  Succeeded: {len(self.succeeded)} episode(s)",
            f"  Failed: {len(self.failed)} episode(s)",
        ]
        if self.succeeded:
            lines.append("")
            lines.append("  Generated files:")
            for topic, path in self.succeeded:
                lines.append(f"    ✓  {topic}")
                lines.append(f"       {path.name}")
        if self.failed:
            lines.append("")
            lines.append("  Failures:")
            for topic, reason in self.failed:
                lines.append(f"    ✗  {topic}: {reason[:80]}")
        lines.append("=" * 55)
        return "\n".join(lines)


# ─────────────────────────────────────────────
#  Single-run main logic
# ─────────────────────────────────────────────

def run_once(count: int = None) -> RunReport:
    """
    Execute one full automated podcast generation cycle:
    1. Call OpenClaw trend-scout agent to discover trending topics
    2. Sort by trend score, select top N
    3. For each topic: DeepSearch + LLM dialogue generation → TXT
    """
    report = RunReport()
    n = count or TOPICS_PER_RUN

    banner = f"""
╔══════════════════════════════════════════════════╗
║        AI Podcast Generator — Auto Mode           ║
║  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  Fully automated, no user input  ║
╚══════════════════════════════════════════════════╝
  Pipeline: Trend Scout → Deep Search → LLM Dialogue Generation
  Episodes this run: {n}
"""
    print(banner)

    # ── Step 1: Service health check ──────────────────────
    wait_for_services()

    # ── Step 2: OpenClaw discovers trending topics ──────────────
    print("\n🔭 Step 1: Trend Scout — discovering trending topics")
    print("   Calling OpenClaw gateway (agent: trend-scout)")
    print("   Agent will perform 2 web searches and analyze trends...")

    try:
        topics = scout_with_fallback(top_n=max(n, 3))
        print(f"\n   ✅ Found {len(topics)} trending topics, selecting top {n} by score")
    except Exception as e:
        print(f"\n   ❌ Trend discovery failed: {e}")
        report.add_failure("TrendScout", str(e))
        print(report.summary())
        return report

    # ── Step 3: Generate podcast per topic ────────────────────
    selected = topics[:n]
    for i, trend in enumerate(selected, 1):
        sep = f"\n{'─'*55}"
        print(f"{sep}")
        print(f"  🎙  Topic {i}/{n}: {trend.topic}")
        print(f"  Score: {trend.trend_score}/10  |  Angle: {trend.angle}")
        print(f"  Keywords: {', '.join(trend.keywords)}")
        print(sep)

        try:
            out_path = generate_podcast_from_topic(
                trend,
                skip_search=SKIP_SEARCH,
            )
            report.add_success(trend.topic, out_path)
            print(f"\n  ✅ Done: {out_path.name}")
        except Exception as e:
            err_msg = str(e)
            print(f"\n  ❌ Generation failed: {err_msg}")
            traceback.print_exc()
            report.add_failure(trend.topic, err_msg)

        # Brief pause between topics (rate limiting + OOM prevention)
        if i < n:
            pause = 10
            print(f"\n  ⏸  Waiting {pause}s before next episode...")
            time.sleep(pause)

    print(report.summary())
    return report


# ─────────────────────────────────────────────
#  Scheduled dispatcher
# ─────────────────────────────────────────────

_running = True

def _handle_signal(sig, frame):
    global _running
    print(f"\nReceived signal {sig}, scheduler stopping...")
    _running = False


def run_scheduler(interval_hours: float, count: int = None):
    """
    Scheduled mode: runs run_once() every interval_hours hours.
    Supports graceful shutdown via SIGTERM / SIGINT.
    """
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    interval_sec = int(interval_hours * 3600)
    run_count = 0

    print(f"""
╔══════════════════════════════════════════════════╗
║       AI Podcast Scheduler — Started              ║
║       Interval: every {interval_hours:.1f} hour(s)              
║       Press Ctrl+C to stop gracefully             ║
╚══════════════════════════════════════════════════╝
""")

    while _running:
        run_count += 1
        print(f"\n[scheduler] Round {run_count} started  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            run_once(count=count)
        except Exception as e:
            print(f"[scheduler] Exception in this round (caught, continuing): {e}")
            traceback.print_exc()

        if not _running:
            break

        next_run = datetime.datetime.now() + datetime.timedelta(seconds=interval_sec)
        print(f"\n[scheduler] Next run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"            (waiting {interval_hours:.1f} hour(s), Ctrl+C to exit early)")

        # segmented sleep, allows mid-wait SIGTERM response
        waited = 0
        while waited < interval_sec and _running:
            chunk = min(30, interval_sec - waited)
            time.sleep(chunk)
            waited += chunk

    print("\n[scheduler] Stopped. Goodbye!")


# ─────────────────────────────────────────────
#  CLI entry point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI Podcast Generator — fully automated, no user input required",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Run modes:
  # Single run, generate 1 episode (default)
  python auto_run.py

  # Single run, generate podcasts for top 2 trending topics
  python auto_run.py --count 2

  # Scheduled mode, run every 6 hours (recommended for Docker)
  python auto_run.py --schedule 6

  # Skip DeepSearch (testing / conserve SerpAPI quota)
  python auto_run.py --no-search

  # Show current trends only (no podcast generation)
  python auto_run.py --scout-only
        """,
    )
    parser.add_argument(
        "--schedule", "-s", type=float, metavar="HOURS",
        help="Scheduled mode: run every N hours (default: single run)"
    )
    parser.add_argument(
        "--count", "-n", type=int, default=TOPICS_PER_RUN,
        help=f"Number of podcast episodes per run (default: {TOPICS_PER_RUN})"
    )
    parser.add_argument(
        "--no-search", dest="no_search", action="store_true",
        help="Skip SerpAPI DeepSearch (for testing)"
    )
    parser.add_argument(
        "--scout-only", action="store_true",
        help="Run trend scouting only, do not generate podcast script"
    )
    args = parser.parse_args()

    if args.no_search:
        os.environ["SKIP_SEARCH"] = "1"
        global SKIP_SEARCH
        SKIP_SEARCH = True

    if args.scout_only:
        print("\n🔭 Running OpenClaw TrendScout only (--scout-only mode)")
        wait_for_services()
        topics = scout_with_fallback(top_n=5)
        print(f"\n{'='*55}")
        print("Top 5 trending AI/tech topics:")
        for i, t in enumerate(topics, 1):
            print(f"\n{i}. {t}")
        return

    if args.schedule:
        run_scheduler(interval_hours=args.schedule, count=args.count)
    else:
        report = run_once(count=args.count)
        sys.exit(0 if report.succeeded else 1)


if __name__ == "__main__":
    main()
