"""
engine/fin_snapshot.py
시연 3사(035420/005380/003490) 2025 사업보고서 연결·별도 계정 추출값 및
ECOS 기준금리 파라미터 스냅샷.

이 모듈의 값은 DART/ECOS live 호출이 모두 실패할 때 최후 fallback 으로 쓰인다.
source="snapshot" 으로 표시되며, 2025 사업보고서 최종 공시 기준이다.

규칙
- API 키·인증 정보는 절대 포함하지 않는다.
- 금액은 원화 정수 문자열(부호 포함). None = 미확인.
- 이 파일을 직접 편집하지 않는다. scripts/build_snapshot.py 로 재생성한다.
"""

from __future__ import annotations

# ── 종목코드 → DART 고유번호 (시연 3사 하드코딩) ─────────────────────
DEMO_CORP_CODE_MAP: dict[str, str] = {
    "035420": "00266961",   # NAVER
    "005380": "00164742",   # 현대자동차
    "003490": "00113526",   # 대한항공
}


# ── ECOS 기준금리 파라미터 ───────────────────────────────────────────
HIST_MAX_HIKE_BP: str = "225.00"   # 과거 최대 12개월 상승폭 (bp)
LATEST_RATE_PCT:  str = "3"    # 최신 기준금리 (연%, 2026-10)
LATEST_RATE_BP:   str = "300"     # 위를 bp 로 환산


# ── 계정 스냅샷 타입 힌트 ───────────────────────────────────────────
SnapAmounts = dict  # {필드명: str|None}


# ── 3사 스냅샷 ────────────────────────────────────────────────────────
# 형식: SNAPSHOT[stock_code][fs_div] = SnapAmounts
SNAPSHOT: dict[str, dict[str, SnapAmounts]] = {
    # A사/NAVER
    "035420": {
        "CFS": {
            "total_liabilities": "12131482961723",
            "total_equity": "28953013365595",
            "operating_income": "2208138388720",
            "interest_expense": "561520268279",
            "short_term_borrowings": "132159574017",
            "current_portion_lt_debt": "0",
            "long_term_borrowings": "874112668001",
            "bonds": "382248922805",
            "lease_liabilities": "1187801270991",
            "operating_cash_flow": "3095806604677",
            "interest_is_approximate": True,
            "lease_found": True,
            "unmapped": [],
        },
        "OFS": {
            "total_liabilities": "4611415586302",
            "total_equity": "15729782986276",
            "operating_income": "1945532432697",
            "interest_expense": "292396477750",
            "short_term_borrowings": None,
            "current_portion_lt_debt": "1597174210005",
            "long_term_borrowings": "100000000000",
            "bonds": "382248922805",
            "lease_liabilities": "269557043575",
            "operating_cash_flow": "2304981084029",
            "interest_is_approximate": True,
            "lease_found": True,
            "unmapped": ['short_term_borrowings'],
        },
    },
    # B사/현대자동차
    "005380": {
        "CFS": {
            "total_liabilities": "241196612000000",
            "total_equity": "127648237000000",
            "operating_income": "11467851000000",
            "interest_expense": "1009986000000",
            "short_term_borrowings": "10388351000000",
            "current_portion_lt_debt": "33761025000000",
            "long_term_borrowings": "24340003000000",
            "bonds": "106900459000000",
            "lease_liabilities": "1125080000000",
            "operating_cash_flow": "-5991270000000",
            "interest_is_approximate": True,
            "lease_found": True,
            "unmapped": [],
        },
        "OFS": {
            "total_liabilities": "28611827000000",
            "total_equity": "69968561000000",
            "operating_income": "3515052000000",
            "interest_expense": "173967000000",
            "short_term_borrowings": "1002527000000",
            "current_portion_lt_debt": "466966000000",
            "long_term_borrowings": "1255538000000",
            "bonds": "149966000000",
            "lease_liabilities": "345265000000",
            "operating_cash_flow": "6934517000000",
            "interest_is_approximate": True,
            "lease_found": True,
            "unmapped": [],
        },
    },
    # C사/대한항공
    "003490": {
        "CFS": {
            "total_liabilities": "38946973671652",
            "total_equity": "11459098427293",
            "operating_income": "1113589428051",
            "interest_expense": "806880974699",
            "short_term_borrowings": "2027009390000",
            "current_portion_lt_debt": "1437525929701",
            "long_term_borrowings": "3277465790737",
            "bonds": "2623259302544",
            "lease_liabilities": "13037559057911",
            "operating_cash_flow": "4075191178550",
            "interest_is_approximate": False,
            "lease_found": True,
            "unmapped": [],
        },
        "OFS": {
            "total_liabilities": "27268786591778",
            "total_equity": "11187861262069",
            "operating_income": "1539252001960",
            "interest_expense": "519483431003",
            "short_term_borrowings": "1027009390000",
            "current_portion_lt_debt": "998320284527",
            "long_term_borrowings": "3176356499985",
            "bonds": "2632261677121",
            "lease_liabilities": "8192786544936",
            "operating_cash_flow": "3516085485665",
            "interest_is_approximate": False,
            "lease_found": True,
            "unmapped": [],
        },
    },
}


def get_corp_code(stock: str) -> str | None:
    """종목코드 → DART 고유번호. 시연 3사만 반환, 없으면 None."""
    return DEMO_CORP_CODE_MAP.get(str(stock).strip())


def get_snapshot(stock: str, fs_div: str) -> SnapAmounts | None:
    """종목코드 + CFS/OFS → 계정 추출 스냅샷. 없으면 None."""
    return SNAPSHOT.get(str(stock).strip(), {}).get(fs_div)
