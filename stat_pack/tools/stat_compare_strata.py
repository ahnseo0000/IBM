"""
stat_compare_strata — 두 그룹 간 층별 비교 및 역전(Simpson) 탐지 (SPEC §7-3, §7-6).
"""

import json, os
from ibm_watsonx_orchestrate.agent_builder.tools import tool
from tools._envelope import make_ok, make_error

_TOOL_NAME = "stat_compare_strata"
_TOOL_VERSION = "0.1.0"


@tool
def stat_compare_strata(
    dataset_id: str,
    group_a: str,
    group_b: str,
    reference_id: str = "",
) -> dict:
    """
    두 그룹의 층별 비율을 비교하고 전체 차이와 반대 부호(Simpson 역전) 여부를 확인한다.

    dataset_id 파일에 groups 배열이 있어야 한다.
    형식: {"groups": [{"id": "A", "strata": [{"id": "h1", "n": 10, "success": 9}]}, ...]}

    Args:
        dataset_id: 비교 데이터 ID. cache/stat/<dataset_id>.json 필요.
            파일에 groups 배열이 있고, 각 그룹은 id와 strata를 가진다.
            strata의 각 층: {id, n, success}.
        group_a: 비교할 첫 번째 그룹 ID (groups 배열의 id 필드).
        group_b: 비교할 두 번째 그룹 ID (groups 배열의 id 필드).
        reference_id: 표준화 차이 계산용 기준 구성표 ID (선택).
            제공 시 cache/stat/<reference_id>.json을 읽어 D_std를 계산한다.
            비어 있으면 standardized_diff_pp는 null로 반환된다.

    Returns:
        ok=True이면 result에 aggregate_diff_pp(A-B 전체 차이%p),
        standardized_diff_pp(기준 구성 가중 차이, 없으면 null),
        strata_directions([{id, diff_pp, direction}]),
        reversal(bool: Simpson 역전 여부), mixed(bool: 층별 방향 혼재),
        robustness("견고"/"취약"/"뒤집힘"), break_condition이 들어온다.
    """
    call_args = {
        "dataset_id": dataset_id,
        "group_a": group_a,
        "group_b": group_b,
        "reference_id": reference_id,
    }

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def load(id_):
        if not id_:
            return None, None
        p = os.path.join(base, "cache", "stat", f"{id_}.json")
        if not os.path.exists(p):
            return None, None
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d, d.get("source", "cache")

    data, src_data = load(dataset_id)
    if data is None:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          code="NO_DATA", message=f"cache/stat/{dataset_id}.json 없음")

    ref_data, src_ref = load(reference_id) if reference_id else (None, None)
    source = "synthetic" if src_data == "synthetic" else "cache"

    # 그룹 추출
    groups = {g["id"]: g["strata"] for g in data.get("groups", [])}
    if group_a not in groups:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          source=source, code="BAD_INPUT",
                          message=f"group_a='{group_a}' 가 데이터에 없다.")
    if group_b not in groups:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          source=source, code="BAD_INPUT",
                          message=f"group_b='{group_b}' 가 데이터에 없다.")

    try:
        from engine.stat_engine import compare_strata
        res = compare_strata(
            group_a=groups[group_a],
            group_b=groups[group_b],
            reference_strata=ref_data["strata"] if ref_data else None,
        )
    except ValueError as e:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          source=source, code="BAD_INPUT", message=str(e))

    # strata_directions가 2KB를 넘을 수 있으므로 2건으로 요약
    full_dirs = res.get("strata_directions", [])
    res["strata_directions"] = full_dirs[:2]
    if len(full_dirs) > 2:
        res["strata_directions_total"] = len(full_dirs)

    return make_ok(
        tool=_TOOL_NAME, tool_version=_TOOL_VERSION,
        call_args=call_args,
        raw_data={"dataset_id": dataset_id, "reference_id": reference_id or None},
        source=source,
        applicable=True,
        result=res,
    )
