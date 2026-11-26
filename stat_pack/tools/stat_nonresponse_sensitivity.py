"""
stat_nonresponse_sensitivity — 무응답 편향 가정 시나리오 분석 (SPEC §7-3, §7-5).
"""

import json, os
from ibm_watsonx_orchestrate.agent_builder.tools import tool
from tools._envelope import make_ok, make_error

_TOOL_NAME = "stat_nonresponse_sensitivity"
_TOOL_VERSION = "0.1.0"


@tool
def stat_nonresponse_sensitivity(
    dataset_id: str,
    criterion_threshold: str,
    criterion_direction: str,
    delta_pp: str = "-20,0,20",
    reference_id: str = "",
) -> dict:
    """
    무응답자의 비율이 응답자와 다를 경우 추정치가 어떻게 달라지는지 시나리오를 계산한다.

    dataset_id 파일에 각 층의 invited(조사 대상 수)가 있어야 한다.
    없으면 n을 invited로 대체한다(무응답 없음 가정, d=0 고정).

    Args:
        dataset_id: 조사 데이터 ID. cache/stat/<dataset_id>.json 필요.
            각 층: {id, n, invited(선택), success 또는 rate}.
            invited가 있어야 무응답 분석이 의미 있다.
        criterion_threshold: 판정 기준값(0~1 비율). 예: "0.55".
        criterion_direction: 판정 방향. "이상", "초과", "이하", "미만" 중 하나.
        delta_pp: 무응답자 비율 차이 가정값(%p) 쉼표 구분 목록. 기본 "-20,0,20".
            0은 무응답 편향 없음 가정(d=0이 baseline). 반드시 0을 포함해야 한다.
            -100~100%p 범위, 최대 9개 값.
        reference_id: 기준 구성표 ID (선택). 비어 있으면 invited 비중으로 가중.
            제공 시 cache/stat/<reference_id>.json을 읽는다.

    Returns:
        ok=True이면 result에 baseline(d=0 추정치), response_rate(관측 응답률),
        scenarios([{delta_pp, estimate, passes, clipped_strata_count}]),
        n_fail(기준 미달 시나리오 수), breakeven_delta_pp(손익분기 차이),
        robustness("견고"/"취약"/"뒤집힘"), break_condition이 들어온다.
    """
    call_args = {
        "dataset_id": dataset_id,
        "criterion_threshold": criterion_threshold,
        "criterion_direction": criterion_direction,
        "delta_pp": delta_pp,
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
    source = "synthetic" if src_data == "synthetic" or src_ref == "synthetic" else "cache"

    # delta 파싱 및 검증
    try:
        delta_list = [d.strip() for d in delta_pp.split(",")]
        if "0" not in delta_list and "0.0" not in delta_list:
            delta_list.append("0")
        if len(delta_list) > 9:
            return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                              source=source, code="BAD_INPUT", message="delta_pp 값이 9개를 초과했다.")
    except Exception as e:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          source=source, code="BAD_INPUT", message=f"delta_pp 파싱 오류: {e}")

    try:
        from engine.stat_engine import nonresponse_sensitivity
        res = nonresponse_sensitivity(
            strata=data["strata"],
            criterion_threshold=criterion_threshold,
            criterion_direction=criterion_direction,
            delta_pp_list=delta_list,
            reference_strata=ref_data["strata"] if ref_data else None,
        )
    except ValueError as e:
        return make_error(tool=_TOOL_NAME, tool_version=_TOOL_VERSION, call_args=call_args,
                          source=source, code="BAD_INPUT", message=str(e))

    return make_ok(
        tool=_TOOL_NAME, tool_version=_TOOL_VERSION,
        call_args=call_args,
        raw_data={"dataset_id": dataset_id, "reference_id": reference_id or None},
        source=source,
        applicable=True,
        result=res,
    )
