"""章节回合预算机制：引擎层统一入口。

实现留在 ``chapter_tools``（它同时负责章节切分与编码探测，那两项属于文本
预处理而不是引擎机制层），这里只做转发，保证机制调用方一律走 ``engine``
而不必直接依赖 ``chapter_tools``。
"""
from __future__ import annotations


def turn_budget(chars: int) -> int:
    """按章节字符数计算回合预算（转发 chapter_tools.turn_budget）。"""
    from .chapter_tools import turn_budget as _turn_budget

    return _turn_budget(chars)


__all__ = ["turn_budget"]
