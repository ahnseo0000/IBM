"""
tools/fin_recompute_ratios.py
보고서 재무비율을 같은 정의(연결, narrow)로 다시 계산해 일치 여부를 확인한다.

에이전트: ri_numeric_checker
SPEC §4·§6-2
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from decimal import Decimal

from ibm_watsonx_orchestrate.agent_builder.tools import tool

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
from engine.fin_metrics import (
    ENGINE_VERSION,
    METRIC_CFO_TO_DEBT,
    METRIC_DEBT_RATIO,
    METRIC_INT_COVERAGE,
    calc_cfo_to_debt,
    calc_debt_ratio,
    calc_interest_coverage,
    calc_narrow_debt,
    check_reproduced,
)

_TOOL_NAME = "fin_recompute_ratios"
_SUPPORTED = {METRIC_DEBT_RATIO, METRIC_INT_COVERAGE, METRIC_CFO_TO_DEBT}


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
def fin_recompute_ratios(
    corp: str,
    year: int,
    metric: str,
    fs_div: str = "CFS",
    reported_value: float = None,
) -> dict:
    """
    보고서의 재무비율을 같은 정의(연결, narrow)로 다시 계산해 일치 여부를 확인한다.

    에이전트 사용 안내
    ------------------
    - ri_numeric_checker 에이전트가 호출한다.
    - 반드시 fin_get_financials 로 공시를 가져온 뒤 이 도구를 호출한다.
      (내부적으로 fin_get_financials 와 동일한 live → cache → snapshot 경로 사용)
    - metric 허용값:
        * "debt_ratio"        — 부채비율(%), 허용 오차 ±1 %p
        * "interest_coverage" — 이자보상배율(배), 허용 오차 ±0.1 배
        * "cfo_to_debt"       — 영업현금흐름 커버(배), 허용 오차 ±0.01 배

    Parameters
    ----------
    corp : str
        DART 고유번호(8자리 숫자 문자열) 또는 종목코드(6자리).
    year : int
        검증 대상 사업연도(4자리). 예: 2025
    metric : str
        재무비율 종류. "debt_ratio" | "interest_coverage" | "cfo_to_debt"
    fs_div : str, optional
        재무제표 구분. "CFS"(연결, 기본값) 또는 "OFS"(별도).
    reported_value : float, optional
        원문(보고서)에 적힌 수치. 제공 시 재계산값과 비교해 reproduced 여부를 반환.
        - debt_ratio        : % 단위 (예: 150.0)
        - interest_coverage : 배 단위 (예: 4.0)
        - cfo_to_debt       : 배 단위 (예: 0.30)

    Returns
    -------
    dict
        SPEC §3-1 봉투. result 주요 키:
        - recomputed : 재계산 값 (문자열)
        - reported   : 원문 값 (문자열, reported_value 미입력 시 null)
        - diff       : recomputed − reported (문자열, reported_value 미입력 시 null)
        - reproduced : bool — 허용 오차 안이면 True (reported_value 미입력 시 null)
    """
    corp = str(corp).strip()
    if not corp:
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT", message="corp 는 필수입니다.")
    if not (1980 <= year <= 2100):
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT", message=f"year={year} 은 유효하지 않습니다.")
    if metric not in _SUPPORTED:
        return make_error(
            tool=_TOOL_NAME,
            code="UNSUPPORTED_METRIC",
            message=f"metric='{metric}' 은 지원하지 않습니다. {sorted(_SUPPORTED)} 중 하나를 쓰세요.",
        )
    if fs_div not in ("CFS", "OFS"):
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT", message="fs_div 는 'CFS' 또는 'OFS' 여야 합니다.")

    # API 키 (실패해도 snapshot 경로 가능)
    api_key = None
    try:
        api_key = get_dart_api_key()
    except RuntimeError:
        pass

    # 데이터 로드 (live → cache → snapshot)
    mapped, corp_code, err = _load_amounts(corp, year, fs_div, api_key)
    if err:
        return make_error(tool=_TOOL_NAME, code="NO_DATA", message=err)

    amt = mapped["amounts"]

    def _d0(v):
        return v if v is not None else Decimal(0)

    try:
        narrow = calc_narrow_debt(
            _d0(amt["short_term_borrowings"]),
            _d0(amt["current_portion_lt_debt"]),
            _d0(amt["long_term_borrowings"]),
            _d0(amt["bonds"]),
        )
        if metric == METRIC_DEBT_RATIO:
            recomputed = calc_debt_ratio(_d0(amt["total_liabilities"]), _d0(amt["total_equity"]))
        elif metric == METRIC_INT_COVERAGE:
            recomputed = calc_interest_coverage(_d0(amt["operating_income"]), _d0(amt["interest_expense"]))
        else:
            recomputed = calc_cfo_to_debt(_d0(amt["operating_cash_flow"]), narrow)
    except Exception as exc:
        return make_error(tool=_TOOL_NAME, code="CALC_ERROR", message=str(exc))

    # reported_value 비교 (선택)
    if reported_value is not None:
        reported_d = Decimal(str(reported_value))
        diff_str = str(recomputed - reported_d)
        reproduced = check_reproduced(metric, recomputed, reported_d)
        reported_str = str(reported_d)
    else:
        diff_str = None
        reproduced = None
        reported_str = None

    # source 판정
    source = "cache_or_live" if mapped.get("_source") != "snapshot" else "snapshot"

    result = {
        "corp_label":  "X사",
        "corp":        corp,
        "corp_code":   corp_code,
        "year":        year,
        "fs_div":      fs_div,
        "metric":      metric,
        "recomputed":  str(recomputed),
        "reported":    reported_str,
        "diff":        diff_str,
        "reproduced":  reproduced,
    }

    notes = []
    if mapped["interest_is_approximate"] and metric == METRIC_INT_COVERAGE:
        notes.append("이자비용은 금융비용으로 근사 (interest_is_approximate=True)")

    return make_ok(
        tool=_TOOL_NAME,
        call_args={"corp": corp, "year": year, "metric": metric,
                   "fs_div": fs_div, "reported_value": reported_value},
        source_data={"corp_code": corp_code, "year": year, "engine_version": ENGINE_VERSION},
        source=source,
        applicable=True,
        result=result,
        notes=notes,
    )
