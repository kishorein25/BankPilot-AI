"""
BankPilot AI - Explainable Multi-Agent Banking Simulator
Submission-Ready Streamlit Application Interface.
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
from database import format_inr, get_demo_account_info

# Load local environment configuration
load_dotenv()

st.set_page_config(
    page_title="BankPilot AI — Simulated Banking Assistant",
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

if "last_llm_status" not in st.session_state:
    st.session_state.last_llm_status = None


def reset_customer_context():
    """Clear account-specific chat messages, tool traces, and clarification context."""
    st.session_state.messages = []
    st.session_state.last_traces = []
    st.session_state.last_tool_results = {}
    st.session_state.session_context = {}
    st.session_state.last_llm_status = None


# ---------------------------------------------------------------------------
# Sidebar: Account Selector & System Status
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("BankPilot AI")
    st.caption("Simulated Banking Assistant")
    st.markdown("---")

    # Section 1: Choose Demo Account
    st.subheader("Choose demo account")
    st.markdown(
        "These are sample business accounts with fictional data. "
        "Choose one to explore its balance, transactions and loan affordability."
    )

    customers = database.list_customers()
    account_labels = {}
    for c in customers:
        cid = c["customer_id"]
        info = get_demo_account_info(cid)
        account_labels[cid] = f"{cid} — {info['business_name']} ({info['proprietor']})"

    selected_account_id = st.selectbox(
        "Active Business Account:",
        options=list(account_labels.keys()),
        format_func=lambda x: account_labels.get(x, x),
        index=0 if st.session_state.selected_customer_id == "CUST-101" else 1,
    )

    # Handle account switching
    if selected_account_id != st.session_state.selected_customer_id:
        st.session_state.selected_customer_id = selected_account_id
        reset_customer_context()
        st.rerun()

    # Grounded description of the selected account based on actual seeded records
    active_info = get_demo_account_info(st.session_state.selected_customer_id)
    cust_details = database.get_customer(st.session_state.selected_customer_id)
    balance_info = database.get_account_balance(st.session_state.selected_customer_id)

    st.info(
        f"**Profile:** {active_info['business_name']}\n\n"
        f"**Account Type:** {cust_details['account_type']}\n\n"
        f"**Record Summary:** {active_info['description']}"
    )

    st.markdown("---")

    # Section 2: Honest AI Status
    st.subheader("AI Engine Status")
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    configured_model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

    if not groq_api_key:
        st.warning(
            "⚪ **API Key Not Configured**\n\n"
            "Operating in Demo Fallback Mode. All queries are handled through deterministic routing and verified tools."
        )
    else:
        # Check the honest status of the last request
        last_status = st.session_state.last_llm_status
        if last_status == "live_success":
            st.success(
                f"🟢 **Live LLM Request Succeeded**\n\n"
                f"Model: `{configured_model}` via Groq LPU.\n\n"
                f"Supervisor & Specialist agents executed live."
            )
        elif last_status == "api_error_fallback":
            st.warning(
                f"🟡 **API Request Failed (Fallback Activated)**\n\n"
                f"The model call failed; the request was completed safely via the deterministic engine."
            )
        elif last_status == "fallback_deterministic":
            st.info(
                f"🟡 **Demo Fallback Mode Active**\n\n"
                f"Processed via rule engine."
            )
        else:
            # Key present, waiting for first request
            st.success(
                f"🟢 **Live Model Configured**\n\n"
                f"Model: `{configured_model}`\n\n"
                f"Ready for live requests."
            )

    st.markdown("---")

    # Section 3: Reset Demo
    st.subheader("Demo Data Reset")
    st.caption("Restores initial demonstration records and clears newly posted transactions.")
    if st.button("Reset Demo Data", use_container_width=True):
        database.reset_demo_db()
        reset_customer_context()
        st.success("Demo records restored to default baseline.")
        st.rerun()

# ---------------------------------------------------------------------------
# Main Dashboard Header & Notice Banner
# ---------------------------------------------------------------------------

st.markdown(
    """
    <div style="background-color: #f8fafc; padding: 14px 20px; border-radius: 8px; border-left: 5px solid #0284c7; border: 1px solid #e2e8f0; margin-bottom: 16px;">
        <div style="font-size: 1.25rem; font-weight: 700; color: #0f172a;">BankPilot AI</div>
        <div style="font-size: 0.92rem; color: #475569; margin-top: 2px;">
            Simulated banking assistant with explainable multi-agent orchestration.
        </div>
        <div style="font-size: 0.82rem; color: #b91c1c; font-weight: 600; margin-top: 6px;">
            ⚠️ Demo data only. All customer balances, transactions, fraud rules and loan assessments are fictional simulation records.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Account Metrics Row (Clearly Distinguishing Income, EMI, and Balance)
# ---------------------------------------------------------------------------

col_bal, col_inc, col_emi, col_risk = st.columns(4)

with col_bal:
    st.metric(
        label="Available Balance",
        value=balance_info["current_balance_formatted"],
        help="Current balance calculated from opening balance + posted credits - posted debits.",
    )
with col_inc:
    st.metric(
        label="Monthly Income",
        value=format_inr(cust_details["declared_income_paise"]),
        help="Fictional declared monthly business cash flow.",
    )
with col_emi:
    st.metric(
        label="Existing Monthly EMI",
        value=format_inr(cust_details["existing_emi_paise"]),
        help="Fictional existing monthly debt commitments.",
    )
with col_risk:
    # Compute actual policy risk from records
    fraud_eval = tools.analyze_suspicious_activity_tool(
        customer_id=st.session_state.selected_customer_id
    )
    if fraud_eval.get("has_suspicious_activity"):
        st.metric(
            label="Simulated Risk Status",
            value="Flagged for Review",
            delta=f"{fraud_eval['suspicious_flags_count']} policy trigger(s)",
            delta_color="inverse",
            help="One or more simulated policies triggered (e.g. single debit >= ₹50,000 or rapid velocity).",
        )
    else:
        st.metric(
            label="Simulated Risk Status",
            value="Clean",
            delta="0 policy triggers",
            delta_color="normal",
            help="All transactions remain within standard fictional risk thresholds.",
        )

st.markdown("---")

# ---------------------------------------------------------------------------
# Two-Column Workspace: Left = Chat & Questions | Right = Transactions & Trace
# ---------------------------------------------------------------------------

left_chat_col, right_panel_col = st.columns([6, 4], gap="medium")

# ---------------------------------------------------------------------------
# Query Execution Logic
# ---------------------------------------------------------------------------


def execute_banking_query(query_text: str):
    """Execute multi-agent workflow for the active demo account."""
    ts_now = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S UTC")
    st.session_state.messages.append(
        {"role": "user", "content": query_text, "timestamp": ts_now}
    )

    with st.spinner("Analyzing banking request with specialist agents..."):
        resp = agents.process_user_query(
            customer_id=st.session_state.selected_customer_id,
            query=query_text,
            session_context=st.session_state.session_context,
        )

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": resp.content,
            "timestamp": ts_now,
            "mode": resp.mode,
            "llm_status": resp.llm_status,
            "route": resp.route,
        }
    )
    st.session_state.last_traces = resp.execution_traces
    st.session_state.last_tool_results = resp.supporting_tool_results
    st.session_state.last_llm_status = resp.llm_status


with left_chat_col:
    st.subheader("Banking Assistant")

    # Five Quick Demo Question Buttons
    st.caption("Select a standard demonstration question:")
    btn_col1, btn_col2 = st.columns(2)
    btn_col3, btn_col4, btn_col5 = st.columns(3)

    submitted_quick_query = None
    with btn_col1:
        if st.button("1. Balance & Details", use_container_width=True):
            submitted_quick_query = "Show my balance and account details."
    with btn_col2:
        if st.button("2. Last 5 Transactions", use_container_width=True):
            submitted_quick_query = "Show my last five transactions."
    with btn_col3:
        if st.button("3. Suspicious Check", use_container_width=True):
            submitted_quick_query = "Are any recent transactions suspicious?"
    with btn_col4:
        if st.button("4. ₹1 Lakh Loan Affordability", use_container_width=True):
            submitted_quick_query = "Can I afford a ₹1 lakh loan over 12 months?"
    with btn_col5:
        if st.button("5. Combined 3-Agent Review", use_container_width=True):
            submitted_quick_query = (
                "Check my account and suspicious transactions before assessing whether "
                "I can afford a ₹1 lakh loan over 12 months."
            )

    # Process quick button click
    if submitted_quick_query:
        execute_banking_query(submitted_quick_query)
        st.rerun()

    st.markdown("---")

    # Render Conversation Messages
    if not st.session_state.messages:
        st.info(
            "Select one of the 5 demo buttons above or enter a banking question below to begin."
        )
    else:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                if msg["role"] == "assistant":
                    llm_status = msg.get("llm_status", "fallback_deterministic")
                    if llm_status == "live_success":
                        status_tag = "Live Groq LLM"
                    elif llm_status == "api_error_fallback":
                        status_tag = "API Fallback (Deterministic)"
                    else:
                        status_tag = "Demo Fallback Mode"

                    route_tag = " → ".join([r.capitalize() for r in msg.get("route", [])])
                    st.caption(
                        f"Evaluated at {msg['timestamp']} | Engine: {status_tag} | Agent Route: {route_tag}"
                    )
                st.markdown(msg["content"])

    # Natural Language Chat Input
    user_input = st.chat_input("Ask a banking question (e.g. 'Can I afford a ₹1 lakh loan over 12 months?')")
    if user_input:
        execute_banking_query(user_input)
        st.rerun()

with right_panel_col:
    # Recent Transactions Table (Prominent and clear)
    st.subheader("Recent Transactions")
    recent_records = database.get_recent_transactions(
        st.session_state.selected_customer_id, limit=8
    )

    if recent_records:
        formatted_table = []
        for r in recent_records:
            formatted_table.append(
                {
                    "Txn ID": r["txn_id"],
                    "Timestamp (UTC)": r["timestamp"][:19].replace("T", " "),
                    "Type": r["txn_type"],
                    "Amount": r["amount_formatted"],
                    "Description": r["description"],
                }
            )
        st.dataframe(formatted_table, use_container_width=True, hide_index=True)
    else:
        st.info("No recorded transactions.")

    st.markdown("---")

    # Collapsible Transaction Simulator
    with st.expander("💳 Transaction Simulator (Simulate Account Event)", expanded=False):
        st.caption(
            "Simulate a user-initiated transaction. Inserts one record into SQLite and refreshes balances."
        )
        with st.form("transaction_form", clear_on_submit=True):
            sim_type = st.selectbox("Transaction Type:", options=["DEBIT", "CREDIT"])
            sim_amount = st.number_input(
                "Amount (INR ₹):",
                min_value=1.0,
                max_value=5000000.0,
                value=25000.0,
                step=5000.0,
            )
            sim_desc = st.text_input(
                "Description:",
                value="Commercial Inventory Procurement",
                placeholder="e.g. Bulk goods purchase",
            )
            submit_sim = st.form_submit_button("Post Transaction to Account", use_container_width=True)

            if submit_sim:
                amt_paise = database.rupees_to_paise(sim_amount)
                try:
                    posted_record = database.add_transaction(
                        customer_id=st.session_state.selected_customer_id,
                        txn_type=sim_type,
                        amount_paise=amt_paise,
                        description=sim_desc,
                    )
                    st.success(
                        f"Posted {posted_record['txn_type']} of {posted_record['amount_formatted']}. "
                        f"Updated balance: {posted_record['updated_balance_formatted']}"
                    )
                    st.rerun()
                except ValueError as err:
                    st.error(f"Transaction Rejected: {err}")

    # Collapsible Agent Activity Trace
    with st.expander("🔍 Explainable Agent Activity Trace", expanded=False):
        if st.session_state.last_traces:
            st.caption("Sequential step logs from the multi-agent coordinator:")
            for idx, trace in enumerate(st.session_state.last_traces, 1):
                st.markdown(
                    f"**Step {idx}: {trace.agent_name}** ({trace.duration_ms} ms)\n"
                    f"- Tool: `{trace.tool_name or 'Router (None)'}`\n"
                    f"- Status: `{trace.status}` | Engine: `{trace.model_identifier}`\n"
                    f"- Summary: {trace.short_result_summary}"
                )
                if trace.sanitized_arguments:
                    st.json(trace.sanitized_arguments)
                st.markdown("---")
        else:
            st.caption("No recent agent activity. Execute a question to see the execution trace.")

    # Collapsible Supporting Tool Output
    with st.expander("📊 Supporting Tool Raw Output (Evidence)", expanded=False):
        if st.session_state.last_tool_results:
            st.caption("Authoritative structured outputs returned by deterministic tools:")
            st.json(st.session_state.last_tool_results)
        else:
            st.caption("No tool output available yet.")
