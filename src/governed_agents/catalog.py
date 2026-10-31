"""The agents and their tools.

Each description says when to use the tool and when not to, naming the sibling
that owns the other case. Without that second half, similar tools compete and
the model picks between them almost at random.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tools import BRANCH, MIN_AGE_DAYS, PART_GROUP, PERIOD, Tool, ToolResult, money


def _parts_revenue_headline(result: ToolResult, lang: str) -> str:
    total = sum(row["revenue"] for row in result.rows)
    best = result.rows[0]
    if lang == "pt":
        return (
            f"Peças faturaram {money(total, lang)} em {result.args['period']}. "
            f"A filial {best['branch']} lidera com {money(best['revenue'], lang)}."
        )
    return (
        f"Parts revenue was {money(total, lang)} in {result.args['period']}. "
        f"The {best['branch']} branch leads with {money(best['revenue'], lang)}."
    )


def _parts_top_headline(result: ToolResult, lang: str) -> str:
    best = result.rows[0]
    if lang == "pt":
        return (
            f"{result.total_rows} itens tiveram venda em {result.args['period']}. "
            f"O líder é {best['sku']} ({best['description']}), "
            f"com {money(best['revenue'], lang)}."
        )
    return (
        f"{result.total_rows} items sold in {result.args['period']}. "
        f"The top one is {best['sku']} ({best['description']}), "
        f"at {money(best['revenue'], lang)}."
    )


def _parts_stock_headline(result: ToolResult, lang: str) -> str:
    total = sum(row["stock_value"] for row in result.rows)
    if lang == "pt":
        return f"O estoque selecionado soma {money(total, lang)} a custo."
    return f"The selected stock adds up to {money(total, lang)} at cost."


def _aged_orders_headline(result: ToolResult, lang: str) -> str:
    days = result.args["min_age_days"]
    if not result.rows:
        return (
            f"Nenhuma OS aberta há {days} dias ou mais."
            if lang == "pt"
            else f"No work order has been open for {days} days or more."
        )
    oldest = result.rows[0]
    if lang == "pt":
        return (
            f"{result.total_rows} OSs abertas há {days} dias ou mais. "
            f"A mais antiga é a {oldest['order_id']}, da filial {oldest['branch']}, "
            f"com {oldest['age_days']} dias."
        )
    return (
        f"{result.total_rows} work orders have been open for {days} days or more. "
        f"The oldest is {oldest['order_id']}, at the {oldest['branch']} branch, "
        f"open for {oldest['age_days']} days."
    )


def _technician_headline(result: ToolResult, lang: str) -> str:
    best = result.rows[0]
    if lang == "pt":
        return (
            f"{best['technician']} ({best['branch']}) lidera {result.args['period']} "
            f"com {money(best['revenue'], lang)} em {best['orders']} OSs."
        )
    return (
        f"{best['technician']} ({best['branch']}) leads {result.args['period']} "
        f"with {money(best['revenue'], lang)} across {best['orders']} work orders."
    )


def _machine_category_headline(result: ToolResult, lang: str) -> str:
    total = sum(row["revenue"] for row in result.rows)
    best = result.rows[0]
    if lang == "pt":
        return (
            f"Máquinas faturaram {money(total, lang)} em {result.args['period']}. "
            f"A categoria {best['category']} lidera, com {best['units']} unidades."
        )
    return (
        f"Machine revenue was {money(total, lang)} in {result.args['period']}. "
        f"The {best['category']} category leads, with {best['units']} units."
    )


def _margin_headline(result: ToolResult, lang: str) -> str:
    best = result.rows[0]
    if lang == "pt":
        return (
            f"{best['seller']} tem a maior margem de {result.args['period']}: "
            f"{best['margin_pct']:.1f}%".replace(".", ",") + f" sobre {money(best['revenue'], lang)}."
        )
    return (
        f"{best['seller']} has the highest margin in {result.args['period']}: "
        f"{best['margin_pct']:.1f}% on {money(best['revenue'], lang)}."
    )


def _receivables_headline(result: ToolResult, lang: str) -> str:
    total = sum(row["amount"] for row in result.rows)
    worst = result.rows[0]
    if lang == "pt":
        return (
            f"Há {money(total, lang)} vencidos. A faixa {worst['bucket']} "
            f"da filial {worst['branch']} é a maior, com {money(worst['amount'], lang)}."
        )
    return (
        f"{money(total, lang)} is overdue. The {worst['bucket']} bucket "
        f"at the {worst['branch']} branch is the largest, at {money(worst['amount'], lang)}."
    )


TOOLS: tuple[Tool, ...] = (
    Tool(
        name="parts_revenue_by_branch",
        agent="parts",
        description=(
            "Faturamento de peças e número de clientes por filial em um período. "
            "NÃO use para ranking de itens (use parts_top_items) nem para estoque "
            "(use parts_stock_value)."
        ),
        params=(PERIOD,),
        sql="""
            SELECT b.name AS branch,
                   ROUND(SUM(s.quantity * s.unit_price), 2) AS revenue,
                   COUNT(DISTINCT s.customer_id) AS customers
            FROM part_sale s
            JOIN branch b ON b.id = s.branch_id
            WHERE s.sold_on BETWEEN :start AND :end
            GROUP BY b.name
            ORDER BY revenue DESC
        """,
        labels={
            "pt": ("Filial", "Faturamento", "Clientes"),
            "en": ("Branch", "Revenue", "Customers"),
        },
        headline=_parts_revenue_headline,
        triggers=("por filial", "faturamento", "faturou", "faturaram", "vendemos", "venda"),
    ),
    Tool(
        name="parts_top_items",
        agent="parts",
        description=(
            "Ranking de itens de peças por faturamento em um período, uma linha por "
            "SKU e filial. NÃO use para total por filial (use parts_revenue_by_branch)."
        ),
        params=(PERIOD, BRANCH),
        sql="""
            SELECT b.name AS branch,
                   s.sku,
                   p.description,
                   SUM(s.quantity) AS quantity,
                   ROUND(SUM(s.quantity * s.unit_price), 2) AS revenue
            FROM part_sale s
            JOIN branch b ON b.id = s.branch_id
            JOIN part p ON p.sku = s.sku
            WHERE s.sold_on BETWEEN :start AND :end
              AND (:branch IS NULL OR b.name = :branch)
            GROUP BY b.name, s.sku, p.description
            ORDER BY revenue DESC, s.sku
        """,
        labels={
            "pt": ("Filial", "SKU", "Descrição", "Qtde", "Faturamento"),
            "en": ("Branch", "SKU", "Description", "Qty", "Revenue"),
        },
        headline=_parts_top_headline,
        triggers=("mais vendid", "ranking", "top", "itens", "curva"),
    ),
    Tool(
        name="parts_stock_value",
        agent="parts",
        description=(
            "Valor atual do estoque a custo por filial e grupo de peças. "
            "NÃO use para vendas (use parts_revenue_by_branch ou parts_top_items)."
        ),
        params=(PART_GROUP, BRANCH),
        sql="""
            SELECT b.name AS branch,
                   p.part_group,
                   SUM(k.quantity) AS quantity,
                   ROUND(SUM(k.quantity * p.unit_cost), 2) AS stock_value
            FROM part_stock k
            JOIN branch b ON b.id = k.branch_id
            JOIN part p ON p.sku = k.sku
            WHERE (:part_group IS NULL OR p.part_group = :part_group)
              AND (:branch IS NULL OR b.name = :branch)
            GROUP BY b.name, p.part_group
            ORDER BY stock_value DESC
        """,
        labels={
            "pt": ("Filial", "Grupo", "Qtde", "Valor em estoque"),
            "en": ("Branch", "Group", "Qty", "Stock value"),
        },
        headline=_parts_stock_headline,
        triggers=("estoque", "saldo"),
    ),
    Tool(
        name="service_aged_orders",
        agent="service",
        description=(
            "Ordens de serviço ainda abertas após uma idade mínima, das mais antigas para as mais novas. "
            "NÃO use para desempenho de técnicos (use service_technician_ranking)."
        ),
        params=(MIN_AGE_DAYS, BRANCH),
        sql="""
            SELECT b.name AS branch,
                   o.id AS order_id,
                   t.name AS technician,
                   o.opened_on,
                   CAST(julianday(:today) - julianday(o.opened_on) AS INTEGER) AS age_days,
                   o.revenue
            FROM service_order o
            JOIN branch b ON b.id = o.branch_id
            JOIN technician t ON t.id = o.technician_id
            WHERE o.closed_on IS NULL
              AND o.opened_on <= :cutoff
              AND (:branch IS NULL OR b.name = :branch)
            ORDER BY o.opened_on, o.id
        """,
        labels={
            "pt": ("Filial", "OS", "Técnico", "Abertura", "Dias", "Valor"),
            "en": ("Branch", "Order", "Technician", "Opened", "Days", "Value"),
        },
        headline=_aged_orders_headline,
        triggers=("aberta", "abertas", "parada", "paradas", "atrasada", "dias"),
    ),
    Tool(
        name="service_technician_ranking",
        agent="service",
        description=(
            "Faturamento e ordens de serviço encerradas por técnico em um período. "
            "NÃO use para OSs em aberto (use service_aged_orders)."
        ),
        params=(PERIOD, BRANCH),
        sql="""
            SELECT b.name AS branch,
                   t.name AS technician,
                   COUNT(*) AS orders,
                   ROUND(SUM(o.revenue), 2) AS revenue
            FROM service_order o
            JOIN branch b ON b.id = o.branch_id
            JOIN technician t ON t.id = o.technician_id
            WHERE o.closed_on BETWEEN :start AND :end
              AND (:branch IS NULL OR b.name = :branch)
            GROUP BY b.name, t.name
            ORDER BY revenue DESC, t.name
        """,
        labels={
            "pt": ("Filial", "Técnico", "OSs", "Faturamento"),
            "en": ("Branch", "Technician", "Orders", "Revenue"),
        },
        headline=_technician_headline,
        triggers=("tecnico", "tecnicos", "ranking", "desempenho", "faturou"),
    ),
    Tool(
        name="machine_sales_by_category",
        agent="machines",
        description=(
            "Faturamento e unidades de máquinas por categoria em um período. "
            "NÃO use para margem ou vendedor (use machine_margin_by_seller)."
        ),
        params=(PERIOD, BRANCH),
        sql="""
            SELECT s.category,
                   COUNT(*) AS units,
                   ROUND(SUM(s.revenue), 2) AS revenue
            FROM machine_sale s
            JOIN branch b ON b.id = s.branch_id
            WHERE s.sold_on BETWEEN :start AND :end
              AND (:branch IS NULL OR b.name = :branch)
            GROUP BY s.category
            ORDER BY revenue DESC
        """,
        labels={
            "pt": ("Categoria", "Unidades", "Faturamento"),
            "en": ("Category", "Units", "Revenue"),
        },
        headline=_machine_category_headline,
        triggers=("categoria", "faturamento", "faturou", "vendemos", "venda", "maquina"),
    ),
    Tool(
        name="machine_margin_by_seller",
        agent="machines",
        description=(
            "Margem bruta por vendedor em um período, da maior para a menor. "
            "NÃO use para faturamento por categoria (use machine_sales_by_category)."
        ),
        params=(PERIOD, BRANCH),
        sql="""
            SELECT b.name AS branch,
                   v.name AS seller,
                   COUNT(*) AS units,
                   ROUND(SUM(s.revenue), 2) AS revenue,
                   ROUND(100.0 * (SUM(s.revenue) - SUM(s.cost)) / SUM(s.revenue), 1) AS margin_pct
            FROM machine_sale s
            JOIN branch b ON b.id = s.branch_id
            JOIN seller v ON v.id = s.seller_id
            WHERE s.sold_on BETWEEN :start AND :end
              AND (:branch IS NULL OR b.name = :branch)
            GROUP BY b.name, v.name
            ORDER BY margin_pct DESC, v.name
        """,
        labels={
            "pt": ("Filial", "Vendedor", "Unidades", "Faturamento", "Margem %"),
            "en": ("Branch", "Seller", "Units", "Revenue", "Margin %"),
        },
        headline=_margin_headline,
        triggers=("margem", "vendedor", "vendedores"),
    ),
    Tool(
        name="receivables_aging",
        agent="finance",
        description=(
            "Títulos vencidos por filial e faixa de atraso, do maior valor para o menor. "
            "NÃO use para faturamento (use as ferramentas de peças, serviços ou máquinas)."
        ),
        params=(BRANCH,),
        sql="""
            SELECT b.name AS branch,
                   CASE
                       WHEN julianday(:today) - julianday(r.due_on) <= 30 THEN '1-30'
                       WHEN julianday(:today) - julianday(r.due_on) <= 90 THEN '31-90'
                       WHEN julianday(:today) - julianday(r.due_on) <= 180 THEN '91-180'
                       ELSE '180+'
                   END AS bucket,
                   COUNT(*) AS invoices,
                   ROUND(SUM(r.amount), 2) AS amount
            FROM receivable r
            JOIN branch b ON b.id = r.branch_id
            WHERE r.paid_on IS NULL
              AND r.due_on < :today
              AND (:branch IS NULL OR b.name = :branch)
            GROUP BY b.name, bucket
            ORDER BY amount DESC
        """,
        labels={
            "pt": ("Filial", "Faixa (dias)", "Títulos", "Valor"),
            "en": ("Branch", "Bucket (days)", "Invoices", "Amount"),
        },
        headline=_receivables_headline,
        triggers=("vencido", "vencidos", "inadimplencia", "receber", "aging"),
    ),
)

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


@dataclass(frozen=True)
class Agent:
    id: str
    name: dict[str, str]
    purpose: str
    keywords: tuple[str, ...]
    allowed_roles: frozenset[str]

    @property
    def tools(self) -> tuple[Tool, ...]:
        return tuple(tool for tool in TOOLS if tool.agent == self.id)

    @property
    def system_prompt(self) -> str:
        return (
            f"Você é o agente de {self.name['pt']} da AgroDemo Máquinas, uma concessionária "
            f"fictícia. {self.purpose} Responda em português do Brasil, só com o que as "
            "ferramentas retornarem, e cite o período e a filial. Se nenhuma ferramenta "
            "responder à pergunta, diga isso em vez de estimar. Não mencione o detalhe "
            "completo em CSV: o sistema acrescenta essa oferta sozinho."
        )


EVERYONE = frozenset({"seller", "manager", "director"})

AGENTS: tuple[Agent, ...] = (
    Agent(
        id="parts",
        name={"pt": "Peças", "en": "Parts"},
        purpose="Cuida de vendas e estoque de peças.",
        keywords=(
            "peca", "pecas", "filtro", "filtros", "lubrificante", "lubrificantes",
            "rodante", "hidraulica", "eletrica", "estoque", "sku", "item", "itens",
        ),
        allowed_roles=EVERYONE,
    ),
    Agent(
        id="service",
        name={"pt": "Serviços", "en": "Service"},
        purpose="Cuida de ordens de serviço e desempenho dos técnicos.",
        keywords=("servico", "servicos", "oficina", "tecnico", "tecnicos", "os", "oss", "ordem"),
        allowed_roles=EVERYONE,
    ),
    Agent(
        id="machines",
        name={"pt": "Máquinas", "en": "Machines"},
        purpose="Cuida de vendas e margem de máquinas.",
        keywords=(
            "maquina", "maquinas", "trator", "tratores", "colheitadeira", "colheitadeiras",
            "pulverizador", "pulverizadores", "plantadeira", "plantadeiras",
            "vendedor", "vendedores", "margem",
        ),
        allowed_roles=EVERYONE,
    ),
    Agent(
        id="finance",
        name={"pt": "Financeiro", "en": "Finance"},
        purpose="Cuida de contas a receber e inadimplência.",
        keywords=("receber", "inadimplencia", "vencido", "vencidos", "titulo", "titulos", "aging"),
        allowed_roles=frozenset({"director"}),
    ),
)

AGENTS_BY_ID = {agent.id: agent for agent in AGENTS}
