"""Record real runs of the assistant as JSON for the portfolio site's demo.

The site replays these traces. They come from this code running offline, so
what a visitor sees is what the repository does, not a mock-up.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from governed_agents.brains import RuleBrain
from governed_agents.catalog import AGENTS_BY_ID, TOOLS_BY_NAME
from governed_agents.data import build_database, connect_read_only
from governed_agents.runtime import DENIED, OUT_OF_SCOPE, TRUNCATED, Assistant

ROWS_ON_SCREEN = 5

CASES = (
    (
        "Quais OSs estão abertas há 30 dias ou mais?",
        "Which work orders have been open for 30 days or more?",
        "manager",
    ),
    (
        "Quais os itens mais vendidos na filial Sul no FY2026?",
        "What were the best-selling items at the Sul branch in FY2026?",
        "manager",
    ),
    (
        "Qual a margem de cada vendedor no FY2026?",
        "What is each seller's margin in FY2026?",
        "manager",
    ),
    (
        "Quanto temos de títulos vencidos a receber?",
        "How much do we have in overdue receivables?",
        "seller",
    ),
    ("Vai chover amanhã?", "Will it rain tomorrow?", "manager"),
)


def record(conn, question_pt: str, question_en: str, role: str) -> dict:
    turn = Assistant(conn, RuleBrain("pt"), role=role).ask(question_pt)
    agent = AGENTS_BY_ID.get(turn.agent) if turn.agent else None
    case = {
        "role": role,
        "question": {"pt": question_pt, "en": question_en},
        "agent": agent.name if agent else None,
        "denied": turn.denied,
        "tool": None,
    }
    if turn.denied:
        case["answer"] = {
            lang: DENIED[lang].format(agent=agent.name[lang]) for lang in ("pt", "en")
        }
        return case
    if turn.last_result is None:
        case["answer"] = OUT_OF_SCOPE
        return case

    result = turn.last_result
    tool = TOOLS_BY_NAME[result.tool]
    case.update(
        tool=tool.name,
        args={key: value for key, value in result.args.items() if value is not None},
        sql=" ".join(tool.sql.split()),
        labels=tool.labels,
        columns=list(result.rows[0]),
        rows=[list(row.values()) for row in result.rows[:ROWS_ON_SCREEN]],
        rows_shown=len(result.rows),
        total_rows=result.total_rows,
        answer={lang: tool.headline(result, lang) for lang in ("pt", "en")},
        footer={
            lang: TRUNCATED[lang].format(shown=len(result.rows), total=result.total_rows)
            for lang in ("pt", "en")
        }
        if result.truncated
        else None,
    )
    return case


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo-traces.json")
    with TemporaryDirectory() as folder:
        conn = connect_read_only(build_database(Path(folder) / "agrodemo.sqlite"))
        cases = [record(conn, *case) for case in CASES]
        conn.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(cases)} traces written to {target}")


if __name__ == "__main__":
    main()
