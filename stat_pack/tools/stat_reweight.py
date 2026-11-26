"""
stat_reweight — 기준 구성표로 사후층화 재가중 후 판정 (SPEC §7-3, §7-4).
"""

import json, os
from ibm_watsonx_orchestrate.agent_builder.tools import tool
from tools._envelope import make_ok, make_error

_TOOL_NAME = "stat_reweight"
_TOOL_VERSION = "0.1.0"


@tool
def stat_reweight(
    dataset_id: str,
    reference_id: str,
    criterion_threshold: str,
    criterion_direction: str,
    weak_margin_pp: str = "2.0",
) -> dict:
    """
    조사 데이터를 기준 구성표로 사후층화 재가중하고 기준 충족 여부를 판정한다.

    dataset_id로 cache/stat/<dataset_id>.json,
    reference_id로 cache/stat/<reference_id>.json을 읽는다.
    reference 파일 형식: {"strata": [{"id": "h1", "population_count": 500}, ...]}

    Args:
        dataset_id: 조사 데이터 ID. cache/stat/<dataset_id>.json에 strata 배열 필요.
            각 층: {id, n, success 또는 rate}.
        reference_id: 기준 구성표 ID. cache/stat/<reference_id>.json에 strata 배열 필요.
            각 층: {id, population_count 또는 share}. share 합은 1이어야 한다.
        criterion_threshold: 판정 기준값(0~1 비율). 예: "0.60".
        criterion_direction: 판정 방향. "이상", "초과", "이하", "미만" 중 하나.
        weak_margin_pp: 취약 판정 여유폭(%p). 기본 2.0%p.
            재가중 후 기준을 충족하지만 여유폭이 이 값 이하면 "취약"으로 판정한다.

    Returns:
        ok=True이면 result에 baseline(비가중 비율), reweighted_estimate,
        delta_pp(재가중-비가중 차이), passes(bool), margin_pp,
        max_weight_factor, robustness("견고"/"취약"/"뒤집힘"),
        break_condition이 들어온다.
    """
    call_args = {
        "dataset_id": dataset_id,
        "reference_id": reference_id,
        "criterion_threshold": criterion_threshold,
        "criterion_direction": criterion_direction,
        "weak_margin_pp": weak_margin_pp,
    }

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def load(id_):
        p = os.path.join(base, "cache", "stat", f"{id_}.json")
        if not os.path.exists(p):
            return None, None
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d, d.get("source", "cache")

    data, src_data = load(dataset_id)
    ref, src_ref = load(reference_id)

    if data is None:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          code="NO_DATA", message=f"cache/stat/{dataset_id}.json 없음")
    if ref is None:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          code="NO_DATA", message=f"cache/stat/{reference_id}.json 없음")

    source = "synthetic" if "synthetic" in (src_data, src_ref) else "cache"

    try:
        from engine.stat_engine import reweight
        res = reweight(
            strata=data["strata"],
            reference_strata=ref["strata"],
            criterion_threshold=criterion_threshold,
            criterion_direction=criterion_direction,
            weak_margin_pp=weak_margin_pp,
        )
    except ValueError as e:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          source=source, code="BAD_INPUT", message=str(e))

    applicable = res.pop("applicable", True)
    return make_ok(
        tool=_TOOL_NAME, tool_version=_TOOL_VERSION,
        call_args=call_args,
        raw_data={"dataset_id": dataset_id, "reference_id": reference_id},
        source=source,
        applicable=applicable,
        applicability_notes=[] if applicable else ["양의 기준 가중치 층에 응답 없음"],
        result=res,
    )
