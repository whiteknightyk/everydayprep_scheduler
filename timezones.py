from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


def get_timezone(name: str) -> ZoneInfo:
    """Return an IANA timezone with a helpful error when tzdata is unavailable.

    Windows does not normally ship an IANA timezone database. Python's zoneinfo
    therefore needs the PyPI ``tzdata`` package on such environments.
    """
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"IANAタイムゾーン '{name}' を読み込めません。"
            " Windowsでは tzdata パッケージが必要です。"
            " 仮想環境を有効にして `python -m pip install -r requirements.txt` "
            "（または `python -m pip install tzdata`）を実行してください。"
        ) from exc


def is_valid_timezone(name: str) -> bool:
    try:
        get_timezone(name)
        return True
    except RuntimeError:
        return False
