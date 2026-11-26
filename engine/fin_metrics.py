"""
engine/fin_metrics.py
금융 팩 지표 계산 — 네트워크 호출 없는 순수 Decimal 연산.

SPEC §6-1·§6-2 구현. 오프라인 테스트 가능.
외부 API·파일 I/O는 tools/ 계층에서만 한다.
"""

from decimal import Decimal, ROUND_HALF_EVEN, InvalidOperation
from typing import Any

ENGINE_VERSION = "0.1.0"

# ── SPEC §6-1 지표 이름 상수 ──────────────────────────────────────────────────
METRIC_DEBT_RATIO   = "debt_ratio"
METRIC_INT_COVERAGE = "interest_coverage"
METRIC_CFO_TO_DEBT  = "cfo_to_debt"

# ── SPEC §6-2 허용 오차 ───────────────────────────────────────────────────────
TOL_DEBT_RATIO_PP  = Decimal("1")     # ±1 %p  (SPEC 명시)
TOL_INT_COVERAGE   = Decimal("0.1")   # ±0.1 배 (SPEC 명시)
TOL_CFO_TO_DEBT    = Decimal("0.01")  # ±0.01 배 (SPEC 미명시 → 프로젝트 정책)


def _d(v: Any) -> Decimal:
    """숫자·문자열을 Decimal 로 변환한다. 변환 불가 시 InvalidOperation."""
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


# ── 지표 계산 ─────────────────────────────────────────────────────────────────

def calc_debt_ratio(total_liabilities: Any, total_equity: Any) -> Decimal:
    """
    부채비율(%) = 부채총계 / 자본총계 × 100  (SPEC §6-1)

    Raises
    ------
    ZeroDivisionError : 자본총계가 0 일 때.
    InvalidOperation  : 입력이 유효한 수치가 아닐 때.
    """
    tl = _d(total_liabilities)
    te = _d(total_equity)
    if te == 0:
        raise ZeroDivisionError("자본총계가 0 이므로 부채비율을 계산할 수 없습니다.")
    return (tl / te * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def calc_interest_coverage(operating_income: Any, interest_expense: Any) -> Decimal:
    """
    이자보상배율 = 영업이익 / 이자비용  (SPEC §6-1)

    Raises
    ------
    ZeroDivisionError : 이자비용이 0 일 때.
    """
    oi = _d(operating_income)
    ie = _d(interest_expense)
    if ie == 0:
        raise ZeroDivisionError("이자비용이 0 이므로 이자보상배율을 계산할 수 없습니다.")
    return (oi / ie).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def calc_narrow_debt(
    short_term_borrowings: Any,
    current_portion_lt_debt: Any,
    long_term_borrowings: Any,
    bonds: Any,
) -> Decimal:
    """
    차입금(좁은 정의, narrow) = 단기차입금 + 유동성장기부채 + 장기차입금 + 사채
    (SPEC §6-1)
    """
    return (
        _d(short_term_borrowings)
        + _d(current_portion_lt_debt)
        + _d(long_term_borrowings)
        + _d(bonds)
    )


def calc_broad_debt(narrow: Any, lease_liabilities: Any) -> Decimal:
    """
    차입금(넓은 정의, broad) = narrow + 리스부채  (SPEC §6-1)
    """
    return _d(narrow) + _d(lease_liabilities)


def calc_cfo_to_debt(operating_cash_flow: Any, debt: Any) -> Decimal:
    """
    영업현금흐름 커버 = 영업활동현금흐름 / 차입금  (SPEC §6-1)

    Raises
    ------
    ZeroDivisionError : 차입금이 0 일 때.
    """
    cfo = _d(operating_cash_flow)
    d   = _d(debt)
    if d == 0:
        raise ZeroDivisionError("차입금이 0 이므로 영업현금흐름 커버를 계산할 수 없습니다.")
    return (cfo / d).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


# ── 재현 판정 ─────────────────────────────────────────────────────────────────

def check_reproduced(metric: str, recomputed: Decimal, reported: Decimal) -> bool:
    """
    보고 수치와 재계산 수치가 허용 오차 안에 있으면 True (SPEC §6-2).

    허용 오차 (프로젝트 정책)
    -------------------------
    - debt_ratio        : ±1 %p    (SPEC 명시)
    - interest_coverage : ±0.1 배  (SPEC 명시)
    - cfo_to_debt       : ±0.01 배 (SPEC 미명시 → 프로젝트 정책)

    Raises
    ------
    ValueError : 지원하지 않는 metric.
    """
    diff = abs(recomputed - _d(reported))
    tol = {
        METRIC_DEBT_RATIO:   TOL_DEBT_RATIO_PP,
        METRIC_INT_COVERAGE: TOL_INT_COVERAGE,
        METRIC_CFO_TO_DEBT:  TOL_CFO_TO_DEBT,
    }.get(metric)
    if tol is None:
        raise ValueError(f"지원하지 않는 지표: {metric}")
    return diff <= tol
