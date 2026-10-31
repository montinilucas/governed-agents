"""Synthetic data for AgroDemo Máquinas, a fictional farm-equipment dealership.

Every name and number comes from a fixed random seed, so two builds are
identical and nothing here describes a real company.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 20261030
FIRST_DATE = date(2024, 11, 1)
REFERENCE_DATE = date(2026, 10, 30)

BRANCHES = ("Norte", "Sul", "Leste", "Oeste", "Centro")
PART_GROUPS = ("Filtros", "Lubrificantes", "Rodante", "Elétrica", "Hidráulica")
MACHINES = (
    ("T-120", "Tratores", 410_000),
    ("T-220", "Tratores", 640_000),
    ("T-340", "Tratores", 920_000),
    ("C-700", "Colheitadeiras", 1_850_000),
    ("C-900", "Colheitadeiras", 2_600_000),
    ("P-30", "Pulverizadores", 980_000),
    ("P-50", "Pulverizadores", 1_320_000),
    ("S-12", "Plantadeiras", 560_000),
)

SCHEMA = """
CREATE TABLE branch (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE customer (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    branch_id INTEGER NOT NULL REFERENCES branch(id)
);
CREATE TABLE part (
    sku TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    part_group TEXT NOT NULL,
    unit_cost REAL NOT NULL
);
CREATE TABLE part_sale (
    id INTEGER PRIMARY KEY,
    sold_on TEXT NOT NULL,
    branch_id INTEGER NOT NULL REFERENCES branch(id),
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    sku TEXT NOT NULL REFERENCES part(sku),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
CREATE TABLE part_stock (
    branch_id INTEGER NOT NULL REFERENCES branch(id),
    sku TEXT NOT NULL REFERENCES part(sku),
    quantity INTEGER NOT NULL,
    PRIMARY KEY (branch_id, sku)
);
CREATE TABLE technician (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    branch_id INTEGER NOT NULL REFERENCES branch(id)
);
CREATE TABLE service_order (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branch(id),
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    technician_id INTEGER NOT NULL REFERENCES technician(id),
    opened_on TEXT NOT NULL,
    closed_on TEXT,
    revenue REAL NOT NULL
);
CREATE TABLE seller (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    branch_id INTEGER NOT NULL REFERENCES branch(id)
);
CREATE TABLE machine_sale (
    id INTEGER PRIMARY KEY,
    sold_on TEXT NOT NULL,
    branch_id INTEGER NOT NULL REFERENCES branch(id),
    seller_id INTEGER NOT NULL REFERENCES seller(id),
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    model TEXT NOT NULL,
    category TEXT NOT NULL,
    revenue REAL NOT NULL,
    cost REAL NOT NULL
);
CREATE TABLE receivable (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branch(id),
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    due_on TEXT NOT NULL,
    paid_on TEXT,
    amount REAL NOT NULL
);
"""


def _random_day(rng: random.Random) -> date:
    span = (REFERENCE_DATE - FIRST_DATE).days
    return FIRST_DATE + timedelta(days=rng.randint(0, span))


def build_database(path: Path) -> Path:
    """Create the demo database at ``path``, replacing any previous build."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    rng = random.Random(SEED)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)

    conn.executemany(
        "INSERT INTO branch (id, name) VALUES (?, ?)",
        list(enumerate(BRANCHES, start=1)),
    )
    branch_ids = range(1, len(BRANCHES) + 1)

    customers = [
        (i, f"Cliente {i:03d}", rng.choice(branch_ids)) for i in range(1, 121)
    ]
    conn.executemany("INSERT INTO customer VALUES (?, ?, ?)", customers)

    parts = []
    for i in range(1, 81):
        group = PART_GROUPS[i % len(PART_GROUPS)]
        parts.append(
            (f"AD{i:04d}", f"{group} item {i:02d}", group, round(rng.uniform(18, 2400), 2))
        )
    conn.executemany("INSERT INTO part VALUES (?, ?, ?, ?)", parts)

    part_sales = []
    for i in range(1, 6001):
        sku, _, _, unit_cost = rng.choice(parts)
        customer_id, _, branch_id = rng.choice(customers)
        part_sales.append(
            (
                i,
                _random_day(rng).isoformat(),
                branch_id,
                customer_id,
                sku,
                rng.randint(1, 12),
                round(unit_cost * rng.uniform(1.2, 1.7), 2),
            )
        )
    conn.executemany("INSERT INTO part_sale VALUES (?, ?, ?, ?, ?, ?, ?)", part_sales)

    conn.executemany(
        "INSERT INTO part_stock VALUES (?, ?, ?)",
        [(b, p[0], rng.randint(0, 60)) for b in branch_ids for p in parts],
    )

    technicians = [
        (i, f"Técnico {i:02d}", (i - 1) % len(BRANCHES) + 1) for i in range(1, 26)
    ]
    conn.executemany("INSERT INTO technician VALUES (?, ?, ?)", technicians)

    orders = []
    for i in range(1, 1801):
        technician_id, _, branch_id = rng.choice(technicians)
        opened = _random_day(rng)
        closed = opened + timedelta(days=rng.randint(1, 45))
        still_open = rng.random() < 0.08 or closed > REFERENCE_DATE
        orders.append(
            (
                i,
                branch_id,
                rng.choice(customers)[0],
                technician_id,
                opened.isoformat(),
                None if still_open else closed.isoformat(),
                round(rng.uniform(600, 48_000), 2),
            )
        )
    conn.executemany("INSERT INTO service_order VALUES (?, ?, ?, ?, ?, ?, ?)", orders)

    sellers = [
        (i, f"Vendedor {i:02d}", (i - 1) % len(BRANCHES) + 1) for i in range(1, 16)
    ]
    conn.executemany("INSERT INTO seller VALUES (?, ?, ?)", sellers)

    machine_sales = []
    for i in range(1, 321):
        seller_id, _, branch_id = rng.choice(sellers)
        model, category, list_price = rng.choice(MACHINES)
        revenue = round(list_price * rng.uniform(0.9, 1.05), 2)
        machine_sales.append(
            (
                i,
                _random_day(rng).isoformat(),
                branch_id,
                seller_id,
                rng.choice(customers)[0],
                model,
                category,
                revenue,
                round(revenue * rng.uniform(0.82, 0.95), 2),
            )
        )
    conn.executemany(
        "INSERT INTO machine_sale VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", machine_sales
    )

    receivables = []
    for i in range(1, 901):
        customer_id, _, branch_id = rng.choice(customers)
        due = _random_day(rng) + timedelta(days=rng.randint(0, 60))
        paid = None if rng.random() < 0.3 else due + timedelta(days=rng.randint(-5, 20))
        if paid is not None and paid > REFERENCE_DATE:
            paid = None
        receivables.append(
            (
                i,
                branch_id,
                customer_id,
                due.isoformat(),
                paid.isoformat() if paid else None,
                round(rng.uniform(900, 180_000), 2),
            )
        )
    conn.executemany("INSERT INTO receivable VALUES (?, ?, ?, ?, ?, ?)", receivables)

    conn.commit()
    conn.close()
    return path


def connect_read_only(path: Path) -> sqlite3.Connection:
    """Open the database so that no tool can ever write to it."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
