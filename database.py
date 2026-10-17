"""
BankPilot AI - Database Layer
SQLite storage with exact integer-paise accounting and reproducible seeding.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "bankpilot.db")


def rupees_to_paise(rupees: float | int) -> int:
    """Convert INR rupees to integer paise safely."""
    return int(round(float(rupees) * 100))


def paise_to_rupees(paise: int) -> float:
    """Convert integer paise to INR rupees."""
    return float(paise) / 100.0


def format_inr(paise: int) -> str:
    """
    Format integer paise as standard Indian Currency format (e.g. ₹1,50,000.00).
    """
    rupees = paise_to_rupees(paise)
    is_negative = rupees < 0
    abs_rupees = abs(rupees)
    whole = int(abs_rupees)
    fraction = int(round((abs_rupees - whole) * 100))

    whole_str = str(whole)
    if len(whole_str) > 3:
        last3 = whole_str[-3:]
        rest = whole_str[:-3]
        # Group in 2s for Indian numbering
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        formatted_whole = ",".join(groups) + "," + last3
    else:
        formatted_whole = whole_str

    formatted = f"₹{formatted_whole}.{fraction:02d}"
    return f"-{formatted}" if is_negative else formatted


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Create short-lived SQLite connection with Row factory and foreign keys."""
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(
    db_path: Optional[str] = None,
    force_reseed: bool = False,
    ref_time: Optional[datetime.datetime] = None,
) -> None:
    """
    Initialize SQLite schema and seed fictional customers if not already seeded.
    If force_reseed is True, tables are dropped and re-created from scratch.
    """
    path = db_path or DEFAULT_DB_PATH
    ref = ref_time or datetime.datetime.now(datetime.timezone.utc)

    with get_connection(path) as conn:
        cursor = conn.cursor()

        if force_reseed:
            cursor.execute("DROP TABLE IF EXISTS transactions;")
            cursor.execute("DROP TABLE IF EXISTS customers;")
            cursor.execute("DROP TABLE IF EXISTS meta;")

        # Create schema
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                business_name TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                account_type TEXT NOT NULL,
                opening_balance_paise INTEGER NOT NULL,
                declared_income_paise INTEGER NOT NULL,
                existing_emi_paise INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                txn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT NOT NULL,
                txn_type TEXT NOT NULL CHECK(txn_type IN ('CREDIT', 'DEBIT')),
                amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
                description TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
            );
            """
        )

        # Check if already seeded
        cursor.execute("SELECT value FROM meta WHERE key = 'seeded';")
        seeded = cursor.fetchone()
        if seeded and not force_reseed:
            conn.commit()
            return

        # Seed reproducible fictional data
        # Customer 1: Apex Traders (Stable business, clean transaction history)
        # Declared Income: ₹1,50,000/mo, Existing EMI: ₹15,000/mo, Opening Balance: ₹2,00,000
        # Customer 2: BlueHorizon Logistics (Active fleet business, has recent burst and large debit)
        # Declared Income: ₹2,50,000/mo, Existing EMI: ₹40,000/mo, Opening Balance: ₹3,50,000
        customers = [
            (
                "CUST-101",
                "Apex Traders",
                "Rajesh Sharma",
                "Current Account - SME",
                rupees_to_paise(200000),  # ₹2,00,000 opening
                rupees_to_paise(150000),  # ₹1,50,000/mo income
                rupees_to_paise(15000),   # ₹15,000/mo existing EMI
                (ref - datetime.timedelta(days=90)).isoformat(),
            ),
            (
                "CUST-102",
                "BlueHorizon Logistics",
                "Priya Nair",
                "Current Account - Transport Commercial",
                rupees_to_paise(350000),  # ₹3,50,000 opening
                rupees_to_paise(250000),  # ₹2,50,000/mo income
                rupees_to_paise(40000),   # ₹40,000/mo existing EMI
                (ref - datetime.timedelta(days=120)).isoformat(),
            ),
        ]

        cursor.executemany(
            """
            INSERT OR REPLACE INTO customers (
                customer_id, business_name, owner_name, account_type,
                opening_balance_paise, declared_income_paise, existing_emi_paise, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            customers,
        )

        # Transactions for CUST-101 (Clean & steady)
        # Balance progression:
        # Opening: +200,000
        # - 5 days ago: Credit ₹50,000 (Invoice Settlement)
        # - 3 days ago: Debit ₹18,000 (Warehouse Rent)
        # - 2 days ago: Credit ₹32,000 (Client Payment)
        # - 1 day ago: Debit ₹12,000 (Utility Bills)
        # - 6 hours ago: Debit ₹15,000 (Vendor Supplies)
        # Net balance: 200,000 + 50,000 - 18,000 + 32,000 - 12,000 - 15,000 = ₹2,37,000
        txns_cust_101 = [
            (
                "CUST-101",
                "CREDIT",
                rupees_to_paise(50000),
                "Invoice settlement - Metro Retail",
                (ref - datetime.timedelta(days=5)).isoformat(),
            ),
            (
                "CUST-101",
                "DEBIT",
                rupees_to_paise(18000),
                "Commercial warehouse lease payment",
                (ref - datetime.timedelta(days=3)).isoformat(),
            ),
            (
                "CUST-101",
                "CREDIT",
                rupees_to_paise(32000),
                "Customer bulk purchase payment",
                (ref - datetime.timedelta(days=2)).isoformat(),
            ),
            (
                "CUST-101",
                "DEBIT",
                rupees_to_paise(12000),
                "Electricity & municipal utility bill",
                (ref - datetime.timedelta(days=1)).isoformat(),
            ),
            (
                "CUST-101",
                "DEBIT",
                rupees_to_paise(15000),
                "Packaging materials invoice",
                (ref - datetime.timedelta(hours=6)).isoformat(),
            ),
        ]

        # Transactions for CUST-102 (Demonstrates Suspicious Activity)
        # Policy rules:
        # 1. Debit >= ₹50,000 is flagged as High-Value Debit.
        # 2. 3 or more debits within 24 hours flagged as Rapid Debit Velocity / Burst.
        # Let's seed:
        # - 4 days ago: Credit ₹1,20,000 (Fleet freight contract)
        # - 2 days ago: Debit ₹25,000 (Diesel fuel depot)
        # - 14 hours ago: Debit ₹65,000 (High-value equipment purchase - triggers large debit rule)
        # - 8 hours ago: Debit ₹10,000 (Toll plaza corporate FASTag)
        # - 2 hours ago: Debit ₹15,000 (Emergency spare parts)
        # Notice: In last 24h, there are 3 debits (65k, 10k, 15k) and one debit >= 50k!
        # Opening: +350,000
        # + 120,000 - 25,000 - 65,000 - 10,000 - 15,000 = ₹3,55,000
        txns_cust_102 = [
            (
                "CUST-102",
                "CREDIT",
                rupees_to_paise(120000),
                "Freight logistics payment - North Corridor",
                (ref - datetime.timedelta(days=4)).isoformat(),
            ),
            (
                "CUST-102",
                "DEBIT",
                rupees_to_paise(25000),
                "Bulk diesel fuel depot invoice",
                (ref - datetime.timedelta(days=2)).isoformat(),
            ),
            (
                "CUST-102",
                "DEBIT",
                rupees_to_paise(65000),
                "High-value hydraulic lift spare engine",
                (ref - datetime.timedelta(hours=14)).isoformat(),
            ),
            (
                "CUST-102",
                "DEBIT",
                rupees_to_paise(10000),
                "Corporate FASTag fleet recharge",
                (ref - datetime.timedelta(hours=8)).isoformat(),
            ),
            (
                "CUST-102",
                "DEBIT",
                rupees_to_paise(15000),
                "Emergency tyre replacement batch",
                (ref - datetime.timedelta(hours=2)).isoformat(),
            ),
        ]

        cursor.executemany(
            """
            INSERT INTO transactions (customer_id, txn_type, amount_paise, description, timestamp)
            VALUES (?, ?, ?, ?, ?);
            """,
            txns_cust_101 + txns_cust_102,
        )

        cursor.execute(
            """
            INSERT OR REPLACE INTO meta (key, value) VALUES ('seeded', ?);
            """,
            (datetime.datetime.now(datetime.timezone.utc).isoformat(),),
        )

        conn.commit()


def reset_demo_db(
    db_path: Optional[str] = None, ref_time: Optional[datetime.datetime] = None
) -> None:
    """Reset the database to default fictional demonstration records."""
    init_db(db_path=db_path, force_reseed=True, ref_time=ref_time)


def list_customers(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all customers in the database."""
    init_db(db_path=db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM customers ORDER BY customer_id ASC;")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_customer(customer_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get customer profile details."""
    init_db(db_path=db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM customers WHERE customer_id = ?;", (customer_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_account_balance(customer_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Calculate current balance consistently from opening balance + credits - debits.
    Returns exact integer paise and human-formatted strings.
    """
    customer = get_customer(customer_id, db_path=db_path)
    if not customer:
        raise ValueError(f"Customer '{customer_id}' not found.")

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN txn_type = 'CREDIT' THEN amount_paise ELSE 0 END), 0) AS total_credits_paise,
                COALESCE(SUM(CASE WHEN txn_type = 'DEBIT' THEN amount_paise ELSE 0 END), 0) AS total_debits_paise
            FROM transactions
            WHERE customer_id = ?;
            """,
            (customer_id,),
        )
        totals = cursor.fetchone()

        opening_paise = customer["opening_balance_paise"]
        total_credits = totals["total_credits_paise"]
        total_debits = totals["total_debits_paise"]
        current_paise = opening_paise + total_credits - total_debits

        return {
            "customer_id": customer_id,
            "business_name": customer["business_name"],
            "opening_balance_paise": opening_paise,
            "opening_balance_formatted": format_inr(opening_paise),
            "total_credits_paise": total_credits,
            "total_credits_formatted": format_inr(total_credits),
            "total_debits_paise": total_debits,
            "total_debits_formatted": format_inr(total_debits),
            "current_balance_paise": current_paise,
            "current_balance_formatted": format_inr(current_paise),
            "currency": "INR",
        }


def get_recent_transactions(
    customer_id: str, limit: int = 5, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve recent transactions for a customer, ordered by newest first."""
    init_db(db_path=db_path)
    if limit <= 0:
        limit = 5

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT txn_id, customer_id, txn_type, amount_paise, description, timestamp
            FROM transactions
            WHERE customer_id = ?
            ORDER BY timestamp DESC, txn_id DESC
            LIMIT ?;
            """,
            (customer_id, limit),
        )
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["amount_formatted"] = format_inr(d["amount_paise"])
            result.append(d)
        return result


def add_transaction(
    customer_id: str,
    txn_type: str,
    amount_paise: int,
    description: str,
    db_path: Optional[str] = None,
    timestamp: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """
    Insert a credit or debit transaction with strict validations:
    - Amount must be positive integer paise
    - Txn type must be CREDIT or DEBIT
    - Debits cannot exceed available balance (no-overdraft simulation policy)
    """
    txn_type = txn_type.upper().strip()
    if txn_type not in ("CREDIT", "DEBIT"):
        raise ValueError(f"Invalid transaction type '{txn_type}'. Must be CREDIT or DEBIT.")

    if not isinstance(amount_paise, int) or amount_paise <= 0:
        raise ValueError("Transaction amount must be a positive integer in paise.")

    desc = description.strip()
    if not desc:
        raise ValueError("Transaction description cannot be empty.")

    customer = get_customer(customer_id, db_path=db_path)
    if not customer:
        raise ValueError(f"Customer '{customer_id}' not found.")

    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Check balance for DEBIT
        if txn_type == "DEBIT":
            balance_info = get_account_balance(customer_id, db_path=db_path)
            current_balance = balance_info["current_balance_paise"]
            if amount_paise > current_balance:
                raise ValueError(
                    f"Transaction rejected: Insufficient funds. Available: {format_inr(current_balance)}, "
                    f"Requested debit: {format_inr(amount_paise)} under the no-overdraft simulation policy."
                )

        ts = (
            timestamp.isoformat()
            if timestamp
            else datetime.datetime.now(datetime.timezone.utc).isoformat()
        )

        cursor.execute(
            """
            INSERT INTO transactions (customer_id, txn_type, amount_paise, description, timestamp)
            VALUES (?, ?, ?, ?, ?);
            """,
            (customer_id, txn_type, amount_paise, desc, ts),
        )
        txn_id = cursor.lastrowid
        conn.commit()

    updated_balance = get_account_balance(customer_id, db_path=db_path)
    return {
        "txn_id": txn_id,
        "customer_id": customer_id,
        "txn_type": txn_type,
        "amount_paise": amount_paise,
        "amount_formatted": format_inr(amount_paise),
        "description": desc,
        "timestamp": ts,
        "updated_balance_paise": updated_balance["current_balance_paise"],
        "updated_balance_formatted": updated_balance["current_balance_formatted"],
    }
