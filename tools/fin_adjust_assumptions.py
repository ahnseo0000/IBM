"""
tools/fin_adjust_assumptions.py
가정 조정 시나리오(S0~S3)를 계산한다.

에이전트: ri_robustness_checker
SPEC §6-3
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from decimal import Decimal

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from engine.fin_verdict import compute_scenarios
from tools._envelope import make_ok, make_error
from tools.fin_get_financials import (
    _resolve_corp_code,
    _load_cache,
    _fetch_dart,
    _snapshot_to_mapped,
)
from engine.account_map import extract_amounts
from tools._keys import get_dart_api_key
from engine.fin_snapshot import (
    get_snapshot as _get_snap,
    DEMO_CORP_CODE_MAP as _SNAP_CORP_MAP,
)

_TOOL_NAME = "fin_adjust_assumptions"


def _load_amounts(corp: str, year: int, fs_div: str, api_key: str | None):
    """
    corp → corp_code → rows → extract_amounts.
    live → cache → snapshot 순. 에러 시 (None, None, msg) 반환.
    빈 rows([]) 는 데이터 없음으로 간주해 snapshot 으로 fallback 한다.
    """
    corp_code = _resolve_corp_code(corp, api_key or "")
    if not corp_code:
        return None, None, f"corp='{corp}' DART 조회 불가"

    rows = None
    if api_key:
        rows, _ = _fetch_dart(corp_code, year, fs_div, api_key)
    if not rows:  # None 또는 빈 리스트 모두 처리
        rows = _load_cache(corp_code, year, fs_div)

    if rows:  # None 또는 빈 리스트가 아닐 때만 사용
        mapped = extract_amounts(rows)
        return mapped, corp_code, None

    # snapshot fallback
    stock_key = corp if len(corp) == 6 else None
    if stock_key is None:
        inv = {v: k for k, v in _SNAP_CORP_MAP.items()}
        stock_key = inv.get(corp_code)
    snap = _get_snap(stock_key, fs_div) if stock_key and year == 2025 else None
    if snap is not None:
        return _snapshot_to_mapped(snap), corp_code, None

    return None, None, f"corp_code={corp_code} year={year} fs_div={fs_div} 데이터 없음"


@tool
def fin_adjust_assumptions(
    corp: str,
    year: int,
    metric: str,
    threshold: float,
    direction: str,
) -> dict:
    """
    S0~S3 가정 조정 시나리오를 계산한다 (SPEC §6-3).

    에이전트 사용 안내
    ------------------
    - ri_robustness_checker 에이전트가 호출한다.
    - fin_get_financials 로 먼저 데이터를 확인한 뒤 호출하는 것을 권장한다.
    - S2·S3(별도 재무제표)는 OFS 캐시가 있어야 계산된다.
      없으면 S2·S3 없이 S0·S1 만 반환한다.

    Parameters
    ----------
    corp : str
        DART 고유번호(8자리) 또는 종목코드(6자리).
    year : int
        사업연도. 예: 2025
    metric : str
        "debt_ratio" | "interest_coverage" | "cfo_to_debt"
    threshold : float
        판정 기준값. 예: 100.0 (부채비율 100% 이하)
    direction : str
        "이하" 또는 "이상"

    Returns
    -------
    dict
        SPEC §3-1 봉투. result 의 주요 키:
        - scenarios : [{id, value, passes, margin_pct}]
        - worst_case : {id, value}
        - n_fail : int
        - metric, threshold, direction
    """
    corp = str(corp).strip()
    if metric not in ("debt_ratio", "interest_coverage", "cfo_to_debt"):
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT",
                          message=f"metric='{metric}' 은 지원하지 않습니다. "
                                  "debt_ratio | interest_coverage | cfo_to_debt 중 하나를 쓰세요.")
    if direction not in ("이하", "이상"):
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT",
                          message="direction 은 '이하' 또는 '이상' 이어야 합니다.")

    # API 키 (실패해도 snapshot 경로 가능)
    api_key = None
    try:
        api_key = get_dart_api_key()
    except RuntimeError:
        pass

    # CFS (연결) — 필수
    cfs_mapped, corp_code, err = _load_amounts(corp, year, "CFS", api_key)
    if err:
        return make_error(tool=_TOOL_NAME, code="NO_DATA", message=err)

    cfs_amt = cfs_mapped["amounts"]
    cfs_lease = cfs_amt["lease_liabilities"]

    # cfs_narrow_amounts: compute_scenarios 에 전달할 형식으로 변환
    def _d0(v):
        return v if v is not None else Decimal(0)

    cfs_narrow = {
        "total_liabilities":       _d0(cfs_amt["total_liabilities"]),
        "total_equity":            _d0(cfs_amt["total_equity"]),
        "operating_income":        _d0(cfs_amt["operating_income"]),
        "interest_expense":        _d0(cfs_amt["interest_expense"]),
        "short_term_borrowings":   _d0(cfs_amt["short_term_borrowings"]),
        "current_portion_lt_debt": _d0(cfs_amt["current_portion_lt_debt"]),
        "long_term_borrowings":    _d0(cfs_amt["long_term_borrowings"]),
        "bonds":                   _d0(cfs_amt["bonds"]),
        "operating_cash_flow":     _d0(cfs_amt["operating_cash_flow"]),
    }

    # OFS (별도) — 선택
    ofs_narrow = None
    ofs_lease = None
    ofs_note = None
    ofs_mapped, _, ofs_err = _load_amounts(corp, year, "OFS", api_key)
    if ofs_mapped:
        ofs_amt = ofs_mapped["amounts"]
        ofs_narrow = {
            "total_liabilities":       _d0(ofs_amt["total_liabilities"]),
            "total_equity":            _d0(ofs_amt["total_equity"]),
            "operating_income":        _d0(ofs_amt["operating_income"]),
            "interest_expense":        _d0(ofs_amt["interest_expense"]),
            "short_term_borrowings":   _d0(ofs_amt["short_term_borrowings"]),
            "current_portion_lt_debt": _d0(ofs_amt["current_portion_lt_debt"]),
            "long_term_borrowings":    _d0(ofs_amt["long_term_borrowings"]),
            "bonds":                   _d0(ofs_amt["bonds"]),
            "operating_cash_flow":     _d0(ofs_amt["operating_cash_flow"]),
        }
        ofs_lease = ofs_amt["lease_liabilities"]
    else:
        ofs_note = f"OFS 데이터 없음 — S2·S3 계산 생략. ({ofs_err})"

    # 시나리오 계산
    try:
        result_data = compute_scenarios(
            metric=metric,
            threshold=threshold,
            direction=direction,
            cfs_narrow_amounts=cfs_narrow,
            cfs_lease=cfs_lease,
            ofs_narrow_amounts=ofs_narrow,
            ofs_lease=ofs_lease,
        )
    except Exception as e:
        return make_error(tool=_TOOL_NAME, code="CALC_ERROR", message=str(e))

    notes = []
    if cfs_mapped["interest_is_approximate"]:
        notes.append("이자비용은 금융비용으로 근사 (interest_is_approximate=True)")
    if not cfs_mapped["lease_found"] and metric == "cfo_to_debt":
        notes.append("리스부채 미확인 — S1 계산 불가")
    if ofs_note:
        notes.append(ofs_note)

    result_data["metric"]    = metric
    result_data["threshold"] = str(threshold)
    result_data["direction"] = direction
    result_data["corp_code"] = corp_code

    return make_ok(
        tool=_TOOL_NAME,
        call_args={"corp": corp, "year": year, "metric": metric,
                   "threshold": threshold, "direction": direction},
        source_data={"corp_code": corp_code, "year": year},
        source="cache_or_live",
        applicable=True,
        result=result_data,
        notes=notes,
    )
