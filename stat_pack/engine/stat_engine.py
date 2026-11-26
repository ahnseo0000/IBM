"""
통계 팩 순수 계산 엔진 (SPEC §7-4, §7-5, §7-6, §7-8).

- 네트워크 호출 없음. 오프라인 테스트 가능.
- 모든 비율 계산은 Decimal(정밀도 34, ROUND_HALF_EVEN).
- 판정은 반올림 전 Decimal 값으로 수행.
- 표시용 반올림: %·%p 소수 둘째 자리.
"""

from decimal import Decimal, getcontext, ROUND_HALF_EVEN, InvalidOperation
from typing import Optional

getcontext().prec = 34
getcontext().rounding = ROUND_HALF_EVEN

D = Decimal
_ZERO = D("0")
_ONE = D("1")
_HUNDRED = D("100")
_DISPLAY = D("0.01")       # 소수 둘째 자리 반올림용
_DEFAULT_TOL = D("0.05")   # 기본 허용 오차 %p


# ──────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────

def _d(v) -> D:
    """str / int / float → Decimal. 실패 시 InvalidOperation."""
    return D(str(v))


def _fmt(v: Optional[D]) -> Optional[str]:
    """Decimal → 표시용 반올림 문자열(소수 둘째 자리). None이면 None."""
    if v is None:
        return None
    return str(v.quantize(_DISPLAY))


def _direction_ok(value: D, threshold: D, direction: str) -> bool:
    """방향 연산자 적용. direction: 이상|초과|이하|미만."""
    if direction == "이상":
        return value >= threshold
    if direction == "초과":
        return value > threshold
    if direction == "이하":
        return value <= threshold
    if direction == "미만":
        return value < threshold
    raise ValueError(f"지원하지 않는 direction: {direction!r}")


def _validate_strata(strata: list[dict]) -> None:
    """층 목록 기본 검증. 오류 시 ValueError."""
    ids = [s["id"] for s in strata]
    if len(ids) != len(set(ids)):
        raise ValueError("BAD_INPUT: 중복 층 ID")
    for s in strata:
        n = _d(s["n"])
        if n < _ZERO:
            raise ValueError(f"BAD_INPUT: 층 {s['id']} n 음수")
        if "success" in s and s["success"] is not None:
            sc = _d(s["success"])
            if sc < _ZERO or sc > n:
                raise ValueError(f"BAD_INPUT: 층 {s['id']} success 범위 위반 (0 <= success <= n)")
        if "rate" in s and s["rate"] is not None:
            r = _d(s["rate"])
            if r < _ZERO or r > _ONE:
                raise ValueError(f"BAD_INPUT: 층 {s['id']} rate 범위 위반 (0~1)")


# ──────────────────────────────────────────────
# 1. stat_recompute_proportions (SPEC §7-4)
# ──────────────────────────────────────────────

def recompute_proportions(
    strata: list[dict],
    reported_value: str,
    reported_unit: str = "percent",
    tolerance_pp: str = "0.05",
) -> dict:
    """
    층별 집계에서 전체 비율을 재계산하고 보고값과 비교한다.

    Returns:
        recomputed(str|None), reported(str), diff_pp(str|None),
        reproduced(bool|None), numeric_accuracy(str),
        n_total(int), estimate_interval(None)
    """
    _validate_strata(strata)
    tol = _d(tolerance_pp)

    # reported → 0~1 비율로 정규화
    rv = _d(reported_value)
    if reported_unit == "percent":
        reported_prop = rv / _HUNDRED
    else:
        reported_prop = rv

    # 재계산
    total_n = _ZERO
    total_success = _ZERO
    all_null = True

    for s in strata:
        n = _d(s["n"])
        total_n += n
        if n == _ZERO:
            continue
        if "success" in s and s["success"] is not None:
            sc = _d(s["success"])
            total_success += sc
            all_null = False
        elif "rate" in s and s["rate"] is not None:
            r = _d(s["rate"])
            total_success += n * r
            all_null = False

    if total_n == _ZERO or all_null:
        return {
            "recomputed": None,
            "reported": _fmt(reported_prop),
            "diff_pp": None,
            "reproduced": None,
            "numeric_accuracy": "미확인",
            "n_total": 0,
            "estimate_interval": None,
        }

    p = total_success / total_n
    diff_pp = (_HUNDRED * p) - (_HUNDRED * reported_prop)
    reproduced = abs(diff_pp) <= tol

    # source가 synthetic이면 numeric_accuracy = 미확인 (호출자가 처리)
    numeric_accuracy = "일치" if reproduced else "오류 확인"

    return {
        "recomputed": _fmt(p),
        "reported": _fmt(reported_prop),
        "diff_pp": _fmt(diff_pp),
        "reproduced": reproduced,
        "numeric_accuracy": numeric_accuracy,
        "n_total": int(total_n),
        "estimate_interval": None,
    }


# ──────────────────────────────────────────────
# 2. stat_reweight (SPEC §7-4)
# ──────────────────────────────────────────────

def reweight(
    strata: list[dict],
    reference_strata: list[dict],
    criterion_threshold: str,
    criterion_direction: str,
    weak_margin_pp: str = "2.0",
) -> dict:
    """
    사후층화 재가중 후 기준 충족 여부를 판정한다.

    strata: [{id, n, success?, rate?}]
    reference_strata: [{id, population_count or share}]
    """
    _validate_strata(strata)
    T = _d(criterion_threshold)
    weak = _d(weak_margin_pp)

    # 기준 가중치 계산
    ref_map = {}
    total_pop = _ZERO
    for rs in reference_strata:
        if "population_count" in rs and rs["population_count"] is not None:
            pc = _d(rs["population_count"])
            if pc < _ZERO:
                raise ValueError("BAD_INPUT: population_count 음수")
            ref_map[rs["id"]] = pc
            total_pop += pc
        elif "share" in rs and rs["share"] is not None:
            sh = _d(rs["share"])
            if sh < _ZERO:
                raise ValueError("BAD_INPUT: share 음수")
            ref_map[rs["id"]] = sh
            total_pop += sh

    if total_pop <= _ZERO:
        raise ValueError("BAD_INPUT: 기준 구성 합이 0 이하")

    # 층 비율 계산
    strata_map = {}
    total_n = _ZERO
    for s in strata:
        n = _d(s["n"])
        total_n += n
        p_h = None
        if n > _ZERO:
            if "success" in s and s["success"] is not None:
                p_h = _d(s["success"]) / n
            elif "rate" in s and s["rate"] is not None:
                p_h = _d(s["rate"])
        strata_map[s["id"]] = {"n": n, "p_h": p_h}

    # baseline (비가중)
    baseline_num = _ZERO
    for sid, sv in strata_map.items():
        if sv["p_h"] is not None:
            baseline_num += sv["n"] * sv["p_h"]
    baseline = baseline_num / total_n if total_n > _ZERO else None

    # 재가중
    p_std_num = _ZERO
    p_std_applicable = True
    max_weight_factor = _ZERO
    q_map = {}
    for sid, sv in strata_map.items():
        q_h = sv["n"] / total_n if total_n > _ZERO else _ZERO
        q_map[sid] = q_h
        W_h = ref_map.get(sid, _ZERO) / total_pop
        if W_h > _ZERO and sv["n"] == _ZERO:
            # 양의 W인데 응답 없음 → applicable=False
            p_std_applicable = False
        if W_h > _ZERO and sv["p_h"] is not None:
            p_std_num += W_h * sv["p_h"]
            if q_h > _ZERO:
                g_h = W_h / q_h
                if g_h > max_weight_factor:
                    max_weight_factor = g_h

    p_std = p_std_num if p_std_applicable else None

    # 판정
    if p_std is None or baseline is None:
        robustness = None
        passes = None
        margin_pp = None
        delta_pp = None
        break_condition = "필수 자료 미확보로 무너지는 조건 미확인"
    else:
        passes = _direction_ok(p_std, T, criterion_direction)
        baseline_passes = _direction_ok(baseline, T, criterion_direction)
        if criterion_direction in ("이상", "초과"):
            margin_pp = _HUNDRED * (p_std - T)
        else:
            margin_pp = _HUNDRED * (T - p_std)

        if not baseline_passes or not passes:
            robustness = "뒤집힘"
            break_condition = f"기준 구성으로 재가중하면 비율은 {_fmt(_HUNDRED*p_std)}%로 기준({_fmt(_HUNDRED*T)}% {criterion_direction})을 충족하지 않음"
        elif margin_pp <= weak:
            robustness = "취약"
            break_condition = f"재가중 후 여유폭 {_fmt(margin_pp)}%p로 기준 미달 근접"
        else:
            robustness = "견고"
            break_condition = "검토한 조건에서 기준 미충족 없음"

    return {
        "baseline": _fmt(baseline),
        "reweighted_estimate": _fmt(p_std),
        "delta_pp": _fmt(_HUNDRED * (p_std - baseline)) if p_std is not None and baseline is not None else None,
        "passes": passes,
        "margin_pp": _fmt(margin_pp) if margin_pp is not None else None,
        "max_weight_factor": _fmt(max_weight_factor),
        "robustness": robustness,
        "break_condition": break_condition,
        "applicable": p_std_applicable,
    }


# ──────────────────────────────────────────────
# 3. stat_nonresponse_sensitivity (SPEC §7-5)
# ──────────────────────────────────────────────

def nonresponse_sensitivity(
    strata: list[dict],          # [{id, n, invited, success?, rate?}]
    criterion_threshold: str,
    criterion_direction: str,
    delta_pp_list: list[str] | None = None,
    reference_strata: list[dict] | None = None,
) -> dict:
    if delta_pp_list is None:
        delta_pp_list = ["-20", "0", "20"]

    _validate_strata(strata)
    T = _d(criterion_threshold)
    deltas = sorted(set(_d(d) for d in delta_pp_list))

    # 가중치 V_h
    if reference_strata:
        total_ref = sum(_d(rs.get("population_count") or rs.get("share", "0")) for rs in reference_strata)
        ref_map = {rs["id"]: _d(rs.get("population_count") or rs.get("share", "0")) / total_ref
                   for rs in reference_strata}
    else:
        total_invited = sum(_d(s["invited"]) for s in strata if "invited" in s)
        ref_map = {s["id"]: _d(s["invited"]) / total_invited for s in strata if "invited" in s}

    strata_info = []
    total_n = _ZERO
    total_m = _ZERO
    for s in strata:
        n = _d(s["n"])
        m = _d(s.get("invited", s["n"]))  # invited 없으면 n으로 fallback
        p_h = None
        if n > _ZERO:
            if "success" in s and s["success"] is not None:
                p_h = _d(s["success"]) / n
            elif "rate" in s and s["rate"] is not None:
                p_h = _d(s["rate"])
        total_n += n
        total_m += m
        strata_info.append({"id": s["id"], "n": n, "m": m, "p_h": p_h})

    response_rate = total_n / total_m if total_m > _ZERO else None

    def compute_P(d_pp: D) -> tuple[D | None, int]:
        d = d_pp / _HUNDRED
        P = _ZERO
        clipped = 0
        for si in strata_info:
            V_h = ref_map.get(si["id"], _ZERO)
            if V_h == _ZERO:
                continue
            if si["p_h"] is None:
                return None, 0
            r_h = si["n"] / si["m"] if si["m"] > _ZERO else _ZERO
            u_h = si["p_h"] + d
            if u_h > _ONE:
                u_h = _ONE
                clipped += 1
            elif u_h < _ZERO:
                u_h = _ZERO
                clipped += 1
            a_h = r_h * si["p_h"] + (_ONE - r_h) * u_h
            P += V_h * a_h
        return P, clipped

    baseline_P, _ = compute_P(_ZERO)
    scenarios = []
    n_fail = 0
    for d_pp in deltas:
        P, clipped = compute_P(d_pp)
        passes = _direction_ok(P, T, criterion_direction) if P is not None else None
        if passes is False:
            n_fail += 1
        scenarios.append({
            "delta_pp": _fmt(d_pp),
            "estimate": _fmt(P),
            "passes": passes,
            "clipped_strata_count": clipped,
        })

    # 판정
    baseline_passes = _direction_ok(baseline_P, T, criterion_direction) if baseline_P is not None else None
    if baseline_P is None:
        robustness = None
        break_condition = "필수 자료 미확보로 무너지는 조건 미확인"
    elif not baseline_passes:
        robustness = "뒤집힘"
        break_condition = f"기준 d=0에서 이미 기준 {_fmt(_HUNDRED*T)}% {criterion_direction} 미충족"
    elif n_fail > 0:
        robustness = "취약"
        # 대표 실패 시나리오
        fail_sc = next(sc for sc in scenarios if sc["passes"] is False)
        fail_P, _ = compute_P(_d(fail_sc["delta_pp"]))
        break_condition = f"무응답자 비율이 응답자보다 {fail_sc['delta_pp']}%p 차이 난다는 가정에서 {_fmt(_HUNDRED*fail_P)}%로 기준({_fmt(_HUNDRED*T)}% {criterion_direction}) 미충족"
    else:
        robustness = "견고"
        break_condition = "검토한 조건에서 기준 미충족 없음"

    # 손익분기 delta
    breakeven = _find_breakeven(strata_info, ref_map, T, criterion_direction, deltas)

    return {
        "baseline": _fmt(baseline_P),
        "response_rate": _fmt(response_rate),
        "scenarios": scenarios,
        "n_fail": n_fail,
        "breakeven_delta_pp": _fmt(breakeven),
        "robustness": robustness,
        "break_condition": break_condition,
    }


def _find_breakeven(strata_info, ref_map, T, direction, deltas) -> D | None:
    """P(d)=T 가 되는 경계 delta 탐색 (구간별 일차식)."""
    # 절단 지점 수집
    breakpoints = {D("-100"), D("100")}
    for si in strata_info:
        if si["p_h"] is not None:
            lo = -si["p_h"] * _HUNDRED
            hi = (_ONE - si["p_h"]) * _HUNDRED
            breakpoints.add(lo)
            breakpoints.add(hi)
    pts = sorted(breakpoints)

    best = None
    for i in range(len(pts) - 1):
        d_lo, d_hi = pts[i], pts[i + 1]
        d_mid = (d_lo + d_hi) / 2
        # 선형 보간으로 교차점 탐색 (단순화: 양 끝에서 선형 추정)
        def P_at(d_pp):
            P = _ZERO
            for si in strata_info:
                V_h = ref_map.get(si["id"], _ZERO)
                if V_h == _ZERO or si["p_h"] is None:
                    continue
                r_h = si["n"] / si["m"] if si["m"] > _ZERO else _ZERO
                d = d_pp / _HUNDRED
                u_h = max(_ZERO, min(_ONE, si["p_h"] + d))
                a_h = r_h * si["p_h"] + (_ONE - r_h) * u_h
                P += V_h * a_h
            return P

        P_lo = P_at(d_lo)
        P_hi = P_at(d_hi)
        # T가 구간 안에 있는지
        if (P_lo - T) * (P_hi - T) <= _ZERO:
            # 선형 보간
            if P_hi != P_lo:
                d_cross = d_lo + (T - P_lo) * (d_hi - d_lo) / (P_hi - P_lo)
            else:
                d_cross = d_lo
            d_cross = max(D("-100"), min(D("100"), d_cross))
            if best is None or abs(d_cross) < abs(best):
                best = d_cross
    return best


# ──────────────────────────────────────────────
# 4. stat_compare_strata (SPEC §7-6)
# ──────────────────────────────────────────────

def compare_strata(
    group_a: list[dict],   # [{id, n, success}]
    group_b: list[dict],
    reference_strata: list[dict] | None = None,
) -> dict:
    """두 그룹 간 전체 차이 및 층별 차이, 역전 여부를 계산한다."""

    def group_proportions(strata):
        total_n, total_s = _ZERO, _ZERO
        per = {}
        for s in strata:
            n = _d(s["n"])
            sc = _d(s["success"])
            total_n += n
            total_s += sc
            per[s["id"]] = (n, sc)
        p = total_s / total_n if total_n > _ZERO else None
        return p, per, total_n

    p_A, per_A, nA = group_proportions(group_a)
    p_B, per_B, nB = group_proportions(group_b)

    D_total = _HUNDRED * (p_A - p_B) if p_A is not None and p_B is not None else None

    # 층별 차이
    common_ids = set(per_A) & set(per_B)
    strata_directions = []
    reversal_candidate = True
    mixed = False
    pos_exists = neg_exists = False

    for sid in sorted(common_ids):
        nAh, sAh = per_A[sid]
        nBh, sBh = per_B[sid]
        p_Ah = sAh / nAh if nAh > _ZERO else None
        p_Bh = sBh / nBh if nBh > _ZERO else None
        if p_Ah is None or p_Bh is None:
            reversal_candidate = False
            strata_directions.append({"id": sid, "diff_pp": None, "direction": None})
            continue
        d_h = _HUNDRED * (p_Ah - p_Bh)
        if d_h > _ZERO:
            pos_exists = True
            dir_str = "양"
        elif d_h < _ZERO:
            neg_exists = True
            dir_str = "음"
        else:
            dir_str = "0"
            reversal_candidate = False
        strata_directions.append({"id": sid, "diff_pp": _fmt(d_h), "direction": dir_str})

    mixed = pos_exists and neg_exists

    # 역전: D와 모든 층 D_h 부호가 반대
    reversal = False
    if D_total is not None and reversal_candidate and len(common_ids) > 0:
        if D_total > _ZERO and neg_exists and not pos_exists:
            reversal = True
        elif D_total < _ZERO and pos_exists and not neg_exists:
            reversal = True

    # 표준화 차이 (기준 구성 있을 때)
    D_std = None
    if reference_strata:
        total_ref = sum(_d(rs.get("population_count") or rs.get("share", "0")) for rs in reference_strata)
        ref_map = {rs["id"]: _d(rs.get("population_count") or rs.get("share", "0")) / total_ref
                   for rs in reference_strata}
        D_std_num = _ZERO
        for sid in common_ids:
            W_h = ref_map.get(sid, _ZERO)
            nAh, sAh = per_A[sid]
            nBh, sBh = per_B[sid]
            p_Ah = sAh / nAh if nAh > _ZERO else None
            p_Bh = sBh / nBh if nBh > _ZERO else None
            if p_Ah is not None and p_Bh is not None:
                D_std_num += W_h * (p_Ah - p_Bh)
        D_std = _fmt(_HUNDRED * D_std_num)

    # 판정
    if reversal:
        robustness = "뒤집힘"
        break_condition = f"전체 차이는 {_fmt(D_total)}%p이나 모든 비교 층에서는 반대 방향"
    elif mixed or (D_total is not None and D_total == _ZERO):
        robustness = "취약"
        break_condition = "층별 방향이 혼재하거나 전체 차이가 0으로 일관된 우위 미확인"
    elif D_total is not None:
        robustness = "견고"
        break_condition = "검토한 조건에서 기준 미충족 없음"
    else:
        robustness = None
        break_condition = "필수 자료 미확보로 무너지는 조건 미확인"

    return {
        "aggregate_diff_pp": _fmt(D_total),
        "standardized_diff_pp": D_std,
        "strata_directions": strata_directions,
        "reversal": reversal,
        "mixed": mixed,
        "robustness": robustness,
        "break_condition": break_condition,
    }


# ──────────────────────────────────────────────
# 5. 판정 규칙 (stat_make_review_report용)
# ──────────────────────────────────────────────

def combine_robustness(check_results: list[Optional[str]]) -> Optional[str]:
    """
    SPEC §7-8 해석 견고성 취합.

    check_results: 해석 검사별 판정. 실행하려 했으나 판정을 못 낸 검사는 None.
    검사가 없거나 하나라도 미완료면 None → 뒤집힘 > 취약 > 견고.
    """
    if not check_results or any(r is None for r in check_results):
        return None
    for level in ("뒤집힘", "취약"):
        if level in check_results:
            return level
    return "견고"


def derive_verdict(
    numeric_accuracy: str | None,
    robustness: str | None,
    source: str,
    applicable: bool,
    kind: str,
) -> str:
    """SPEC §7-8 주장별 사용 권고 (위에서 첫 번째 맞는 규칙)."""
    if (source == "synthetic"
            or not applicable
            or numeric_accuracy == "미확인"
            or kind == "causal_or_forecast"):
        return "사용 보류"
    if numeric_accuracy == "오류 확인":
        return "수정 후 인용"
    if robustness == "취약":
        return "조건부 인용"
    if robustness == "견고" or robustness is None:
        return "인용 가능"
    # 뒤집힘
    return "사용 보류"


def derive_doc_verdict(verdicts: list[str]) -> str:
    """문서 전체 판정: 가장 엄격한 행의 권고."""
    order = ["사용 보류", "수정 후 인용", "조건부 인용", "인용 가능"]
    for v in order:
        if v in verdicts:
            return v
    return "인용 가능"
