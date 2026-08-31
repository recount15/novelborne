# -*- coding: utf-8 -*-
"""提示词文案加载器。

文案按用途拆成 prompts/ 下的独立小文件，代码只保留装配逻辑。
- load(name)：读取并缓存（每回合都会取用，避免重复磁盘 IO）。
- render(name, **kwargs)：把 @@KEY@@ 占位符替换为实参。
占位符用 @@KEY@@ 而非 str.format 的 {key}：文案里的中文与 Markdown 里出现
字面花括号时 format 会抛 KeyError/ValueError，replace 无此风险。
"""
import os
import re
import sys

_CACHE = {}


def _resource_dir():
    """项目根目录（assets/ 所在）。与 fate_engine._resource_dir() 同逻辑（此处不 import 以免循环导入）。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    # 本文件位于 core/prompts/：项目根是再往上两级。
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PROMPTS_DIR = os.path.join(_resource_dir(), "assets", "prompts")


def load(name):
    """读取 prompts/<name> 的正文（去掉尾部换行），带进程内缓存。缺失即报错，不静默降级。"""
    if name in _CACHE:
        return _CACHE[name]
    path = os.path.join(PROMPTS_DIR, name)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise FileNotFoundError(f"提示词文案缺失：{path}（请确认 assets/prompts/ 已随程序分发）") from exc
    text = text.rstrip("\n")
    _CACHE[name] = text
    return text


_PLACEHOLDER = re.compile(r"@@([A-Z0-9_]+)@@")


def render(name, **kwargs):
    """读取文案并把 @@KEY@@ 占位符替换为 kwargs 中同名（大写）的值。

    单次扫描替换：代入的正文（原著节选、性格模型等来自用户）即便含 @@X@@ 字样也不会被二次替换。
    值按 str() 转换（与原先的 f-string 语义一致）；未提供的占位符原样保留，便于发现装配漏项。
    """
    values = {key.upper(): str(value) for key, value in kwargs.items()}
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), load(name))
