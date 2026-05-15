"""Rosh Creatives autonomous agent - daily run.

Steps each run:
  1. pitcher.py   - Gemini generates personalised email pitches for new leads
  2. outreach.py  - builds outreach.csv, auto-approves up to cap, sends approved rows
  3. replier.py   - checks inbox, classifies replies, auto-sends routine ones,
                    drafts complex ones

Usage:
    python agent.py
    python agent.py --skip-pitch
    python agent.py --replies-only
    python agent.py --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from config import AGENT

ROOT = Path(__file__).parent


def run(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pitch", action="store_true")
    ap.add_argument("--replies-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-pitches", type=int, default=AGENT["max_pitches_per_day"])
    ap.add_argument("--max-replies", type=int, default=AGENT["max_auto_replies_per_day"])
    args = ap.parse_args()

    py = sys.executable

    if not args.replies_only:
        if not args.skip_pitch:
            run([py, "pitcher.py", "--limit", str(args.max_pitches * 2)])

        run([py, "outreach.py", "--build"])
        run([py, "outreach.py", "--auto-approve", "--limit", str(args.max_pitches)])

        outreach_cmd = [py, "outreach.py", "--send", "--limit", str(args.max_pitches)]
        if args.dry_run:
            outreach_cmd.append("--dry-run")
        run(outreach_cmd)

    reply_cmd = [py, "replier.py", "--max", str(args.max_replies)]
    if args.dry_run:
        reply_cmd.append("--dry-run")
    run(reply_cmd)

    print("\n[Rosh Agent] Daily run complete.")


if __name__ == "__main__":
    main()
