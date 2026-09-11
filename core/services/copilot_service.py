# -*- coding: utf-8 -*-
"""Copilot 助手服务：一站式了解对局状态、检索用户文档、通过白名单工具执行操作。

设计约束：
- 模型只能调用注册表内的确定性工具，绝不转发任意 HTTP 或执行任意代码；
- 对局凭据只在端点层取得并传给 fe.make_client，不进入日志、提示词或响应；
- 回答与工具结果统一脱敏（不得出现 API Key）；
- 没有进行中对局时 Copilot 仍可用（文档 / 书库 / 入口导航）。

工具协议（提供商无关，纯 chat.completions 即可驱动）：
- 模型需要执行操作时只输出一行 JSON：{"tool":"名称","args":{...}}；
- 服务端执行后以「【工具结果】名称：JSON」回填对话，可继续调用或作答；
- 输出自然语言即视为最终回答，循环结束（最多 MAX_STEPS 步）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from core import engine
from core import fate_engine as fe
from core.services import book_library_service

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_manual_path() -> Path:
    """定位用户手册：冻结构建落在 _internal/docs，源码运行在仓库 docs。

    PyInstaller onedir 下 ``__file__`` 位于 sys._MEIPASS（即 _internal）内，
    spec 已把 docs/USER_MANUAL.md 打到 _internal/docs；再兜底可执行文件
    同级目录，覆盖手动放置手册的场景。
    """
    candidates = [PROJECT_ROOT / "docs" / "USER_MANUAL.md"]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.insert(0, exe_dir / "_internal" / "docs" / "USER_MANUAL.md")
        candidates.append(exe_dir / "docs" / "USER_MANUAL.md")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


MANUAL_PATH = _resolve_manual_path()

MAX_STEPS = 10
_MAX_DOC_SECTIONS = 6
_DOC_EXCERPT_CHARS = 600


class CopilotClientError(Exception):
    """请求侧错误：端点映射为 HTTP 400。"""


class CopilotUpstreamError(Exception):
    """模型调用侧错误：端点映射为 HTTP 502。"""


# ---------------------------------------------------------------- 入口目录

ENTRIES: list[dict[str, str]] = [
    {"id": "workbench", "label": "工作台", "area": "导航",
     "desc": "回到叙事舞台：查看剧情、选项与人物状态。"},
    {"id": "library", "label": "我的书库", "area": "导航",
     "desc": "管理已上传原著，进入阅读或准备。"},
    {"id": "dossier", "label": "人物档案", "area": "导航",
     "desc": "查看全书蒸馏出的角色卡与角色库。"},
    {"id": "upload", "label": "上传原著", "area": "书库",
     "desc": "上传 TXT 原著，服务端自动切章。"},
    {"id": "prepare", "label": "全书准备", "area": "书库",
     "desc": "对一本原著发起窗口/全书蒸馏任务并查看进度。"},
    {"id": "reader", "label": "原著阅读器", "area": "阅读",
     "desc": "阅读原著正文，支持搜索、书签与章节跳转。"},
    {"id": "reader_chat", "label": "角色访谈", "area": "阅读",
     "desc": "在阅读器中与原著角色对话（知识边界到当前章节）。"},
    {"id": "config", "label": "开局配置", "area": "对局",
     "desc": "选择原著、位置、人物与剧情丰度，校验后开局。"},
    {"id": "model_settings", "label": "AI 配置", "area": "对局",
     "desc": "配置提供商、API Key、模型与思考模式。"},
    {"id": "save_manage", "label": "存档与读档", "area": "对局",
     "desc": "保存进度点或从任意存档恢复。"},
    {"id": "quests", "label": "任务面板", "area": "对局",
     "desc": "查看、接受或婉拒当前任务 offer。"},
    {"id": "autoplay", "label": "托管选项", "area": "对局",
     "desc": "让主角性格子智能体为本回合自动选择。"},
    {"id": "export", "label": "导出小说", "area": "对局",
     "desc": "把已提交回合整理为连贯小说并导出。"},
    {"id": "copilot_docs", "label": "帮助文档", "area": "帮助",
     "desc": "浏览用户手册章节。"},
]


# ---------------------------------------------------------------- 文档检索

def _manual_sections() -> list[dict[str, str]]:
    """把用户手册按 ##/### 标题切成节；文件缺失时返回空表。"""
    try:
        text = MANUAL_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    sections: list[dict[str, str]] = []
    title, buf = "", []
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            if title:
                sections.append({"title": title, "body": "\n".join(buf).strip()})
            title, buf = line.strip("# ").strip(), []
        elif title:
            buf.append(line)
    if title:
        sections.append({"title": title, "body": "\n".join(buf).strip()})
    return sections


def search_docs(query: str, limit: int = _MAX_DOC_SECTIONS) -> list[dict[str, str]]:
    """关键词检索用户手册：标题命中权重高于正文，返回节选。"""
    sections = _manual_sections()
    terms = [t for t in re.split(r"\s+", (query or "").strip()) if t]
    if not terms:
        return [{"title": s["title"], "excerpt": s["body"][:_DOC_EXCERPT_CHARS]}
                for s in sections[:limit]]
    scored: list[tuple[int, dict[str, str]]] = []
    for section in sections:
        score = 0
        for term in terms:
            score += section["title"].count(term) * 5 + section["body"].count(term)
        if score > 0:
            excerpt = section["body"][:_DOC_EXCERPT_CHARS]
            scored.append((score, {"title": section["title"], "excerpt": excerpt}))
    scored.sort(key=lambda pair: -pair[0])
    return [item for _, item in scored[:limit]]


def doc_catalog() -> list[str]:
    return [s["title"] for s in _manual_sections()]


# ---------------------------------------------------------------- 状态快照

def state_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    """Copilot 视角的对局快照：公开状态字段，绝不含凭据与系统提示。"""
    if not isinstance(state, dict) or not state.get("system"):
        return {"in_game": False}
    params = state.get("start_params") if isinstance(state.get("start_params"), dict) else {}
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), dict) else {}
    quest = state.get("quest") if isinstance(state.get("quest"), dict) else {}
    options = state.get("options") if isinstance(state.get("options"), list) else []
    roster = state.get("active_members") if isinstance(state.get("active_members"), list) else []
    work = state.get("work") or params.get("work")
    return {
        "in_game": True,
        "mode": state.get("mode"),
        "work": work,
        "role": params.get("role"),
        "round": state.get("round"),
        "chapter": f"{state.get('current_chapter')}/{state.get('total_chapters')}",
        "difficulty": params.get("difficulty"),
        "golden_finger": ((memory.get("abilities") or {}).get("golden_finger") or {}).get("name"),
        "goals": (memory.get("goals") or {}).get("current"),
        "quest_status": quest.get("status"),
        "options_count": len(options),
        "active_members": [str(m.get("name") if isinstance(m, dict) else m) for m in roster][:8],
        "game_ready": bool(state.get("game_ready")),
    }


# ---------------------------------------------------------------- 工具注册表

TOOLS: dict[str, dict[str, Any]] = {}


def _tool(name: str, desc: str, args_hint: str = "{}") -> Callable:
    def register(run: Callable[[dict[str, Any], dict[str, Any]], tuple[bool, Any]]):
        TOOLS[name] = {"name": name, "desc": desc, "args_hint": args_hint, "run": run}
        return run
    return register


def _run_action(ctx: dict[str, Any], name: str, args: dict[str, Any]) -> tuple[bool, Any]:
    """注入型操作工具：端点层提供闭包（持有会话锁与凭据），服务层只做转发。"""
    actions = ctx.get("actions") if isinstance(ctx.get("actions"), dict) else {}
    fn = actions.get(name)
    if not callable(fn):
        return False, f"工具 {name} 当前不可用（需要正在进行的对局或前端上下文）"
    try:
        return True, fn(**(args if isinstance(args, dict) else {}))
    except TypeError as exc:
        return False, f"参数不匹配：{exc}"
    except Exception as exc:  # noqa: BLE001 操作失败必须可见，不吞异常
        return False, f"{type(exc).__name__}: {exc}"


@_tool("get_game_overview", "当前对局状态总览（模式/回合/章节/任务/金手指/在场人物）")
def _t_overview(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    return True, state_snapshot(ctx.get("state"))


@_tool("list_saves", "列出全部存档点（编号、时间、进度描述）")
def _t_saves(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    saves = engine.persistence.list_saves(root=ctx.get("writable_root") or fe.WRITABLE_DIR)
    return True, {"count": len(saves), "saves": saves[:12]}


@_tool("list_books", "列出已上传并切章的原著")
def _t_books(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    root = Path(ctx.get("writable_root") or fe.WRITABLE_DIR) / "books"
    books = []
    if root.is_dir():
        for book_dir in root.iterdir():
            if not book_dir.is_dir() or not (book_dir / "chapter_index.json").is_file():
                continue
            data = {}
            try:
                raw = json.loads((book_dir / "chapter_index.json").read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except (OSError, ValueError):
                continue
            rows = [r for r in (data.get("chapters") or []) if isinstance(r, dict)]
            books.append({
                "book_id": book_dir.name,
                "chapters": len(rows) or data.get("total") or "?",
            })
    books.sort(key=lambda b: str(b["book_id"]))
    return True, {"count": len(books), "books": books[:12]}


@_tool("list_playable_library", "列出已玩作品库（可复用开局的作品）")
def _t_playable(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    root = Path(ctx.get("writable_root") or fe.WRITABLE_DIR) / "books"
    try:
        items = book_library_service.list_playable_books(root)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, {"count": len(items), "books": items[:12]}


@_tool("search_docs", "检索用户手册（开局/书库/准备/阅读器/导出等说明）",
       '{"query": "关键词"}')
def _t_docs(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    query = str((args or {}).get("query") or "")
    return True, {"query": query, "results": search_docs(query)}


@_tool("list_entries", "列出全部功能入口目录（id/名称/说明）")
def _t_entries(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    return True, {"entries": ENTRIES}


@_tool("open_entry", "向用户呈现可点击的功能入口（UI 导航由用户点击完成）",
       '{"entry_id": "library"}')
def _t_open(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    entry_id = str((args or {}).get("entry_id") or "")
    entry = next((e for e in ENTRIES if e["id"] == entry_id), None)
    if entry is None:
        return False, f"未知入口 {entry_id}；可用：{'、'.join(e['id'] for e in ENTRIES)}"
    return True, {"entry": entry, "ui_hint": "已在回复中放置入口按钮"}


@_tool("save_game", "保存当前对局进度为存档点", '{"save_id": "可选自定义编号"}')
def _t_save(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    return _run_action(ctx, "save_game", args)


@_tool("load_save", "读档：用存档点覆盖当前会话进度（不可撤销，先向用户确认）",
       '{"save_id": "存档编号"}')
def _t_load(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    return _run_action(ctx, "load_save", args)


@_tool("create_preparation_job", "为一本原著发起准备（蒸馏）任务",
       '{"book_id": "书目录名", "mode": "window|fullbook"}')
def _t_prep(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    return _run_action(ctx, "create_preparation_job", args)


@_tool("autoplay_choice", "让主角性格子智能体为本回合自动选择一个选项")
def _t_autoplay(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    return _run_action(ctx, "autoplay_choice", args)


@_tool("quest_accept", "接受当前任务 offer")
def _t_qa(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    return _run_action(ctx, "quest_accept", args)


@_tool("quest_decline", "婉拒当前任务 offer")
def _t_qd(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    return _run_action(ctx, "quest_decline", args)


@_tool("export_novel", "把已提交回合整理导出为小说", '{"style": "可选风格"}')
def _t_export(ctx: dict[str, Any], args: dict[str, Any]) -> tuple[bool, Any]:
    return _run_action(ctx, "export_novel", args)


# ---------------------------------------------------------------- 对话协议

def parse_tool_call(text: str) -> dict[str, Any] | None:
    """从模型输出解析工具调用；非 JSON 或缺 tool 字段视为普通回答。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"```\s*$", "", s).strip()
    if not s.startswith("{") or not s.endswith("}"):
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
        return obj
    return None


def _tool_lines() -> str:
    return "\n".join(f"- {t['name']}：{t['desc']} 参数：{t['args_hint']}"
                     for t in TOOLS.values())


def build_system_prompt(snapshot: dict[str, Any]) -> str:
    return (
        "你是《书中织梦》的 Copilot 助手（标记 COPILOT_SYSTEM_V1）：帮助用户一站式了解"
        "游戏状态、查阅文档并完成操作。\n"
        "规则：只用简体中文；依据快照、工具结果与用户手册回答，不编造；"
        "破坏性操作（读档覆盖进度）先向用户确认再调用工具。\n"
        "# 当前快照\n" + json.dumps(snapshot, ensure_ascii=False, default=str)
        + "\n# 可用工具\n" + _tool_lines()
        + "\n# 协议\n"
        "1. 需要执行操作时，只输出一行 JSON：{\"tool\":\"名称\",\"args\":{...}}，不要附加其他文字；\n"
        "2. 工具结果会以【工具结果】回传，你可继续调用工具或基于结果作答；\n"
        "3. 直接回答时输出自然语言（非 JSON）；回答里可以建议用户打开某入口"
        "（调用 open_entry 会在界面上放置可点击按钮）。"
    )


def _scrub(value: Any, api_key: str) -> Any:
    """递归脱敏：任何字符串里的 API Key 替换为 ***。"""
    if isinstance(value, str):
        return value.replace(api_key, "***") if api_key and api_key in value else value
    if isinstance(value, list):
        return [_scrub(item, api_key) for item in value]
    if isinstance(value, dict):
        return {str(k): _scrub(v, api_key) for k, v in value.items()}
    return value


def _call_model(client: Any, model: str, messages: list[dict[str, str]],
                provider: str, system_prompt: str, question: str) -> str:
    """单次模型调用：anthropic 走原生网关，其余走 chat.completions。

    900 tokens 会把稍长的回答拦腰截断；Copilot 回答不上限敏感，
    4096 与引擎技能调用的输出预算一致。
    """
    kwargs = dict(model=model, messages=messages, temperature=0.3, max_tokens=4096)
    with engine.parallel.slot(engine.parallel.PRIORITY_TURN):
        if provider == "anthropic":
            from core.services.native_gateway import native_complete
            response = native_complete(client, provider, model, question,
                                       system=system_prompt, max_tokens=4096,
                                       extra={"temperature": 0.3})
            return response.text.strip()
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception:  # noqa: BLE001 兼容不接受温度/max_tokens 的兼容服务
            kwargs.pop("temperature", None)
            kwargs.pop("max_tokens", None)
            response = client.chat.completions.create(**kwargs)
        if getattr(response, "choices", None):
            return str(response.choices[0].message.content or "").strip()
        return ""


def handle_chat(history: list[dict[str, Any]], ctx: dict[str, Any],
                provider: str, base_url: str | None, api_key: str,
                model: str, busy_note: str = "") -> dict[str, Any]:
    """Copilot 对话主流程：系统提示 + 历史消息 + 工具循环，返回可 JSON 化响应。

    ``ctx["state"]`` 可为 None（无对局）；``ctx["actions"]`` 由端点层注入
    持锁闭包。同步执行；单次对话最多 MAX_STEPS 个工具步，步数用尽时追加
    一次禁用工具的收尾合成调用，保证用户拿到完整回答而不是截断提示。
    ``busy_note`` 非空表示会话锁被占用、操作工具不可用的降级模式，仅追加
    到系统提示，不改变工具白名单本身。
    """
    snapshot = state_snapshot(ctx.get("state"))
    system_prompt = build_system_prompt(snapshot)
    if busy_note:
        system_prompt = system_prompt + "\n\n" + busy_note
    messages = [{"role": "system", "content": system_prompt}]
    for item in history:
        if isinstance(item, dict) and item.get("role") in ("user", "assistant"):
            content = str(item.get("content") or "")[:4000]
            if content.strip():
                messages.append({"role": str(item["role"]), "content": content})
    question = next((str(m["content"]) for m in reversed(messages)
                     if m["role"] == "user"), "")
    if not question:
        raise CopilotClientError("消息列表为空或缺少用户消息")
    try:
        client = fe.make_client(api_key, provider, base_url)
    except Exception as exc:  # noqa: BLE001
        raise CopilotClientError(f"模型客户端初始化失败：{exc}") from exc

    actions: list[dict[str, Any]] = []
    answer = ""
    for _ in range(MAX_STEPS):
        try:
            text = _call_model(client, model, messages, provider, system_prompt, question)
        except Exception as exc:  # noqa: BLE001
            raise CopilotUpstreamError(f"Copilot 模型调用失败：{exc}") from exc
        if not text:
            raise CopilotUpstreamError("Copilot 模型返回为空，请重试")
        call = parse_tool_call(text)
        if call is None:
            answer = text
            break
        name = str(call.get("tool") or "")
        tool = TOOLS.get(name)
        if tool is None:
            result = (False, f"未知工具 {name}；可用：{'、'.join(TOOLS)}")
        else:
            result = tool["run"](ctx, call.get("args") or {})
        ok, payload = result
        actions.append({"tool": name, "args": _scrub(call.get("args") or {}, api_key),
                        "ok": bool(ok), "result": _scrub(payload, api_key)})
        messages.append({"role": "assistant", "content": text})
        messages.append({
            "role": "user",
            "content": "【工具结果】" + name + "："
                       + json.dumps(payload, ensure_ascii=False, default=str)[:3000]
                       + ("\n可继续调用工具，或直接给出最终回答。" if ok else "\n该工具失败，请向用户说明或换一种方式。"),
        })
    else:
        # 步数用尽不代表回答完成：追加一次“禁用工具”的收尾合成调用，
        # 让模型把已执行的工具结果整理成完整回答（替换旧版罐头结束语）。
        messages.append({
            "role": "user",
            "content": "【系统】已达到单次对话工具步数上限，请立即基于以上工具结果"
                       "给出完整的最终回答；不要再输出工具调用。",
        })
        try:
            text = _call_model(client, model, messages, provider, system_prompt, question)
        except Exception as exc:  # noqa: BLE001
            raise CopilotUpstreamError(f"Copilot 模型调用失败：{exc}") from exc
        answer = text if (text and parse_tool_call(text) is None) else (
            "已执行 " + str(len(actions)) + " 步操作；如需继续请再发一条消息。")

    answer = _scrub(answer, api_key)
    if system_prompt and system_prompt in answer:
        answer = answer.replace(system_prompt, "").strip()
    return {"answer": answer, "actions": actions, "snapshot": snapshot}
