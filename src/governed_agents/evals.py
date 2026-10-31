"""Score a brain against the golden questions in ``evals/golden.json``.

Each case checks the things that can go wrong before the wording of an answer
matters: the right agent, the right tool, the right arguments, and a refusal
when the role or the subject does not allow an answer.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .brains import Brain
from .runtime import Assistant

GOLDEN_PATH = Path(__file__).resolve().parents[2] / "evals" / "golden.json"


@dataclass(frozen=True)
class CaseResult:
    id: str
    passed: bool
    reason: str


def load_cases(path: Path = GOLDEN_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def score_case(case: dict[str, Any], conn: sqlite3.Connection, brain: Brain) -> CaseResult:
    assistant = Assistant(conn, brain, role=case.get("role", "manager"))
    turn = assistant.ask(case["question"])

    if turn.agent != case.get("agent"):
        return CaseResult(case["id"], False, f"agent {turn.agent!r}, expected {case.get('agent')!r}")
    if turn.denied != case.get("denied", False):
        return CaseResult(case["id"], False, f"denied={turn.denied}")
    if "tool" not in case:
        return CaseResult(case["id"], not turn.tools_called, "no tool expected")
    if turn.tools_called != [case["tool"]]:
        return CaseResult(case["id"], False, f"tools {turn.tools_called}, expected {case['tool']}")

    got = turn.last_result.args if turn.last_result else {}
    for name, expected in case.get("args", {}).items():
        if got.get(name) != expected:
            return CaseResult(case["id"], False, f"{name}={got.get(name)!r}, expected {expected!r}")
    return CaseResult(case["id"], True, "ok")


def run_evals(conn: sqlite3.Connection, brain: Brain) -> list[CaseResult]:
    return [score_case(case, conn, brain) for case in load_cases()]
