"""
tools/fin_get_financials.py
DART 에서 단일회사 전체 재무제표를 가져와 SPEC §4 형식으로 반환한다.

에이전트: ri_numeric_checker
SPEC §4·§5·§3-2

데이터 가져오기 순서 (SPEC §3-2, 클라우드 대책 포함)
1. 종목코드 → 고유번호: snapshot 하드코딩 우선, 없으면 CORPCODE.xml, 없으면 live 다운로드
2. DART API 호출 → live
3. 실패 시 cache/dart/<파일명>.json 로드 → cache
4. 캐시도 없고 시연 3사이면 engine/fin_snapshot.py 상수 → snapshot
5. 모두 실패 → NO_DATA 오류
"""

import json
import sys
from decimal import Decimal
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from engine.account_map import extract_amounts, check_unmapped
from engine.fin_snapshot import get_corp_code as _snapshot_corp_code, get_snapshot
from tools._envelope import make_ok, make_error
from tools._keys import get_dart_api_key, mask_key

_TOOL_NAME     = "fin_get_financials"
_DART_URL      = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
_CACHE_DIR     = _ROOT / "cache" / "dart"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 사업보고서 보고서 코드
_REPRT_CODE = "11011"


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _cache_path(corp_code: str, year: int, fs_div: str) -> Path:
    return _CACHE_DIR / f"fnlttSinglAcntAll_{corp_code}_{year}_{fs_div}.json"


def _load_cache(corp_code: str, year: int, fs_div: str) -> list[dict] | None:
    p = _cache_path(corp_code, year, fs_div)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("list", [])


def _save_cache(corp_code: str, year: int, fs_div: str,
                rows: list[dict], dart_meta: dict) -> None:
    payload = {
        "request_args": {"corp_code": corp_code, "bsns_year": year, "fs_div": fs_div},
        "dart_status":  dart_meta.get("status"),
        "dart_message": dart_meta.get("message"),
        "list_count":   len(rows),
        "list":         rows,
    }
    p = _cache_path(corp_code, year, fs_div)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_corp_code(corp: str, api_key: str) -> str | None:
    """
    종목코드(6자리) → DART 고유번호(8자리) 변환.
    우선순위:
      1. 이미 8자리 숫자이면 그대로 반환
      2. fin_snapshot.py 하드코딩 테이블 (시연 3사, 네트워크 불필요)
      3. cache/dart/CORPCODE.xml 조회
      4. DART API live 다운로드 (실패 시 None)
    """
    corp = corp.strip()

    # 1) 이미 8자리 → 고유번호로 간주
    if len(corp) == 8 and corp.isdigit():
        return corp

    # 2) 스냅샷 하드코딩 (시연 3사, 항상 성공)
    cc = _snapshot_corp_code(corp)
    if cc:
        return cc

    # 3) 로컬 CORPCODE.xml
    xml_path = _CACHE_DIR / "CORPCODE.xml"
    if xml_path.exists():
        import re
        xml_text = xml_path.read_text(encoding="utf-8")
        for block in re.findall(r"<list>(.*?)</list>", xml_text, re.DOTALL):
            s = re.search(r"<stock_code>\s*(.*?)\s*</stock_code>", block)
            c = re.search(r"<corp_code>\s*(.*?)\s*</corp_code>", block)
            if s and c and s.group(1).strip() == corp:
                return c.group(1).strip()

    # 4) live 다운로드
    try:
        import io, zipfile
        r = requests.get(f"{_CORP_CODE_URL}?crtfc_key={api_key}", timeout=30)
        if r.status_code != 200:
            return None
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])
        xml_path.write_bytes(xml_bytes)
        import re
        xml_text = xml_bytes.decode("utf-8")
        for block in re.findall(r"<list>(.*?)</list>", xml_text, re.DOTALL):
            s = re.search(r"<stock_code>\s*(.*?)\s*</stock_code>", block)
            c = re.search(r"<corp_code>\s*(.*?)\s*</corp_code>", block)
            if s and c and s.group(1).strip() == corp:
                return c.group(1).strip()
    except Exception:
        pass
    return None


def _fetch_dart(corp_code: str, year: int, fs_div: str, api_key: str) -> tuple[list[dict] | None, str]:
    """DART API 호출. 반환: (rows, source)"""
    params = {
        "crtfc_key":  api_key,
        "corp_code":  corp_code,
        "bsns_year":  str(year),
        "reprt_code": _REPRT_CODE,
        "fs_div":     fs_div,
    }
    try:
        r = requests.get(_DART_URL, params=params, timeout=30)
        if r.status_code != 200:
            return None, "api_error"
        data = r.json()
        if data.get("status") != "000":
            return None, f"dart_error:{data.get('status')}"
        rows = data.get("list", [])
        _save_cache(corp_code, year, fs_div, rows, data)
        return rows, "live"
    except Exception:
        return None, "exception"


def _build_result_from_amounts(
    mapped: dict,
    corp_code: str,
    corp_input: str,
    year: int,
    fs_div: str,
    source: str,
) -> dict:
    """extract_amounts 결과를 @tool 반환 형식으로 조립."""
    unmapped_errors = check_unmapped(mapped)

    def _fmt(v) -> str | None:
        return str(v) if v is not None else None

    amt = mapped["amounts"]
    result = {
        "corp_label":  "X사",
        "corp_code":   corp_code,
        "fiscal_year": year,
        "fs_div":      fs_div,
        "amounts_krw": {
            "부채총계":        _fmt(amt["total_liabilities"]),
            "자본총계":        _fmt(amt["total_equity"]),
            "영업이익":        _fmt(amt["operating_income"]),
            "이자비용":        _fmt(amt["interest_expense"]),
            "단기차입금":      _fmt(amt["short_term_borrowings"]),
            "유동성장기부채":   _fmt(amt["current_portion_lt_debt"]),
            "장기차입금":      _fmt(amt["long_term_borrowings"]),
            "사채":           _fmt(amt["bonds"]),
            "리스부채":        _fmt(amt["lease_liabilities"]),
            "영업활동현금흐름": _fmt(amt["operating_cash_flow"]),
        },
        "interest_is_approximate": mapped["interest_is_approximate"],
        "lease_found":             mapped["lease_found"],
        "unmapped":                mapped["unmapped"],
    }

    notes = []
    if mapped["interest_is_approximate"]:
        notes.append("이자비용 직접 계정 없음 → 금융비용으로 근사.")
    if not mapped["lease_found"]:
        notes.append("리스부채를 재무상태표에서 찾지 못함. S1(broad) 시나리오는 미확인 처리.")
    if unmapped_errors:
        notes.append(f"UNMAPPED_ACCOUNT: {[e['account'] for e in unmapped_errors]}")
    if source == "snapshot":
        notes.append("네트워크 연결 없이 사전 추출된 스냅샷 데이터를 사용했습니다. "
                     "(2025 사업보고서 최종 공시 기준)")

    source_data = {
        "corp_code": corp_code, "year": year,
        "fs_div": fs_div, "reprt_code": _REPRT_CODE,
        "cache_file": _cache_path(corp_code, year, fs_div).name,
    }

    return make_ok(
        tool=_TOOL_NAME,
        call_args={"corp": corp_input, "year": year, "fs_div": fs_div},
        source_data=source_data,
        source=source,
        applicable=len(unmapped_errors) == 0,
        result=result,
        applicability_notes=[e["account"] + " 미매핑" for e in unmapped_errors],
        notes=notes,
    )


def _snapshot_to_mapped(snap: dict) -> dict:
    """
    fin_snapshot.py 의 SnapAmounts 를 extract_amounts() 반환 형식으로 변환.
    """
    def _d(v) -> Decimal | None:
        return Decimal(v) if v is not None else None

    return {
        "amounts": {
            "total_liabilities":        _d(snap.get("total_liabilities")),
            "total_equity":             _d(snap.get("total_equity")),
            "operating_income":         _d(snap.get("operating_income")),
            "interest_expense":         _d(snap.get("interest_expense")),
            "short_term_borrowings":    _d(snap.get("short_term_borrowings")),
            "current_portion_lt_debt":  _d(snap.get("current_portion_lt_debt")),
            "long_term_borrowings":     _d(snap.get("long_term_borrowings")),
            "bonds":                    _d(snap.get("bonds")),
            "lease_liabilities":        _d(snap.get("lease_liabilities")),
            "operating_cash_flow":      _d(snap.get("operating_cash_flow")),
        },
        "interest_is_approximate": snap.get("interest_is_approximate", False),
        "lease_found":             snap.get("lease_found", False),
        "unmapped":                snap.get("unmapped", []),
    }


# ── @tool ─────────────────────────────────────────────────────────────────────

@tool
def fin_get_financials(
    corp: str,
    year: int,
    fs_div: str = "CFS",
) -> dict:
    """
    DART 에서 사업보고서 연결/별도 재무제표를 가져와 SPEC §4 형식으로 반환한다.

    에이전트 사용 안내
    ------------------
    - ri_numeric_checker, ri_robustness_checker 에이전트가 호출한다.
    - 반환된 amounts_krw 를 fin_recompute_ratios / fin_adjust_assumptions 에 넘긴다.
    - interest_is_approximate=True 이면 이자비용은 금융비용으로 근사한 것이다.
      판정은 그대로 하되 보완 요청서에 이유를 포함한다.
    - lease_found=False 이면 리스부채를 재무상태표에서 찾지 못한 것이다.
      S1 시나리오(broad 차입금)는 "미확인" 처리하고 S0 와 같은 값으로 채우지 않는다.
    - unmapped 에 항목이 있으면 UNMAPPED_ACCOUNT 경고를 함께 반환한다.
      계정 매핑을 추측하지 말고 사용자에게 확인을 요청한다.
    - source="snapshot" 은 네트워크 없이 사전 추출된 스냅샷 데이터이다.
      시연 3사(035420/005380/003490)에 한해 적용된다.

    Parameters
    ----------
    corp : str
        DART 고유번호(8자리 숫자) 또는 종목코드(6자리).
        예: "00126380" 또는 "005930" (삼성전자)
    year : int
        검증 대상 사업연도(4자리). 예: 2025
    fs_div : str, optional
        재무제표 구분. "CFS"(연결, 기본값) 또는 "OFS"(별도).

    Returns
    -------
    dict
        SPEC §3-1 봉투. result 의 주요 키 (SPEC §4):
        - corp_label            : 발표 화면용 가명 (A사/B사/C사)
        - fiscal_year           : 사업연도
        - fs_div                : CFS|OFS
        - amounts_krw           : {부채총계, 자본총계, 영업이익, 이자비용,
                                    단기차입금, 유동성장기부채, 장기차입금,
                                    사채, 리스부채(None=미확인), 영업활동현금흐름}
        - interest_is_approximate : bool
        - lease_found           : bool (False 이면 S1 미확인)
        - unmapped              : list — 매핑 실패 계정명
    """
    # 입력 검증
    corp = str(corp).strip()
    if not corp:
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT", message="corp 는 필수입니다.")
    if not (1980 <= year <= 2100):
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT",
                          message=f"year={year} 은 유효하지 않습니다.")
    if fs_div not in ("CFS", "OFS"):
        return make_error(tool=_TOOL_NAME, code="BAD_INPUT",
                          message="fs_div 는 'CFS' 또는 'OFS' 여야 합니다.")

    # API 키 로드 (실패해도 snapshot 경로로 갈 수 있으므로 별도 처리)
    api_key = None
    try:
        api_key = get_dart_api_key()
    except RuntimeError:
        pass

    # 종목코드 → 고유번호 변환
    corp_code = _resolve_corp_code(corp, api_key or "")
    if not corp_code:
        return make_error(tool=_TOOL_NAME, code="CORP_NOT_FOUND",
                          message=f"corp='{corp}' 를 DART 에서 찾을 수 없습니다. "
                                  "상장·외감 법인만 조회 가능합니다.")

    # ── 경로 1: live DART API ──────────────────────────────────────────────────
    rows = None
    source = "snapshot"

    if api_key:
        rows, _src = _fetch_dart(corp_code, year, fs_div, api_key)
        if rows:
            source = "live"

    # ── 경로 2: 로컬 캐시 ─────────────────────────────────────────────────────
    if not rows:  # None 또는 빈 리스트 모두 처리
        rows = _load_cache(corp_code, year, fs_div)
        if rows:
            source = "cache"

    # ── 경로 3: 스냅샷 상수 (시연 3사 2025년에 한함) ───────────────────────────
    if not rows:  # None 또는 빈 리스트가 아닐 때만 사용
        stock_key = corp if len(corp) == 6 else None
        # 고유번호로 입력됐을 때도 역매핑으로 종목코드를 찾아 스냅샷 조회
        if stock_key is None:
            from engine.fin_snapshot import DEMO_CORP_CODE_MAP
            inv = {v: k for k, v in DEMO_CORP_CODE_MAP.items()}
            stock_key = inv.get(corp_code)

        snap = get_snapshot(stock_key, fs_div) if stock_key and year == 2025 else None
        if snap is not None:
            mapped = _snapshot_to_mapped(snap)
            return _build_result_from_amounts(mapped, corp_code, corp, year, fs_div, "snapshot")

        return make_error(tool=_TOOL_NAME, code="NO_DATA",
                          message=f"DART API, 캐시, 스냅샷 모두 실패. "
                                  f"corp_code={corp_code}, year={year}, fs_div={fs_div}")

    # ── 계정 매핑 (live/cache 경로) ────────────────────────────────────────────
    mapped = extract_amounts(rows)
    return _build_result_from_amounts(mapped, corp_code, corp, year, fs_div, source)
