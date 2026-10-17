"""
BankPilot AI - Multi-Agent Orchestrator and Specialists
Plain Python orchestration with strict tool allowlists, customer-scope injection,
bounded loops, and deterministic fallback support.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

import tools
from database import (
    DEFAULT_DB_PATH,
    format_inr,
    get_account_balance,
    get_customer,
    get_recent_transactions,
)

logger = logging.getLogger("BankPilotAI.Agents")

VALID_SPECIALISTS = ["banking", "fraud", "loan"]
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
REQUEST_TIMEOUT_SECONDS = 15.0


@dataclass
class ExecutionTraceItem:
    """Detailed record of an agent step for transparent UI visualization."""

    agent_name: str
    tool_name: Optional[str]
    sanitized_arguments: Optional[Dict[str, Any]]
    short_result_summary: str
    duration_ms: int
    model_identifier: str
    status: str  # 'live_success', 'fallback_deterministic', 'error'
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class MultiAgentResponse:
    """Unified response object returned to the application/Streamlit layer."""

    content: str
    route: List[str]
    execution_traces: List[ExecutionTraceItem]
    supporting_tool_results: Dict[str, Any]
    mode: str  # 'live_llm' or 'fallback_deterministic'
    needs_clarification: bool = False
    pending_loan_context: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Tool Dispatcher with Strict Allowlist & Injected Customer Scope
# ---------------------------------------------------------------------------


def dispatch_tool_call(
    agent_name: str,
    tool_name: str,
    tool_arguments: Dict[str, Any],
    customer_id: str,
    db_path: Optional[str] = None,
    unresolved_fraud_flags: bool = False,
    ref_time: Optional[datetime.datetime] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Execute tool strictly within agent's allowlist.
    Invariable security rule: customer_id is ALWAYS injected by application code,
    never derived from untrusted LLM arguments.
    """
    allowlist = tools.AGENT_TOOL_ALLOWLIST.get(agent_name, [])
    if tool_name not in allowlist:
        error_msg = f"Security Violation: Agent '{agent_name}' is not authorized to call tool '{tool_name}'."
        logger.warning(error_msg)
        return {"status": "error", "message": error_msg}, error_msg

    # Ground arguments with injected customer_id
    args = dict(tool_arguments or {})
    args["customer_id"] = customer_id
    args["db_path"] = db_path

    if tool_name == "get_customer_account_summary":
        res = tools.get_customer_account_summary(customer_id=customer_id, db_path=db_path)
        summary = (
            f"Summary for {res.get('business_name')}: Balance {res.get('current_balance_formatted')}, "
            f"Income {res.get('declared_monthly_income_formatted')}/mo"
        )
        return res, summary

    elif tool_name == "get_account_balance_tool":
        res = tools.get_account_balance_tool(customer_id=customer_id, db_path=db_path)
        summary = f"Current balance: {res.get('current_balance_formatted')}"
        return res, summary

    elif tool_name == "get_recent_transactions_tool":
        limit = args.get("limit", 5)
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = 5
        res = tools.get_recent_transactions_tool(customer_id=customer_id, limit=limit, db_path=db_path)
        summary = f"Retrieved {res.get('returned_count', 0)} recent transactions"
        return res, summary

    elif tool_name == "analyze_suspicious_activity_tool":
        res = tools.analyze_suspicious_activity_tool(
            customer_id=customer_id, db_path=db_path, ref_time=ref_time
        )
        flag_count = res.get("suspicious_flags_count", 0)
        summary = (
            f"Suspicious activity scan: {flag_count} flags detected"
            if flag_count > 0
            else "Suspicious activity scan: Clean (0 flags)"
        )
        return res, summary

    elif tool_name == "calculate_loan_affordability_tool":
        principal_paise = args.get("principal_paise")
        tenure_months = args.get("tenure_months")
        rate = args.get("annual_interest_rate_percent", tools.DEFAULT_LOAN_INTEREST_RATE_PERCENT)

        # Ensure correct integer conversion
        try:
            principal_paise = int(principal_paise)
            tenure_months = int(tenure_months)
            rate = float(rate)
        except (ValueError, TypeError) as e:
            err = f"Invalid loan parameters: {e}"
            return {"status": "error", "message": err}, err

        res = tools.calculate_loan_affordability_tool(
            customer_id=customer_id,
            principal_paise=principal_paise,
            tenure_months=tenure_months,
            annual_interest_rate_percent=rate,
            unresolved_fraud_flags=unresolved_fraud_flags,
            db_path=db_path,
        )
        rec = res.get("recommendation", "UNKNOWN")
        emi_fmt = res.get("proposed_monthly_emi_formatted", "")
        summary = f"Loan affordability evaluated: {rec} (EMI: {emi_fmt})"
        return res, summary

    return {"status": "error", "message": f"Unknown tool '{tool_name}'"}, "Unknown tool execution"


# ---------------------------------------------------------------------------
# Groq Client Initialization
# ---------------------------------------------------------------------------


def get_groq_client() -> Optional[Any]:
    """Initialize Groq client if valid API key is present."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from groq import Groq

        return Groq(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as e:
        logger.error(f"Failed to initialize Groq client: {e}")
        return None


# ---------------------------------------------------------------------------
# Deterministic Router (Used in Fallback and as Schema Validator)
# ---------------------------------------------------------------------------


def route_query_deterministically(query: str) -> List[str]:
    """
    Reliable rule-based routing for the 5 key use cases and variations.
    Guarantees canonical Banking -> Fraud -> Loan ordering.
    """
    q = query.lower()

    # Case 5: Combined multi-agent evaluation
    if (
        ("account" in q or "balance" in q)
        and ("suspicious" in q or "fraud" in q)
        and ("loan" in q or "afford" in q or "emi" in q)
    ):
        return ["banking", "fraud", "loan"]

    # Fraud + Loan combination
    if ("suspicious" in q or "fraud" in q) and ("loan" in q or "afford" in q or "emi" in q):
        return ["fraud", "loan"]

    is_fraud = any(
        k in q
        for k in [
            "suspicious",
            "fraud",
            "anomaly",
            "anomalies",
            "unauthorized",
            "risk check",
        ]
    )
    is_loan = any(
        k in q for k in ["loan", "afford", "emi", "borrow", "tenure", "interest rate"]
    )
    is_balance = any(
        k in q for k in ["balance", "account details", "account summary", "how much money"]
    )

    # Standalone fraud check (e.g. "Are any recent transactions suspicious?")
    if is_fraud and not is_loan:
        wants_banking = any(k in q for k in ["account", "balance", "show", "list", "display", "statement"])
        if not wants_banking:
            return ["fraud"]

    is_pure_txns = any(
        k in q
        for k in [
            "last five",
            "past five",
            "statement",
            "history",
            "show my transaction",
        ]
    ) or ("transaction" in q and not is_fraud and not is_loan)

    selected = []
    if is_balance or is_pure_txns or ("account" in q):
        selected.append("banking")
    if is_fraud:
        selected.append("fraud")
    if is_loan:
        selected.append("loan")

    # Order strictly: banking -> fraud -> loan
    ordered = [s for s in VALID_SPECIALISTS if s in selected]
    return ordered


def route_query_with_llm(
    client: Any, model: str, query: str
) -> Tuple[List[str], ExecutionTraceItem]:
    """
    Route natural language query to appropriate specialist agents via Groq LLM.
    Falls back gracefully if LLM response is malformed or times out.
    """
    t0 = time.time()
    system_prompt = (
        "You are the Supervisor Orchestrator for BankPilot AI, an explainable multi-agent banking system.\n"
        "Your task is to analyze the user's request and select which specialist agents must be invoked.\n"
        "Allowed specialist agent names:\n"
        "- 'banking': For balance inquiries, account profile/details, and transaction history.\n"
        "- 'fraud': For analyzing suspicious transactions, fraud risks, and velocity anomalies.\n"
        "- 'loan': For calculating EMI and assessing loan affordability.\n\n"
        "If a user asks a combined request (e.g., check account and suspicious transactions before loan affordability), "
        "include all relevant agents.\n"
        "Respond ONLY in valid JSON matching this schema:\n"
        '{"selected_agents": ["banking", "fraud", "loan"], "rationale": "Brief explanation"}\n'
        "Do not include code blocks or markdown around the JSON."
    )

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            temperature=0.0,
            max_tokens=150,
        )
        duration_ms = int((time.time() - t0) * 1000)
        raw_text = completion.choices[0].message.content.strip()

        # Parse JSON safely
        # Strip potential markdown fences if model outputs them
        if raw_text.startswith("```"):
            lines = raw_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw_text = "\n".join(lines).strip()

        data = json.loads(raw_text)
        candidates = data.get("selected_agents", [])
        # Strict validation
        valid_agents = [
            agent for agent in VALID_SPECIALISTS if agent in [str(c).lower().strip() for c in candidates]
        ]

        if not valid_agents:
            # Fall back to deterministic routing
            valid_agents = route_query_deterministically(query)

        trace = ExecutionTraceItem(
            agent_name="Supervisor",
            tool_name=None,
            sanitized_arguments={"query": query},
            short_result_summary=f"Routed query to agents: {valid_agents}",
            duration_ms=duration_ms,
            model_identifier=model,
            status="live_success",
        )
        return valid_agents, trace

    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        logger.warning(f"Supervisor LLM routing failed: {e}. Using deterministic fallback.")
        fallback_route = route_query_deterministically(query)
        trace = ExecutionTraceItem(
            agent_name="Supervisor",
            tool_name=None,
            sanitized_arguments={"query": query},
            short_result_summary=f"LLM routing failed ({e}). Fallback routed to: {fallback_route}",
            duration_ms=duration_ms,
            model_identifier="deterministic_router",
            status="fallback_deterministic",
        )
        return fallback_route, trace


# ---------------------------------------------------------------------------
# Specialist Agent Implementations
# ---------------------------------------------------------------------------


def run_specialist_agent(
    agent_name: str,
    customer_id: str,
    query: str,
    client: Optional[Any],
    model: str,
    context: Dict[str, Any],
    db_path: Optional[str] = None,
    ref_time: Optional[datetime.datetime] = None,
) -> Tuple[Dict[str, Any], List[ExecutionTraceItem]]:
    """
    Run specialist agent with enforced tool allowlist and bounded execution loops.
    Supports live Groq tool calling with automatic deterministic fallback.
    """
    traces: List[ExecutionTraceItem] = []

    # Prepare agent schemas & instructions
    if agent_name == "banking":
        agent_schemas = tools.BANKING_TOOL_SCHEMAS
        system_instruction = (
            "You are the Banking Specialist Agent for BankPilot AI.\n"
            "Your role is to inspect customer balances, account summaries, and transaction history using your authorized tools.\n"
            "Rules:\n"
            "1. You must call an authorized tool to fetch facts.\n"
            "2. Never invent balances or transactions.\n"
            "3. If user asks for account details or balance, call get_customer_account_summary or get_account_balance_tool.\n"
            "4. If user asks for recent transactions, call get_recent_transactions_tool."
        )
    elif agent_name == "fraud":
        agent_schemas = tools.FRAUD_TOOL_SCHEMAS
        system_instruction = (
            "You are the Fraud & Risk Specialist Agent for BankPilot AI.\n"
            "Your role is to analyze account transactions against simulated fraud policies:\n"
            "- Policy 1: Large debit >= ₹50,000.\n"
            "- Policy 2: Rapid debit velocity (>= 3 debits within 24 hours).\n"
            "Rules:\n"
            "1. Call analyze_suspicious_activity_tool to evaluate policies.\n"
            "2. Fictional simulation only: a flag indicates review necessity, NOT proof of fraud."
        )
    elif agent_name == "loan":
        agent_schemas = tools.LOAN_TOOL_SCHEMAS
        unresolved_fraud = context.get("unresolved_fraud_flags", False)
        system_instruction = (
            "You are the Loan Affordability Specialist Agent for BankPilot AI.\n"
            "Your role is to calculate Equated Monthly Installment (EMI) and assess debt-to-income affordability (FOIR <= 50%).\n"
            f"Context: Unresolved suspicious activity flagged = {unresolved_fraud}.\n"
            "Rules:\n"
            "1. Call calculate_loan_affordability_tool with exact principal in paise and tenure in months.\n"
            "2. If unresolved suspicious activity exists, set unresolved_fraud_flags=True so it holds for review.\n"
            "3. Affordability does NOT constitute loan approval."
        )
    else:
        return {"status": "error", "message": f"Unknown agent {agent_name}"}, traces

    # 1. Attempt Live Groq Execution if client available
    if client is not None:
        t0 = time.time()
        try:
            # First turn: Ask model to pick tool
            messages = [
                {"role": "system", "content": system_instruction},
                {
                    "role": "user",
                    "content": (
                        f"Customer ID: {customer_id}\n"
                        f"User Request: {query}\n"
                        f"Available context: {json.dumps({k: v for k, v in context.items() if k != 'db_path'})}"
                    ),
                },
            ]

            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=agent_schemas,
                tool_choice="auto",
                temperature=0.0,
                max_tokens=300,
            )
            resp_message = response.choices[0].message

            # Check if model requested a tool call
            if resp_message.tool_calls:
                tool_call = resp_message.tool_calls[0]
                tool_name = tool_call.function.name
                raw_args = tool_call.function.arguments

                try:
                    parsed_args = json.loads(raw_args) if raw_args else {}
                except Exception:
                    parsed_args = {}

                # Execute through secure dispatcher with code-injected customer_id
                t_tool_0 = time.time()
                tool_result, summary = dispatch_tool_call(
                    agent_name=agent_name,
                    tool_name=tool_name,
                    tool_arguments=parsed_args,
                    customer_id=customer_id,
                    db_path=db_path,
                    unresolved_fraud_flags=context.get("unresolved_fraud_flags", False),
                    ref_time=ref_time,
                )
                tool_duration_ms = int((time.time() - t_tool_0) * 1000)

                traces.append(
                    ExecutionTraceItem(
                        agent_name=f"{agent_name.capitalize()} Specialist",
                        tool_name=tool_name,
                        sanitized_arguments={k: v for k, v in parsed_args.items() if "secret" not in k.lower()},
                        short_result_summary=summary,
                        duration_ms=tool_duration_ms,
                        model_identifier=model,
                        status="live_success",
                    )
                )

                return {
                    "agent": agent_name,
                    "tool_name": tool_name,
                    "tool_result": tool_result,
                    "summary": summary,
                    "model_used": model,
                }, traces

        except Exception as e:
            logger.warning(
                f"Live specialist agent '{agent_name}' encountered error: {e}. Switching to deterministic fallback."
            )

    # 2. Deterministic Fallback Specialist Execution
    t_fallback_0 = time.time()
    fallback_result, fallback_tool, fallback_summary = execute_specialist_fallback(
        agent_name=agent_name,
        customer_id=customer_id,
        query=query,
        context=context,
        db_path=db_path,
        ref_time=ref_time,
    )
    fallback_duration_ms = int((time.time() - t_fallback_0) * 1000)

    traces.append(
        ExecutionTraceItem(
            agent_name=f"{agent_name.capitalize()} Specialist",
            tool_name=fallback_tool,
            sanitized_arguments={"customer_id": customer_id, "query": query},
            short_result_summary=fallback_summary,
            duration_ms=fallback_duration_ms,
            model_identifier="deterministic_engine",
            status="fallback_deterministic",
        )
    )

    return {
        "agent": agent_name,
        "tool_name": fallback_tool,
        "tool_result": fallback_result,
        "summary": fallback_summary,
        "model_used": "deterministic_engine",
    }, traces


def execute_specialist_fallback(
    agent_name: str,
    customer_id: str,
    query: str,
    context: Dict[str, Any],
    db_path: Optional[str] = None,
    ref_time: Optional[datetime.datetime] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Deterministic fallbacks executing real tools based on verified pattern matches.
    Ensures 100% functionality even during complete network/API outages.
    """
    q = query.lower()

    if agent_name == "banking":
        # If user explicitly asks for transactions/statement without asking for overall account/balance
        is_transaction_query = any(k in q for k in ["last five", "past five", "statement", "history", "recent transactions"]) or ("transaction" in q and "account" not in q and "balance" not in q)
        if is_transaction_query and "account" not in q and "balance" not in q:
            tool_name = "get_recent_transactions_tool"
            limit = 5
            # Check if user requested a specific count (e.g. last 3 transactions)
            import re
            m = re.search(r"(?:last|past)\s*(\d+)", q)
            if m:
                limit = int(m.group(1))
            res, summary = dispatch_tool_call(
                agent_name="banking",
                tool_name=tool_name,
                tool_arguments={"limit": limit},
                customer_id=customer_id,
                db_path=db_path,
            )
            return res, tool_name, summary
        else:
            tool_name = "get_customer_account_summary"
            res, summary = dispatch_tool_call(
                agent_name="banking",
                tool_name=tool_name,
                tool_arguments={},
                customer_id=customer_id,
                db_path=db_path,
            )
            return res, tool_name, summary

    elif agent_name == "fraud":
        tool_name = "analyze_suspicious_activity_tool"
        res, summary = dispatch_tool_call(
            agent_name="fraud",
            tool_name=tool_name,
            tool_arguments={},
            customer_id=customer_id,
            db_path=db_path,
            ref_time=ref_time,
        )
        return res, tool_name, summary

    elif agent_name == "loan":
        tool_name = "calculate_loan_affordability_tool"
        # Extract principal and tenure
        principal_paise, tenure_months = tools.extract_loan_parameters(query)

        # Check session context if clarification was pending
        if principal_paise is None and context.get("pending_principal_paise"):
            principal_paise = context["pending_principal_paise"]
        if tenure_months is None and context.get("pending_tenure_months"):
            tenure_months = context["pending_tenure_months"]

        # If principal still missing, return parameter-missing indicator
        if principal_paise is None:
            return (
                {
                    "status": "needs_clarification",
                    "message": "Please specify the loan amount you wish to assess (e.g. ₹1 lakh).",
                },
                tool_name,
                "Clarification needed: loan principal amount not specified",
            )

        if tenure_months is None:
            # Use clearly documented standard default tenure of 12 months
            tenure_months = 12

        unresolved_fraud = context.get("unresolved_fraud_flags", False)
        res, summary = dispatch_tool_call(
            agent_name="loan",
            tool_name=tool_name,
            tool_arguments={
                "principal_paise": principal_paise,
                "tenure_months": tenure_months,
                "annual_interest_rate_percent": tools.DEFAULT_LOAN_INTEREST_RATE_PERCENT,
            },
            customer_id=customer_id,
            db_path=db_path,
            unresolved_fraud_flags=unresolved_fraud,
        )
        return res, tool_name, summary

    return {"status": "error", "message": "Unknown specialist"}, "unknown_tool", "Error"


# ---------------------------------------------------------------------------
# Structured & Authoritative Response Synthesis
# ---------------------------------------------------------------------------


def synthesize_response(
    query: str,
    customer_id: str,
    routes: List[str],
    specialist_findings: Dict[str, Any],
    mode: str,
) -> str:
    """
    Format authoritative tool-grounded financial facts.
    Uses structured templates for critical figures, tables, and decisions
    so free-form model text cannot replace or misrepresent authoritative figures.
    """
    if not routes:
        return (
            "### ℹ️ BankPilot AI Capability Overview\n\n"
            "I am your explainable banking assistant. I can assist you with:\n"
            "1. **Account Balances & Profile**: *'Show my balance and account details.'*\n"
            "2. **Recent Transactions**: *'Show my last five transactions.'*\n"
            "3. **Suspicious Transaction Analysis**: *'Are any recent transactions suspicious?'*\n"
            "4. **Loan Affordability & EMI**: *'Can I afford a ₹1 lakh loan over 12 months?'*\n"
            "5. **Combined Assessment**: *'Check my account and suspicious transactions before assessing whether I can afford a ₹1 lakh loan over 12 months.'*\n\n"
            "Please ask a banking query or select one of the quick demo buttons."
        )

    sections = []

    # 1. Banking Specialist Section
    if "banking" in specialist_findings:
        b_res = specialist_findings["banking"].get("tool_result", {})
        if b_res.get("status") == "success":
            if "opening_balance_formatted" in b_res:
                # Summary tool
                sections.append(
                    "#### 🏦 Account Summary & Available Balance\n"
                    f"- **Business Name:** {b_res.get('business_name')} (`{b_res.get('customer_id')}`)\n"
                    f"- **Account Type:** {b_res.get('account_type')}\n"
                    f"- **Current Balance:** **{b_res.get('current_balance_formatted')}**\n"
                    f"- **Declared Monthly Income:** {b_res.get('declared_monthly_income_formatted')}\n"
                    f"- **Existing Monthly Obligations (EMI):** {b_res.get('existing_monthly_emi_formatted')}"
                )
            elif "current_balance_formatted" in b_res:
                # Balance tool
                sections.append(
                    "#### 🏦 Available Balance Details\n"
                    f"- **Current Balance:** **{b_res.get('current_balance_formatted')}**\n"
                    f"- **Opening Balance:** {b_res.get('opening_balance_formatted')}\n"
                    f"- **Total Credits:** {b_res.get('total_credits_formatted')} | **Total Debits:** {b_res.get('total_debits_formatted')}"
                )
            elif "transactions" in b_res:
                # Transactions list
                txns = b_res.get("transactions", [])
                rows = []
                for t in txns:
                    t_type_badge = "🟢 Credit" if t["txn_type"] == "CREDIT" else "🔴 Debit"
                    ts_short = t["timestamp"][:19].replace("T", " ")
                    rows.append(
                        f"| {t['txn_id']} | {ts_short} | {t_type_badge} | **{t['amount_formatted']}** | {t['description']} |"
                    )
                table_md = "\n".join(rows) if rows else "| None | - | - | - | - |"
                sections.append(
                    "#### 📋 Recent Transactions\n"
                    "| ID | Timestamp (UTC) | Type | Amount | Description |\n"
                    "|:---|:---|:---|:---|:---|\n"
                    f"{table_md}"
                )

    # 2. Fraud Specialist Section
    if "fraud" in specialist_findings:
        f_res = specialist_findings["fraud"].get("tool_result", {})
        if f_res.get("status") == "success":
            has_suspicious = f_res.get("has_suspicious_activity", False)
            flags = f_res.get("flags", [])
            badge = "⚠️ **FLAGGED FOR REVIEW**" if has_suspicious else "✅ **NORMAL / CLEAN**"

            fraud_text = [
                f"#### 🛡️ Suspicious Transaction Analysis ({badge})\n",
                f"- **Evaluation Status:** {f_res.get('summary')}",
            ]
            if flags:
                fraud_text.append("\n**Detected Policy Violations:**")
                for idx, flag in enumerate(flags, 1):
                    fraud_text.append(
                        f"{idx}. **[{flag.get('severity')} RISK] {flag.get('flag_type')}**: {flag.get('description')}"
                    )
            fraud_text.append(
                "\n*Policy evaluated: Single debit >= ₹50,000 threshold and >= 3 debits in 24 hours velocity window. "
                "(Simulation note: A risk flag indicates internal review requirement, not proof of fraudulent intent).*"
            )
            sections.append("\n".join(fraud_text))

    # 3. Loan Specialist Section
    if "loan" in specialist_findings:
        l_res = specialist_findings["loan"].get("tool_result", {})
        if l_res.get("status") == "needs_clarification":
            sections.append(f"#### 💰 Loan Assessment\n⚠️ {l_res.get('message')}")
        elif l_res.get("status") == "success":
            rec = l_res.get("recommendation")
            if rec == "AFFORDABLE":
                rec_badge = "✅ **AFFORDABLE (Within Policy)**"
            elif rec == "REQUIRES_MANUAL_REVIEW":
                rec_badge = "⚠️ **HELD FOR UNDERWRITER MANUAL REVIEW**"
            else:
                rec_badge = "❌ **NOT AFFORDABLE (Exceeds Policy Ratio)**"

            loan_text = [
                f"#### 💰 Loan Affordability Assessment\n",
                f"- **Simulated Outcome:** {rec_badge}\n",
                f"- **Requested Principal:** {l_res.get('principal_formatted')} over **{l_res.get('tenure_months')} months** (@ {l_res.get('annual_interest_rate_percent')}% p.a.)",
                f"- **Calculated Monthly EMI:** **{l_res.get('proposed_monthly_emi_formatted')}**",
                f"- **Existing Monthly EMI:** {l_res.get('existing_monthly_emi_formatted')}",
                f"- **Total Monthly Debt Obligation:** **{l_res.get('total_monthly_obligations_formatted')}**",
                f"- **Declared Monthly Income:** {l_res.get('declared_monthly_income_formatted')}",
                f"- **Debt-to-Income Ratio (FOIR):** **{l_res.get('foir_percent')}%** (Policy limit: {l_res.get('max_foir_threshold_percent')}%)",
                f"\n**Assessment Rationale:**\n{l_res.get('decision_rationale')}",
                f"\n*{l_res.get('disclaimer')}*",
            ]
            sections.append("\n".join(loan_text))

    return "\n\n---\n\n".join(sections)


# ---------------------------------------------------------------------------
# Primary Orchestration Entry Point
# ---------------------------------------------------------------------------


def process_user_query(
    customer_id: str,
    query: str,
    session_context: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
    ref_time: Optional[datetime.datetime] = None,
) -> MultiAgentResponse:
    """
    Main entry point for BankPilot AI:
    1. Injects customer ID by application code.
    2. Resolves route via Supervisor (Groq LLM or deterministic fallback).
    3. Invokes specialists sequentially in canonical Banking -> Fraud -> Loan order.
    4. Passes structured findings (e.g. fraud risk flags to loan affordability).
    5. Formats authoritative, grounded multi-agent response.
    """
    context = dict(session_context or {})
    all_traces: List[ExecutionTraceItem] = []
    specialist_findings: Dict[str, Any] = {}

    client = get_groq_client()
    mode = "live_llm" if client is not None else "fallback_deterministic"

    # Step 1: Supervisor Routing
    if client is not None:
        route, supervisor_trace = route_query_with_llm(
            client=client, model=DEFAULT_MODEL, query=query
        )
        all_traces.append(supervisor_trace)
    else:
        t0 = time.time()
        route = route_query_deterministically(query)
        duration_ms = int((time.time() - t0) * 1000)
        all_traces.append(
            ExecutionTraceItem(
                agent_name="Supervisor",
                tool_name=None,
                sanitized_arguments={"query": query},
                short_result_summary=f"Deterministic supervisor routed to: {route}",
                duration_ms=duration_ms,
                model_identifier="deterministic_router",
                status="fallback_deterministic",
            )
        )

    # Step 2: Sequential Execution in canonical order (Banking -> Fraud -> Loan)
    unresolved_fraud_flags = False

    for agent_name in route:
        # Pass forward fraud findings to loan agent
        if agent_name == "loan":
            context["unresolved_fraud_flags"] = unresolved_fraud_flags

        finding, specialist_traces = run_specialist_agent(
            agent_name=agent_name,
            customer_id=customer_id,
            query=query,
            client=client,
            model=DEFAULT_MODEL,
            context=context,
            db_path=db_path,
            ref_time=ref_time,
        )
        all_traces.extend(specialist_traces)
        specialist_findings[agent_name] = finding

        # Check if fraud agent found suspicious flags
        if agent_name == "fraud":
            tool_res = finding.get("tool_result", {})
            if tool_res.get("has_suspicious_activity"):
                unresolved_fraud_flags = True

    # Step 3: Synthesis
    response_content = synthesize_response(
        query=query,
        customer_id=customer_id,
        routes=route,
        specialist_findings=specialist_findings,
        mode=mode,
    )

    # Collect tool results dictionary for UI expandable view
    supporting_results = {
        agent: finding.get("tool_result") for agent, finding in specialist_findings.items()
    }

    return MultiAgentResponse(
        content=response_content,
        route=route,
        execution_traces=all_traces,
        supporting_tool_results=supporting_results,
        mode=mode,
    )
