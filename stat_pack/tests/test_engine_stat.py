"""
통계 팩 엔진 단위 테스트 (SPEC §7-9 손계산 픽스처).

실행: cd src && python -m pytest tests/test_engine_stat.py -v
모든 수치 비교는 Decimal 절대오차 1e-12 이내.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
import pytest
from engine.stat_engine import (
    recompute_proportions,
    reweight,
    nonresponse_sensitivity,
    compare_strata,
    derive_verdict,
    derive_doc_verdict,
)

D = Decimal
EPS = D("1e-12")


def approx(a, b):
    """Decimal 절대오차 1e-12 이내."""
    if a is None or b is None:
        return a == b
    return abs(D(str(a)) - D(str(b))) <= EPS


# ──────────────────────────────────────────────
# 재계산 (SPEC §7-9 사례)
# ──────────────────────────────────────────────

def test_recompute_match():
    """h1 n=80,s=64; h2 n=20,s=4; reported=68% → p=0.68, diff=0%p, reproduced=true"""
    strata = [{"id": "h1", "n": 80, "success": 64}, {"id": "h2", "n": 20, "success": 4}]
    r = recompute_proportions(strata, "68", "percent", "0.05")
    assert approx(r["recomputed"], "0.68")
    assert approx(r["diff_pp"], "0.00")
    assert r["reproduced"] is True
    assert r["n_total"] == 100
    # source=synthetic이면 numeric_accuracy=미확인은 도구 계층에서 처리; 엔진은 일치 반환
    assert r["numeric_accuracy"] == "일치"


def test_recompute_mismatch():
    """reported=70% → diff=-2%p, reproduced=false"""
    strata = [{"id": "h1", "n": 80, "success": 64}, {"id": "h2", "n": 20, "success": 4}]
    r = recompute_proportions(strata, "70", "percent", "0.05")
    assert approx(r["diff_pp"], "-2.00")
    assert r["reproduced"] is False
    assert r["numeric_accuracy"] == "오류 확인"


def test_recompute_tolerance_boundary():
    """reported=68.05% → 기본 오차(0.05%p) 이내 → reproduced=true"""
    strata = [{"id": "h1", "n": 80, "success": 64}, {"id": "h2", "n": 20, "success": 4}]
    r = recompute_proportions(strata, "68.05", "percent", "0.05")
    assert r["reproduced"] is True


def test_recompute_tolerance_over():
    """reported=68.0501% → 오차 초과 → reproduced=false"""
    strata = [{"id": "h1", "n": 80, "success": 64}, {"id": "h2", "n": 20, "success": 4}]
    r = recompute_proportions(strata, "68.0501", "percent", "0.05")
    assert r["reproduced"] is False


# ──────────────────────────────────────────────
# 재가중 역전 (SPEC §7-9)
# ──────────────────────────────────────────────

def test_reweight_reversal():
    """W=(0.5,0.5), T=0.60 이상 → p_std=0.50, 뒤집힘"""
    strata = [{"id": "h1", "n": 80, "success": 64}, {"id": "h2", "n": 20, "success": 4}]
    ref = [{"id": "h1", "share": "0.5"}, {"id": "h2", "share": "0.5"}]
    r = reweight(strata, ref, "0.60", "이상", "2.0")
    assert approx(r["reweighted_estimate"], "0.50")
    assert approx(r["delta_pp"], "-18.00")
    assert r["robustness"] == "뒤집힘"


def test_reweight_weak_margin():
    """W=(0.7,0.3), T=0.60 이상, weak=2%p → p_std=0.62, margin=2%p → 취약"""
    strata = [{"id": "h1", "n": 80, "success": 64}, {"id": "h2", "n": 20, "success": 4}]
    ref = [{"id": "h1", "share": "0.7"}, {"id": "h2", "share": "0.3"}]
    r = reweight(strata, ref, "0.60", "이상", "2.0")
    assert approx(r["reweighted_estimate"], "0.62")
    assert approx(r["margin_pp"], "2.00")
    assert r["robustness"] == "취약"


def test_reweight_robust():
    """W=(0.8,0.2), T=0.60 이상 → p_std=0.68, 견고"""
    strata = [{"id": "h1", "n": 80, "success": 64}, {"id": "h2", "n": 20, "success": 4}]
    ref = [{"id": "h1", "share": "0.8"}, {"id": "h2", "share": "0.2"}]
    r = reweight(strata, ref, "0.60", "이상", "2.0")
    assert approx(r["reweighted_estimate"], "0.68")
    assert r["robustness"] == "견고"


# ──────────────────────────────────────────────
# 무응답 민감도 (SPEC §7-9)
# ──────────────────────────────────────────────

def test_nonresponse_weak():
    """M=100,n=50,s=30; d=(-20,0,20), T=0.55 이상 → baseline=0.60, 취약"""
    strata = [{"id": "h1", "n": 50, "invited": 100, "success": 30}]
    r = nonresponse_sensitivity(strata, "0.55", "이상", ["-20", "0", "20"])
    assert approx(r["baseline"], "0.60")
    assert r["robustness"] == "취약"
    assert r["n_fail"] == 1


def test_nonresponse_strict_boundary():
    """T=0.55 이상: d=-10에서 이상은 통과, 초과는 실패"""
    strata = [{"id": "h1", "n": 50, "invited": 100, "success": 30}]
    # 경계값 d=-10 추가
    r_gte = nonresponse_sensitivity(strata, "0.55", "이상",  ["-20", "-10", "0", "20"])
    r_gt  = nonresponse_sensitivity(strata, "0.55", "초과", ["-20", "-10", "0", "20"])
    # d=-10: P ≈ 0.55 → 이상은 통과, 초과는 실패
    sc_gte = next(s for s in r_gte["scenarios"] if s["delta_pp"] == "-10.00")
    sc_gt  = next(s for s in r_gt["scenarios"]  if s["delta_pp"] == "-10.00")
    assert sc_gte["passes"] is True
    assert sc_gt["passes"] is False


def test_nonresponse_clipped():
    """M=100,n=50,s=45, d=20 → u_h 절단 → clipped_strata_count=1"""
    strata = [{"id": "h1", "n": 50, "invited": 100, "success": 45}]
    r = nonresponse_sensitivity(strata, "0.80", "이상", ["0", "20"])
    sc = next(s for s in r["scenarios"] if s["delta_pp"] == "20.00")
    assert sc["clipped_strata_count"] == 1


# ──────────────────────────────────────────────
# 층별 비교 (SPEC §7-9)
# ──────────────────────────────────────────────

def test_compare_reversal():
    """Simpson 역전: 전체 A<B, 층별 A>B → reversal=True, 뒤집힘"""
    group_a = [{"id": "easy", "n": 10, "success": 9},
               {"id": "hard", "n": 100, "success": 30}]
    group_b = [{"id": "easy", "n": 100, "success": 80},
               {"id": "hard", "n": 10, "success": 2}]
    r = compare_strata(group_a, group_b)
    # 전체: A=39/110 ≈ 0.355, B=82/110 ≈ 0.745 → D<0
    # 층별: easy A=0.9>B=0.8, hard A=0.3>B=0.2 → D_h>0 모두
    assert r["reversal"] is True
    assert r["robustness"] == "뒤집힘"


def test_compare_mixed():
    """A=(8/10,2/10), B=(6/10,4/10) → D=0, 혼합 → 취약"""
    group_a = [{"id": "h1", "n": 10, "success": 8},
               {"id": "h2", "n": 10, "success": 2}]
    group_b = [{"id": "h1", "n": 10, "success": 6},
               {"id": "h2", "n": 10, "success": 4}]
    r = compare_strata(group_a, group_b)
    assert r["mixed"] is True
    assert r["robustness"] == "취약"


# ──────────────────────────────────────────────
# 판정 규칙 (SPEC §7-8)
# ──────────────────────────────────────────────

def test_verdict_causal():
    assert derive_verdict("미확인", None, "cache", True, "causal_or_forecast") == "사용 보류"

def test_verdict_synthetic():
    assert derive_verdict("미확인", None, "synthetic", True, "proportion") == "사용 보류"

def test_verdict_error():
    assert derive_verdict("오류 확인", None, "cache", True, "proportion") == "수정 후 인용"

def test_verdict_weak():
    assert derive_verdict("일치", "취약", "cache", True, "proportion") == "조건부 인용"

def test_verdict_robust():
    assert derive_verdict("일치", "견고", "cache", True, "proportion") == "인용 가능"

def test_doc_verdict_strictest():
    verdicts = ["인용 가능", "조건부 인용", "사용 보류"]
    assert derive_doc_verdict(verdicts) == "사용 보류"
