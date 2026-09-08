"""Unit tests for `app/services/altana_jobs.py`.

Pure-function tests — no DB, no HTTP, no chain. The wrapper only does
calldata encoding (4-byte selector + ABI-encoded args) and decoded-tuple
parsing. Tests run RED against the absent module, GREEN after implementation.

Spec: docs/category-study.md §ERC-8183 buyer-side.
"""

from __future__ import annotations

from datetime import datetime, timezone

# These imports must FAIL until altana_jobs.py is implemented (TDD RED).
import eth_abi
import pytest

from app.services.altana_abi import (
    COMMERCE_FUNCTION_ABI,
    ERC20_FUNCTION_ABI,
    JOB_TUPLE_TYPE,
)
from app.services.altana_jobs import (
    APPROVE_SELECTOR,
    CREATE_JOB_SELECTOR,
    FUND_SELECTOR,
    JobState,
    build_approve_calldata,
    build_create_job_calldata,
    build_fund_calldata,
    parse_job_state,
)

# Fixed test fixtures — deterministic so calldata hashes match across runs.
_PROVIDER = "0xea4daa3100a767e86fded867729ae7446476eba6"  # commerce proxy (as provider, for tests)
_EVALUATOR = "0x51895229e12f9876011789b04f8698af06ccd6da"  # router
_COMMERCE = "0xea4daa3100a767e86fded867729ae7446476eba6"
_U_TOKEN = "0xcE24439F2D9C6a2289F741120FE202248B666666"
_EXPIRED_AT = 1_900_000_000  # 2030-03-17 (well in the future for tests)
_DESCRIPTION = "Audit wallet 0xabc…'s Venus position"
_BUDGET_WEI = 100_000_000_000_000_000  # 0.1 $U


# ============================================================================
# T1 RED — function selectors
# ============================================================================


def test_create_job_selector_is_keccak_first_4_bytes():
    """createJob(address,address,uint256,string,address) → 4-byte selector."""
    assert isinstance(CREATE_JOB_SELECTOR, str)
    assert CREATE_JOB_SELECTOR.startswith("0x")
    assert len(CREATE_JOB_SELECTOR) == 10  # 0x + 8 hex chars


def test_fund_selector_is_keccak_first_4_bytes():
    assert FUND_SELECTOR.startswith("0x") and len(FUND_SELECTOR) == 10


def test_approve_selector_is_keccak_first_4_bytes():
    assert APPROVE_SELECTOR.startswith("0x") and len(APPROVE_SELECTOR) == 10


# ============================================================================
# T2 RED — build_create_job_calldata
# ============================================================================


def test_create_job_calldata_starts_with_selector():
    calldata = build_create_job_calldata(
        provider=_PROVIDER,
        evaluator=_EVALUATOR,
        expired_at=_EXPIRED_AT,
        description=_DESCRIPTION,
        hook=_EVALUATOR,  # router doubles as hook
    )
    assert calldata.startswith(CREATE_JOB_SELECTOR)


def test_create_job_calldata_is_valid_hex():
    """Output must be hex-encoded (decodable as bytes)."""
    calldata = build_create_job_calldata(
        provider=_PROVIDER,
        evaluator=_EVALUATOR,
        expired_at=_EXPIRED_AT,
        description=_DESCRIPTION,
        hook=_EVALUATOR,
    )
    raw = bytes.fromhex(calldata.removeprefix("0x"))
    # selector (4) + at least 5*32 = 164 bytes for the static args
    assert len(raw) > 164


def test_create_job_rejects_zero_provider():
    with pytest.raises(ValueError, match="provider"):
        build_create_job_calldata(
            provider="0x0000000000000000000000000000000000000000",
            evaluator=_EVALUATOR,
            expired_at=_EXPIRED_AT,
            description=_DESCRIPTION,
            hook=_EVALUATOR,
        )


def test_create_job_rejects_invalid_address():
    with pytest.raises(ValueError, match="(provider|invalid)"):
        build_create_job_calldata(
            provider="not-an-address",
            evaluator=_EVALUATOR,
            expired_at=_EXPIRED_AT,
            description=_DESCRIPTION,
            hook=_EVALUATOR,
        )


def test_create_job_rejects_expiry_in_past():
    """Contract rejects expiredAt <= now+5min; wrapper mirrors."""
    past = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
    with pytest.raises(ValueError, match="expir"):
        build_create_job_calldata(
            provider=_PROVIDER,
            evaluator=_EVALUATOR,
            expired_at=past,
            description=_DESCRIPTION,
            hook=_EVALUATOR,
        )


def test_create_job_description_is_canonical_json_safe():
    """Description with non-ASCII must serialize to ABI string (no canonical hash here)."""
    calldata = build_create_job_calldata(
        provider=_PROVIDER,
        evaluator=_EVALUATOR,
        expired_at=_EXPIRED_AT,
        description="Audit with é and 🚀",  # non-ASCII
        hook=_EVALUATOR,
    )
    raw = bytes.fromhex(calldata.removeprefix("0x"))
    # utf-8 bytes for the description end up in the calldata
    assert "Audit with é and 🚀".encode("utf-8") in raw


# ============================================================================
# T3 RED — build_fund_calldata
# ============================================================================


def test_fund_calldata_starts_with_selector():
    calldata = build_fund_calldata(job_id=42, expected_budget=_BUDGET_WEI)
    assert calldata.startswith(FUND_SELECTOR)


def test_fund_calldata_encodes_job_id_and_budget():
    """job_id=42, budget=1e17 → both appear as 32-byte words in the calldata."""
    calldata = build_fund_calldata(job_id=42, expected_budget=_BUDGET_WEI)
    raw = bytes.fromhex(calldata.removeprefix("0x"))
    # selector (4) + jobId (32) + budget (32) + optParams_offset (32) + empty bytes (32)
    assert len(raw) == 4 + 32 * 4
    # jobId = 42, right-aligned in 32 bytes
    assert raw[4 + 31] == 42
    # budget = 1e17 = 0x16345785d8a0000
    assert raw[4 + 32 + 31 - 7 : 4 + 32 + 32] == (1 * 10**17).to_bytes(8, "big").rjust(8, b"\x00")


def test_fund_rejects_zero_job_id():
    with pytest.raises(ValueError, match="job"):
        build_fund_calldata(job_id=0, expected_budget=_BUDGET_WEI)


def test_fund_rejects_zero_budget():
    with pytest.raises(ValueError, match="budget"):
        build_fund_calldata(job_id=42, expected_budget=0)


# ============================================================================
# T4 RED — build_approve_calldata
# ============================================================================


def test_approve_calldata_starts_with_selector():
    calldata = build_approve_calldata(spender=_COMMERCE, amount=_BUDGET_WEI)
    assert calldata.startswith(APPROVE_SELECTOR)


def test_approve_calldata_encodes_spender_and_amount():
    calldata = build_approve_calldata(spender=_COMMERCE, amount=_BUDGET_WEI)
    raw = bytes.fromhex(calldata.removeprefix("0x"))
    # selector (4) + spender (32, padded) + amount (32)
    assert len(raw) == 4 + 32 + 32
    # last 20 bytes of spender word = the address (left-padded with zeros)
    spender_word = raw[4 + 12 : 4 + 32]
    assert bytes.fromhex(_COMMERCE.removeprefix("0x")).lower() == spender_word.lower()


def test_approve_rejects_zero_spender():
    with pytest.raises(ValueError, match="spender"):
        build_approve_calldata(
            spender="0x0000000000000000000000000000000000000000",
            amount=_BUDGET_WEI,
        )


# ============================================================================
# T5 RED — parse_job_state
# ============================================================================


def test_parse_job_state_open_status():
    """Open job: status=0, all fields populated."""
    raw = (
        42,  # id
        "0x1111111111111111111111111111111111111111",  # client
        "0x2222222222222222222222222222222222222222",  # provider
        "0x3333333333333333333333333333333333333333",  # evaluator
        "Test job",  # description
        0,  # budget (not funded yet)
        _EXPIRED_AT,  # expiredAt
        0,  # status = Open
        "0x4444444444444444444444444444444444444444",  # hook
        0,  # submittedAt
        b"\x00" * 32,  # deliverable
    )
    state = parse_job_state(raw)
    assert isinstance(state, JobState)
    assert state.job_id == 42
    assert state.status == "Open"
    assert state.budget_wei == 0


def test_parse_job_state_funded_status():
    """Funded job: status=1, budget > 0."""
    raw = (
        7,
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
        "Job",
        _BUDGET_WEI,
        _EXPIRED_AT,
        1,  # Funded
        "0x4444444444444444444444444444444444444444",
        0,
        b"\x00" * 32,
    )
    state = parse_job_state(raw)
    assert state.status == "Funded"
    assert state.budget_wei == _BUDGET_WEI


def test_parse_job_state_submitted_status():
    """Submitted job: status=2, submittedAt > 0, deliverable non-zero."""
    deliverable = bytes.fromhex("ab" * 32)
    raw = (
        7,
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
        "Job",
        _BUDGET_WEI,
        _EXPIRED_AT,
        2,  # Submitted
        "0x4444444444444444444444444444444444444444",
        1_700_000_000,  # submittedAt
        deliverable,
    )
    state = parse_job_state(raw)
    assert state.status == "Submitted"
    assert state.submitted_at == 1_700_000_000
    assert state.deliverable == deliverable


def test_parse_job_state_completed_status():
    raw = (7, "0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20, "Job",
           _BUDGET_WEI, _EXPIRED_AT, 3, "0x" + "44" * 20, 1_700_000_000, b"\x00" * 32)
    state = parse_job_state(raw)
    assert state.status == "Completed"


def test_parse_job_state_rejects_unknown_status():
    raw = (7, "0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20, "Job",
           0, _EXPIRED_AT, 99, "0x" + "44" * 20, 0, b"\x00" * 32)
    with pytest.raises(ValueError, match="status"):
        parse_job_state(raw)


# ============================================================================
# T6 RED — ABI cross-checks (integration with the abi module)
# ============================================================================


def test_create_job_function_in_abi():
    fn_names = {item["name"] for item in COMMERCE_FUNCTION_ABI}
    assert "createJob" in fn_names
    assert "fund" in fn_names
    assert "getJob" in fn_names


def test_erc20_function_in_abi():
    fn_names = {item["name"] for item in ERC20_FUNCTION_ABI}
    assert "approve" in fn_names


def test_job_tuple_type_is_parseable_by_eth_abi():
    """The JOB_TUPLE_TYPE string must be a valid eth_abi canonical type.

    Smoke test: round-trip a zero job through encode → decode.
    """
    import eth_abi

    from app.services.altana_abi import JOB_TUPLE_TYPE

    zero_job = (
        0,
        "0x0000000000000000000000000000000000000000",
        "0x0000000000000000000000000000000000000000",
        "0x0000000000000000000000000000000000000000",
        "",
        0,
        0,
        0,
        "0x0000000000000000000000000000000000000000",
        0,
        b"\x00" * 32,
    )
    encoded = eth_abi.encode([JOB_TUPLE_TYPE], [zero_job])
    decoded = eth_abi.decode([JOB_TUPLE_TYPE], encoded)
    assert decoded[0][0] == 0  # id

# ============================================================================
# T7 TRIANGULATE — round-trip tests (encode → decode must preserve inputs).
# Catches ABI misalignment between the builder and the on-chain selector.
# ============================================================================


def test_create_job_calldata_round_trip():
    calldata = build_create_job_calldata(
        provider=_PROVIDER,
        evaluator=_EVALUATOR,
        expired_at=_EXPIRED_AT,
        description=_DESCRIPTION,
        hook=_EVALUATOR,
    )
    raw = bytes.fromhex(calldata.removeprefix("0x"))
    assert raw[:4] == bytes.fromhex(CREATE_JOB_SELECTOR.removeprefix("0x"))
    decoded = eth_abi.decode(
        ["address", "address", "uint256", "string", "address"],
        raw[4:],
    )
    assert decoded[0].lower() == _PROVIDER.lower()
    assert decoded[1].lower() == _EVALUATOR.lower()
    assert decoded[2] == _EXPIRED_AT
    assert decoded[3] == _DESCRIPTION
    assert decoded[4].lower() == _EVALUATOR.lower()


def test_fund_calldata_round_trip():
    calldata = build_fund_calldata(job_id=99_999, expected_budget=_BUDGET_WEI)
    raw = bytes.fromhex(calldata.removeprefix("0x"))
    assert raw[:4] == bytes.fromhex(FUND_SELECTOR.removeprefix("0x"))
    dec = eth_abi.decode(["uint256", "uint256", "bytes"], raw[4:])
    assert dec[0] == 99_999
    assert dec[1] == _BUDGET_WEI
    assert dec[2] == b""


def test_approve_calldata_round_trip():
    calldata = build_approve_calldata(spender=_COMMERCE, amount=_BUDGET_WEI)
    raw = bytes.fromhex(calldata.removeprefix("0x"))
    assert raw[:4] == bytes.fromhex(APPROVE_SELECTOR.removeprefix("0x"))
    dec = eth_abi.decode(["address", "uint256"], raw[4:])
    assert dec[0].lower() == _COMMERCE.lower()
    assert dec[1] == _BUDGET_WEI


def test_parse_job_state_round_trip_via_encode():
    """Build a Job tuple, encode on-chain style, decode via parse_job_state."""
    raw_job = (
        123,
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
        "round-trip",
        _BUDGET_WEI,
        _EXPIRED_AT,
        4,  # Rejected status (less-common branch)
        "0x4444444444444444444444444444444444444444",
        1_700_000_000,
        b"\xab" * 32,
    )
    enc = eth_abi.encode([JOB_TUPLE_TYPE], [raw_job])
    decoded_tuple = eth_abi.decode([JOB_TUPLE_TYPE], enc)[0]
    state = parse_job_state(decoded_tuple)
    assert state.job_id == 123
    assert state.status == "Rejected"
    assert state.budget_wei == _BUDGET_WEI
    assert state.deliverable == b"\xab" * 32
    assert state.description == "round-trip"


def test_create_job_with_long_description():
    long_desc = "A" * 1024
    calldata = build_create_job_calldata(
        provider=_PROVIDER,
        evaluator=_EVALUATOR,
        expired_at=_EXPIRED_AT,
        description=long_desc,
        hook=_EVALUATOR,
    )
    raw = bytes.fromhex(calldata.removeprefix("0x"))
    dec = eth_abi.decode(
        ["address", "address", "uint256", "string", "address"],
        raw[4:],
    )
    assert dec[3] == long_desc
