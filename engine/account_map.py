"""
engine/account_map.py
DART fnlttSinglAcntAll 응답 행 목록에서 SPEC §6-1 계정과목을 추출한다.
네트워크 호출 없는 순수 매핑 엔진. 오프라인 테스트 가능.

매핑 우선순위
1. account_id 가 알려진 IFRS/DART 표준 ID 와 정확히 일치
2. account_nm(계정명)이 키워드를 포함 (부분일치)
3. 1·2 모두 실패 → UNMAPPED_ACCOUNT 오류 (후보 목록 포함)

이자비용 특례
- account_id "ifrs-full_FinanceCosts" (금융비용) 으로 근사하면 interest_is_approximate=True
- account_id "dart_InterestExpense" 등 직접 이자비용이 있으면 approximate=False

리스부채 특례
- 유동·비유동 리스부채를 모두 합산
- 한 행도 없으면 lease_found=False, value=None (0 으로 채우지 않는다)
- S1 시나리오는 호출자가 lease_found 를 보고 "미확인"으로 처리해야 한다
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# ── 표준 계정 ID 매핑 테이블 (account_id → SPEC 계정명) ─────────────────────────
# DART 공시에서 실제 확인된 ID 를 우선으로, 대안 ID 를 순서대로 나열한다.

_ID_MAP: dict[str, list[str]] = {
    # 재무상태표 (BS)
    "total_liabilities": [
        "ifrs-full_Liabilities",
    ],
    "total_equity": [
        "ifrs-full_Equity",
        "ifrs-full_EquityAttributableToOwnersOfParent",   # 지배주주지분만인 경우 fallback
    ],
    "short_term_borrowings": [
        "dart_ShortTermBorrowings",
        "ifrs-full_ShorttermBorrowings",
        # 삼성전자처럼 '-표준계정코드 미사용-' 인 경우 계정명 fallback 으로 처리
    ],
    "current_portion_lt_debt": [
        "ifrs-full_CurrentPortionOfLongtermBorrowings",
        "dart_CurrentPortionOfLongTermBorrowings",
    ],
    "long_term_borrowings": [
        "ifrs-full_NoncurrentPortionOfNoncurrentLoansReceived",
        "ifrs-full_LongtermBorrowings",
        "dart_LongTermBorrowings",
    ],
    "bonds": [
        "ifrs-full_NoncurrentPortionOfNoncurrentBondsIssued",
        "ifrs-full_BondsIssued",
        "dart_BondsPayable",
    ],
    # 리스부채: 유동 + 비유동 합산 (각각 별도 처리)
    "lease_liabilities_current": [
        "ifrs-full_CurrentLeaseLiabilities",
        "dart_CurrentLeaseLiabilities",
    ],
    "lease_liabilities_noncurrent": [
        "ifrs-full_NoncurrentLeaseLiabilities",
        "dart_NoncurrentLeaseLiabilities",
        "ifrs-full_LeaseLiabilities",
    ],
    # 손익계산서 (IS)
    "operating_income": [
        "dart_OperatingIncomeLoss",
        "ifrs-full_OperatingIncomeLoss",
        "ifrs-full_ProfitLossFromOperatingActivities",
    ],
    "interest_expense_direct": [
        "dart_InterestExpense",
        "ifrs-full_InterestExpense",
        "dart_FinanceExpenses",
    ],
    "interest_expense_approx": [
        # 직접 이자비용이 없을 때 금융비용으로 근사 (SPEC §6-1)
        "ifrs-full_FinanceCosts",
        "dart_FinanceCosts",
    ],
    # 현금흐름표 (CF)
    "operating_cash_flow": [
        "ifrs-full_CashFlowsFromUsedInOperatingActivities",
        "dart_CashFlowsFromOperatingActivities",
    ],
}

# ── 계정명 키워드 fallback (account_id 미스 또는 '-표준계정코드 미사용-' 인 경우) ───
_NM_KEYWORDS: dict[str, list[str]] = {
    "total_liabilities":          ["부채총계"],
    "total_equity":               ["자본총계"],
    "short_term_borrowings":      ["단기차입금"],
    "current_portion_lt_debt":    ["유동성장기부채", "유동성장기차입금"],
    "long_term_borrowings":       ["장기차입금"],
    "bonds":                      ["사채"],
    "lease_liabilities_current":  ["유동리스부채", "유동 리스부채", "리스부채(유동)", "단기리스부채"],
    "lease_liabilities_noncurrent": ["비유동리스부채", "비유동 리스부채", "리스부채(비유동)", "장기리스부채", "리스부채"],
    "operating_income":           ["영업이익", "영업손익"],
    "interest_expense_direct":    ["이자비용"],
    "interest_expense_approx":    ["금융비용", "금융원가"],
    "operating_cash_flow":        ["영업활동현금흐름", "영업활동으로 인한 현금흐름"],
}

# 한 계정이 여러 sj_div 에 중복 등장할 때 우선할 재무제표 구분
_SJ_PRIORITY = {"BS": 0, "IS": 1, "CF": 2, "CIS": 3, "SCE": 4}


def _pick(rows: list[dict], account_key: str) -> dict | None:
    """
    rows 에서 account_key 에 해당하는 단 하나의 행을 반환한다.
    없으면 None, 여럿이면 sj_div 우선순위로 첫 번째 선택.
    """
    # 1순위: account_id 직접 일치
    for cid in _ID_MAP.get(account_key, []):
        matched = [r for r in rows if r.get("account_id") == cid]
        if matched:
            matched.sort(key=lambda r: _SJ_PRIORITY.get(r.get("sj_div", ""), 99))
            return matched[0]

    # 2순위: account_nm 키워드 포함
    for kw in _NM_KEYWORDS.get(account_key, []):
        matched = [r for r in rows if kw in r.get("account_nm", "")]
        if matched:
            matched.sort(key=lambda r: _SJ_PRIORITY.get(r.get("sj_div", ""), 99))
            return matched[0]

    return None


def _amount(row: dict | None) -> Decimal | None:
    if row is None:
        return None
    raw = row.get("thstrm_amount", "")
    if not raw or raw.strip() == "":
        return None
    try:
        # DART 는 쉼표 없는 정수 문자열
        return Decimal(str(raw).replace(",", "").strip())
    except Exception:
        return None


def _candidates(rows: list[dict], keywords: list[str]) -> list[str]:
    """매핑 실패 시 후보 계정 목록 반환 (account_nm + account_id)."""
    result = []
    for kw in keywords:
        for r in rows:
            if kw in r.get("account_nm", "") or kw in r.get("account_id", ""):
                entry = f"{r.get('account_nm','')} ({r.get('account_id','')})"
                if entry not in result:
                    result.append(entry)
    return result[:5]  # 최대 5개


# ══════════════════════════════════════════════════════════════════════════════
# 공개 API
# ══════════════════════════════════════════════════════════════════════════════

class MappingError(Exception):
    """계정 매핑 실패."""
    def __init__(self, code: str, message: str, candidates: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.candidates = candidates or []


def extract_amounts(rows: list[dict]) -> dict:
    """
    DART fnlttSinglAcntAll 응답 행 목록에서 SPEC §6-1 계정과목을 추출한다.

    Parameters
    ----------
    rows : list of dict
        fnlttSinglAcntAll API 응답의 "list" 필드 전체.
        (sj_div 가 BS·IS·CF 행이 모두 포함된 상태)

    Returns
    -------
    dict
        {
          "amounts": {
            "total_liabilities":        Decimal | None,
            "total_equity":             Decimal | None,
            "operating_income":         Decimal | None,
            "interest_expense":         Decimal | None,  # 근사값 포함
            "short_term_borrowings":    Decimal | None,
            "current_portion_lt_debt":  Decimal | None,
            "long_term_borrowings":     Decimal | None,
            "bonds":                    Decimal | None,
            "lease_liabilities":        Decimal | None,  # None = 미확인
            "operating_cash_flow":      Decimal | None,
          },
          "interest_is_approximate": bool,
          "lease_found":             bool,
          "unmapped":                list[str],   # 매핑 실패 계정 이름
        }

    Notes
    -----
    - lease_liabilities 가 None 이면 S1 시나리오는 "미확인" 처리.
    - unmapped 에 항목이 있으면 도구 호출자가 UNMAPPED_ACCOUNT 오류를 고려한다.
    """
    unmapped: list[str] = []

    def _get(key: str, required: bool = True) -> Decimal | None:
        row = _pick(rows, key)
        val = _amount(row)
        if val is None and required:
            cands = _candidates(rows, _NM_KEYWORDS.get(key, []) + _ID_MAP.get(key, []))
            unmapped.append(key)
            return None
        return val

    # ── 필수 계정 ──────────────────────────────────────────────────────────────
    total_liabilities       = _get("total_liabilities")
    total_equity            = _get("total_equity")
    operating_income        = _get("operating_income")
    operating_cash_flow     = _get("operating_cash_flow")
    short_term_borrowings   = _get("short_term_borrowings")
    current_portion_lt_debt = _get("current_portion_lt_debt")
    long_term_borrowings    = _get("long_term_borrowings")
    bonds                   = _get("bonds")

    # ── 이자비용 (직접 → 금융비용 근사) ────────────────────────────────────────
    interest_is_approximate = False
    ie_row = _pick(rows, "interest_expense_direct")
    interest_expense = _amount(ie_row)
    if interest_expense is None:
        ie_row = _pick(rows, "interest_expense_approx")
        interest_expense = _amount(ie_row)
        if interest_expense is not None:
            interest_is_approximate = True
        else:
            unmapped.append("interest_expense")

    # ── 리스부채 (유동+비유동 합산; 없으면 None) ─────────────────────────────
    lc_row = _pick(rows, "lease_liabilities_current")
    lnc_row = _pick(rows, "lease_liabilities_noncurrent")
    lc  = _amount(lc_row)
    lnc = _amount(lnc_row)

    if lc is None and lnc is None:
        lease_liabilities = None
        lease_found = False
    else:
        lease_liabilities = (lc or Decimal(0)) + (lnc or Decimal(0))
        lease_found = True

    return {
        "amounts": {
            "total_liabilities":        total_liabilities,
            "total_equity":             total_equity,
            "operating_income":         operating_income,
            "interest_expense":         interest_expense,
            "short_term_borrowings":    short_term_borrowings,
            "current_portion_lt_debt":  current_portion_lt_debt,
            "long_term_borrowings":     long_term_borrowings,
            "bonds":                    bonds,
            "lease_liabilities":        lease_liabilities,
            "operating_cash_flow":      operating_cash_flow,
        },
        "interest_is_approximate": interest_is_approximate,
        "lease_found":             lease_found,
        "unmapped":                unmapped,
    }


def check_unmapped(result: dict) -> list[dict]:
    """
    extract_amounts() 결과에서 매핑 실패 항목을 UNMAPPED_ACCOUNT 형식으로 반환.

    Returns
    -------
    list of dict
        [{"code": "UNMAPPED_ACCOUNT", "account": "...", "candidates": [...]}]
    """
    errors = []
    for acct in result.get("unmapped", []):
        errors.append({
            "code": "UNMAPPED_ACCOUNT",
            "account": acct,
        })
    return errors
