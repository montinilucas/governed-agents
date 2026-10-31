from governed_agents.brains import RuleBrain, extract_period
from governed_agents.catalog import AGENTS_BY_ID
from governed_agents.evals import run_evals
from governed_agents.runtime import DENIED, OUT_OF_SCOPE, Assistant


class FixedRouteBrain(RuleBrain):
    """Routes to a chosen agent and then tries to call a chosen tool."""

    def __init__(self, agent_id, tool_name):
        super().__init__()
        self.agent_id = agent_id
        self.tool_name = tool_name

    def route(self, question, agents):
        return self.agent_id

    def answer(self, question, agent, call_tool):
        return call_tool(self.tool_name, {}).content


def test_every_golden_case_passes(conn):
    results = run_evals(conn, RuleBrain())
    failures = [f"{r.id}: {r.reason}" for r in results if not r.passed]
    assert not failures


def test_the_runtime_adds_the_csv_offer_when_a_list_is_cut(conn):
    turn = Assistant(conn, RuleBrain()).ask("Quais os itens mais vendidos no FY2026?")
    assert turn.footer.startswith("Mostrando 20 de ")
    assert turn.answer.count("CSV") == 0


def test_no_offer_when_everything_fits(conn):
    turn = Assistant(conn, RuleBrain()).ask("Quanto vendemos de peças por filial no FY2026?")
    assert turn.footer == ""


def test_a_denied_role_never_reaches_a_tool(conn):
    turn = Assistant(conn, RuleBrain(), role="seller").ask("Quanto temos de títulos vencidos?")
    assert turn.denied
    assert turn.tools_called == []
    assert turn.answer == DENIED["pt"].format(agent="Financeiro")


def test_out_of_scope_questions_get_no_tool(conn):
    turn = Assistant(conn, RuleBrain()).ask("Vai chover amanhã?")
    assert turn.agent is None
    assert turn.answer == OUT_OF_SCOPE["pt"]


def test_an_agent_cannot_use_another_agents_tool(conn):
    brain = FixedRouteBrain("parts", "receivables_aging")
    turn = Assistant(conn, brain, role="manager").ask("qualquer coisa")
    assert turn.tools_called == []
    assert turn.trace[-1].kind == "tool_rejected"


def test_an_unknown_agent_is_treated_as_out_of_scope(conn):
    turn = Assistant(conn, FixedRouteBrain("hr", "x")).ask("qualquer coisa")
    assert turn.agent is None


def test_the_trace_records_what_ran(conn):
    turn = Assistant(conn, RuleBrain()).ask("Qual o ranking de técnicos no FY2026?")
    call = next(step for step in turn.trace if step.kind == "tool_call")
    assert call.detail["tool"] == "service_technician_ranking"
    assert call.detail["args"]["period"] == "FY2026"
    assert call.detail["total_rows"] >= call.detail["rows_shown"]


def test_english_headlines_exist_for_every_tool(conn):
    for agent in AGENTS_BY_ID.values():
        for tool in agent.tools:
            result = tool.run(conn, {p.name: None for p in tool.params})
            assert tool.headline(result, "en")
            assert tool.headline(result, "pt")


def test_period_extraction():
    assert extract_period("no FY2025") == "FY2025"
    assert extract_period("em março de 2026") == "2026-03"
    assert extract_period("em 2025") == "2025"
    assert extract_period("sem data") == "FY2026"
