"""Governed tools: fixed SQL, typed parameters, and a hard limit on chat output.

The model chooses a tool and fills in its parameters. It never writes SQL:
every value is validated here and sent as a bound parameter, so the worst a
bad tool call can do is return an error.
"""

from __future__ import annotations

import calendar
import csv
import json
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from .data import BRANCHES, PART_GROUPS, REFERENCE_DATE

CHAT_TOP_N = 20
EXPORT_LIMIT = 100_000
CURRENT_FISCAL_YEAR = "FY2026"


class ToolError(ValueError):
    """A tool call the model can fix by trying again with other arguments."""


def resolve_period(value: str) -> tuple[str, str]:
    """Expand a period label. The fiscal year runs from November to October."""
    if match := re.fullmatch(r"FY(\d{4})", value):
        year = int(match[1])
        return f"{year - 1}-11-01", f"{year}-10-31"
    if match := re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", value):
        year, month = int(match[1]), int(match[2])
        last_day = calendar.monthrange(year, month)[1]
        return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"
    if re.fullmatch(r"\d{4}", value):
        return f"{value}-01-01", f"{value}-12-31"
    raise ToolError(f"Invalid period {value!r}. Use FY2026, 2026-09 or 2026.")


def _clean_period(value: Any) -> str:
    if value is None:
        return CURRENT_FISCAL_YEAR
    if not isinstance(value, str):
        raise ToolError("period must be a string such as FY2026, 2026-09 or 2026.")
    resolve_period(value)
    return value


def _bind_period(value: str) -> dict[str, Any]:
    start, end = resolve_period(value)
    return {"start": start, "end": end}


def _clean_choice(name: str, choices: tuple[str, ...]) -> Callable[[Any], str | None]:
    def clean(value: Any) -> str | None:
        if value is None or value in choices:
            return value
        raise ToolError(f"Invalid {name} {value!r}. Choose one of {', '.join(choices)}.")

    return clean


def _clean_age(value: Any) -> int:
    if value is None:
        return 30
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 720:
        raise ToolError("min_age_days must be a whole number from 1 to 720.")
    return value


def _bind_age(value: int) -> dict[str, Any]:
    return {"cutoff": (REFERENCE_DATE - timedelta(days=value)).isoformat()}


@dataclass(frozen=True)
class Param:
    name: str
    description: str
    schema: dict[str, Any]
    clean: Callable[[Any], Any]
    bind: Callable[[Any], dict[str, Any]]


PERIOD = Param(
    name="period",
    description="FY2026 (novembro a outubro), 2026-09 (um mês) ou 2026 (ano civil). "
    "Nulo significa o ano fiscal atual.",
    schema={"type": ["string", "null"]},
    clean=_clean_period,
    bind=_bind_period,
)
BRANCH = Param(
    name="branch",
    description="Nome da filial, ou nulo para todas.",
    schema={"type": ["string", "null"], "enum": [*BRANCHES, None]},
    clean=_clean_choice("branch", BRANCHES),
    bind=lambda value: {"branch": value},
)
PART_GROUP = Param(
    name="part_group",
    description="Grupo de peças, ou nulo para todos.",
    schema={"type": ["string", "null"], "enum": [*PART_GROUPS, None]},
    clean=_clean_choice("part_group", PART_GROUPS),
    bind=lambda value: {"part_group": value},
)
MIN_AGE_DAYS = Param(
    name="min_age_days",
    description="Idade mínima em dias. Nulo significa 30.",
    schema={"type": ["integer", "null"]},
    clean=_clean_age,
    bind=_bind_age,
)


@dataclass(frozen=True)
class ToolResult:
    tool: str
    args: dict[str, Any]
    rows: list[dict[str, Any]]
    total_rows: int
    elapsed_ms: float

    @property
    def truncated(self) -> bool:
        return self.total_rows > len(self.rows)

    def to_model_content(self) -> str:
        """The JSON the model reads. It states the cut so nobody treats it as the whole."""
        return json.dumps(
            {
                "rows": self.rows,
                "rows_shown": len(self.rows),
                "total_rows": self.total_rows,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class Tool:
    name: str
    agent: str
    description: str
    params: tuple[Param, ...]
    sql: str
    labels: dict[str, tuple[str, ...]]
    headline: Callable[["ToolResult", str], str]
    triggers: tuple[str, ...] = field(default=())

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    p.name: {**p.schema, "description": p.description} for p in self.params
                },
                "required": [p.name for p in self.params],
                "additionalProperties": False,
            },
        }

    def bind(self, raw_args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        known = {p.name for p in self.params}
        if unknown := sorted(set(raw_args) - known):
            raise ToolError(f"Unknown arguments for {self.name}: {', '.join(unknown)}.")
        clean: dict[str, Any] = {}
        bound: dict[str, Any] = {"today": REFERENCE_DATE.isoformat()}
        for param in self.params:
            clean[param.name] = param.clean(raw_args.get(param.name))
            bound.update(param.bind(clean[param.name]))
        return clean, bound

    def run(
        self,
        conn: sqlite3.Connection,
        raw_args: dict[str, Any],
        top_n: int = CHAT_TOP_N,
    ) -> ToolResult:
        clean, bound = self.bind(raw_args)
        started = time.perf_counter()
        total = conn.execute(f"SELECT COUNT(*) FROM ({self.sql})", bound).fetchone()[0]
        cursor = conn.execute(f"{self.sql} LIMIT :top_n", {**bound, "top_n": top_n})
        rows = [dict(row) for row in cursor]
        elapsed_ms = (time.perf_counter() - started) * 1000
        return ToolResult(self.name, clean, rows, total, elapsed_ms)

    def export_csv(
        self, conn: sqlite3.Connection, raw_args: dict[str, Any], path: Path
    ) -> int:
        """Write every row to a CSV. The chat shows the top; the file holds the rest."""
        result = self.run(conn, raw_args, top_n=EXPORT_LIMIT)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as output:
            if result.rows:
                writer = csv.DictWriter(output, fieldnames=list(result.rows[0]))
                writer.writeheader()
                writer.writerows(result.rows)
        return len(result.rows)


def money(value: float, lang: str) -> str:
    text = f"{value:,.2f}"
    if lang == "pt":
        text = text.replace(",", "§").replace(".", ",").replace("§", ".")
    return f"R$ {text}"
