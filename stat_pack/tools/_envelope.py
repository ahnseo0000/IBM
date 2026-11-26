"""
공통 응답 봉투 (SPEC §3-1).

모든 도구는 make_ok() 또는 make_error()를 써서 응답을 만든다.
응답은 2 KB(UTF-8) 이하로 유지한다.
"""

import hashlib
import json
from typing import Any

_VERSION_MAX_BYTES = 2048  # SPEC §3-1


def _input_hash(call_args: dict, raw_data: Any = None) -> str:
    """호출 인자 + 사용한 원데이터의 sha256."""
    payload = {"args": call_args, "data": raw_data}
    serialized = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()


def make_ok(
    *,
    tool: str,
    tool_version: str,
    call_args: dict,
    raw_data: Any = None,
    source: str,          # live | cache | synthetic | NO_DATA
    applicable: bool = True,
    applicability_notes: list[str] | None = None,
    result: dict,
    notes: list[str] | None = None,
) -> dict:
    """
    성공 응답 봉투를 만든다.

    Args:
        tool: 도구 이름 (예: "fin_recompute_ratios").
        tool_version: 도구 버전 문자열 (예: "0.1.0").
        call_args: 도구에 전달된 인자 dict (API 키 제외).
        raw_data: 사용한 원데이터 요약 (해시 계산에만 쓰인다; 원본 전체 금지).
        source: 데이터 출처 — "live" | "cache" | "synthetic" | "NO_DATA".
        applicable: 방법의 전제가 충족되었는지.
        applicability_notes: applicable=False 이유 목록.
        result: 도구별 결과 dict (2 KB 상한에 맞게 핵심 값만).
        notes: 추가 안내 목록.

    Returns:
        2 KB 이하 봉투 dict.
    """
    envelope = {
        "ok": True,
        "tool": tool,
        "tool_version": tool_version,
        "input_hash": _input_hash(call_args, raw_data),
        "source": source,
        "applicable": applicable,
        "applicability_notes": applicability_notes or [],
        "result": result,
        "notes": notes or [],
    }
    _assert_size(envelope, tool)
    return envelope


def make_error(
    *,
    tool: str,
    tool_version: str,
    call_args: dict,
    raw_data: Any = None,
    source: str = "NO_DATA",
    code: str,            # NO_DATA | BAD_INPUT | UNSUPPORTED | 팩 전용 코드
    message: str,
) -> dict:
    """
    오류 응답 봉투를 만든다. code는 NO_DATA / BAD_INPUT / UNSUPPORTED 또는 팩 전용.
    """
    envelope = {
        "ok": False,
        "tool": tool,
        "tool_version": tool_version,
        "input_hash": _input_hash(call_args, raw_data),
        "source": source,
        "applicable": False,
        "applicability_notes": [],
        "result": {},
        "notes": [],
        "error": {"code": code, "message": message},
    }
    _assert_size(envelope, tool)
    return envelope


def _assert_size(envelope: dict, tool_name: str) -> None:
    size = len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
    if size > _VERSION_MAX_BYTES:
        raise ValueError(
            f"{tool_name}: 응답 봉투가 {size}바이트로 2KB 상한({_VERSION_MAX_BYTES}B)을 초과했다."
        )
