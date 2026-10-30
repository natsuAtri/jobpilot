"""51job 城市/地区码。

实测验证过的常用城市；未收录的城市回退到 000000（平台默认城市），
再由调用方按地点文本做二次过滤。
"""
from __future__ import annotations

CITY_CODES: dict[str, str] = {
    "北京": "010000",
    "上海": "020000",
    "广州": "030200",
    "深圳": "040000",
    "天津": "050000",
    "南京": "070200",
    "苏州": "070300",
    "杭州": "080200",
    "成都": "090200",
    "武汉": "180200",
    "西安": "200200",
}

DEFAULT_CODE = "000000"


def code_for(city: str) -> str:
    """城市名 → 51job 地区码；未知城市返回默认。"""
    city = (city or "").strip()
    if not city:
        return DEFAULT_CODE
    for name, code in CITY_CODES.items():
        if name == city or name in city:
            return code
    return DEFAULT_CODE
