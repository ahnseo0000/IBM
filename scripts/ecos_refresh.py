"""scripts/ecos_refresh.py — 종료를 202611로 재조회 + 상세 보고"""
import hashlib, json, sys
from decimal import Decimal
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools._keys import get_ecos_api_key, mask_key

ECOS_BASE  = "https://ecos.bok.or.kr/api"
CACHE_DIR  = Path(__file__).resolve().parent.parent / "cache" / "ecos"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

STAT_CODE  = "722Y001"
ITEM_CODE  = "0101000"
FREQ       = "M"
START_DATE = "202001"
END_DATE   = "202611"   # 2026-11

api_key = get_ecos_api_key()
url = (f"{ECOS_BASE}/StatisticSearch/{api_key}/json/kr"
       f"/1/200/{STAT_CODE}/{FREQ}/{START_DATE}/{END_DATE}/{ITEM_CODE}")
print(f"GET {mask_key(url, api_key)}")

r = requests.get(url, timeout=20)
data = r.json()
ss   = data["StatisticSearch"]
rows = ss.get("row", [])
print(f"total={ss.get('list_total_count')}  rows={len(rows)}")

# 캐시 저장
safe = {"request": {"stat_code": STAT_CODE, "item_code": ITEM_CODE,
                    "freq": FREQ, "start": START_DATE, "end": END_DATE},
        "total_count": ss.get("list_total_count"), "rows": rows}
fname = f"StatisticSearch_{STAT_CODE}_{ITEM_CODE}_{START_DATE}_{END_DATE}.json"
(CACHE_DIR / fname).write_bytes(json.dumps(safe, ensure_ascii=False, indent=2).encode("utf-8"))
print(f"saved: {fname}")

# 시계열 파싱
series = []
for row in rows:
    t, v = row.get("TIME",""), row.get("DATA_VALUE","")
    if t and v and v.strip() not in ("", "-", " "):
        try:
            series.append((t, Decimal(v.strip())))
        except Exception:
            pass
series.sort(key=lambda x: x[0])

print(f"\n최근 5건:")
for t, v in series[-5:]:
    print(f"  {t}: {v}%")

# 12개월 창 최대 상승폭
max_hike = Decimal(0)
max_start = max_end = ""
for i in range(len(series) - 12):
    hike = series[i+12][1] - series[i][1]
    if hike > max_hike:
        max_hike = hike
        max_start, max_end = series[i][0], series[i+12][0]

print(f"\nhist_max_hike_bp = {max_hike * 100} bp")
print(f"  창: {max_start} → {max_end}")
# 시작·끝 월 금리
rate_start = next(v for t,v in series if t == max_start)
rate_end   = next(v for t,v in series if t == max_end)
print(f"  시작({max_start}) 금리: {rate_start}%")
print(f"  끝({max_end}) 금리:   {rate_end}%")

# 결과 저장
result = {"hist_max_hike_bp": str(max_hike*100), "max_hike_period": f"{max_start}→{max_end}",
          "rate_at_start_pct": str(rate_start), "rate_at_end_pct": str(rate_end),
          "latest_rate_pct": str(series[-1][1]), "latest_time": series[-1][0],
          "series_len": len(series)}
(CACHE_DIR / "hist_max_hike_bp.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n최신 기준금리: {series[-1][1]}%  ({series[-1][0]})")
