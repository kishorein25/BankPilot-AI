"""
BankPilot AI - Simulated Banking Tools
Defines tool implementations, schemas for Groq LLM tool calling, and input validations.
"""

from __future__ import annotations

import datetime
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from database import (
    DEFAULT_DB_PATH,
    format_inr,
    get_account_balance,
    get_customer,
    get_recent_transactions,
    paise_to_rupees,
    rupees_to_paise,
)

# Policy constants for transparent explanation
SUSPICIOUS_LARGE_DEBIT_THRESHOLD_PAISE = 5000000  # ₹50,000 in paise
SUSPICIOUS_RAPID_DEBIT_WINDOW_HOURS = 24  # Within last 24 hours
SUSPICIOUS_RAPID_DEBIT_COUNT_THRESHOLD = 3  # 3 or more debits in window
DEFAULT_LOAN_INTEREST_RATE_PERCENT = 12.0  # 12% p.a. standard commercial rate
MAX_LOAN_FOIR_RATIO = 0.50  # Max 50% debt-to-income (Fixed Obligation to Income Ratio)


def parse_indian_currency_to_paise(text: str) -> Optional[int]:
    """
    Parse natural language and numeric Indian amount expressions into integer paise.
    Supports formats:
      - '₹1 lakh', '1 lakh', '1.5 Lakhs', '2 lakhs'
      - '1 crore', '0.5 crore'
      - '₹1,00,000', '100000', '50000'
      - '50k', '50 thousand'
    """
    if not text:
        return None

    cleaned = text.strip().replace(",", "")

    # Match Lakhs: e.g. ₹1.5 lakh, 1 lakh, 2.5lakhs
    lakh_match = re.search(
        r"(?:₹|rs\.?|inr)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:lakh|lacs|lac|lakhs)",
        cleaned,
        re.IGNORECASE,
    )
    if lakh_match:
        try:
            val = float(lakh_match.group(1))
            return int(round(val * 100000 * 100))
        except ValueError:
            pass

    # Match Crores: e.g. 1 crore, ₹0.5 crore
    crore_match = re.search(
        r"(?:₹|rs\.?|inr)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:crore|crores|cr)",
        cleaned,
        re.IGNORECASE,
    )
    if crore_match:
        try:
            val = float(crore_match.group(1))
            return int(round(val * 10000000 * 100))
        except ValueError:
            pass

    # Match Thousands / k: e.g. 50k, 50 thousand
    k_match = re.search(
        r"(?:₹|rs\.?|inr)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:k|thousand|thousands)",
        cleaned,
        re.IGNORECASE,
    )
    if k_match:
        try:
            val = float(k_match.group(1))
            return int(round(val * 1000 * 100))
        except ValueError:
            pass

    # Match raw numeric amounts: e.g. ₹100000, 50000.50
    num_match = re.search(
        r"(?:₹|rs\.?|inr)?\s*([0-9]+(?:\.[0-9]+)?)",
        cleaned,
        re.IGNORECASE,
    )
    if num_match:
        try:
            val = float(num_match.group(1))
            return int(round(val * 100))
        except ValueError:
            pass

    return None


def extract_loan_parameters(query: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Extract principal in paise and tenure in months from a natural language query.
    Example: 'Can I afford a ₹1 lakh loan over 12 months?' -> (10000000, 12)
    """
    principal_paise = parse_indian_currency_to_paise(query)

    # Extract tenure in months or years
    tenure_months: Optional[int] = None
    month_match = re.search(r"(\d+)\s*(?:months|month|m\b)", query, re.IGNORECASE)
    if month_match:
        tenure_months = int(month_match.group(1))
    else:
        year_match = re.search(r"(\d+)\s*(?:years|year|yr|yrs)\b", query, re.IGNORECASE)
        if year_match:
            tenure_months = int(year_match.group(1)) * 12

    return principal_paise, tenure_months


# ---------------------------------------------------------------------------
# Core Banking Tools
# ---------------------------------------------------------------------------


def get_customer_account_summary(
    customer_id: str, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Retrieve full customer profile and current balance overview."""
    customer = get_customer(customer_id, db_path=db_path)
    if not customer:
        return {
            "status": "error",
            "message": f"Customer ID '{customer_id}' was not found.",
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    balance_info = get_account_balance(customer_id, db_path=db_path)

    return {
        "status": "success",
        "customer_id": customer["customer_id"],
        "business_name": customer["business_name"],
        "owner_name": customer["owner_name"],
        "account_type": customer["account_type"],
        "opening_balance_paise": customer["opening_balance_paise"],
        "opening_balance_formatted": format_inr(customer["opening_balance_paise"]),
        "current_balance_paise": balance_info["current_balance_paise"],
        "current_balance_formatted": balance_info["current_balance_formatted"],
        "declared_monthly_income_paise": customer["declared_income_paise"],
        "declared_monthly_income_formatted": format_inr(customer["declared_income_paise"]),
        "existing_monthly_emi_paise": customer["existing_emi_paise"],
        "existing_monthly_emi_formatted": format_inr(customer["existing_emi_paise"]),
        "currency": "INR",
        "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def get_account_balance_tool(
    customer_id: str, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Retrieve current available balance derived consistently from opening balance and ledger."""
    try:
        balance_info = get_account_balance(customer_id, db_path=db_path)
        return {
            "status": "success",
            **balance_info,
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


def get_recent_transactions_tool(
    customer_id: str, limit: int = 5, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Retrieve the most recent transactions for the specified customer."""
    try:
        # Validate limit
        if not isinstance(limit, int) or limit <= 0:
            limit = 5
        elif limit > 50:
            limit = 50

        txns = get_recent_transactions(customer_id, limit=limit, db_path=db_path)
        return {
            "status": "success",
            "customer_id": customer_id,
            "limit_requested": limit,
            "returned_count": len(txns),
            "transactions": txns,
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Fraud Analysis Tool
# ---------------------------------------------------------------------------


def analyze_suspicious_activity_tool(
    customer_id: str,
    db_path: Optional[str] = None,
    ref_time: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """
    Analyze recent transactions against documented fictional fraud & risk policies:
    1. Large-transaction threshold: Single DEBIT >= ₹50,000 (50,00,000 paise).
    2. Rapid debit velocity: 3 or more DEBITs within the last 24-hour window.
    """
    customer = get_customer(customer_id, db_path=db_path)
    if not customer:
        return {
            "status": "error",
            "message": f"Customer ID '{customer_id}' was not found.",
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    now = ref_time or datetime.datetime.now(datetime.timezone.utc)
    # Fetch recent transactions (up to 30)
    txns = get_recent_transactions(customer_id, limit=30, db_path=db_path)

    flags = []
    window_debits = []

    for txn in txns:
        # Parse transaction timestamp safely
        try:
            txn_dt = datetime.datetime.fromisoformat(txn["timestamp"])
            if txn_dt.tzinfo is None:
                txn_dt = txn_dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            continue

        if txn["txn_type"] == "DEBIT":
            # Policy 1: Single large debit check
            if txn["amount_paise"] >= SUSPICIOUS_LARGE_DEBIT_THRESHOLD_PAISE:
                flags.append(
                    {
                        "flag_type": "LARGE_DEBIT_THRESHOLD",
                        "severity": "HIGH",
                        "description": (
                            f"Debit of {txn['amount_formatted']} for '{txn['description']}' "
                            f"exceeds single-transaction risk threshold of {format_inr(SUSPICIOUS_LARGE_DEBIT_THRESHOLD_PAISE)}."
                        ),
                        "transaction_id": txn["txn_id"],
                        "timestamp": txn["timestamp"],
                    }
                )

            # Policy 2: Check if debit falls within 24-hour window
            time_diff = now - txn_dt
            if datetime.timedelta(0) <= time_diff <= datetime.timedelta(hours=SUSPICIOUS_RAPID_DEBIT_WINDOW_HOURS):
                window_debits.append(txn)

    # Check rapid velocity in 24h
    if len(window_debits) >= SUSPICIOUS_RAPID_DEBIT_COUNT_THRESHOLD:
        flags.append(
            {
                "flag_type": "RAPID_DEBIT_VELOCITY",
                "severity": "MEDIUM",
                "description": (
                    f"Detected {len(window_debits)} debits within the last {SUSPICIOUS_RAPID_DEBIT_WINDOW_HOURS} hours, "
                    f"exceeding velocity threshold of {SUSPICIOUS_RAPID_DEBIT_COUNT_THRESHOLD} debits."
                ),
                "transactions_involved": [
                    {
                        "txn_id": d["txn_id"],
                        "amount_formatted": d["amount_formatted"],
                        "description": d["description"],
                        "timestamp": d["timestamp"],
                    }
                    for d in window_debits
                ],
            }
        )

    has_suspicious = len(flags) > 0
    if has_suspicious:
        summary = (
            f"Detected {len(flags)} risk indicators requiring review: "
            + "; ".join(f["description"] for f in flags)
        )
    else:
        summary = "No suspicious transactions or velocity anomalies detected under current policy thresholds."

    return {
        "status": "success",
        "customer_id": customer_id,
        "business_name": customer["business_name"],
        "has_suspicious_activity": has_suspicious,
        "suspicious_flags_count": len(flags),
        "flags": flags,
        "summary": summary,
        "policy_rules_evaluated": [
            f"Rule 1: Single DEBIT >= {format_inr(SUSPICIOUS_LARGE_DEBIT_THRESHOLD_PAISE)}",
            f"Rule 2: >= {SUSPICIOUS_RAPID_DEBIT_COUNT_THRESHOLD} DEBITs within {SUSPICIOUS_RAPID_DEBIT_WINDOW_HOURS} hours",
        ],
        "data_timestamp": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Loan Affordability Tool
# ---------------------------------------------------------------------------


def calculate_emi_paise(
    principal_paise: int, tenure_months: int, annual_interest_rate_percent: float
) -> int:
    """
    Calculate Equated Monthly Installment (EMI) in integer paise.
    Formula:
      If r == 0: E = P / N
      If r > 0:  E = P * r * (1 + r)^N / ((1 + r)^N - 1)
      where r = annual_interest_rate_percent / (12 * 100)
    """
    if tenure_months <= 0:
        raise ValueError("Tenure months must be greater than zero.")
    if principal_paise <= 0:
        raise ValueError("Principal must be greater than zero.")
    if annual_interest_rate_percent < 0:
        raise ValueError("Interest rate cannot be negative.")

    if math.isclose(annual_interest_rate_percent, 0.0, abs_tol=1e-9):
        return int(round(principal_paise / tenure_months))

    r = annual_interest_rate_percent / (12.0 * 100.0)
    numerator = principal_paise * r * ((1.0 + r) ** tenure_months)
    denominator = ((1.0 + r) ** tenure_months) - 1.0
    emi = numerator / denominator
    return int(round(emi))


def calculate_loan_affordability_tool(
    customer_id: str,
    principal_paise: int,
    tenure_months: int,
    annual_interest_rate_percent: float = DEFAULT_LOAN_INTEREST_RATE_PERCENT,
    unresolved_fraud_flags: bool = False,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Calculate EMI and evaluate simulated loan affordability against customer cash flow.
    Integrates risk policy: if unresolved fraud flags exist, recommendation is marked
    REQUIRES_MANUAL_REVIEW while preserving transparent mathematical calculations.
    """
    # Validations
    if not isinstance(principal_paise, int) or principal_paise <= 0:
        return {
            "status": "error",
            "message": "Principal amount must be a positive integer in paise.",
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    # Upper bound check (e.g. ₹5 crore)
    if principal_paise > 5000000000:
        return {
            "status": "error",
            "message": "Principal exceeds maximum simulated limit of ₹5 crore.",
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    if not isinstance(tenure_months, int) or tenure_months <= 0 or tenure_months > 360:
        return {
            "status": "error",
            "message": f"Tenure must be between 1 and 360 months (received {tenure_months}).",
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    if (
        not isinstance(annual_interest_rate_percent, (int, float))
        or not math.isfinite(annual_interest_rate_percent)
        or annual_interest_rate_percent < 0
        or annual_interest_rate_percent > 100
    ):
        return {
            "status": "error",
            "message": "Annual interest rate must be a finite percentage between 0% and 100%.",
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    customer = get_customer(customer_id, db_path=db_path)
    if not customer:
        return {
            "status": "error",
            "message": f"Customer ID '{customer_id}' not found.",
            "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    # EMI calculation
    proposed_emi_paise = calculate_emi_paise(
        principal_paise, tenure_months, annual_interest_rate_percent
    )

    declared_income_paise = customer["declared_income_paise"]
    existing_emi_paise = customer["existing_emi_paise"]
    total_proposed_emi_paise = existing_emi_paise + proposed_emi_paise

    # FOIR (Fixed Obligation to Income Ratio)
    foir = (
        (total_proposed_emi_paise / declared_income_paise)
        if declared_income_paise > 0
        else 1.0
    )
    foir_percent = round(foir * 100, 2)
    max_foir_percent = int(MAX_LOAN_FOIR_RATIO * 100)

    # Policy Assessment
    meets_affordability_ratio = foir <= MAX_LOAN_FOIR_RATIO

    if unresolved_fraud_flags:
        recommendation = "REQUIRES_MANUAL_REVIEW"
        decision_rationale = (
            f"Affordability calculation indicates total monthly debt obligations of {format_inr(total_proposed_emi_paise)} "
            f"({foir_percent}% of declared monthly income vs {max_foir_percent}% threshold). "
            f"However, because recent unresolved suspicious account activity was flagged, the simulated application "
            f"cannot be automatically confirmed affordable and is held for Underwriter Manual Review. "
            f"(Note: A simulated flag does not prove fraud, and affordability does not guarantee credit approval)."
        )
    elif meets_affordability_ratio:
        recommendation = "AFFORDABLE"
        decision_rationale = (
            f"Total proposed monthly obligations of {format_inr(total_proposed_emi_paise)} "
            f"(Existing EMI: {format_inr(existing_emi_paise)} + Proposed EMI: {format_inr(proposed_emi_paise)}) "
            f"is {foir_percent}% of declared monthly income ({format_inr(declared_income_paise)}), "
            f"which is within the maximum policy threshold of {max_foir_percent}%."
        )
    else:
        recommendation = "NOT_AFFORDABLE"
        decision_rationale = (
            f"Total proposed monthly obligations of {format_inr(total_proposed_emi_paise)} "
            f"represents {foir_percent}% of declared monthly income ({format_inr(declared_income_paise)}), "
            f"exceeding the maximum allowed policy debt-to-income threshold of {max_foir_percent}%."
        )

    return {
        "status": "success",
        "customer_id": customer_id,
        "business_name": customer["business_name"],
        "principal_paise": principal_paise,
        "principal_formatted": format_inr(principal_paise),
        "tenure_months": tenure_months,
        "annual_interest_rate_percent": annual_interest_rate_percent,
        "proposed_monthly_emi_paise": proposed_emi_paise,
        "proposed_monthly_emi_formatted": format_inr(proposed_emi_paise),
        "existing_monthly_emi_paise": existing_emi_paise,
        "existing_monthly_emi_formatted": format_inr(existing_emi_paise),
        "declared_monthly_income_paise": declared_income_paise,
        "declared_monthly_income_formatted": format_inr(declared_income_paise),
        "total_monthly_obligations_paise": total_proposed_emi_paise,
        "total_monthly_obligations_formatted": format_inr(total_proposed_emi_paise),
        "foir_percent": foir_percent,
        "max_foir_threshold_percent": max_foir_percent,
        "meets_affordability_ratio": meets_affordability_ratio,
        "unresolved_fraud_flags_present": unresolved_fraud_flags,
        "recommendation": recommendation,
        "decision_rationale": decision_rationale,
        "disclaimer": (
            "Simulation only. Fictional banking assessment. Affordability does not constitute "
            "formal credit approval or loan sanction."
        ),
        "data_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Tool Specifications (OpenAI / Groq Function Calling Schema)
# ---------------------------------------------------------------------------

BANKING_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_account_summary",
            "description": "Fetch account profile, opening balance, current balance, and declared income.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_balance_tool",
            "description": "Fetch current available balance, total credits, and total debits.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_transactions_tool",
            "description": "Fetch recent posted transactions (credits and debits) up to a specified limit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent transactions to retrieve (e.g. 5).",
                        "default": 5,
                    }
                },
                "required": [],
            },
        },
    },
]

FRAUD_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_suspicious_activity_tool",
            "description": "Scan account transactions for large debit anomalies (>= ₹50,000) and rapid debit bursts (>= 3 debits in 24 hours).",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }
]

LOAN_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "calculate_loan_affordability_tool",
            "description": "Calculate monthly EMI and assess loan affordability against declared income and policy thresholds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "principal_paise": {
                        "type": "integer",
                        "description": "Requested loan principal in integer paise (e.g. ₹1 lakh = 10000000 paise).",
                    },
                    "tenure_months": {
                        "type": "integer",
                        "description": "Loan repayment duration in months (e.g. 12).",
                    },
                    "annual_interest_rate_percent": {
                        "type": "number",
                        "description": "Annual interest rate percentage (default 12.0%).",
                        "default": 12.0,
                    },
                    "unresolved_fraud_flags": {
                        "type": "boolean",
                        "description": "True if recent unresolved suspicious activity exists for this customer.",
                        "default": False,
                    },
                },
                "required": ["principal_paise", "tenure_months"],
            },
        },
    }
]

# Tool Allowlist per specialist agent
AGENT_TOOL_ALLOWLIST = {
    "banking": ["get_customer_account_summary", "get_account_balance_tool", "get_recent_transactions_tool"],
    "fraud": ["analyze_suspicious_activity_tool"],
    "loan": ["calculate_loan_affordability_tool"],
}
