# -*- coding: utf-8 -*-
"""角色闲聊服务（阶段 5F）。

允许玩家与当前活跃角色闲聊，生成符合角色 voice 和当下剧情的对话，
但**绝不推进剧情、不改变状态**——状态隔离硬保证。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Sequence

from core.engine.distill import distill_model

Model = Callable[[str], Any]

#: 单条回复字数窗口
CHAT_MIN_LENGTH = 50
CHAT_MAX_LENGTH = 150

#: 轻量质量门规则关键词
FORBIDDEN_ACTIONS = (
    "给你", "送你", "赠你", "这是给你的",
    "告诉你一个秘密", "其实真相是", "幕后",
    "离开了", "走了", "离去", "告别",
    "新的", "突然出现", "忽然发生",
)


class ChatClientError(Exception):
    """请求侧错误（无活跃角色/角色不存在）：端点映射 HTTP 400。"""


class ChatUpstreamError(Exception):
    """模型侧错误（生成失败）：端点映射 HTTP 502。"""


def get_roster(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """获取当前活跃角色列表（供前端下拉框）。

    返回 [{"name": str, "voice": str, "desire": str}, ...]
    ``active_members`` 兼容两种形态：含 ``name`` 键的对象序列、或名字字符串
    序列（字符串形态从 companions/heroines 池补齐 voice 等字段）。
    """
    active_members = state.get("active_members") if isinstance(state, Mapping) else None
    if not isinstance(active_members, list):
        active_members = []

    # 伴随池提供 voice/desire/fear 兜底（同名优先取池内卡片）
    pool: dict[str, dict[str, Any]] = {}
    for key in ("companions", "heroines"):
        for card in state.get(key) or ():
            if isinstance(card, Mapping):
                name = str(card.get("name") or "").strip()
                if name:
                    pool.setdefault(name, {
                        "voice": str(card.get("voice") or card.get("persona") or ""),
                        "desire": str(card.get("desire") or ""),
                        "fear": str(card.get("fear") or ""),
                    })

    # active_members 在回合推进时才写入：开局阶段（或角色尚未入戏时）
    # 退回 companions + heroines 池，保证下拉框开局即可见。
    if not active_members:
        active_members = list(pool.keys())

    roster: list[dict[str, Any]] = []
    seen: set[str] = set()
    for member in active_members:
        if isinstance(member, Mapping):
            name = str(member.get("name") or "").strip()
            card = {
                "voice": str(member.get("voice") or ""),
                "desire": str(member.get("desire") or ""),
                "fear": str(member.get("fear") or ""),
            }
        else:
            name = str(member or "").strip()
            card = pool.get(name, {"voice": "", "desire": "", "fear": ""})
        if not name or name in seen:
            continue
        seen.add(name)
        fallback = pool.get(name, {})
        roster.append({
            "name": name,
            "voice": card["voice"] or fallback.get("voice", ""),
            "desire": card["desire"] or fallback.get("desire", ""),
            "fear": card["fear"] or fallback.get("fear", ""),
        })

    return roster


def build_chat_prompt(character: Mapping[str, Any], 
                      player_input: str,
                      state: Mapping[str, Any],
                      scene_excerpt: str = "",
                      recent_summary: str = "") -> str:
    """构造闲聊生成提示词。
    
    上下文：角色卡 + 当前场景 + 近期摘要 + 主角状态卡硬事实
    铁律：符合 voice、符合剧情、不推进剧情、对话为主 50-150 字
    """
    from core.engine import protagonist_state
    
    name = str(character.get("name") or "未知角色")
    voice = str(character.get("voice") or "普通语气")
    desire = str(character.get("desire") or "")
    fear = str(character.get("fear") or "")
    
    # 获取主角状态硬事实
    state_facts = protagonist_state.hard_facts_text(state, limit=600)
    
    # 从 state_memory 读取与该角色的关系
    relationships = ""
    state_memory = state.get("state_memory") if isinstance(state, Mapping) else None
    if isinstance(state_memory, Mapping):
        rel_data = state_memory.get("relationships") or {}
        if isinstance(rel_data, Mapping):
            chars = rel_data.get("characters") or ""
            if name in str(chars):
                relationships = f"与主角关系：{chars}"
    
    prompt = f"""【角色闲聊生成规则】
你是《书中行》的角色对话生成器。玩家正在与角色「{name}」闲聊。

# 角色设定
- 名字：{name}
- 语气特征：{voice}
- 当前欲望：{desire or "无特别欲望"}
- 恐惧/顾虑：{fear or "无"}
{relationships}

# 当前场景
{scene_excerpt[:500] if scene_excerpt else "（场景信息缺失）"}

# 近期剧情摘要
{recent_summary[:600] if recent_summary else "（剧情刚开始）"}

# 主角当前状态（角色已知信息）
{state_facts}

# 玩家对你说
{player_input}

# 生成规则（代码级铁律）
1. **符合角色 voice**：语气、用词、态度必须符合角色设定
2. **符合当下剧情**：对话内容必须基于当前场景和关系现状
3. **绝不推进剧情**：
   - 不能产生新事件（"突然"、"忽然"、"这时"）
   - 不能给予物品、能力、情报、线索
   - 不能剧透锚点或未来剧情
   - 不能离场、死亡、改变状态
4. **对话为主**：50-150 字，以对话为主体，动作描写最小化
5. **不要元叙述**：不要"作为 AI"、"我无法"之类的说明

# 输出要求
直接输出角色的回复（对话+必要的神态动作），不要任何前缀、标签或解释。
"""
    
    return prompt


def generate_reply(character_name: str, player_input: str, state: Mapping[str, Any], *,
                   client=None, model: str = "", request_kwargs: dict | None = None,
                   provider: str = "deepseek", model_fn: Optional[Model] = None,
                   attempts: int = 2) -> dict[str, Any]:
    """生成角色回复（agent_refill 模式：批改-重填循环）。
    
    返回 {"reply": str, "character": str, "meta": dict}
    
    采用 agent_refill 模式：
    1. 初稿生成
    2. 结构化批改（chat_grader.grade_chat_reply）
    3. 若有错误，重填（≤attempts 次）
    4. Keep-best：选择错误最少的版本
    
    v2.0.4: 初始化独立 Token 累加器（回合外调用，阶段 E）
    """
    from core.services import chat_grader
    from core.engine import token_accounting
    
    # v2.0.4: 独立 usage 累加器（回合外端点）
    token_accounting.init_turn_usage()
    
    roster = get_roster(state)
    character = next((c for c in roster if c["name"] == character_name), None)
    if not character:
        raise ChatClientError(f"角色「{character_name}」不在当前活跃名册中")
    
    # 获取场景节选和近期摘要
    scene_excerpt = ""
    recent_summary = ""
    history = state.get("history") if isinstance(state, Mapping) else None
    if isinstance(history, list) and len(history) > 0:
        last_turn = history[-1]
        if isinstance(last_turn, Mapping):
            scene_excerpt = str(last_turn.get("narrative") or "")[:500]
    
    # 构造提示词
    prompt = build_chat_prompt(character, player_input, state, scene_excerpt, recent_summary)
    
    # 生成初稿（走 distill_model choke point，自动记录 usage）
    model_call = model_fn or (lambda p: distill_model(client, model, p, request_kwargs, provider, usage_category="chat"))
    # v2.0.5 Q4：双卷并发 best-of-2——两份候选并行生成、批改择优，
    # voice 命中率取上界；耗时 ≈ 单卷（零重填场景）。
    try:
        from core.engine import parallel as _parallel
        import contextvars as _cv
        _chat_ctx = _cv.copy_context()
        _chat_jobs = [
            (lambda: _chat_ctx.run(lambda: str(model_call(prompt) or "").strip()))
            for _ in range(2)
        ]
        _chat_results = _parallel.run_parallel(_chat_jobs, _parallel.PRIORITY_TURN)
        _candidates = [item.value for item in _chat_results
                       if getattr(item, "ok", False) and str(item.value or "").strip()]
        if not _candidates:
            raise ChatUpstreamError("生成失败: 双卷均未返回正文")
    except ChatUpstreamError:
        raise
    except Exception as exc:
        raise ChatUpstreamError(f"生成失败: {exc}") from exc
    # 择优：批改错误少的候选作为当前稿。
    current_reply = min(
        _candidates, key=lambda text: len(chat_grader.grade_chat_reply(character, text)))
    best_errors = chat_grader.grade_chat_reply(character, current_reply)
    best_reply = current_reply
    refills = 0
    
    # 批改-重填循环（agent_refill 模式）
    current_errors = best_errors
    for _ in range(max(0, int(attempts))):
        if not current_errors:
            break  # 通过批改，提前结束
        
        try:
            refill_prompt = chat_grader.build_refill_prompt(
                character, player_input, state, scene_excerpt, recent_summary, current_errors
            )
            candidate = str(model_call(refill_prompt) or "").strip()
        except Exception:  # noqa: BLE001
            break  # 重填失败，使用当前最佳
        
        if not candidate:
            break
        
        refills += 1
        candidate_errors = chat_grader.grade_chat_reply(character, candidate)
        
        # Keep-best: 选择错误更少的版本
        if len(candidate_errors) < len(best_errors):
            best_reply = candidate
            best_errors = candidate_errors
        
        current_reply = candidate
        current_errors = candidate_errors
    
    # v2.0.4: 收集本次调用的 usage
    usage_data = token_accounting.get_turn_usage() or {}
    usage_response = {
        "total": usage_data.get("total_tokens", 0),
        "prompt": usage_data.get("prompt_tokens", 0),
        "completion": usage_data.get("completion_tokens", 0),
    }
    
    return {
        "reply": best_reply,
        "character": character_name,
        "meta": {
            "input_length": len(player_input),
            "reply_length": len(best_reply),
            "quality_issues": best_errors,
            "refills": refills,
            "mode": "agent_refill",
            "usage": usage_response,  # v2.0.4 Token 计量
        }
    }


# LEGACY: 保留用于向后兼容，新代码使用 chat_grader.grade_chat_reply
def _check_chat_quality(text: str) -> list[str]:
    """LEGACY 轻量质量门（向后兼容）。
    
    新代码应使用 chat_grader.grade_chat_reply 获得更全面的批改。
    """
    from core.services import chat_grader
    # 简化版：只返回基本错误，不检查 voice 一致性
    issues: list[str] = []
    
    length = len(text)
    if length < CHAT_MIN_LENGTH:
        issues.append(f"回复过短（{length} 字，最少 {CHAT_MIN_LENGTH} 字）")
    elif length > CHAT_MAX_LENGTH:
        issues.append(f"回复过长（{length} 字，最多 {CHAT_MAX_LENGTH} 字）")
    
    if any(marker in text for marker in ("```", "【", "】", "<<<", ">>>")):
        issues.append("残留脚手架或标记")
    
    if text.strip().startswith(("{", "[")):
        issues.append("残留 JSON 格式")
    
    if any(phrase in text for phrase in ("作为AI", "作为人工智能", "我无法", "我不能")):
        issues.append("包含元叙述")
    
    for action in FORBIDDEN_ACTIONS:
        if action in text:
            issues.append(f"包含禁止行为：{action}")
            break
    
    return issues


def save_chat(state: dict, character_name: str, player_input: str, reply: str) -> None:
    """保存聊天记录到 state["side_chats"]（不影响剧情）。"""
    side_chats = state.get("side_chats")
    if not isinstance(side_chats, dict):
        side_chats = {}
        state["side_chats"] = side_chats
    
    char_history = side_chats.get(character_name)
    if not isinstance(char_history, list):
        char_history = []
        side_chats[character_name] = char_history
    
    char_history.append({
        "player": player_input,
        "reply": reply,
        "round": int(state.get("round") or 0),
    })
    
    # 只保留最近 10 条
    if len(char_history) > 10:
        side_chats[character_name] = char_history[-10:]


__all__ = [
    "ChatClientError",
    "ChatUpstreamError",
    "get_roster",
    "generate_reply",
    "save_chat",
]
