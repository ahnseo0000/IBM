"""
tests/test_fin_engine.py
SPEC §8 손계산 픽스처로 engine/fin_metrics.py + engine/fin_verdict.py 를 검증한다.
네트워크·API·파일 I/O 없음 — 완전 오프라인.

실행: pytest tests/test_fin_engine.py -v
"""

import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.fin_metrics import (
    ENGINE_VERSION,
    METRIC_CFO_TO_DEBT,
    METRIC_DEBT_RATIO,
    METRIC_INT_COVERAGE,
    TOL_CFO_TO_DEBT,
    TOL_DEBT_RATIO_PP,
    TOL_INT_COVERAGE,
    calc_broad_debt,
    calc_cfo_to_debt,
    calc_debt_ratio,
    calc_interest_coverage,
    calc_narrow_debt,
    check_reproduced,
)
from engine.fin_verdict import (
    SCENARIO_S0,
    SCENARIO_S1,
    VERDICT_REVERSED,
    VERDICT_REVERSED_DEF,
    VERDICT_SOLID,
    VERDICT_WEAK,
    compute_rate_stress,
    compute_scenarios,
    compute_verdict,
)

# ══════════════════════════════════════════════════════════════════════════════
# SPEC §8 손계산 픽스처 (공통)
# 부채총계 600, 자본총계 400, 영업이익 120, 이자비용 30
# 단기차입금 100, 장기차입금 200, 리스부채 50, 영업현금흐름 90
# narrow=300, broad=350, T=3.0배
# ══════════════════════════════════════════════════════════════════════════════
TL  = Decimal("600")
TE  = Decimal("400")
OI  = Decimal("120")
IE  = Decimal("30")
STB = Decimal("100")
CLB = Decimal("0")
LTB = Decimal("200")
BND = Decimal("0")
LSE = Decimal("50")
CFO = Decimal("90")
T_THRESHOLD = Decimal("3.0")

AMOUNTS_CFS = {
    "total_liabilities":        TL,
    "total_equity":             TE,
    "operating_income":         OI,
    "interest_expense":         IE,
    "short_term_borrowings":    STB,
    "current_portion_lt_debt":  CLB,
    "long_term_borrowings":     LTB,
    "bonds":                    BND,
    "operating_cash_flow":      CFO,
}


# ══════════════════════════════════════════════════════════════════════════════
# 1. 허용 오차 상수 확인 (정책 고정)
# ══════════════════════════════════════════════════════════════════════════════

class TestToleranceConstants:
    def test_debt_ratio_tol(self):
        assert TOL_DEBT_RATIO_PP == Decimal("1")

    def test_int_coverage_tol(self):
        assert TOL_INT_COVERAGE == Decimal("0.1")

    def test_cfo_to_debt_tol(self):
        """cfo_to_debt 허용 오차는 ±0.01 (프로젝트 정책)."""
        assert TOL_CFO_TO_DEBT == Decimal("0.01")


# ══════════════════════════════════════════════════════════════════════════════
# 2. 지표 계산
# ══════════════════════════════════════════════════════════════════════════════

class TestCalcMetrics:
    def test_debt_ratio(self):
        """부채비율 = 600/400×100 = 150%"""
        assert calc_debt_ratio(TL, TE) == Decimal("150.00")

    def test_narrow_debt(self):
        """narrow = 100+0+200+0 = 300"""
        assert calc_narrow_debt(STB, CLB, LTB, BND) == Decimal("300")

    def test_broad_debt(self):
        """broad = 300+50 = 350"""
        assert calc_broad_debt(calc_narrow_debt(STB, CLB, LTB, BND), LSE) == Decimal("350")

    def test_interest_coverage(self):
        """이자보상배율 = 120/30 = 4.0배"""
        assert calc_interest_coverage(OI, IE) == Decimal("4.0000")

    def test_cfo_to_debt_narrow(self):
        """현금흐름 커버 = 90/300 = 0.30"""
        assert calc_cfo_to_debt(CFO, calc_narrow_debt(STB, CLB, LTB, BND)) == Decimal("0.3000")

    def test_zero_equity_raises(self):
        with pytest.raises(ZeroDivisionError):
            calc_debt_ratio(600, 0)

    def test_zero_interest_raises(self):
        with pytest.raises(ZeroDivisionError):
            calc_interest_coverage(120, 0)

    def test_zero_debt_raises(self):
        with pytest.raises(ZeroDivisionError):
            calc_cfo_to_debt(90, 0)

    def test_string_input_accepted(self):
        assert calc_debt_ratio("600", "400") == Decimal("150.00")

    def test_invalid_input_raises(self):
        with pytest.raises(InvalidOperation):
            calc_debt_ratio("abc", 400)


# ══════════════════════════════════════════════════════════════════════════════
# 3. 재현 판정 (reproduced) — cfo_to_debt 허용 오차 ±0.01
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckReproduced:
    # debt_ratio
    def test_debt_ratio_exact(self):
        assert check_reproduced(METRIC_DEBT_RATIO, Decimal("150.00"), Decimal("150.00")) is True

    def test_debt_ratio_at_boundary(self):
        # |150 - 151| = 1.00 ≤ 1 → True (경계 포함)
        assert check_reproduced(METRIC_DEBT_RATIO, Decimal("150.00"), Decimal("151.00")) is True

    def test_debt_ratio_outside(self):
        # |150 - 151.01| = 1.01 > 1 → False
        assert check_reproduced(METRIC_DEBT_RATIO, Decimal("150.00"), Decimal("151.01")) is False

    # interest_coverage
    def test_int_coverage_at_boundary(self):
        # |4.0 - 4.1| = 0.10 ≤ 0.1 → True
        assert check_reproduced(METRIC_INT_COVERAGE, Decimal("4.0"), Decimal("4.1")) is True

    def test_int_coverage_outside(self):
        # |4.0 - 4.11| = 0.11 > 0.1 → False
        assert check_reproduced(METRIC_INT_COVERAGE, Decimal("4.0"), Decimal("4.11")) is False

    # cfo_to_debt — 허용 오차 ±0.01
    def test_cfo_to_debt_exact(self):
        assert check_reproduced(METRIC_CFO_TO_DEBT, Decimal("0.30"), Decimal("0.30")) is True

    def test_cfo_to_debt_at_boundary(self):
        # |0.30 - 0.31| = 0.01 ≤ 0.01 → True (경계 포함)
        assert check_reproduced(METRIC_CFO_TO_DEBT, Decimal("0.30"), Decimal("0.31")) is True

    def test_cfo_to_debt_just_outside(self):
        # |0.30 - 0.311| = 0.011 > 0.01 → False
        assert check_reproduced(METRIC_CFO_TO_DEBT, Decimal("0.30"), Decimal("0.311")) is False

    def test_cfo_to_debt_old_tol_now_fails(self):
        # 0단계에서 ±0.1 로 허용됐던 케이스 (|0.30-0.40|=0.10).
        # 정책이 ±0.01 로 바뀌어 이제는 False 여야 한다.
        assert check_reproduced(METRIC_CFO_TO_DEBT, Decimal("0.30"), Decimal("0.40")) is False

    def test_unsupported_metric_raises(self):
        with pytest.raises(ValueError):
            check_reproduced("unknown_metric", Decimal("1"), Decimal("1"))


# ══════════════════════════════════════════════════════════════════════════════
# 4. 손익분기 충격 Δ* (SPEC §8)
# α=1.0, narrow=300, T=3.0 → Δ* = (120/3−30)/(1.0×300/10000) = 10/0.03 ≈ 333.33 bp
# ══════════════════════════════════════════════════════════════════════════════

class TestRateStressBreakeven:
    def _run(self, alpha=1.0, hist_bp=300):
        return compute_rate_stress(
            metric=METRIC_INT_COVERAGE,
            threshold=T_THRESHOLD,
            operating_income=OI,
            interest_expense=IE,
            operating_cash_flow=CFO,
            debt=calc_narrow_debt(STB, CLB, LTB, BND),
            base_rate_latest_bp=350,
            hist_max_hike_bp=hist_bp,
            floating_shares=[alpha],
        )

    def test_breakeven_alpha_1(self):
        res = self._run(alpha=1.0)
        bp_val = Decimal(res["by_alpha"][0]["breakeven_bp"])
        assert abs(bp_val - Decimal("333.33")) < Decimal("0.1")

    def test_already_below_false(self):
        assert self._run()["already_below"] is False

    def test_value_at_100bp_above_threshold(self):
        """α=1.0, Δ=100bp → IE_new=30+1×300×0.01=33 → ICR=120/33≈3.636 > 3.0"""
        res = self._run(alpha=1.0)
        v = Decimal(res["by_alpha"][0]["value_at_100bp"])
        assert v > T_THRESHOLD


# ══════════════════════════════════════════════════════════════════════════════
# 5. 시나리오 계산
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeScenarios:
    def test_s0_interest_coverage(self):
        res = compute_scenarios(
            metric=METRIC_INT_COVERAGE,
            threshold=T_THRESHOLD,
            direction="이상",
            cfs_narrow_amounts=AMOUNTS_CFS,
            cfs_lease=LSE,
        )
        s0 = next(s for s in res["scenarios"] if s["id"] == SCENARIO_S0)
        assert Decimal(s0["value"]) == Decimal("4.0000")
        assert s0["passes"] is True

    def test_s1_broad_cfo(self):
        """broad=350 → CFO/debt=90/350≈0.2571 < 0.3 → fail"""
        res = compute_scenarios(
            metric=METRIC_CFO_TO_DEBT,
            threshold=Decimal("0.3"),
            direction="이상",
            cfs_narrow_amounts=AMOUNTS_CFS,
            cfs_lease=LSE,
        )
        s1 = next(s for s in res["scenarios"] if s["id"] == SCENARIO_S1)
        assert Decimal(s1["value"]) < Decimal("0.3")
        assert s1["passes"] is False

    def test_n_fail_counts(self):
        res = compute_scenarios(
            metric=METRIC_CFO_TO_DEBT,
            threshold=Decimal("0.3"),
            direction="이상",
            cfs_narrow_amounts=AMOUNTS_CFS,
            cfs_lease=LSE,
        )
        # S0: 0.3000 ≥ 0.3 → pass, S1: ~0.2571 < 0.3 → fail
        assert res["n_fail"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# 6. 판정 규칙 경계값 (SPEC §6-5)
# ══════════════════════════════════════════════════════════════════════════════

class TestVerdict:
    def _scen(self, s0_pass: bool, s1_pass: bool | None = None):
        rows = [{"id": SCENARIO_S0, "value": "4.0", "passes": s0_pass, "margin_pct": "30.00"}]
        if s1_pass is not None:
            rows.append({"id": SCENARIO_S1, "value": "2.8", "passes": s1_pass, "margin_pct": "-6.67"})
        return rows

    def test_reversed_confirmed_when_s0_fails(self):
        scenarios = [{"id": SCENARIO_S0, "value": "2.5", "passes": False, "margin_pct": "-16.67"}]
        assert compute_verdict(
            metric=METRIC_INT_COVERAGE, threshold="3.0", direction="이상", scenarios=scenarios
        ) == VERDICT_REVERSED_DEF

    def test_reversed_when_alt_fails(self):
        assert compute_verdict(
            metric=METRIC_INT_COVERAGE, threshold="3.0", direction="이상",
            scenarios=self._scen(s0_pass=True, s1_pass=False)
        ) == VERDICT_REVERSED

    def test_weak_when_rate_stress_fails(self):
        stress = {"by_alpha": [{"alpha": "1.0", "value_at_100bp": "2.5",
                                 "value_at_hist_max": "2.8", "breakeven_bp": "333"}]}
        assert compute_verdict(
            metric=METRIC_INT_COVERAGE, threshold="3.0", direction="이상",
            scenarios=self._scen(s0_pass=True), rate_stress=stress
        ) == VERDICT_WEAK

    def test_solid_all_pass(self):
        stress = {"by_alpha": [{"alpha": "1.0", "value_at_100bp": "3.5",
                                 "value_at_hist_max": "3.2", "breakeven_bp": "1000"}]}
        assert compute_verdict(
            metric=METRIC_INT_COVERAGE, threshold="3.0", direction="이상",
            scenarios=self._scen(s0_pass=True), rate_stress=stress
        ) == VERDICT_SOLID

    def test_debt_ratio_weak_when_margin_lte_10(self):
        scenarios = [{"id": SCENARIO_S0, "value": "145.0", "passes": True, "margin_pct": "3.33"}]
        assert compute_verdict(
            metric=METRIC_DEBT_RATIO, threshold="150.0", direction="이하", scenarios=scenarios
        ) == VERDICT_WEAK

    def test_debt_ratio_solid_when_margin_gt_10(self):
        scenarios = [{"id": SCENARIO_S0, "value": "120.0", "passes": True, "margin_pct": "20.00"}]
        assert compute_verdict(
            metric=METRIC_DEBT_RATIO, threshold="150.0", direction="이하", scenarios=scenarios
        ) == VERDICT_SOLID


# ══════════════════════════════════════════════════════════════════════════════
# 7. 봉투 크기 (2 KB 제한)
# ══════════════════════════════════════════════════════════════════════════════

class TestEnvelope:
    def test_ok_envelope_under_2kb(self):
        import json
        from tools._envelope import make_ok
        payload = make_ok(
            tool="fin_recompute_ratios",
            call_args={"corp": "00126380", "year": 2023, "metric": "debt_ratio",
                       "reported_value": 150.0, "fs_div": "CFS"},
            source_data={"fixture": "SPEC_S8"},
            source="synthetic",
            applicable=False,
            result={"recomputed": "150.00", "reported": "150.0",
                    "diff": "0.00", "reproduced": True},
            applicability_notes=["0단계 synthetic"],
            notes=["가상 자료"],
        )
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        assert size <= 2048, f"봉투 크기 초과: {size} bytes"
        assert payload["ok"] is True

    def test_error_envelope_structure(self):
        from tools._envelope import make_error
        err = make_error(tool="fin_recompute_ratios", code="NO_DATA", message="DART 조회 실패")
        assert err["ok"] is False
        assert err["error"]["code"] == "NO_DATA"

    def test_oversized_result_returns_bad_input(self):
        from tools._envelope import make_ok
        payload = make_ok(
            tool="fin_recompute_ratios",
            call_args={}, source_data={}, source="synthetic",
            applicable=False, result={"data": "x" * 3000},
        )
        assert payload["ok"] is False
        assert payload["error"]["code"] == "BAD_INPUT"


# ── fin_make_review_report 4 KB 상한 테스트 ──────────────────────────────────

class TestMakeReviewReport4KB:
    """
    fin_make_review_report 의 4 KB 상한 정책을 검증한다.
    - C사 2주장(최대 케이스)이 4 KB 이내에서 ok=True 를 반환해야 한다.
    - A사 1주장은 2 KB 이내여야 한다 (이전 공통 상한 기준 여전히 충족).
    - 4 KB 초과 시 BAD_INPUT 오류를 반환해야 한다.
    """

    def _call(self, corp, claims_list):
        import json
        from tools.fin_make_review_report import fin_make_review_report
        r = fin_make_review_report(corp=corp, year=2025, claims=json.dumps(claims_list))
        raw = json.dumps(r, ensure_ascii=False, default=str)
        return r, len(raw.encode("utf-8"))

    def test_c_two_claims_within_4kb(self):
        """C사 2주장 응답이 4 KB(4096 bytes) 이내이고 ok=True."""
        claims = [
            {"metric": "interest_coverage", "threshold": 1.5, "direction": "이상",
             "reported_value": 2.1, "corp_label": "C사"},
            {"metric": "cfo_to_debt", "threshold": 0.35, "direction": "이상",
             "reported_value": 0.45, "corp_label": "C사"},
        ]
        r, size = self._call("003490", claims)
        content = r.content if hasattr(r, "content") else r
        assert content["ok"] is True, f"ok=False: {content}"
        assert size <= 4096, f"C사 2주장 응답 {size} bytes > 4096"

    def test_a_one_claim_within_2kb(self):
        """A사 1주장 응답이 2 KB(2048 bytes) 이내이고 ok=True."""
        claims = [
            {"metric": "debt_ratio", "threshold": 100, "direction": "이하",
             "reported_value": 41.9, "corp_label": "A사"},
        ]
        r, size = self._call("035420", claims)
        content = r.content if hasattr(r, "content") else r
        assert content["ok"] is True, f"ok=False: {content}"
        assert size <= 2048, f"A사 1주장 응답 {size} bytes > 2048"

    def test_oversized_returns_bad_input(self):
        """4 KB 초과 payload 는 BAD_INPUT 오류를 반환해야 한다."""
        import json
        from tools.fin_make_review_report import _check_size_4k
        big_payload = {
            "ok": True,
            "tool": "fin_make_review_report",
            "result": {"data": "x" * 5000},
        }
        result = _check_size_4k(big_payload)
        assert result["ok"] is False
        assert result["error"]["code"] == "BAD_INPUT"
