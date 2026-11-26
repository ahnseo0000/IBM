"""
scripts/screen_candidates.py
상장 대기업 30개를 2025 사업보고서 연결(CFS) 기준으로 조회해
리스부채 표시 여부, 부채비율, 이자보상배율, 이자비용 근사 여부를 표로 출력한다.

속도 제한: DART API 는 1초당 10회 권고 → 요청 간 0.6초 간격 사용.
결과는 cache/dart/ 에 저장 (이미 있으면 재조회 생략).
"""

import json
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.account_map import extract_amounts
from tools._keys import get_dart_api_key, mask_key

DART_URL  = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "dart"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

REPRT_CODE = "11011"   # 사업보고서
YEAR       = 2025
FS_DIV     = "CFS"
DELAY_SEC  = 0.6       # 요청 간 간격

# 대기업 30개 (종목코드 → 회사명)
# 시총 상위, 업종 다양화, 리스 비중 높은 업종(유통·통신·항공) 포함
CANDIDATES: list[tuple[str, str]] = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("035420", "NAVER"),
    ("005380", "현대차"),
    ("000270", "기아"),
    ("051910", "LG화학"),
    ("003550", "LG"),
    ("068270", "셀트리온"),
    ("096770", "SK이노베이션"),
    ("017670", "SK텔레콤"),
    ("030200", "KT"),
    ("032830", "삼성생명"),
    ("086790", "하나금융지주"),
    ("105560", "KB금융"),
    ("055550", "신한지주"),
    ("003490", "대한항공"),
    ("020150", "롯데쇼핑"),
    ("023530", "롯데칠성"),
    ("011170", "롯데케미칼"),
    ("004020", "현대제철"),
    ("047050", "포스코인터내셔널"),
    ("005490", "POSCO홀딩스"),
    ("009150", "삼성전기"),
    ("006400", "삼성SDI"),
    ("028260", "삼성물산"),
    ("010950", "S-Oil"),
    ("034730", "SK"),
    ("036570", "엔씨소프트"),
    ("251270", "넷마블"),
    ("010130", "고려아연"),
]


def _cache_path(corp_code: str) -> Path:
    return CACHE_DIR / f"fnlttSinglAcntAll_{corp_code}_{YEAR}_{FS_DIV}.json"


def _load_cache(corp_code: str) -> list[dict] | None:
    p = _cache_path(corp_code)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("list", [])
    return None


def _fetch(corp_code: str, api_key: str) -> tuple[list[dict] | None, str]:
    cached = _load_cache(corp_code)
    if cached is not None:
        return cached, "cache"
    params = {
        "crtfc_key":  api_key,
        "corp_code":  corp_code,
        "bsns_year":  str(YEAR),
        "reprt_code": REPRT_CODE,
        "fs_div":     FS_DIV,
    }
    try:
        r = requests.get(DART_URL, params=params, timeout=20)
        if r.status_code != 200:
            return None, f"http_{r.status_code}"
        data = r.json()
        if data.get("status") != "000":
            return None, f"dart_{data.get('status')}"
        rows = data.get("list", [])
        # 캐시 저장
        payload = {
            "request_args": {"corp_code": corp_code, "bsns_year": YEAR, "fs_div": FS_DIV},
            "dart_status": data.get("status"),
            "list_count": len(rows),
            "list": rows,
        }
        _cache_path(corp_code).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return rows, "live"
    except Exception as e:
        return None, f"err:{e}"


def _ratio(a, b) -> str:
    try:
        if a is None or b is None:
            return "N/A"
        v = Decimal(str(a)) / Decimal(str(b))
        return str(v.quantize(Decimal("0.01")))
    except Exception:
        return "N/A"


def _resolve_corp_code(stock_code: str, xml_text: str) -> str | None:
    import re
    blocks = re.findall(r"<list>(.*?)</list>", xml_text, re.DOTALL)
    for block in blocks:
        s = re.search(r"<stock_code>\s*(.*?)\s*</stock_code>", block)
        c = re.search(r"<corp_code>\s*(.*?)\s*</corp_code>", block)
        if s and c and s.group(1).strip() == stock_code:
            return c.group(1).strip()
    return None


if __name__ == "__main__":
    api_key = get_dart_api_key()

    # corpCode XML 로드
    xml_path = CACHE_DIR / "CORPCODE.xml"
    if not xml_path.exists():
        print("[INFO] CORPCODE.xml 없음 — 다운로드 중...")
        import io, zipfile
        r = requests.get(
            f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={api_key}", timeout=30
        )
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])
        xml_path.write_bytes(xml_bytes)
    xml_text = xml_path.read_text(encoding="utf-8")

    results = []
    for stock_code, name in CANDIDATES:
        corp_code = _resolve_corp_code(stock_code, xml_text)
        if not corp_code:
            results.append({"name": name, "stock": stock_code, "error": "고유번호 없음"})
            continue

        rows, src = _fetch(corp_code, api_key)
        if src == "live":
            time.sleep(DELAY_SEC)   # API 속도 제한

        if rows is None:
            results.append({"name": name, "stock": stock_code, "error": src})
            continue

        mapped = extract_amounts(rows)
        amt    = mapped["amounts"]

        tl = amt["total_liabilities"]
        te = amt["total_equity"]
        oi = amt["operating_income"]
        ie = amt["interest_expense"]
        ls = amt["lease_liabilities"]

        debt_ratio = _ratio(tl, te) + "%" if tl and te else "N/A"
        icr        = _ratio(oi, ie) if oi and ie else "N/A"

        results.append({
            "name":           name,
            "stock":          stock_code,
            "source":         src,
            "lease_found":    mapped["lease_found"],
            "lease_krw":      str(ls) if ls is not None else "N/A",
            "debt_ratio":     debt_ratio,
            "icr":            icr,
            "int_approx":     mapped["interest_is_approximate"],
            "unmapped":       mapped["unmapped"],
            "error":          None,
        })
        sys.stdout.write(f"  {name}({stock_code}) {src} lease={mapped['lease_found']}\n")
        sys.stdout.flush()

    # 표 출력 — 리스부채 있는 기업 먼저
    has_lease  = [r for r in results if r.get("lease_found")]
    no_lease   = [r for r in results if not r.get("lease_found") and not r.get("error")]
    errors     = [r for r in results if r.get("error")]

    def _print_table(rows_in, title):
        if not rows_in:
            return
        print(f"\n{'='*90}")
        print(f" {title}")
        print('='*90)
        h = f"{'회사명':<14} {'종목':>7}  {'리스부채(조)':>12}  {'부채비율':>10}  {'ICR':>8}  {'이자근사':>6}  {'미매핑'}"
        print(h)
        print("-"*90)
        for r in rows_in:
            lease_tr = f"{int(r['lease_krw'])/1e12:.2f}" if r['lease_krw'] not in ("N/A", None) else "-"
            print(
                f"{r['name']:<14} {r['stock']:>7}  {lease_tr:>12}  "
                f"{r['debt_ratio']:>10}  {r['icr']:>8}  "
                f"{'O' if r['int_approx'] else '-':>6}  {r['unmapped']}"
            )

    _print_table(has_lease,  f"리스부채 표시 기업 ({len(has_lease)}개)")
    _print_table(no_lease,   f"리스부채 미표시 기업 ({len(no_lease)}개)")

    if errors:
        print(f"\n[오류] {len(errors)}개")
        for r in errors:
            print(f"  {r['name']}({r['stock']}): {r['error']}")

    # JSON 저장
    out_path = CACHE_DIR / "screen_candidates_2025.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {out_path.name}")
