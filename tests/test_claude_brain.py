"""ClaudeBrain against a scripted client: no network, no key, no cost."""

import json
from types import SimpleNamespace

import pytest

from governed_agents.brains import MAX_MODEL_TURNS, ClaudeBrain, ModelRefused, ToolOutcome
from governed_agents.catalog import AGENTS, AGENTS_BY_ID
from governed_agents.runtime import Assistant


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def reply(*content, stop_reason="end_turn", stop_details=None):
    return SimpleNamespace(content=list(content), stop_reason=stop_reason, stop_details=stop_details)


class ScriptedClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **params):
        self.calls.append(params)
        return self._responses.pop(0)


def test_route_reads_the_structured_answer():
    client = ScriptedClient(reply(text(json.dumps({"agent": "service"}))))
    assert ClaudeBrain(client=client).route("OSs abertas?", AGENTS) == "service"
    format_ = client.calls[0]["output_config"]["format"]
    assert format_["schema"]["properties"]["agent"]["enum"][-1] == "none"
    assert client.calls[0]["fallbacks"] == "default"


def test_route_none_means_out_of_scope():
    client = ScriptedClient(reply(text(json.dumps({"agent": "none"}))))
    assert ClaudeBrain(client=client).route("Vai chover?", AGENTS) is None


def test_the_loop_runs_a_tool_and_returns_the_final_text():
    client = ScriptedClient(
        reply(tool_use("t1", "parts_revenue_by_branch", {"period": "FY2026"}), stop_reason="tool_use"),
        reply(text("Peças faturaram bem.")),
    )
    seen = []

    def call_tool(name, args):
        seen.append((name, args))
        return ToolOutcome('{"rows": []}')

    answer = ClaudeBrain(client=client).answer("Quanto?", AGENTS_BY_ID["parts"], call_tool)
    assert answer == "Peças faturaram bem."
    assert seen == [("parts_revenue_by_branch", {"period": "FY2026"})]
    result_message = client.calls[1]["messages"][-1]
    assert result_message["role"] == "user"
    assert result_message["content"][0]["tool_use_id"] == "t1"


def test_parallel_calls_come_back_in_one_user_message():
    client = ScriptedClient(
        reply(
            tool_use("t1", "parts_revenue_by_branch", {"period": "FY2025"}),
            tool_use("t2", "parts_revenue_by_branch", {"period": "FY2026"}),
            stop_reason="tool_use",
        ),
        reply(text("ok")),
    )
    ClaudeBrain(client=client).answer("?", AGENTS_BY_ID["parts"], lambda n, a: ToolOutcome("{}"))
    results = client.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]


def test_a_tool_error_goes_back_to_the_model_flagged():
    client = ScriptedClient(
        reply(tool_use("t1", "parts_revenue_by_branch", {"period": "ontem"}), stop_reason="tool_use"),
        reply(text("Preciso de um período válido.")),
    )
    brain = ClaudeBrain(client=client)
    brain.answer("?", AGENTS_BY_ID["parts"], lambda n, a: ToolOutcome("Invalid period", is_error=True))
    assert client.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_the_loop_has_a_turn_limit():
    endless = [
        reply(tool_use(f"t{i}", "parts_revenue_by_branch", {}), stop_reason="tool_use")
        for i in range(MAX_MODEL_TURNS)
    ]
    client = ScriptedClient(*endless)
    with pytest.raises(RuntimeError, match="model turns"):
        ClaudeBrain(client=client).answer("?", AGENTS_BY_ID["parts"], lambda n, a: ToolOutcome("{}"))


def test_a_refusal_stops_the_turn():
    details = SimpleNamespace(category="cyber", explanation="declined")
    client = ScriptedClient(reply(stop_reason="refusal", stop_details=details))
    with pytest.raises(ModelRefused, match="cyber"):
        ClaudeBrain(client=client).answer("?", AGENTS_BY_ID["parts"], lambda n, a: ToolOutcome("{}"))


def test_strict_tools_are_sent_to_the_model():
    client = ScriptedClient(reply(text("ok")))
    ClaudeBrain(client=client).answer("?", AGENTS_BY_ID["parts"], lambda n, a: ToolOutcome("{}"))
    assert all(tool["strict"] is True for tool in client.calls[0]["tools"])


def test_end_to_end_through_the_runtime(conn):
    client = ScriptedClient(
        reply(text(json.dumps({"agent": "service"}))),
        reply(
            tool_use("t1", "service_aged_orders", {"min_age_days": 60, "branch": None}),
            stop_reason="tool_use",
        ),
        reply(text("Há OSs paradas.")),
    )
    turn = Assistant(conn, ClaudeBrain(client=client)).ask("OSs paradas há 60 dias?")
    assert turn.agent == "service"
    assert turn.tools_called == ["service_aged_orders"]
    assert turn.answer == "Há OSs paradas."
    assert turn.footer.startswith("Mostrando 20 de ")


def test_a_tool_outside_the_agent_is_refused_even_if_the_model_asks(conn):
    client = ScriptedClient(
        reply(text(json.dumps({"agent": "parts"}))),
        reply(tool_use("t1", "receivables_aging", {"branch": None}), stop_reason="tool_use"),
        reply(text("Não tenho essa ferramenta.")),
    )
    turn = Assistant(conn, ClaudeBrain(client=client)).ask("Títulos vencidos?")
    assert turn.tools_called == []
    assert client.calls[2]["messages"][-1]["content"][0]["is_error"] is True
