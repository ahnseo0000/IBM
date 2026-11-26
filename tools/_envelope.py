"""
tools/_envelope.py
SPEC §3-1 공통 응답 봉투 — 모든 fin_/stat_ 도구가 반드시 사용한다.

팀원과 인터페이스를 맞추기 위해 이 파일을 직접 수정하지 않는다.
"""

import hashlib
import json
from typing import Any

TOOL_VERSION = "0.1.0"
_MAX_BYTES = 2048  # UTF-8 직렬화 기준 2 KB 상한 (SPEC §3-1)


# ──────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────

def _sha256(data: Any) -> str:
    """임의 직렬화 가능 값의 SHA-256 hex digest 를 반환한다."""
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_size(payload: dict) -> dict:
    """직렬화 크기가 2 KB 를 초과하면 BAD_INPUT 오류로 대체한다."""
    serialized = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(serialized) > _MAX_BYTES:
        return make_error(
            tool=payload.get("tool", "unknown"),
            code="BAD_INPUT",
            message=(
                f"응답 크기가 {len(serialized)} bytes 로 2 KB 상한을 초과합니다. "
                "result 를 요약하거나 detail_ref 를 사용하세요."
            ),
        )
    return payload


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def make_ok(
    *,
    tool: str,
    call_args: Any,
    source_data: Any,
    source: str,
    applicable: bool,
    result: dict,
    applicability_notes: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict:
    """
    성공 응답 봉투를 만들어 반환한다.

    Parameters
    ----------
    tool : str
        도구 이름 (예: "fin_recompute_ratios").
    call_args : Any
        도구 호출 인자 전체 (API 키 제외). input_hash 계산에 사용한다.
    source_data : Any
        실제로 사용한 원데이터 요약 (캐시 파일 경로·API 응답 키값 등).
        input_hash 에 포함된다. API 키·개인정보는 절대 넣지 않는다.
    source : str
        "live" | "cache" | "synthetic" | "NO_DATA"
    applicable : bool
        방법의 전제(모집단 일치, 표본 설계, 단위·기간 등)가 충족되었는지.
        False 이면 총괄이 "계산 가능, 실무 채택 보류"로 표시한다.
    result : dict
        도구별 결과 필드. 2 KB 상한에 맞게 핵심 값만 담는다.
    applicability_notes : list[str], optional
        applicable=False 사유 목록.
    notes : list[str], optional
        추가 주의사항.

    Returns
    -------
    dict
        SPEC §3-1 봉투 구조. 2 KB 초과 시 BAD_INPUT 오류로 대체된다.
    """
    payload = {
        "ok": True,
        "tool": tool,
        "tool_version": TOOL_VERSION,
        "input_hash": _sha256({"args": call_args, "data": source_data}),
        "source": source,
        "applicable": applicable,
        "applicability_notes": applicability_notes or [],
        "result": result,
        "notes": notes or [],
    }
    return _check_size(payload)


def make_error(
    *,
    tool: str,
    code: str,
    message: str,
) -> dict:
    """
    오류 응답 봉투를 만들어 반환한다.

    Parameters
    ----------
    tool : str
        오류가 발생한 도구 이름.
    code : str
        공통 오류 코드: "NO_DATA" | "BAD_INPUT" | "UNSUPPORTED"
        금융 팩 전용 코드: "CORP_NOT_FOUND" | "UNMAPPED_ACCOUNT" | "UNSUPPORTED_METRIC"
    message : str
        사람이 읽을 수 있는 오류 설명. API 키·개인정보를 포함하지 않는다.

    Returns
    -------
    dict
        {"ok": false, "error": {"code": ..., "message": ...}}
    """
    return {
        "ok": False,
        "tool": tool,
        "tool_version": TOOL_VERSION,
        "error": {
            "code": code,
            "message": message,
        },
    }
