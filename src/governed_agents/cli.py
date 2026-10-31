"""Command line: build the demo data, ask a question, run the evals."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .brains import Brain, ClaudeBrain, RuleBrain
from .data import build_database, connect_read_only
from .evals import run_evals
from .runtime import Assistant

DEFAULT_DB = Path("data/agrodemo.sqlite")


def make_brain(name: str) -> Brain:
    return ClaudeBrain() if name == "claude" else RuleBrain()


def open_database(path: Path):
    if not path.exists():
        build_database(path)
    return connect_read_only(path)


def cmd_seed(args: argparse.Namespace) -> int:
    print(f"Built {build_database(args.db)}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    assistant = Assistant(open_database(args.db), make_brain(args.brain), role=args.role)
    turn = assistant.ask(args.question)

    for step in turn.trace:
        print(f"[{step.kind}] {step.detail}")
    print(f"\n{turn.answer}")
    if turn.footer:
        print(turn.footer)
    if args.csv and turn.last_result is not None:
        written = assistant.export_last(turn, args.csv)
        print(f"\n{written} rows written to {args.csv}")
    return 0


def cmd_evals(args: argparse.Namespace) -> int:
    results = run_evals(open_database(args.db), make_brain(args.brain))
    for result in results:
        print(f"{'PASS' if result.passed else 'FAIL'}  {result.id}: {result.reason}")
    passed = sum(result.passed for result in results)
    print(f"\n{passed}/{len(results)} cases passed")
    return 0 if passed == len(results) else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="governed-agents", description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("seed", help="build the synthetic database").set_defaults(run=cmd_seed)

    ask = commands.add_parser("ask", help="ask the assistant a question")
    ask.add_argument("question")
    ask.add_argument("--role", choices=["seller", "manager", "director"], default="manager")
    ask.add_argument("--brain", choices=["rules", "claude"], default="rules")
    ask.add_argument("--csv", type=Path, help="write the full detail to this file")
    ask.set_defaults(run=cmd_ask)

    evals = commands.add_parser("evals", help="score a brain against the golden questions")
    evals.add_argument("--brain", choices=["rules", "claude"], default="rules")
    evals.set_defaults(run=cmd_evals)

    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
