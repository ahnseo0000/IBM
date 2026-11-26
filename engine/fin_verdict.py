"""
engine/fin_verdict.py
금융 팩 시나리오 계산·금리 충격·주장 판정 — 네트워크 호출 없는 순수 계산.

SPEC §6-3·§6-4·§6-5 구현. 오프라인 테스트 가능.
외부 API·파일 I/O는 tools/ 계층에서만 한다.
"""

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

from engine.fin_metrics import (
    _d,
    calc_narrow_debt,
    calc_broad_debt,
    calc_debt_ratio,
    calc_interest_coverage,
    calc_cfo_to_debt,
    METRIC_DEBT_RATIO,
    METRIC_INT_COVERAGE,
    METRIC_CFO_TO_DEBT,
)

# ── SPEC §6-3 시나리오 ID ─────────────────────────────────────────────────────
SCENARIO_S0 = "S0"  # 보고서 기준 (연결, narrow)
SCENARIO_S1 = "S1"  # 리스부채 포함 (broad)
SCENARIO_S2 = "S2"  # 별도 재무제표
SCENARIO_S3 = "S3"  # S1 + S2

# ── SPEC §6-5 판정 단어 ───────────────────────────────────────────────────────
VERDICT_SOLID        = "견고"
VERDICT_WEAK         = "취약"
VERDICT_REVERSED     = "뒤집힘"
VERDICT_REVERSED_DEF = "뒤집힘(확정)"


# ── §6-3  가정 조정 시나리오 ──────────────────────────────────────────────────

def compute_scenarios(
    *,
    metric: str,
    threshold: Any,
    direction: str,
    cfs_narrow_amounts: dict,
    cfs_lease: Any,
    ofs_narrow_amounts: dict | None = None,
    ofs_lease: Any = None,
) -> dict:
    """
    S0~S3 시나리오별 지표값과 기준 충족 여부를 반환한다 (SPEC §6-3).

    Parameters
    ----------
    metric : str
        "debt_ratio" | "interest_coverage" | "cfo_to_debt"
    threshold : numeric
        판정 기준값.
    direction : str
        "이하" (부채비율) 또는 "이상" (이자보상배율·현금흐름 커버).
    cfs_narrow_amounts : dict
        연결 재무제표 계정 금액.
        필수 키: total_liabilities, total_equity, operating_income,
                  interest_expense, short_term_borrowings,
                  current_portion_lt_debt, long_term_borrowings,
                  bonds, operating_cash_flow
    cfs_lease : numeric
        연결 재무제표 리스부채.
    ofs_narrow_amounts : dict, optional
        별도 재무제표 계정 금액 (S2·S3 시나리오에 필요).
    ofs_lease : numeric, optional
        별도 재무제표 리스부채.

    Returns
    -------
    dict
        {"scenarios": [{"id","value","passes","margin_pct"}],
         "worst_case": {"id","value"}, "n_fail": int}
    """
    T = _d(threshold)

    def _metric_val(amounts: dict, lease: Any, debt_def: str) -> Decimal:
        """
        지표값 계산.

        설계 원칙 (SPEC §6-1):
        - debt_ratio      : 부채총계/자본총계 — debt_def·lease 무관
        - interest_coverage: 영업이익/이자비용 — debt_def·lease 무관
        - cfo_to_debt     : 영업현금흐름/차입금 — debt_def(narrow/broad) 에 따라 D 가 달라짐
                             broad = narrow + lease → lease 가 필요함
        """
        narrow = calc_narrow_debt(
            amounts["short_term_borrowings"],
            amounts["current_portion_lt_debt"],
            amounts["long_term_borrowings"],
            amounts["bonds"],
        )
        if metric == METRIC_DEBT_RATIO:
            # 리스부채는 이미 부채총계에 포함 → debt_def 무시
            return calc_debt_ratio(amounts["total_liabilities"], amounts["total_equity"])
        if metric == METRIC_INT_COVERAGE:
            # 이자비용은 리스 포함 여부와 무관 → debt_def 무시
            return calc_interest_coverage(amounts["operating_income"], amounts["interest_expense"])
        if metric == METRIC_CFO_TO_DEBT:
            debt = narrow if debt_def == "narrow" else calc_broad_debt(narrow, lease)
            return calc_cfo_to_debt(amounts["operating_cash_flow"], debt)
        raise ValueError(f"지원하지 않는 지표: {metric}")

    def _passes(val: Decimal) -> bool:
        return (val <= T) if direction == "이하" else (val >= T)

    def _margin(val: Decimal) -> str:
        if T == 0:
            return "N/A"
        m = ((T - val) / abs(T) * 100) if direction == "이하" else ((val - T) / abs(T) * 100)
        return str(m.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))

    rows = []
    # S0: 연결 narrow
    v0 = _metric_val(cfs_narrow_amounts, cfs_lease, "narrow")
    rows.append({"id": SCENARIO_S0, "value": str(v0), "passes": _passes(v0), "margin_pct": _margin(v0)})

    # S1: cfo_to_debt → broad(narrow+lease) 로 D 변경
    #     debt_ratio / interest_coverage → S1 은 의미 없으므로 생략
    # cfs_lease=None 이면 리스부채 미확인 → 계산 불가, 미확인 행으로 표시
    if metric == METRIC_CFO_TO_DEBT:
        if cfs_lease is None:
            rows.append({
                "id": SCENARIO_S1, "value": None, "passes": None,
                "margin_pct": None, "note": "리스부채 미확인 — S1 계산 불가",
            })
        else:
            v1 = _metric_val(cfs_narrow_amounts, cfs_lease, "broad")
            rows.append({"id": SCENARIO_S1, "value": str(v1), "passes": _passes(v1), "margin_pct": _margin(v1)})

    # S2·S3: 별도 재무제표
    if ofs_narrow_amounts is not None:
        # S2: 별도 narrow — debt_ratio, interest_coverage, cfo_to_debt 모두 별도 수치로 계산
        v2 = _metric_val(ofs_narrow_amounts, ofs_lease, "narrow")
        rows.append({"id": SCENARIO_S2, "value": str(v2), "passes": _passes(v2), "margin_pct": _margin(v2)})
        # S3: 별도 broad (cfo_to_debt 만 의미 있음)
        if metric == METRIC_CFO_TO_DEBT:
            if ofs_lease is None:
                rows.append({
                    "id": SCENARIO_S3, "value": None, "passes": None,
                    "margin_pct": None, "note": "별도 리스부채 미확인 — S3 계산 불가",
                })
            else:
                v3 = _metric_val(ofs_narrow_amounts, ofs_lease, "broad")
                rows.append({"id": SCENARIO_S3, "value": str(v3), "passes": _passes(v3), "margin_pct": _margin(v3)})

    # 집계 (value=None 인 미확인 행은 제외)
    valid_rows = [r for r in rows if r.get("value") is not None]
    n_fail = sum(1 for r in valid_rows if not r["passes"])
    worst = (min if direction == "이상" else max)(valid_rows, key=lambda r: Decimal(r["value"]))
    return {"scenarios": rows, "worst_case": {"id": worst["id"], "value": worst["value"]}, "n_fail": n_fail}


# ── §6-4  금리 충격 ───────────────────────────────────────────────────────────

def compute_rate_stress(
    *,
    metric: str,
    threshold: Any,
    operating_income: Any,
    interest_expense: Any,
    operating_cash_flow: Any,
    debt: Any,
    base_rate_latest_bp: Any,
    hist_max_hike_bp: Any,
    floating_shares: list | None = None,
) -> dict:
    """
    금리 충격 시나리오를 계산한다 (SPEC §6-4).

    변동금리 비중 α 는 공시로 알 수 없는 가정값이다 (기본 [0.5, 1.0]).

    Parameters
    ----------
    metric : str
        "interest_coverage" | "cfo_to_debt"
    threshold : numeric
        판정 기준값.
    operating_income, interest_expense, operating_cash_flow, debt : numeric
        연결 narrow 기준 재무 수치.
    base_rate_latest_bp : numeric
        ECOS 최신 기준금리 (bp).
    hist_max_hike_bp : numeric
        과거 5년 내 12개월 최대 상승폭 (bp). ECOS 실측값.
    floating_shares : list of float, optional
        변동금리 비중 α 값 목록. 기본 [0.5, 1.0].

    Returns
    -------
    dict
        {"base_rate_latest", "hist_max_hike_bp",
         "by_alpha": [{"alpha","value_at_100bp","value_at_hist_max","breakeven_bp"}],
         "already_below": bool}
    """
    if metric not in (METRIC_INT_COVERAGE, METRIC_CFO_TO_DEBT):
        raise ValueError(f"fin_rate_stress 는 {metric} 를 지원하지 않습니다.")

    if floating_shares is None:
        floating_shares = [0.5, 1.0]

    T    = _d(threshold)
    oi   = _d(operating_income)
    ie   = _d(interest_expense)
    cfo  = _d(operating_cash_flow)
    D    = _d(debt)
    BP   = _d("10000")  # 1 bp = 1/10000

    already_below = (
        (oi / ie < T) if metric == METRIC_INT_COVERAGE and ie != 0
        else (cfo / D < T) if metric == METRIC_CFO_TO_DEBT and D != 0
        else False
    )

    by_alpha = []
    for alpha_raw in floating_shares:
        a = _d(str(alpha_raw))

        def _val_at(delta_bp: Decimal, _a: Decimal = a) -> Decimal | None:
            delta_rate = delta_bp / BP
            if metric == METRIC_INT_COVERAGE:
                new_ie = ie + _a * D * delta_rate
                if new_ie == 0:
                    return None
                return (oi / new_ie).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            else:
                if D == 0:
                    return None
                return ((cfo - _a * D * delta_rate) / D).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

        v100   = _val_at(_d("100"))
        v_hist = _val_at(_d(str(hist_max_hike_bp)))

        # 손익분기 Δ* (SPEC §6-4)
        breakeven_bp = None
        if a != 0 and D != 0:
            if metric == METRIC_INT_COVERAGE and T != 0:
                num = oi / T - ie
                den = a * D / BP
                if den != 0:
                    breakeven_bp = str((num / den).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
            elif metric == METRIC_CFO_TO_DEBT:
                num = cfo - T * D
                den = a * D / BP
                if den != 0:
                    breakeven_bp = str((num / den).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))

        by_alpha.append({
            "alpha":             str(a),
            "value_at_100bp":    str(v100)   if v100   is not None else None,
            "value_at_hist_max": str(v_hist) if v_hist is not None else None,
            "breakeven_bp":      breakeven_bp,
        })

    return {
        "base_rate_latest": str(_d(str(base_rate_latest_bp))),
        "hist_max_hike_bp": str(_d(str(hist_max_hike_bp))),
        "by_alpha":         by_alpha,
        "already_below":    already_below,
    }


# ── §6-5  주장 판정 규칙 ──────────────────────────────────────────────────────

def compute_verdict(
    *,
    metric: str,
    threshold: Any,
    direction: str,
    scenarios: list[dict],
    rate_stress: dict | None = None,
) -> str:
    """
    주장 판정 규칙을 적용해 판정 단어를 반환한다 (SPEC §6-5).

    규칙 (위에서 처음 맞는 것 적용)
    1. S0 로 재계산해도 기준 미달 → "뒤집힘(확정)"
    2. S0 충족이지만 S1·S2·S3 중 하나라도 미달 → "뒤집힘"
    3. 모든 정의 충족이지만 금리 충격에서 미달 → "취약"
       (부채비율: 최악 시나리오 여유폭 ≤ 10% → 취약)
    4. 위 어느 것도 아님 → "견고"
    """
    T = _d(threshold)

    def _passes(val_str: str) -> bool:
        v = _d(val_str)
        return (v <= T) if direction == "이하" else (v >= T)

    s0 = next((s for s in scenarios if s["id"] == SCENARIO_S0), None)
    if s0 is None:
        raise ValueError("S0 시나리오가 없습니다.")

    if not _passes(s0["value"]):
        return VERDICT_REVERSED_DEF

    if any(not s["passes"] for s in scenarios if s["id"] != SCENARIO_S0):
        return VERDICT_REVERSED

    if metric == METRIC_DEBT_RATIO:
        worst = min(
            (s["margin_pct"] for s in scenarios if s["margin_pct"] != "N/A"),
            key=lambda x: Decimal(x),
            default=None,
        )
        return VERDICT_WEAK if worst is not None and Decimal(worst) <= Decimal("10") else VERDICT_SOLID

    if rate_stress:
        for entry in rate_stress.get("by_alpha", []):
            v100   = entry.get("value_at_100bp")
            v_hist = entry.get("value_at_hist_max")
            if (v100   is not None and not _passes(v100)) or \
               (v_hist is not None and not _passes(v_hist)):
                return VERDICT_WEAK

    return VERDICT_SOLID
