# -*- coding: utf-8 -*-
"""进程级可变全局的中立注册表（Phase 3a 收编层）。

历史上 `core/app.py` 用两个模块级可变全局承载跨层共享状态：

- ``_DISTILLERS``：``{abs_book_dir: AnchorDistiller}`` 后台蒸馏线程注册表，
  被 server.py 的 distill/progress 端点直接读取（API 层摸 UI 层私有全局）；
- ``_CHARACTER_POOL_CACHE``：内置角色池缓存，被
  engine/character_library.py 反向置空（engine 层清 UI 层缓存）。

本模块把两者收编为中立对象，语义与原全局变量完全一致（薄封装、零行为变化）：

- server / engine 改为 import 本模块，app↔server↔engine 三方循环依赖断根；
- app.py 保留 ``_DISTILLERS = distillers`` 兼容别名（注册表实现完整 dict
  协议，历史读写方式全部可用）。

注意：注册表持有的是线程对象，严禁进入 Gradio State 或存档。
"""
from __future__ import annotations

import threading
from typing import Any, Iterator


class DistillerRegistry:
    """进程级蒸馏线程注册表：``{abs_book_dir: AnchorDistiller}``。

    实现常用 dict 协议（get/set/pop/items/values/``in``/``[]``/len），
    既可以直接当字典用（兼容 app 层历史用法），又提供 ``stop_all``
    统一停机入口。所有读写都在 ``threading.Lock`` 保护下进行。
    """

    def __init__(self) -> None:
        self._distillers: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ---- dict 协议（兼容历史用法） ------------------------------------
    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            return self._distillers.get(key, default)

    def __getitem__(self, key: Any) -> Any:
        with self._lock:
            return self._distillers[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            self._distillers[key] = value

    def __contains__(self, key: Any) -> bool:
        with self._lock:
            return key in self._distillers

    def __len__(self) -> int:
        with self._lock:
            return len(self._distillers)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.keys())

    def keys(self) -> list[Any]:
        with self._lock:
            return list(self._distillers.keys())

    def values(self) -> list[Any]:
        with self._lock:
            return list(self._distillers.values())

    def items(self) -> list[tuple[Any, Any]]:
        with self._lock:
            return list(self._distillers.items())

    def pop(self, key: Any, *default: Any) -> Any:
        with self._lock:
            if default:
                return self._distillers.pop(key, default[0])
            return self._distillers.pop(key)

    def clear(self) -> None:
        with self._lock:
            self._distillers.clear()

    # ---- 注册表显式接口 ------------------------------------------------
    def set(self, key: Any, distiller: Any) -> None:
        """注册/覆盖一个蒸馏器（等价于 ``registry[key] = distiller``）。"""
        with self._lock:
            self._distillers[key] = distiller

    def stop_all(self, except_key: Any = None) -> None:
        """停止（可选排除一个 key 后的）全部蒸馏器并从注册表移除。

        与原 app._stop_distillers 语义一致：逐个 ``stop(join=False)``，
        单个停止失败不阻断其余；被停的条目随即弹出。
        """
        for key, distiller in self.items():
            if except_key and key == except_key:
                continue
            try:
                distiller.stop(join=False)
            except Exception:  # noqa: BLE001  单个停止失败不阻断其余
                pass
            self.pop(key, None)


class _CharacterPoolCache:
    """内置角色池缓存的进程级单例存储（原 app._CHARACTER_POOL_CACHE）。

    值为 ``None``（未缓存）或卡片 tuple；engine 层改动角色库后调
    ``invalidate()`` 使缓存失效。
    """

    def __init__(self) -> None:
        self._value: Any = None
        self._lock = threading.Lock()

    def get(self) -> Any:
        with self._lock:
            return self._value

    def set(self, value: Any) -> None:
        with self._lock:
            self._value = value

    def invalidate(self) -> None:
        with self._lock:
            self._value = None


#: 蒸馏线程注册表单例（app.py 的 _DISTILLERS 兼容别名指向它）。
distillers = DistillerRegistry()

#: 角色池缓存单例（app.py 读写，engine/character_library 失效）。
character_pool_cache = _CharacterPoolCache()


def get_character_pool_cache() -> Any:
    """读取角色池缓存（None 表示未缓存）。"""
    return character_pool_cache.get()


def set_character_pool_cache(value: Any) -> None:
    """写入角色池缓存。"""
    character_pool_cache.set(value)


def invalidate_character_pool_cache() -> None:
    """使角色池缓存失效（下一次开局重新加载）。"""
    character_pool_cache.invalidate()
