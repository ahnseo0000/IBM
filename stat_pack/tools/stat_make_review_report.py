"""
stat_make_review_report — 통계 팩 최종 판정 보고서 (SPEC §7-3, §7-8).

총괄(ri_coordinator)이 호출한다. 이전 에이전트 판정을 재사용하지 않고
같은 엔진으로 직접 재실행해 결정적 판정을 낸다.
"""

import json, os, hashlib
from ibm_watsonx_orchestrate.agent_builder.tools import tool
from tools._envelope import make_ok, make_error

_TOOL_NAME = "stat_make_review_report"
_TOOL_VERSION = "0.1.0"


def _load_cache(base: str, id_: str):
    if not id_:
        return None, None
    p = os.path.join(base, "cache", "stat", f"{id_}.json")
    if not os.path.exists(p):
        return None, None
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return d, d.get("source", "cache")


def _save_artifact(base: str, content: dict) -> tuple[str, str]:
    """상세 결과를 cache/stat/artifacts/에 저장하고 (ref, hash)를 반환."""
    art_dir = os.path.join(base, "cache", "stat", "artifacts")
    os.makedirs(art_dir, exist_ok=True)
    serialized = json.dumps(content, ensure_ascii=False, sort_keys=True)
    h = "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()
    fname = h.replace("sha256:", "") + ".json"
    with open(os.path.join(art_dir, fname), "w", encoding="utf-8") as f:
        f.write(serialized)
    return fname, h


@tool
def stat_make_review_report(
    claims: str,
) -> dict:
    """
    통계 주장 목록을 받아 각 주장을 엔진으로 재실행하고 최종 판정 보고서를 만든다.

    총괄(ri_coordinator)이 호출한다. 이 도구가 통계 팩 판정의 유일한 원천이다.
    이전 에이전트가 전달한 판정은 사용하지 않고 동일 엔진으로 직접 재계산한다.

    Args:
        claims: JSON 문자열. 주장 배열.
            각 원소 필드:
            - id (필수): 주장 고유 ID. 예 "C01".
            - text (필수): 원문 주장 텍스트.
            - kind (필수): "proportion" | "group_comparison" | "causal_or_forecast".
              proportion: 비율 주장 (재계산+재가중+무응답 필수).
              group_comparison: 집단 비교 주장 (층별 비교 필수).
              causal_or_forecast: 인과/전망 — 계산하지 않고 미확인 처리.
            - dataset_id (선택): cache/stat/<id>.json 데이터 파일 ID.
            - reported_value (선택): 원문 보고값. proportion 주장에 필요.
            - reported_unit (선택): "percent"(기본) 또는 "proportion".
            - criterion_threshold (선택): 판정 기준값(0~1). 예 "0.60".
            - criterion_direction (선택): "이상"|"초과"|"이하"|"미만".
            - reference_id (선택): 기준 구성표 ID.
            - group_a / group_b (선택): group_comparison 주장의 그룹 ID.
            예시: '[{"id":"C01","text":"응답자 80%가 도입 예정","kind":"proportion","dataset_id":"survey_01","reported_value":"80","criterion_threshold":"0.60","criterion_direction":"이상"}]'

    Returns:
        ok=True이면 result에 rows(주장별 판정 2건 요약), doc_verdict(전체 권고),
        audit(감사 로그 요약), report_ref(상세 파일명), detail_hash가 들어온다.
    """
    call_args = {"claims": claims[:200]}  # 해시용, 전체 저장 금지

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # claims 파싱
    try:
        claim_list = json.loads(claims)
    except json.JSONDecodeError as e:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          code="BAD_INPUT", message=f"claims JSON 파싱 오류: {e}")

    if not claim_list:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          code="BAD_INPUT", message="claims 배열이 비어 있다.")

    from engine.stat_engine import (
        recompute_proportions, reweight, nonresponse_sensitivity,
        compare_strata, derive_verdict, derive_doc_verdict,
    )

    rows = []
    audit_tools = []
    overall_source = "cache"

    for claim in claim_list:
        cid = claim.get("id", "?")
        text = claim.get("text", "")
        kind = claim.get("kind", "proportion")
        dataset_id = claim.get("dataset_id", "")
        reported_value = claim.get("reported_value")
        reported_unit = claim.get("reported_unit", "percent")
        criterion_threshold = claim.get("criterion_threshold")
        criterion_direction = claim.get("criterion_direction", "이상")
        reference_id = claim.get("reference_id", "")
        group_a = claim.get("group_a", "")
        group_b = claim.get("group_b", "")

        numeric_accuracy = None
        robustness = None
        recomputed = None
        source = "cache"
        applicable = True
        skip_reasons = []

        if kind == "causal_or_forecast":
            numeric_accuracy = "미확인"
            robustness = None
            skip_reasons.append("인과/전망 주장 — 계산 검증 대상 아님")

        elif kind == "proportion" and dataset_id:
            data, src = _load_cache(base, dataset_id)
            source = src or "NO_DATA"
            if overall_source != "synthetic":
                overall_source = source

            if data is None:
                numeric_accuracy = "미확인"
                skip_reasons.append(f"NO_DATA: {dataset_id}")
            else:
                strata = data.get("strata", [])
                # 재계산
                try:
                    r = recompute_proportions(strata, reported_value or "0", reported_unit)
                    recomputed = r.get("recomputed")
                    numeric_accuracy = "미확인" if source == "synthetic" else r.get("numeric_accuracy", "미확인")
                    audit_tools.append(f"stat_recompute_proportions({cid})")
                except Exception as e:
                    numeric_accuracy = "미확인"
                    skip_reasons.append(f"재계산 오류: {e}")

                # 재가중
                if reference_id and criterion_threshold:
                    ref_data, _ = _load_cache(base, reference_id)
                    if ref_data:
                        try:
                            rw = reweight(strata, ref_data["strata"], criterion_threshold, criterion_direction)
                            if rw.get("robustness"):
                                robustness = rw["robustness"]
                            audit_tools.append(f"stat_reweight({cid})")
                        except Exception as e:
                            skip_reasons.append(f"재가중 오류: {e}")

                # 무응답
                if criterion_threshold and any("invited" in s for s in strata):
                    try:
                        nr = nonresponse_sensitivity(strata, criterion_threshold, criterion_direction)
                        if robustness is None and nr.get("robustness"):
                            robustness = nr["robustness"]
                        audit_tools.append(f"stat_nonresponse_sensitivity({cid})")
                    except Exception as e:
                        skip_reasons.append(f"무응답 분석 오류: {e}")

        elif kind == "group_comparison" and dataset_id:
            data, src = _load_cache(base, dataset_id)
            source = src or "NO_DATA"
            if data is None:
                numeric_accuracy = "미확인"
                skip_reasons.append(f"NO_DATA: {dataset_id}")
            else:
                ref_data, _ = _load_cache(base, reference_id) if reference_id else (None, None)
                groups = {g["id"]: g["strata"] for g in data.get("groups", [])}
                if group_a in groups and group_b in groups:
                    try:
                        cs = compare_strata(groups[group_a], groups[group_b],
                                            ref_data["strata"] if ref_data else None)
                        robustness = cs.get("robustness")
                        numeric_accuracy = "미확인"  # 집단 비교는 수치 정확성 별도
                        audit_tools.append(f"stat_compare_strata({cid})")
                    except Exception as e:
                        skip_reasons.append(f"층별 비교 오류: {e}")
                else:
                    skip_reasons.append(f"group_a/b ID 불일치")
        else:
            numeric_accuracy = "미확인"
            skip_reasons.append("dataset_id 없음 또는 지원하지 않는 kind")

        verdict = derive_verdict(
            numeric_accuracy=numeric_accuracy,
            robustness=robustness,
            source=source,
            applicable=applicable,
            kind=kind,
        )

        rows.append({
            "claim_id": cid,
            "pack": "stat",
            "claim": text[:80],
            "recomputed": recomputed,
            "numeric_accuracy": numeric_accuracy,
            "robustness": robustness,
            "verdict": verdict,
            "summary_numbers": recomputed or "미확인",
        })

    verdicts = [r["verdict"] for r in rows]
    doc_verdict = derive_doc_verdict(verdicts)

    # 상세 파일 저장
    detail = {"rows": rows, "audit_tools": list(set(audit_tools))}
    report_ref, detail_hash = _save_artifact(base, detail)

    return make_ok(
        tool=_TOOL_NAME, tool_version=_TOOL_VERSION,
        call_args=call_args,
        source=overall_source,
        result={
            "rows": rows[:2],           # 2KB 상한: 2건 요약
            "rows_total": len(rows),
            "doc_verdict": doc_verdict,
            "audit": {"tools": list(set(audit_tools))[:5], "tool_version": _TOOL_VERSION},
            "report_ref": report_ref,
            "detail_hash": detail_hash,
        },
    )
