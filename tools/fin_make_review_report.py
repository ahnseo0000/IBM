"""
tools/fin_make_review_report.py
모든 주장을 종합해 최종 재무 검토 보고서를 생성한다.

에이전트: ri_coordinator
SPEC §6-5·§6-6·§6-7

doc_verdict 규칙 (§6-6)
1. 반려  — 뒤집힘(확정)이 하나라도 있음
2. 보완 요청 — 뒤집힘, 취약, 수치 불일치 중 하나라도 있음
3. 통과  — 모든 주장이 견고하고 수치 일치

무너지는 조건 문구 템플릿 (§6-7):
- 정의: "리스부채를 차입금에 포함하면 {metric}이 {val}로 기준({T}) 미달"
- 금리: "변동금리 비중 {alpha*100}%일 때 기준금리가 +{N}bp 오르면 기준 미달"

응답 크기 정책
- 복수 주장(C사 2개 이상)을 한 번에 처리하면 scenarios/rate_stress 상세 데이터 때문에
  공통 2 KB 상한을 초과한다. 이 도구에 한해 4 KB 상한을 적용한다.
  (에이전트가 markdown_table 을 직접 조립하므로 markdown_table 필드는 포함하지 않는다.)
"""

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from engine.fin_metrics import (
    calc_debt_ratio, calc_interest_coverage, calc_cfo_to_debt,
    calc_narrow_debt, calc_broad_debt,
    METRIC_DEBT_RATIO, METRIC_INT_COVERAGE, METRIC_CFO_TO_DEBT,
    ENGINE_VERSION,
)
from engine.fin_verdict import (
    compute_scenarios, compute_rate_stress, compute_verdict,
    VERDICT_SOLID, VERDICT_WEAK, VERDICT_REVERSED, VERDICT_REVERSED_DEF,
)
from tools._envelope import make_ok, make_error, TOOL_VERSION
from tools._keys import get_dart_api_key
from tools.fin_get_financials import (
    _resolve_corp_code, _load_cache, _fetch_dart, _snapshot_to_mapped,
)
from engine.account_map import extract_amounts
from engine.fin_snapshot import (
    get_snapshot as _get_snap,
    HIST_MAX_HIKE_BP as _SNAP_HIKE,
    LATEST_RATE_BP as _SNAP_BP,
    DEMO_CORP_CODE_MAP as _SNAP_CORP_MAP,
)

_TOOL_NAME = "fin_make_review_report"

# 이 도구에 한해 4 KB 상한 적용.
# 이유: 복수 주장(C사 2개)을 한 번에 처리하면 scenarios/rate_stress 상세 데이터가
#       포함되어 공통 2 KB 한도를 초과한다. markdown_table 은 에이전트가 직접 조립하므로
#       여기서는 제외하고, rows 를 최소화해도 2개 주장 기준 ~3 KB 가 필요하다.
_MAX_BYTES = 4096

_ECOS_HIST_CACHE = _ROOT / "cache" / "ecos" / "hist_max_hike_bp.json"

_METRIC_LABELS = {
    METRIC_DEBT_RATIO:   "부채비율",
    METRIC_INT_COVERAGE: "이자보상배율",
    METRIC_CFO_TO_DEBT:  "영업현금흐름 커버",
}

_ASSUMPTION_CODES = ["alpha=0.5/1.0", "hist_hike=225bp", "rate=3.00%", "세전단순화"]


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _load_ecos() -> tuple[Decimal, Decimal]:
    """ECOS 캐시 → snapshot 상수 순으로 (hist_max_hike_bp, latest_rate_bp) 반환."""
    if _ECOS_HIST_CACHE.exists():
        try:
            d = json.loads(_ECOS_HIST_CACHE.read_text(encoding="utf-8"))
            hike = Decimal(str(d["hist_max_hike_bp"]))
            latest_bp = Decimal(str(d["latest"])) * 100
            return hike, latest_bp
        except Exception:
            pass
    return Decimal(_SNAP_HIKE), Decimal(_SNAP_BP)


def _get_amounts(corp_code: str, year: int, fs_div: str, api_key: str | None,
                 stock_input: str | None = None):
    """live → cache → snapshot 순으로 계정 추출. (mapped, source) 반환."""
    rows = None
    source = "snapshot"

    if api_key:
        rows, _src = _fetch_dart(corp_code, year, fs_div, api_key)
        if rows:
            source = "live"

    if not rows:  # None 또는 빈 리스트 모두 처리
        rows = _load_cache(corp_code, year, fs_div)
        if rows:
            source = "cache"

    if rows:  # None 또는 빈 리스트가 아닐 때만 사용
        return extract_amounts(rows), source

    # snapshot fallback
    stock_key = stock_input if (stock_input and len(stock_input) == 6) else None
    if stock_key is None:
        inv = {v: k for k, v in _SNAP_CORP_MAP.items()}
        stock_key = inv.get(corp_code)
    snap = _get_snap(stock_key, fs_div) if stock_key and year == 2025 else None
    if snap is not None:
        return _snapshot_to_mapped(snap), "snapshot"
    return None, None


def _d0(v):
    return v if v is not None else Decimal(0)


def _build_narrow(amt: dict) -> dict:
    return {
        "total_liabilities":       _d0(amt["total_liabilities"]),
        "total_equity":            _d0(amt["total_equity"]),
        "operating_income":        _d0(amt["operating_income"]),
        "interest_expense":        _d0(amt["interest_expense"]),
        "short_term_borrowings":   _d0(amt["short_term_borrowings"]),
        "current_portion_lt_debt": _d0(amt["current_portion_lt_debt"]),
        "long_term_borrowings":    _d0(amt["long_term_borrowings"]),
        "bonds":                   _d0(amt["bonds"]),
        "operating_cash_flow":     _d0(amt["operating_cash_flow"]),
    }


def _metric_label(metric: str) -> str:
    return _METRIC_LABELS.get(metric, metric)


def _break_condition_definition(metric: str, failing_scenario: dict, threshold, direction: str) -> str:
    sid   = failing_scenario.get("id", "")
    val   = failing_scenario.get("value", "?")
    T     = str(threshold)
    label = _metric_label(metric)
    if sid == "S1":
        return f"리스부채 포함 {label} {val} < 기준 {T}"
    if sid == "S2":
        return f"별도 {label} {val} < 기준 {T}"
    if sid == "S3":
        return f"별도·리스 {label} {val} < 기준 {T}"
    return f"S0 {label} {val} < 기준 {T}"


def _break_condition_rate(alpha: float, breakeven_bp: str) -> str:
    return f"변동금리 {int(alpha*100)}% 시 +{breakeven_bp}bp → 기준 미달"


def _process_claim(
    claim: dict,
    cfs_mapped, ofs_mapped,
    hist_bp: Decimal, latest_bp: Decimal,
) -> dict:
    """단일 주장을 처리해 행 dict 를 반환한다."""
    metric    = claim["metric"]
    threshold = claim["threshold"]
    direction = claim["direction"]
    reported  = claim.get("reported_value")

    cfs_amt    = cfs_mapped["amounts"]
    cfs_lease  = cfs_amt["lease_liabilities"]
    cfs_narrow = _build_narrow(cfs_amt)

    ofs_narrow = None
    ofs_lease  = None
    if ofs_mapped:
        ofs_amt    = ofs_mapped["amounts"]
        ofs_lease  = ofs_amt["lease_liabilities"]
        ofs_narrow = _build_narrow(ofs_amt)

    # 재계산
    try:
        if metric == METRIC_DEBT_RATIO:
            recomputed_val = calc_debt_ratio(cfs_amt["total_liabilities"], cfs_amt["total_equity"])
        elif metric == METRIC_INT_COVERAGE:
            recomputed_val = calc_interest_coverage(cfs_amt["operating_income"], cfs_amt["interest_expense"])
        elif metric == METRIC_CFO_TO_DEBT:
            nd = calc_narrow_debt(
                _d0(cfs_amt["short_term_borrowings"]),
                _d0(cfs_amt["current_portion_lt_debt"]),
                _d0(cfs_amt["long_term_borrowings"]),
                _d0(cfs_amt["bonds"]),
            )
            recomputed_val = calc_cfo_to_debt(cfs_amt["operating_cash_flow"], nd)
        else:
            recomputed_val = None
    except Exception:
        recomputed_val = None

    reproduced = None
    if reported is not None and recomputed_val is not None:
        from engine.fin_metrics import check_reproduced
        try:
            reproduced = check_reproduced(metric, recomputed_val, Decimal(str(reported)))
        except Exception:
            reproduced = None

    # 시나리오
    try:
        scen_data = compute_scenarios(
            metric=metric,
            threshold=threshold,
            direction=direction,
            cfs_narrow_amounts=cfs_narrow,
            cfs_lease=cfs_lease,
            ofs_narrow_amounts=ofs_narrow,
            ofs_lease=ofs_lease,
        )
    except Exception as e:
        scen_data = {"scenarios": [], "worst_case": {}, "n_fail": -1, "error": str(e)}

    # 금리 충격
    rate_stress = None
    if metric in (METRIC_INT_COVERAGE, METRIC_CFO_TO_DEBT):
        try:
            nd = calc_narrow_debt(
                _d0(cfs_amt["short_term_borrowings"]),
                _d0(cfs_amt["current_portion_lt_debt"]),
                _d0(cfs_amt["long_term_borrowings"]),
                _d0(cfs_amt["bonds"]),
            )
            rate_stress = compute_rate_stress(
                metric=metric,
                threshold=threshold,
                operating_income=_d0(cfs_amt["operating_income"]),
                interest_expense=_d0(cfs_amt["interest_expense"]),
                operating_cash_flow=_d0(cfs_amt["operating_cash_flow"]),
                debt=nd,
                base_rate_latest_bp=latest_bp,
                hist_max_hike_bp=hist_bp,
                floating_shares=[0.5, 1.0],
            )
        except Exception:
            rate_stress = None

    # 판정
    verdict = compute_verdict(
        metric=metric,
        threshold=threshold,
        direction=direction,
        scenarios=scen_data.get("scenarios", []),
        rate_stress=rate_stress,
    ) if scen_data.get("scenarios") else "판정불가"

    # 무너지는 조건 문구 (§6-7) — 한 줄로 압축
    break_conditions = []
    for s in scen_data.get("scenarios", []):
        if s.get("passes") is False:
            bc = _break_condition_definition(metric, s, threshold, direction)
            break_conditions.append(bc)
    if rate_stress and verdict == VERDICT_WEAK:
        for entry in rate_stress.get("by_alpha", []):
            bp_str = entry.get("breakeven_bp")
            if bp_str:
                try:
                    if Decimal(bp_str) < hist_bp:
                        bc = _break_condition_rate(float(entry["alpha"]), bp_str)
                        break_conditions.append(bc)
                except Exception:
                    pass

    # 요약 수치 — 핵심 2개(S0값, 최악 시나리오)만
    s0 = next((s for s in scen_data.get("scenarios", []) if s["id"] == "S0"), None)
    summary_numbers = {
        "s0":    s0["value"] if s0 else None,
        "worst": scen_data.get("worst_case", {}).get("value"),
    }

    return {
        "claim":      {"metric": metric, "threshold": str(threshold), "direction": direction},
        "recomputed": str(recomputed_val) if recomputed_val is not None else None,
        "reproduced": reproduced,
        "verdict":    verdict,
        "break_condition": "; ".join(break_conditions) if break_conditions else None,
        "summary_numbers": summary_numbers,
        "scenarios":  scen_data.get("scenarios", []),
        "rate_stress": rate_stress,
        "interest_is_approximate": cfs_mapped.get("interest_is_approximate", False),
    }


def _doc_verdict(rows: list[dict]) -> str:
    """§6-6 문서 전체 판정."""
    verdicts   = [r["verdict"] for r in rows]
    reproduced = [r["reproduced"] for r in rows]

    if VERDICT_REVERSED_DEF in verdicts:
        return "반려"
    mismatch = any(r is False for r in reproduced)
    if VERDICT_REVERSED in verdicts or VERDICT_WEAK in verdicts or mismatch:
        return "보완 요청"
    return "통과"


def _check_size_4k(payload: dict) -> dict:
    """
    이 도구 전용 4 KB 크기 가드.
    공통 _envelope._check_size(2 KB) 대신 여기서 호출한다.
    이유: 복수 주장(2개 이상) 처리 시 scenarios/rate_stress 상세 때문에
         공통 2 KB 한도를 구조적으로 초과한다. markdown_table 제거 및
         break_condition/summary_numbers 슬림화 후에도 C사 2주장 기준 ~3 KB.
    """
    serialized = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    if len(serialized) > _MAX_BYTES:
        return make_error(
            tool=payload.get("tool", _TOOL_NAME),
            code="BAD_INPUT",
            message=(
                f"응답 크기 {len(serialized)} bytes 가 {_MAX_BYTES} bytes 상한 초과. "
                "claims 수를 줄이거나 reported_value 를 생략하세요."
            ),
        )
    return payload


# ── @tool ─────────────────────────────────────────────────────────────────────

@tool
def fin_make_review_report(
    corp: str,
    year: int,
    claims: str,
) -> dict:
    """
    모든 주장을 종합해 최종 재무 검토 보고서를 생성한다 (SPEC §6-5·§6-6).

    에이전트 사용 안내
    ------------------
    - ri_coordinator 에이전트가 호출한다.
    - 최종 판정의 유일한 원천이다.
    - claims 는 JSON 배열 문자열이다:
      [{"metric":"debt_ratio","threshold":100,"direction":"이하"}, ...]
    - break_condition 의 문구는 에이전트가 그대로 인용한다 (§6-7).
    - doc_verdict 가 "반려"이면 에이전트는 추가 의견 없이 문구를 그대로 전달한다.
    - markdown_table 은 에이전트가 rows 를 이용해 직접 조립한다 (이 도구에서 반환하지 않음).

    Parameters
    ----------
    corp : str
        DART 고유번호(8자리) 또는 종목코드(6자리).
    year : int
        사업연도.
    claims : str
        JSON 배열. 각 원소:
          metric      : "debt_ratio" | "interest_coverage" | "cfo_to_debt"
          threshold   : 숫자 (부채비율 100, 이자보상배율 1.5 등)
          direction   : "이하" | "이상"
          reported_value : 보고서 기재값 (선택, 없으면 null)
          corp_label  : 가명 (선택, 없으면 "X사")

    Returns
    -------
    dict
        SPEC §3-1 봉투. result 의 주요 키:
        - rows        : [{claim, recomputed, reproduced, verdict,
                          break_condition, summary_numbers, scenarios, rate_stress}]
        - doc_verdict : "반려" | "보완 요청" | "통과"
        - audit       : {tool, tool_version, input_hash, source, assumptions}
    """
    corp = str(corp).strip()
    if not corp:
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT", message="corp 는 필수입니다.")

    # claims 파싱
    try:
        claims_list = json.loads(claims)
        if not isinstance(claims_list, list) or len(claims_list) == 0:
            raise ValueError("빈 배열")
    except Exception as e:
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT",
                          message=f"claims JSON 파싱 실패: {e}")

    # API 키 로드 (실패해도 snapshot 경로로 갈 수 있으므로 별도 처리)
    api_key = None
    try:
        api_key = get_dart_api_key()
    except RuntimeError:
        pass

    corp_code = _resolve_corp_code(corp, api_key or "")
    if not corp_code:
        return make_error(tool=_TOOL_NAME, code="CORP_NOT_FOUND",
                          message=f"corp='{corp}' 를 DART 에서 찾을 수 없습니다.")

    # CFS 데이터 (live → cache → snapshot)
    cfs_mapped, cfs_source = _get_amounts(corp_code, year, "CFS", api_key, stock_input=corp)
    if cfs_mapped is None:
        return make_error(tool=_TOOL_NAME, code="NO_DATA",
                          message=f"corp_code={corp_code} year={year} CFS 데이터 없음 "
                                  "(live/cache/snapshot 모두 실패)")

    # OFS 데이터 (선택)
    ofs_mapped, ofs_source = _get_amounts(corp_code, year, "OFS", api_key, stock_input=corp)

    # ECOS
    hist_bp, latest_bp = _load_ecos()

    # corp_label (첫 번째 claim 의 corp_label 또는 "X사")
    corp_label = claims_list[0].get("corp_label", "X사") if isinstance(claims_list[0], dict) else "X사"

    # 각 주장 처리
    rows = []
    for cl in claims_list:
        if not isinstance(cl, dict):
            continue
        required = {"metric", "threshold", "direction"}
        if not required.issubset(cl.keys()):
            rows.append({"error": f"필수 키 누락: {required - cl.keys()}", "claim": cl})
            continue
        try:
            row = _process_claim(cl, cfs_mapped, ofs_mapped, hist_bp, latest_bp)
            rows.append(row)
        except Exception as e:
            rows.append({"error": str(e), "claim": cl})

    doc = _doc_verdict([r for r in rows if "error" not in r])

    # audit — 도구명·version·hash·source·가정 코드만
    input_str  = json.dumps({"corp": corp, "year": year, "claims": claims_list}, ensure_ascii=False)
    data_source = cfs_source
    if ofs_mapped:
        data_source += f"+OFS({ofs_source})"
    audit = {
        "tool":         _TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "input_hash":   hashlib.sha256(input_str.encode()).hexdigest()[:16],
        "source":       data_source,
        "assumptions":  _ASSUMPTION_CODES,
    }

    result_data = {
        "corp_code":   corp_code,
        "corp_label":  corp_label,
        "fiscal_year": year,
        "rows":        rows,
        "doc_verdict": doc,
        "audit":       audit,
        # markdown_table 은 에이전트가 rows 를 이용해 직접 조립한다.
    }

    payload = {
        "ok": True,
        "tool": _TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "input_hash": audit["input_hash"],
        "source": data_source,
        "applicable": True,
        "applicability_notes": [],
        "result": result_data,
        "notes": [
            f"doc_verdict={doc}",
            f"claims_count={len(claims_list)}",
            f"size_limit=4096B (복수주장 허용)",
        ],
    }
    return _check_size_4k(payload)
