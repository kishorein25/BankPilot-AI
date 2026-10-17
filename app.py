"""
BankPilot AI - Explainable Multi-Agent Banking Simulator
Streamlit Application Interface.
"""

from __future__ import annotations

import datetime
import os
import sys
from typing import Any, Dict, List, Optional

# Ensure project root is in python path
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from dotenv import load_dotenv

import agents
import database
import tools
from database import format_inr

# Load local environment configuration
load_dotenv()

st.set_page_config(
    page_title="BankPilot AI — Banking Simulator",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# State Initialization
# ---------------------------------------------------------------------------

if "initialized" not in st.session_state:
    database.init_db()
    st.session_state.initialized = True

if "selected_customer_id" not in st.session_state:
    st.session_state.selected_customer_id = "CUST-101"

if "messages" not in st.session_state:
    st.session_state.messages = []

if "last_traces" not in st.session_state:
    st.session_state.last_traces = []

if "last_tool_results" not in st.session_state:
    st.session_state.last_tool_results = {}

if "session_context" not in st.session_state:
    st.session_state.session_context = {}

if "txn_form_submitted" not in st.session_state:
    st.session_state.txn_form_submitted = False


def reset_customer_context():
    """Clear chat messages and clarification context when switching customer or resetting."""
    st.session_state.messages = []
    st.session_state.last_traces = []
    st.session_state.last_tool_results = {}
    st.session_state.session_context = {}


# ---------------------------------------------------------------------------
# Sidebar: Settings, Customer Selector, and Reset Action
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🏦 BankPilot AI")
    st.caption("Explainable Multi-Agent Banking Simulator")

    st.markdown("---")
    st.subheader("👤 Select Fictional Customer")

    customers = database.list_customers()
    cust_options = {
        c["customer_id"]: f"{c['customer_id']} — {c['business_name']} ({c['owner_name']})"
        for c in customers
    }

    selected_id = st.selectbox(
        "Current Business Profile:",
        options=list(cust_options.keys()),
        format_func=lambda x: cust_options.get(x, x),
        index=0 if st.session_state.selected_customer_id == "CUST-101" else 1,
    )

    if selected_id != st.session_state.selected_customer_id:
        st.session_state.selected_customer_id = selected_id
        reset_customer_context()
        st.rerun()

    # Customer profile card
    cust_details = database.get_customer(st.session_state.selected_customer_id)
    balance_info = database.get_account_balance(st.session_state.selected_customer_id)

    if cust_details and balance_info:
        st.markdown(
            f"**Account Type:** {cust_details['account_type']}\n\n"
            f"**Opening Balance:** {format_inr(cust_details['opening_balance_paise'])}\n\n"
            f"**Available Balance:** `{balance_info['current_balance_formatted']}`\n\n"
            f"**Declared Monthly Income:** `{format_inr(cust_details['declared_income_paise'])}`\n\n"
            f"**Existing Monthly EMI:** `{format_inr(cust_details['existing_emi_paise'])}`"
        )

    st.markdown("---")
    st.subheader("⚙️ System Status")

    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    groq_model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

    if groq_api_key:
        st.success(f"🟢 **Live LLM Connected**\n\nModel: `{groq_model}`")
    else:
        st.warning(
            "🟡 **Demo Fallback Mode Active**\n\n"
            "Operating with deterministic router & verified pattern engine. "
            "Configure `GROQ_API_KEY` in `.env` for live LLM execution."
        )

    st.markdown("---")
    st.subheader("🔄 Demo Reset")
    st.caption("Restores original fictional accounts and transaction history.")
    if st.button("Reset Demo Data", use_container_width=True, type="secondary"):
        database.reset_demo_db()
        reset_customer_context()
        st.success("Database restored to initial demonstration records!")
        st.rerun()

# ---------------------------------------------------------------------------
# Main Header & Disclaimer
# ---------------------------------------------------------------------------

st.markdown(
    """
    <div style="background-color: #f0f4f8; padding: 12px 18px; border-radius: 8px; border-left: 5px solid #0284c7; margin-bottom: 16px;">
        <span style="font-size: 1.15rem; font-weight: 600; color: #0f172a;">BankPilot AI — Explainable Multi-Agent Banking Simulator</span><br/>
        <span style="font-size: 0.90rem; color: #475569;">
            Autonomous multi-agent orchestration for small business banking operations, risk assessment, and loan underwriting.
        </span><br/>
        <span style="font-size: 0.82rem; color: #b91c1c; font-weight: 600;">
            ⚠️ Simulation only. All customer data, fraud rules and loan assessments are fictional.
        </span>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# KPI Summary Row
# ---------------------------------------------------------------------------

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
with kpi1:
    st.metric(
        label="Available Balance",
        value=balance_info["current_balance_formatted"],
        delta=f"Credits: {balance_info['total_credits_formatted']}",
    )
with kpi2:
    st.metric(
        label="Monthly Income",
        value=format_inr(cust_details["declared_income_paise"]),
        delta="Declared SME Cashflow",
    )
with kpi3:
    st.metric(
        label="Existing Monthly EMI",
        value=format_inr(cust_details["existing_emi_paise"]),
        delta="Prior Commitments",
    )
with kpi4:
    # Quick risk badge
    fraud_eval = tools.analyze_suspicious_activity_tool(
        customer_id=st.session_state.selected_customer_id
    )
    if fraud_eval.get("has_suspicious_activity"):
        st.metric(label="Risk Status", value="Flagged", delta=f"{fraud_eval['suspicious_flags_count']} Anomaly", delta_color="inverse")
    else:
        st.metric(label="Risk Status", value="Clean", delta="0 Flags", delta_color="normal")

st.markdown("---")

# ---------------------------------------------------------------------------
# Quick Demo Questions
# ---------------------------------------------------------------------------

st.markdown("##### 💡 Five Quick Demo Questions")
col_btn1, col_btn2, col_btn3, col_btn4, col_btn5 = st.columns(5)

quick_query = None
with col_btn1:
    if st.button("1. Balance & Details", use_container_width=True):
        quick_query = "Show my balance and account details."
with col_btn2:
    if st.button("2. Last 5 Transactions", use_container_width=True):
        quick_query = "Show my last five transactions."
with col_btn3:
    if st.button("3. Suspicious Check", use_container_width=True):
        quick_query = "Are any recent transactions suspicious?"
with col_btn4:
    if st.button("4. ₹1 Lakh Loan Affordability", use_container_width=True):
        quick_query = "Can I afford a ₹1 lakh loan over 12 months?"
with col_btn5:
    if st.button("5. Combined 3-Agent Evaluation", use_container_width=True):
        quick_query = "Check my account and suspicious transactions before assessing whether I can afford a ₹1 lakh loan over 12 months."

# ---------------------------------------------------------------------------
# Chat Query Processor
# ---------------------------------------------------------------------------


def handle_user_query(user_text: str):
    """Execute multi-agent workflow for user query and update session state."""
    # Store user message with timestamp
    ts_now = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S UTC")
    st.session_state.messages.append({"role": "user", "content": user_text, "timestamp": ts_now})

    # Run multi-agent orchestrator
    response = agents.process_user_query(
        customer_id=st.session_state.selected_customer_id,
        query=user_text,
        session_context=st.session_state.session_context,
    )

    # Store assistant response and traces
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": response.content,
            "timestamp": ts_now,
            "mode": response.mode,
            "route": response.route,
        }
    )
    st.session_state.last_traces = response.execution_traces
    st.session_state.last_tool_results = response.supporting_tool_results


# Execute quick button if clicked
if quick_query:
    handle_user_query(quick_query)
    st.rerun()

# ---------------------------------------------------------------------------
# Chat & Execution Trace View
# ---------------------------------------------------------------------------

left_col, right_col = st.columns([6, 4])

with left_col:
    st.subheader("💬 Multi-Agent Banking Assistant")

    # Render conversation
    if not st.session_state.messages:
        st.info(
            "👋 Select one of the quick demo buttons above or type a natural language banking request below."
        )
    else:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                if msg["role"] == "assistant":
                    mode_label = (
                        "🟢 Live Groq LLM"
                        if msg.get("mode") == "live_llm"
                        else "🟡 Demo Fallback Engine"
                    )
                    route_str = " → ".join([r.capitalize() for r in msg.get("route", [])])
                    st.caption(f"Evaluated at {msg['timestamp']} | Mode: {mode_label} | Route: {route_str}")
                st.markdown(msg["content"])

    # Chat Input Box
    chat_prompt = st.chat_input("Type your banking query (e.g. 'Can I afford a ₹1 lakh loan over 12 months?')")
    if chat_prompt:
        handle_user_query(chat_prompt)
        st.rerun()

with right_col:
    st.subheader("🔍 Explainable Execution Trace")

    if st.session_state.last_traces:
        st.caption("Step-by-step multi-agent orchestration logs:")
        for idx, trace in enumerate(st.session_state.last_traces, 1):
            badge = "🟢" if trace.status == "live_success" else ("🟡" if trace.status == "fallback_deterministic" else "🔴")
            with st.expander(
                f"{badge} Step {idx}: {trace.agent_name} ({trace.duration_ms} ms)",
                expanded=True,
            ):
                st.markdown(f"**Tool:** `{trace.tool_name or 'None (Supervisor Router)'}`")
                st.markdown(f"**Model / Engine:** `{trace.model_identifier}`")
                st.markdown(f"**Status:** `{trace.status}`")
                if trace.sanitized_arguments:
                    st.markdown("**Arguments:**")
                    st.json(trace.sanitized_arguments)
                st.markdown(f"**Summary:** {trace.short_result_summary}")
    else:
        st.caption("No recent execution trace yet. Ask a question to view agent logs.")

    # Supporting Tool Results
    if st.session_state.last_tool_results:
        with st.expander("📊 Supporting Tool Raw Output (Evidence)", expanded=False):
            st.caption("Authoritative structured outputs returned directly by verified tools:")
            st.json(st.session_state.last_tool_results)

st.markdown("---")

# ---------------------------------------------------------------------------
# Transaction Simulator & Recent Ledger
# ---------------------------------------------------------------------------

sim_col, ledger_col = st.columns([5, 5])

with sim_col:
    st.subheader("💳 Transaction Simulator")
    st.caption(
        "Inject immediate user-generated banking events. Writes occur strictly through this form."
    )

    with st.form("transaction_simulator_form", clear_on_submit=True):
        txn_type = st.selectbox("Transaction Type:", options=["DEBIT", "CREDIT"])
        amount_rupees = st.number_input(
            "Amount (INR ₹):",
            min_value=1.0,
            max_value=5000000.0,
            value=15000.0,
            step=1000.0,
        )
        description = st.text_input(
            "Transaction Description:",
            value="Commercial Vendor Payment",
            placeholder="e.g. Bulk inventory purchase",
        )
        submit_txn = st.form_submit_button("Post Transaction to Ledger", use_container_width=True)

        if submit_txn:
            amount_paise = database.rupees_to_paise(amount_rupees)
            try:
                posted = database.add_transaction(
                    customer_id=st.session_state.selected_customer_id,
                    txn_type=txn_type,
                    amount_paise=amount_paise,
                    description=description,
                )
                st.success(
                    f"Successfully posted {posted['txn_type']} of {posted['amount_formatted']}. "
                    f"New Balance: {posted['updated_balance_formatted']}"
                )
                st.rerun()
            except ValueError as ve:
                st.error(f"Transaction Rejected: {ve}")

with ledger_col:
    st.subheader("📋 Recent Transaction Ledger")
    st.caption("Real-time posted records for the active customer profile:")
    recent_txns = database.get_recent_transactions(
        st.session_state.selected_customer_id, limit=8
    )

    if recent_txns:
        table_rows = []
        for t in recent_txns:
            table_rows.append(
                {
                    "Txn ID": t["txn_id"],
                    "Timestamp (UTC)": t["timestamp"][:19].replace("T", " "),
                    "Type": t["txn_type"],
                    "Amount": t["amount_formatted"],
                    "Description": t["description"],
                }
            )
        st.dataframe(table_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No transactions found.")
