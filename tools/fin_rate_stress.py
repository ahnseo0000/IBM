"""
tools/fin_rate_stress.py
금리 충격 시나리오를 계산한다.

에이전트: ri_robustness_checker
SPEC §6-4
"""

import json
import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from engine.fin_metrics import calc_narrow_debt, METRIC_INT_COVERAGE, METRIC_CFO_TO_DEBT
from engine.fin_verdict import compute_rate_stress
from tools._envelope import make_ok, make_error
from tools._keys import get_dart_api_key, get_ecos_api_key
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

_TOOL_NAME = "fin_rate_stress"
_ECOS_CACHE_DIR = _ROOT / "cache" / "ecos"
_HIST_CACHE = _ECOS_CACHE_DIR / "hist_max_hike_bp.json"

# ECOS 기준금리 조회 파라미터
_ECOS_STAT_CODE = "722Y001"
_ECOS_ITEM_CODE = "0101000"
_ECOS_START     = "202001"
_ECOS_END       = "202611"


def _load_ecos_params() -> tuple[Decimal, Decimal]:
    """
    캐시 → live → snapshot 상수 순으로 (hist_max_hike_bp, latest_rate_bp) 반환.
    latest 는 bp 로 반환.
    """
    if _HIST_CACHE.exists():
        try:
            data = json.loads(_HIST_CACHE.read_text(encoding="utf-8"))
            hike = Decimal(str(data["hist_max_hike_bp"]))
            latest_bp = Decimal(str(data["latest"])) * 100  # 연% → bp
            return hike, latest_bp
        except Exception:
            pass

    # live 조회
    try:
        api_key = get_ecos_api_key()
        import requests
        url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr"
               f"/1/200/{_ECOS_STAT_CODE}/M/{_ECOS_START}/{_ECOS_END}/{_ECOS_ITEM_CODE}")
        r = requests.get(url, timeout=20)
        rows = r.json().get("StatisticSearch", {}).get("row", [])
        vals = [(row["TIME"], Decimal(row["DATA_VALUE"])) for row in rows if row.get("DATA_VALUE")]
        if vals:
            latest_bp = vals[-1][1] * 100
            max_hike = Decimal(0)
            for i in range(12, len(vals)):
                hike = vals[i][1] - vals[i - 12][1]
                if hike > max_hike:
                    max_hike = hike
            hist_bp = (max_hike * 100).quantize(Decimal("1"))
            return hist_bp, latest_bp
    except Exception:
        pass

    # snapshot fallback (클라우드 환경에서 캐시/네트워크 모두 불가 시)
    return Decimal(_SNAP_HIKE), Decimal(_SNAP_BP)


@tool
def fin_rate_stress(
    corp: str,
    year: int,
    metric: str,
    threshold: float,
    debt_def: str = "narrow",
    floating_shares: str = "0.5,1.0",
) -> dict:
    """
    금리 충격 시나리오를 계산한다 (SPEC §6-4).

    에이전트 사용 안내
    ------------------
    - ri_robustness_checker 에이전트가 호출한다.
    - metric 이 "debt_ratio" 이면 금리 충격이 적용되지 않으므로 호출하지 않는다.
    - 변동금리 비중(alpha)은 공시로 알 수 없는 가정값이다.
      0.5 와 1.0 두 값으로 범위를 보여준다.
    - breakeven_bp 가 hist_max_hike_bp 보다 작으면 과거 금리 사이클에서 이미 한 번 기준 미달이 될 수 있다.

    Parameters
    ----------
    corp : str
        DART 고유번호(8자리) 또는 종목코드(6자리).
    year : int
        사업연도.
    metric : str
        "interest_coverage" | "cfo_to_debt"
    threshold : float
        판정 기준값.
    debt_def : str, optional
        차입금 정의. "narrow"(기본) 또는 "broad".
    floating_shares : str, optional
        쉼표로 구분된 변동금리 비중 목록. 기본 "0.5,1.0".

    Returns
    -------
    dict
        SPEC §3-1 봉투. result 의 주요 키:
        - base_rate_latest  : 최신 ECOS 기준금리 (bp)
        - hist_max_hike_bp  : 과거 5년 내 12개월 최대 상승폭 (bp)
        - by_alpha          : [{alpha, value_at_100bp, value_at_hist_max, breakeven_bp}]
        - already_below     : bool
    """
    corp = str(corp).strip()
    if metric not in (METRIC_INT_COVERAGE, METRIC_CFO_TO_DEBT):
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT",
                          message=f"metric='{metric}' 은 지원하지 않습니다. "
                                  "interest_coverage 또는 cfo_to_debt 를 쓰세요.")
    if debt_def not in ("narrow", "broad"):
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT",
                          message="debt_def 는 'narrow' 또는 'broad' 이어야 합니다.")

    # floating_shares 파싱
    try:
        alpha_list = [float(x.strip()) for x in floating_shares.split(",") if x.strip()]
    except ValueError:
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT",
                          message=f"floating_shares 파싱 실패: '{floating_shares}'")

    # API 키 (실패해도 snapshot 경로 가능)
    api_key = None
    try:
        api_key = get_dart_api_key()
    except RuntimeError:
        pass

    # 종목코드 → 고유번호 (snapshot 하드코딩 우선)
    corp_code = _resolve_corp_code(corp, api_key or "")
    if not corp_code:
        return make_error(tool=_TOOL_NAME, code="CORP_NOT_FOUND",
                          message=f"corp='{corp}' 를 DART 에서 찾을 수 없습니다.")

    # DART 데이터 로드 (live → cache → snapshot)
    rows = None
    data_source = "snapshot"
    if api_key:
        rows, _src = _fetch_dart(corp_code, year, "CFS", api_key)
        if rows:
            data_source = "live"
    if not rows:  # None 또는 빈 리스트 모두 처리
        rows = _load_cache(corp_code, year, "CFS")
        if rows:
            data_source = "cache"

    if rows:  # None 또는 빈 리스트가 아닐 때만 사용
        mapped = extract_amounts(rows)
    else:
        # snapshot fallback
        stock_key = corp if len(corp) == 6 else None
        if stock_key is None:
            inv = {v: k for k, v in _SNAP_CORP_MAP.items()}
            stock_key = inv.get(corp_code)
        snap = _get_snap(stock_key, "CFS") if stock_key and year == 2025 else None
        if snap is None:
            return make_error(tool=_TOOL_NAME, code="NO_DATA",
                              message=f"corp_code={corp_code} year={year} CFS 데이터 없음 "
                                      "(live/cache/snapshot 모두 실패)")
        mapped = _snapshot_to_mapped(snap)

    amt = mapped["amounts"]

    def _d0(v):
        return v if v is not None else Decimal(0)

    oi  = _d0(amt["operating_income"])
    ie  = _d0(amt["interest_expense"])
    cfo = _d0(amt["operating_cash_flow"])
    nd  = calc_narrow_debt(
        _d0(amt["short_term_borrowings"]),
        _d0(amt["current_portion_lt_debt"]),
        _d0(amt["long_term_borrowings"]),
        _d0(amt["bonds"]),
    )
    if debt_def == "broad":
        from engine.fin_metrics import calc_broad_debt
        lease = amt["lease_liabilities"]
        if lease is None:
            return make_error(tool=_TOOL_NAME, code="NO_DATA",
                              message="debt_def='broad' 이지만 리스부채 미확인. "
                                      "debt_def='narrow' 로 호출하거나 리스부채를 확인하세요.")
        D = calc_broad_debt(nd, lease)
    else:
        D = nd

    # ECOS 금리 파라미터
    hist_bp, latest_bp = _load_ecos_params()

    # 금리 충격 계산
    try:
        stress = compute_rate_stress(
            metric=metric,
            threshold=threshold,
            operating_income=oi,
            interest_expense=ie,
            operating_cash_flow=cfo,
            debt=D,
            base_rate_latest_bp=latest_bp,
            hist_max_hike_bp=hist_bp,
            floating_shares=alpha_list,
        )
    except Exception as e:
        return make_error(tool=_TOOL_NAME, code="CALC_ERROR", message=str(e))

    notes = []
    notes.append("alpha(변동금리 비중)는 공시로 알 수 없는 가정값입니다. 세전 단순화 가정 적용.")
    if mapped["interest_is_approximate"]:
        notes.append("이자비용은 금융비용으로 근사 (interest_is_approximate=True)")

    result_data = {
        **stress,
        "metric":    metric,
        "threshold": str(threshold),
        "debt_def":  debt_def,
        "corp_code": corp_code,
        "narrow_debt": str(nd),
        "used_debt": str(D),
    }

    if data_source == "snapshot":
        notes.append("재무 데이터: 스냅샷 사용 (2025 사업보고서 최종 공시 기준)")

    return make_ok(
        tool=_TOOL_NAME,
        call_args={"corp": corp, "year": year, "metric": metric,
                   "threshold": threshold, "debt_def": debt_def},
        source_data={"corp_code": corp_code, "year": year,
                     "ecos_hist_cache": str(_HIST_CACHE.name)},
        source=data_source,
        applicable=True,
        result=result_data,
        notes=notes,
    )
