import csv
import hashlib
import re
import sqlite3

import pytest

from governed_agents.catalog import AGENTS, TOOLS, TOOLS_BY_NAME
from governed_agents.data import build_database
from governed_agents.tools import CHAT_TOP_N, ToolError, resolve_period


def test_the_build_is_deterministic(tmp_path):
    first = build_database(tmp_path / "a.sqlite").read_bytes()
    second = build_database(tmp_path / "b.sqlite").read_bytes()
    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("FY2026", ("2025-11-01", "2026-10-31")),
        ("2026-02", ("2026-02-01", "2026-02-28")),
        ("2024-02", ("2024-02-01", "2024-02-29")),
        ("2026", ("2026-01-01", "2026-12-31")),
    ],
)
def test_periods_expand_correctly(label, expected):
    assert resolve_period(label) == expected


@pytest.mark.parametrize("label", ["FY26", "2026-13", "ontem", "2026-1"])
def test_bad_periods_are_rejected(label):
    with pytest.raises(ToolError):
        resolve_period(label)


def test_arguments_outside_the_schema_never_reach_the_database(conn):
    tool = TOOLS_BY_NAME["parts_top_items"]
    with pytest.raises(ToolError):
        tool.run(conn, {"period": "FY2026", "branch": "Norte'; DROP TABLE part_sale; --"})
    with pytest.raises(ToolError):
        tool.run(conn, {"period": "FY2026", "branch": None, "sql": "SELECT 1"})


def test_the_connection_cannot_write(conn):
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM part_sale")


def test_chat_output_is_cut_and_says_so(conn):
    result = TOOLS_BY_NAME["parts_top_items"].run(conn, {"period": "FY2026", "branch": None})
    assert len(result.rows) == CHAT_TOP_N
    assert result.total_rows > CHAT_TOP_N
    assert result.truncated


def test_csv_holds_every_row(conn, tmp_path):
    tool = TOOLS_BY_NAME["parts_top_items"]
    args = {"period": "FY2026", "branch": None}
    total = tool.run(conn, args).total_rows
    target = tmp_path / "detail.csv"
    assert tool.export_csv(conn, args, target) == total
    with target.open(encoding="utf-8-sig") as output:
        assert len(list(csv.DictReader(output))) == total


def test_a_fiscal_year_equals_its_twelve_months(conn):
    tool = TOOLS_BY_NAME["parts_revenue_by_branch"]

    def revenue(period):
        return sum(row["revenue"] for row in tool.run(conn, {"period": period}).rows)

    months = [f"2025-{m:02d}" for m in (11, 12)] + [f"2026-{m:02d}" for m in range(1, 11)]
    assert revenue("FY2026") == pytest.approx(sum(revenue(month) for month in months), abs=0.5)


# The checks below are a linter for the catalog: they keep the governance rules
# true as tools are added, instead of relying on someone remembering them.


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_sql_is_a_single_read_only_statement(tool):
    sql = tool.sql.strip()
    assert sql.upper().startswith(("SELECT", "WITH"))
    assert ";" not in sql
    assert "{" not in sql and "%s" not in sql


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_description_says_when_not_to_use_the_tool(tool):
    assert "NÃO use" in tool.description
    named = re.findall(r"use ([a-z_]+)", tool.description)
    siblings = [name for name in named if name in TOOLS_BY_NAME and name != tool.name]
    sees_other_agents = tool.agent == "finance"
    assert siblings or sees_other_agents


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_every_column_has_a_label_in_both_languages(tool, conn):
    args = {p.name: None for p in tool.params}
    rows = tool.run(conn, args).rows
    assert rows
    assert len(rows[0]) == len(tool.labels["pt"]) == len(tool.labels["en"])


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_schema_is_strict(tool):
    schema = tool.to_anthropic()["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_every_tool_belongs_to_a_known_agent():
    assert {tool.agent for tool in TOOLS} == {agent.id for agent in AGENTS}
