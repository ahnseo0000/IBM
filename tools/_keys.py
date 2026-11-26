"""
tools/_keys.py
API 키 로더 — 환경변수 우선, 없으면 프로젝트 루트 key.txt 에서 읽는다.

key.txt 형식 (줄 순서 무관):
    DART=<키값>
    ECOS=<키값>

규칙
- 키 값은 코드·로그·출력·테스트·응답·캐시 파일 어디에도 출력하지 않는다.
- 요청 URL 로그에 키를 넣을 때는 반드시 mask_key() 로 가린다.
- 캐시에는 URL 대신 요청 인자(딕셔너리)만 저장한다.
"""

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_KEY_FILE = _ROOT / "key.txt"

# key.txt 파싱 결과를 모듈 로드 시 한 번만 읽어 캐시한다.
_file_cache: dict[str, str] = {}


def _load_file() -> dict[str, str]:
    global _file_cache
    if _file_cache:
        return _file_cache
    if not _KEY_FILE.exists():
        return {}
    result: dict[str, str] = {}
    for line in _KEY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            result[k.strip().upper()] = v.strip()
    _file_cache = result
    return result


def get_dart_api_key() -> str:
    """
    DART_API_KEY 환경변수 → WXO connection 환경변수 → key.txt DART= 순으로 읽는다.

    WXO cloud 실행 환경에서는 dart_api connection 의 key_value 자격증명이
    WXO_CONNECTION_dart_api_DART 환경변수로 주입된다.

    Raises
    ------
    RuntimeError
        키를 찾을 수 없을 때.
    """
    val = (
        os.environ.get("DART_API_KEY")
        or os.environ.get("WXO_CONNECTION_dart_api_DART")  # WXO key_value connection 주입
        or _load_file().get("DART")
    )
    if not val:
        raise RuntimeError(
            "DART API 키를 찾을 수 없습니다. "
            "환경변수 DART_API_KEY, WXO_CONNECTION_dart_api_DART "
            "또는 key.txt 에 DART=<키> 를 설정하세요."
        )
    return val


def get_ecos_api_key() -> str:
    """
    ECOS_API_KEY 환경변수 → WXO connection 환경변수 → key.txt ECOS= 순으로 읽는다.

    Raises
    ------
    RuntimeError
        키를 찾을 수 없을 때.
    """
    val = (
        os.environ.get("ECOS_API_KEY")
        or os.environ.get("WXO_CONNECTION_dart_api_ECOS")  # 같은 connection 에 ECOS 키도 보관 가능
        or _load_file().get("ECOS")
    )
    if not val:
        raise RuntimeError(
            "ECOS API 키를 찾을 수 없습니다. "
            "환경변수 ECOS_API_KEY 또는 key.txt 에 ECOS=<키> 를 설정하세요."
        )
    return val


def mask_key(url_or_str: str, key: str) -> str:
    """
    URL·문자열에서 키 값을 '****' 으로 치환한다.
    로그 출력 전에 반드시 호출한다.

    Parameters
    ----------
    url_or_str : str
        마스킹 대상 문자열.
    key : str
        숨길 키 값.

    Returns
    -------
    str
        키가 가려진 문자열.
    """
    if not key or not url_or_str:
        return url_or_str
    return url_or_str.replace(key, "****")
