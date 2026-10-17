"""
BankPilot AI - Core Test Suite
Automated unit and integration tests covering:
- Balance consistency & ledger math
- Overdraft protection and invalid transaction rejection
- Fraud detection rules (large debit threshold & burst velocity)
- EMI mathematical accuracy (including zero-interest edge case)
- Loan affordability & fraud-review interaction
- Input validation & nonfinite/negative value rejection
- Indian currency amount parsing (₹1 lakh, 1.5 lakhs, 100000, etc.)
- Multi-agent supervisor routing validation
- Specialist tool permission & allowlist enforcement
- Customer-scope security enforcement
- LLM failure and deterministic fallback resilience
- Combined Banking -> Fraud -> Loan workflow
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

import agents
import database
import tools


# ---------------------------------------------------------------------------
# Fixture: Temporary Isolated Database
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path):
    """Provides a fresh isolated SQLite database with reference-anchored seeds."""
    db_file = str(tmp_path / "test_bankpilot.db")
    # Fixed anchor time for reproducible timestamp testing
    ref_time = datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
    database.init_db(db_path=db_file, force_reseed=True, ref_time=ref_time)
    return db_file, ref_time


# ---------------------------------------------------------------------------
# 1. Database & Balance Consistency Tests
# ---------------------------------------------------------------------------


def test_balance_consistency_after_credit_and_debit(temp_db):
    db_file, _ = temp_db
    customer_id = "CUST-101"

    initial_bal = database.get_account_balance(customer_id, db_path=db_file)
    initial_paise = initial_bal["current_balance_paise"]

    # Post a credit of ₹20,000 (20,00,000 paise)
    credit_amount = database.rupees_to_paise(20000)
    database.add_transaction(
        customer_id=customer_id,
        txn_type="CREDIT",
        amount_paise=credit_amount,
        description="Test Credit",
        db_path=db_file,
    )

    bal_after_credit = database.get_account_balance(customer_id, db_path=db_file)
    assert bal_after_credit["current_balance_paise"] == initial_paise + credit_amount

    # Post a debit of ₹5,000 (5,00,000 paise)
    debit_amount = database.rupees_to_paise(5000)
    database.add_transaction(
        customer_id=customer_id,
        txn_type="DEBIT",
        amount_paise=debit_amount,
        description="Test Debit",
        db_path=db_file,
    )

    final_bal = database.get_account_balance(customer_id, db_path=db_file)
    assert final_bal["current_balance_paise"] == initial_paise + credit_amount - debit_amount


def test_rejection_of_overdraft_transaction(temp_db):
    db_file, _ = temp_db
    customer_id = "CUST-101"
    bal = database.get_account_balance(customer_id, db_path=db_file)
    current_paise = bal["current_balance_paise"]

    # Attempt debit of current_balance + ₹10,000
    excessive_debit = current_paise + database.rupees_to_paise(10000)
    with pytest.raises(ValueError, match="Insufficient funds"):
        database.add_transaction(
            customer_id=customer_id,
            txn_type="DEBIT",
            amount_paise=excessive_debit,
            description="Overdraft Attempt",
            db_path=db_file,
        )


def test_rejection_of_invalid_transaction_inputs(temp_db):
    db_file, _ = temp_db
    customer_id = "CUST-101"

    # Non-positive amount
    with pytest.raises(ValueError, match="positive integer"):
        database.add_transaction(
            customer_id=customer_id,
            txn_type="CREDIT",
            amount_paise=0,
            description="Zero amount",
            db_path=db_file,
        )

    with pytest.raises(ValueError, match="positive integer"):
        database.add_transaction(
            customer_id=customer_id,
            txn_type="CREDIT",
            amount_paise=-500,
            description="Negative amount",
            db_path=db_file,
        )

    # Invalid type
    with pytest.raises(ValueError, match="Invalid transaction type"):
        database.add_transaction(
            customer_id=customer_id,
            txn_type="TRANSFER",
            amount_paise=1000,
            description="Unknown type",
            db_path=db_file,
        )

    # Empty description
    with pytest.raises(ValueError, match="cannot be empty"):
        database.add_transaction(
            customer_id=customer_id,
            txn_type="CREDIT",
            amount_paise=1000,
            description="   ",
            db_path=db_file,
        )


# ---------------------------------------------------------------------------
# 2. Fraud Policy Tests
# ---------------------------------------------------------------------------


def test_fraud_analysis_clean_profile(temp_db):
    """CUST-101 has stable, normal transactions and should have 0 flags."""
    db_file, ref_time = temp_db
    res = tools.analyze_suspicious_activity_tool(
        customer_id="CUST-101", db_path=db_file, ref_time=ref_time
    )
    assert res["status"] == "success"
    assert res["has_suspicious_activity"] is False
    assert res["suspicious_flags_count"] == 0


def test_fraud_analysis_detects_large_debit_and_rapid_velocity(temp_db):
    """CUST-102 was seeded with 3 debits in 24h and a debit >= ₹50,000."""
    db_file, ref_time = temp_db
    res = tools.analyze_suspicious_activity_tool(
        customer_id="CUST-102", db_path=db_file, ref_time=ref_time
    )
    assert res["status"] == "success"
    assert res["has_suspicious_activity"] is True
    assert res["suspicious_flags_count"] >= 2

    flag_types = [f["flag_type"] for f in res["flags"]]
    assert "LARGE_DEBIT_THRESHOLD" in flag_types
    assert "RAPID_DEBIT_VELOCITY" in flag_types


# ---------------------------------------------------------------------------
# 3. EMI & Loan Affordability Mathematical Tests
# ---------------------------------------------------------------------------


def test_emi_calculation_against_independent_formula():
    """
    Independent Formula:
      P = 1,00,000 (10,000,000 paise)
      N = 12 months
      R = 12% p.a. -> r = 0.01 per month
      E = 100,000 * 0.01 * (1.01^12) / ((1.01^12) - 1)
      E = 8884.8788... rupees = 888488 paise.
    """
    principal_paise = 10000000  # ₹1 lakh
    tenure_months = 12
    annual_rate = 12.0

    emi_paise = tools.calculate_emi_paise(principal_paise, tenure_months, annual_rate)
    assert emi_paise == 888500 or abs(emi_paise - 888488) <= 20  # accurate within ~10 paise rounding


def test_zero_interest_emi():
    """Zero percent interest should strictly be P / N."""
    principal_paise = 12000000  # ₹1.2 lakh
    tenure_months = 12
    annual_rate = 0.0

    emi_paise = tools.calculate_emi_paise(principal_paise, tenure_months, annual_rate)
    assert emi_paise == 1000000  # ₹10,000 exactly in paise


def test_loan_affordability_approval_and_rejection(temp_db):
    db_file, _ = temp_db
    # CUST-101 has declared income ₹1,50,000 and existing EMI ₹15,000.
    # ₹1 lakh loan over 12 months EMI is ~₹8,885. Total = ₹23,885 (15.9% of income <= 50%).
    affordable_res = tools.calculate_loan_affordability_tool(
        customer_id="CUST-101",
        principal_paise=database.rupees_to_paise(100000),
        tenure_months=12,
        annual_interest_rate_percent=12.0,
        unresolved_fraud_flags=False,
        db_path=db_file,
    )
    assert affordable_res["status"] == "success"
    assert affordable_res["recommendation"] == "AFFORDABLE"
    assert affordable_res["meets_affordability_ratio"] is True

    # A massive loan of ₹50 lakh over 12 months EMI is ~₹4.4 lakh, exceeding income!
    not_affordable_res = tools.calculate_loan_affordability_tool(
        customer_id="CUST-101",
        principal_paise=database.rupees_to_paise(5000000),
        tenure_months=12,
        annual_interest_rate_percent=12.0,
        unresolved_fraud_flags=False,
        db_path=db_file,
    )
    assert not_affordable_res["status"] == "success"
    assert not_affordable_res["recommendation"] == "NOT_AFFORDABLE"
    assert not_affordable_res["meets_affordability_ratio"] is False


def test_loan_affordability_held_for_review_when_fraud_flags_present(temp_db):
    """When unresolved fraud flags are present, recommendation must be REQUIRES_MANUAL_REVIEW."""
    db_file, _ = temp_db
    res = tools.calculate_loan_affordability_tool(
        customer_id="CUST-101",
        principal_paise=database.rupees_to_paise(100000),
        tenure_months=12,
        annual_interest_rate_percent=12.0,
        unresolved_fraud_flags=True,  # Injected from fraud specialist
        db_path=db_file,
    )
    assert res["status"] == "success"
    assert res["recommendation"] == "REQUIRES_MANUAL_REVIEW"
    assert "Underwriter Manual Review" in res["decision_rationale"]


def test_loan_input_validations(temp_db):
    db_file, _ = temp_db
    # Invalid negative principal
    res1 = tools.calculate_loan_affordability_tool(
        customer_id="CUST-101",
        principal_paise=-1000,
        tenure_months=12,
        db_path=db_file,
    )
    assert res1["status"] == "error"

    # Invalid tenure 0
    res2 = tools.calculate_loan_affordability_tool(
        customer_id="CUST-101",
        principal_paise=1000000,
        tenure_months=0,
        db_path=db_file,
    )
    assert res2["status"] == "error"

    # Nonfinite rate
    res3 = tools.calculate_loan_affordability_tool(
        customer_id="CUST-101",
        principal_paise=1000000,
        tenure_months=12,
        annual_interest_rate_percent=float("nan"),
        db_path=db_file,
    )
    assert res3["status"] == "error"


# ---------------------------------------------------------------------------
# 4. Indian Currency Natural Language Parsing
# ---------------------------------------------------------------------------


def test_indian_amount_expressions():
    # ₹1 lakh = 10,000,000 paise
    assert tools.parse_indian_currency_to_paise("₹1 lakh") == 10000000
    assert tools.parse_indian_currency_to_paise("1 lakh") == 10000000
    assert tools.parse_indian_currency_to_paise("1.5 lakh") == 15000000
    assert tools.parse_indian_currency_to_paise("2.5 lakhs") == 25000000
    assert tools.parse_indian_currency_to_paise("₹1,00,000") == 10000000
    assert tools.parse_indian_currency_to_paise("100000") == 10000000
    assert tools.parse_indian_currency_to_paise("50000") == 5000000
    assert tools.parse_indian_currency_to_paise("1 crore") == 1000000000


def test_extract_loan_parameters():
    q = "Can I afford a ₹1 lakh loan over 12 months?"
    p, t = tools.extract_loan_parameters(q)
    assert p == 10000000  # ₹1 lakh
    assert t == 12

    q2 = "Can I borrow 50000 for 2 years?"
    p2, t2 = tools.extract_loan_parameters(q2)
    assert p2 == 5000000
    assert t2 == 24


# ---------------------------------------------------------------------------
# 5. Routing, Security, Allowlist, and Scope Enforcement
# ---------------------------------------------------------------------------


def test_deterministic_routing_for_five_use_cases():
    assert agents.route_query_deterministically("Show my balance and account details.") == ["banking"]
    assert agents.route_query_deterministically("Show my last five transactions.") == ["banking"]
    assert agents.route_query_deterministically("Are any recent transactions suspicious?") == ["fraud"]
    assert agents.route_query_deterministically("Can I afford a ₹1 lakh loan over 12 months?") == ["loan"]
    assert agents.route_query_deterministically(
        "Check my account and suspicious transactions before assessing whether I can afford a ₹1 lakh loan over 12 months."
    ) == ["banking", "fraud", "loan"]


def test_tool_allowlist_enforcement(temp_db):
    db_file, _ = temp_db
    # Fraud agent attempting to call loan affordability tool must be blocked
    result, summary = agents.dispatch_tool_call(
        agent_name="fraud",
        tool_name="calculate_loan_affordability_tool",
        tool_arguments={"principal_paise": 10000000, "tenure_months": 12},
        customer_id="CUST-101",
        db_path=db_file,
    )
    assert result["status"] == "error"
    assert "Security Violation" in result["message"]


def test_customer_scope_enforcement(temp_db):
    db_file, _ = temp_db
    # Pass malicious customer_id in tool arguments: application code must inject trusted customer
    result, _ = agents.dispatch_tool_call(
        agent_name="banking",
        tool_name="get_customer_account_summary",
        tool_arguments={"customer_id": "MALICIOUS-OVERRIDE"},
        customer_id="CUST-101",  # Verified injected customer ID
        db_path=db_file,
    )
    assert result["status"] == "success"
    assert result["customer_id"] == "CUST-101"
    assert result["customer_id"] != "MALICIOUS-OVERRIDE"


# ---------------------------------------------------------------------------
# 6. Fallback and Malformed LLM Handling
# ---------------------------------------------------------------------------


def test_llm_failure_graceful_fallback(temp_db):
    db_file, ref_time = temp_db
    # Mock Groq client throwing an exception
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("Groq Rate Limit Exceeded")

    with patch("agents.get_groq_client", return_value=mock_client):
        response = agents.process_user_query(
            customer_id="CUST-101",
            query="Show my balance and account details.",
            db_path=db_file,
            ref_time=ref_time,
        )

    # Response should succeed via deterministic fallback without crashing
    assert "Account Summary & Available Balance" in response.content
    assert response.route == ["banking"]
    # Traces should reflect fallback status
    statuses = [t.status for t in response.execution_traces]
    assert "fallback_deterministic" in statuses


def test_combined_workflow_fraud_findings_passed_to_loan(temp_db):
    """
    In the 3-agent combined workflow for CUST-102 (which has suspicious activity),
    the fraud specialist findings must propagate to the loan specialist so that
    affordability is evaluated and held for review.
    """
    db_file, ref_time = temp_db
    response = agents.process_user_query(
        customer_id="CUST-102",
        query="Check my account and suspicious transactions before assessing whether I can afford a ₹1 lakh loan over 12 months.",
        db_path=db_file,
        ref_time=ref_time,
    )

    assert response.route == ["banking", "fraud", "loan"]
    # Check that content contains all 3 sections
    assert "Account Summary & Available Balance" in response.content or "Available Balance" in response.content
    assert "Suspicious Transaction Analysis" in response.content
    assert "FLAGGED FOR REVIEW" in response.content
    assert "HELD FOR UNDERWRITER MANUAL REVIEW" in response.content
    # Raw supporting tool output contains all three
    assert "banking" in response.supporting_tool_results
    assert "fraud" in response.supporting_tool_results
    assert "loan" in response.supporting_tool_results


# ---------------------------------------------------------------------------
# 7. Regression Tests: Grounding, Status Reporting, Simulator & Reset
# ---------------------------------------------------------------------------


def test_demo_account_descriptions_grounded(temp_db):
    """Verify demo account descriptions accurately match stored database facts."""
    db_file, _ = temp_db
    info_101 = database.get_demo_account_info("CUST-101")
    info_102 = database.get_demo_account_info("CUST-102")

    cust_101 = database.get_customer("CUST-101", db_path=db_file)
    cust_102 = database.get_customer("CUST-102", db_path=db_file)

    assert info_101["business_name"] == cust_101["business_name"]
    assert info_101["proprietor"] == cust_101["owner_name"]
    assert "₹15,000" in info_101["description"]

    assert info_102["business_name"] == cust_102["business_name"]
    assert info_102["proprietor"] == cust_102["owner_name"]
    assert "₹65,000" in info_102["description"]


def test_honest_llm_status_reporting(temp_db):
    """Verify honest status reporting across offline fallback and API failure."""
    db_file, _ = temp_db

    # Test 1: Offline fallback (no client)
    with patch("agents.get_groq_client", return_value=None):
        resp_fallback = agents.process_user_query(
            customer_id="CUST-101",
            query="Show my balance and account details.",
            db_path=db_file,
        )
        assert resp_fallback.llm_status == "fallback_deterministic"

    # Test 2: API error fallback (client throws exception)
    mock_bad_client = MagicMock()
    mock_bad_client.chat.completions.create.side_effect = RuntimeError("Groq Connection Refused")
    with patch("agents.get_groq_client", return_value=mock_bad_client):
        resp_error = agents.process_user_query(
            customer_id="CUST-101",
            query="Show my balance and account details.",
            db_path=db_file,
        )
        assert resp_error.llm_status == "api_error_fallback"


def test_simulator_updates_refresh_account_and_balance(temp_db):
    """Verify posting a simulator transaction immediately updates account balance for next request."""
    db_file, _ = temp_db
    customer_id = "CUST-101"

    bal_before = database.get_account_balance(customer_id, db_path=db_file)
    initial_balance = bal_before["current_balance_paise"]

    # Simulate posting a ₹25,000 inventory procurement debit
    debit_paise = database.rupees_to_paise(25000)
    database.add_transaction(
        customer_id=customer_id,
        txn_type="DEBIT",
        amount_paise=debit_paise,
        description="Simulator Inventory Procurement",
        db_path=db_file,
    )

    # Next query should immediately reflect new balance
    resp = agents.process_user_query(
        customer_id=customer_id,
        query="Show my balance and account details.",
        db_path=db_file,
    )
    b_tool_res = resp.supporting_tool_results["banking"]
    assert b_tool_res["current_balance_paise"] == initial_balance - debit_paise


def test_reset_demo_db_restores_baseline(temp_db):
    """Verify reset_demo_db completely restores original accounts and transaction counts."""
    db_file, ref_time = temp_db
    customer_id = "CUST-101"

    # Add a custom transaction
    database.add_transaction(
        customer_id=customer_id,
        txn_type="CREDIT",
        amount_paise=database.rupees_to_paise(50000),
        description="Temporary Custom Credit",
        db_path=db_file,
    )
    txns_modified = database.get_recent_transactions(customer_id, limit=20, db_path=db_file)
    assert len(txns_modified) == 6  # 5 seeded + 1 custom

    # Reset
    database.reset_demo_db(db_path=db_file, ref_time=ref_time)
    txns_restored = database.get_recent_transactions(customer_id, limit=20, db_path=db_file)
    assert len(txns_restored) == 5
    assert not any("Temporary Custom Credit" in t["description"] for t in txns_restored)
