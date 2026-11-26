"""
stat_recompute_proportions — 층별 집계에서 전체 비율을 재계산하고 보고값과 비교 (SPEC §7-3, §7-4).
"""

import json
from ibm_watsonx_orchestrate.agent_builder.tools import tool
from tools._envelope import make_ok, make_error

_TOOL_NAME = "stat_recompute_proportions"
_TOOL_VERSION = "0.1.0"


@tool
def stat_recompute_proportions(
    dataset_id: str,
    reported_value: str,
    reported_unit: str = "percent",
    tolerance_pp: str = "0.05",
) -> dict:
    """
    층별 집계 데이터에서 전체 비율을 재계산하고 보고값과 비교한다.

    dataset_id로 cache/stat/<dataset_id>.json 파일을 읽어 층 집계를 가져온다.
    파일 형식: {"strata": [{"id": "h1", "n": 80, "success": 64}, ...]}

    Args:
        dataset_id: manifest에 등록된 조사 데이터 ID.
            cache/stat/<dataset_id>.json 파일이 있어야 한다.
            파일에는 strata 배열이 있고 각 층은 id, n, success(또는 rate)를 가진다.
        reported_value: 원문에서 보고된 수치.
            reported_unit이 "percent"이면 0~100, "proportion"이면 0~1.
        reported_unit: 보고값 단위. "percent"(기본) 또는 "proportion".
        tolerance_pp: 보고값과 재계산값의 허용 오차(%p). 기본 0.05%p.
            이 범위 안이면 reproduced=true.

    Returns:
        ok=True이면 result에 recomputed(0~1 비율), reported, diff_pp,
        reproduced(bool), numeric_accuracy("일치"/"오류 확인"/"미확인"),
        n_total, estimate_interval이 들어온다.
    """
    call_args = {
        "dataset_id": dataset_id,
        "reported_value": reported_value,
        "reported_unit": reported_unit,
        "tolerance_pp": tolerance_pp,
    }

    # 데이터 로드 (live → cache 순서; 이 버전은 cache 전용)
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_path = os.path.join(base, "cache", "stat", f"{dataset_id}.json")

    if not os.path.exists(cache_path):
        return make_error(
            tool=_TOOL_NAME, tool_version=_TOOL_VERSION,
            call_args=call_args,
            code="NO_DATA",
            message=f"cache/stat/{dataset_id}.json 파일 없음. prefetch 또는 직접 생성 필요.",
        )

    with open(cache_path, encoding="utf-8") as f:
        raw = json.load(f)

    source = raw.get("source", "cache")
    strata = raw.get("strata", [])

    if not strata:
        return make_error(
            tool=_TOOL_NAME, tool_version=_TOOL_VERSION,
            call_args=call_args, source=source,
            code="BAD_INPUT",
            message="strata 배열이 비어 있다.",
        )

    try:
        from engine.stat_engine import recompute_proportions
        res = recompute_proportions(strata, reported_value, reported_unit, tolerance_pp)
    except ValueError as e:
        return make_error(
            tool=_TOOL_NAME, tool_version=_TOOL_VERSION,
            call_args=call_args, source=source,
            code="BAD_INPUT", message=str(e),
        )

    # source=synthetic이면 numeric_accuracy 강제 미확인 (SPEC §7-4)
    if source == "synthetic":
        res["numeric_accuracy"] = "미확인"

    return make_ok(
        tool=_TOOL_NAME, tool_version=_TOOL_VERSION,
        call_args=call_args, raw_data={"dataset_id": dataset_id, "source": source},
        source=source,
        applicable=res["n_total"] > 0,
        result=res,
    )
