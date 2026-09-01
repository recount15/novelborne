"""类 Agent 故事生成模式：draft → 自检 → 定向修订。

本模块只做确定性装配与解析，不调用模型：
- ``build_self_check_prompt``：把运行时约束与草稿装进自检提示词；
- ``parse_issues``：从自检输出中提取严格 JSON 问题清单（容忍围栏/前后杂文本）；
- ``build_revise_prompt``：把问题清单组装成修订指令；
- ``machine_findings``：把机械三校验结果翻译成同一套问题结构，
  让语义自检与机械门禁共用一条修订通路。

自检是增值环节：解析失败一律视为无问题，绝不阻断正常生成。
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

# 单回合最多修订次数。两轮定向修订：第一轮消化主要硬伤，第二轮兜住
# 「修订引入的新问题」（实测单轮修订后仍有 ~10% 残留）。多轮循环在
# 弱模型上容易发散且延迟翻倍，故上限固定为 2。
MAX_REVISIONS = 2

_KINDS = ("体量", "角色", "锚点", "因果", "橡皮筋", "节奏", "人物一致性", "世界观")
_JSON_RE = re.compile(r"\{.*\}", re.S)


def build_self_check_prompt(draft: str, style: str, budget: Mapping[str, int],
                            active_names: Sequence[str], anchor_text: str,
                            convergence: str) -> str:
    """装配语义自检提示词；草稿超长截断以控制 token 预算。"""
    from core.prompts import render
    names = "、".join(active_names) if active_names else "（本回合无强制出场角色）"
    return render(
        "agent_self_check.md",
        style=style or "未判定",
        target=budget.get("target", 700),
        minimum=budget.get("minimum", 595),
        maximum=budget.get("maximum", 805),
        active_names=names,
        anchor=(anchor_text or "（缺失）")[:1200],
        convergence=convergence,
        draft=draft[:6000],
    )


def parse_issues(raw: str) -> list[dict[str, str]]:
    """从自检输出解析问题清单；任何异常都返回空列表（增值环节不阻断主线）。"""
    text = str(raw or "").strip()
    if not text:
        return []
    match = _JSON_RE.search(text)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    issues = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(issues, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in issues:
        if not isinstance(item, Mapping):
            continue
        detail = str(item.get("detail") or "").strip()
        if not detail:
            continue
        kind = str(item.get("kind") or "").strip()
        if kind not in _KINDS:
            kind = next((k for k in _KINDS if k in kind), kind or "节奏")
        cleaned.append({
            "kind": kind,
            "detail": detail[:200],
            "fix_hint": str(item.get("fix_hint") or "").strip()[:200],
        })
    return cleaned[:6]


def machine_findings(budget_check: Mapping[str, Any] | None,
                     interaction_check: Mapping[str, Any] | None,
                     anchor_check: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """把机械门禁失败项翻译成与语义自检同一结构的问题清单。"""
    issues: list[dict[str, str]] = []
    if budget_check and not budget_check.get("valid"):
        chars, low, high = (budget_check.get("chars", 0), budget_check.get("minimum"),
                            budget_check.get("maximum"))
        direction = "低于下限" if isinstance(chars, int) and isinstance(low, int) and chars < low else "超出上限"
        issues.append({"kind": "体量",
                       "detail": f"正文 {chars} 字{direction}（允许 {low}–{high} 字）",
                       "fix_hint": ("压缩重复描写与次要支线" if direction == "超出上限"
                                    else f"补充约 {(low - chars) if isinstance(chars, int) and isinstance(low, int) else 100} 字有效剧情")})
    if interaction_check and not interaction_check.get("valid"):
        names = "、".join(interaction_check.get("active_names") or [])
        issues.append({"kind": "角色",
                       "detail": f"活跃角色未形成可验证交互（需点名 {names} 中至少一人并给出回应或动作）",
                       "fix_hint": f"让 {'、'.join(interaction_check.get('active_names') or [])[:1] or '在场角色'} 对玩家行动做出明确回应"})
    if anchor_check and not anchor_check.get("valid"):
        status = str(anchor_check.get("status") or "")
        hint = {"pending": "正文中补入锚点事件并被玩家行动触发",
                "mentioned": "让锚点由玩家行动落成并给出可观察结果",
                "partial": "补全锚点的动作或结果一侧，加入因果连接词",
                "conflicted": "移除与锚点相抵触的表述，回到锚点要求的事件走向"}.get(status, "按锚点要求收束本回合")
        issues.append({"kind": "锚点",
                       "detail": f"尚未收束到当前锚点（状态 {status or '未知'}）",
                       "fix_hint": hint})
    return issues


def build_revise_prompt(issues: Sequence[Mapping[str, Any]], budget: Mapping[str, int]) -> str:
    """装配定向修订指令；问题清单渲染为编号列表。"""
    from core.prompts import render
    lines = []
    for index, item in enumerate(issues, start=1):
        line = f"{index}. [{item.get('kind', '?')}] {item.get('detail', '')}"
        hint = str(item.get("fix_hint") or "").strip()
        if hint:
            line += f" → {hint}"
        lines.append(line)
    return render(
        "agent_revise.md",
        issues="\n".join(lines),
        target=budget.get("target", 700),
        minimum=budget.get("minimum", 595),
        maximum=budget.get("maximum", 805),
    )


# ---------- 本地（零模型）软伤检查 ----------
# 语义自检的目标是「只有读懂剧情才能发现」的问题（因果/橡皮筋）。
# 凡能用确定性规则判定的，一律在这里本地完成，不依赖任何远端接口——
# 这也是接口兼容性降级的兜底：模型自检不可用时，质量循环仍有底线。

import re as _re

_SENT_SPLIT = _re.compile(r"[。！？!?\n]+")
_REPEAT_MIN_CHARS = 6        # 短于该长度的句子忽略（多为语气短句）
_REPEAT_THRESHOLD = 5        # 同一句子原样出现超过该次数即判凑字


def _worst_repetition(content: str) -> tuple[str, int]:
    """返回重复次数最多的完整句子及其次数。"""
    counts: dict[str, int] = {}
    for sent in _SENT_SPLIT.split(content):
        text = sent.strip()
        if len(text) >= _REPEAT_MIN_CHARS:
            counts[text] = counts.get(text, 0) + 1
    if not counts:
        return "", 0
    return max(counts.items(), key=lambda kv: kv[1])


def local_findings(draft: str, budget: Mapping[str, int] | None = None,
                   active_names: Sequence[str] | None = None) -> list[dict[str, str]]:
    """本地确定性软伤检查：重复凑字、体量粗检、活跃角色点名。

    与机械门禁的差异：机械门禁是提交前的硬标准（不过即回滚），
    这里是修订前的预警线——在模型自检之前先把最便宜的硬伤抓出来，
    让一次修订就能同时消化机械与语义两类问题。
    """
    content = str(draft or "")
    issues: list[dict[str, str]] = []
    # 1) 重复凑字：同一句子逐字复现多次，是弱模型最典型的退化形态。
    worst, count = _worst_repetition(content)
    if count > _REPEAT_THRESHOLD:
        excerpt = worst[:20] + ("…" if len(worst) > 20 else "")
        issues.append({
            "kind": "节奏",
            "detail": f"存在大段重复描写（「{excerpt}」复现 {count} 次），疑似凑字",
            "fix_hint": "用新事件、对话或环境变化替换重复段落",
        })
    # 2) 体量粗检：与机械门禁同一口径，提前预警以便合并进同一次修订。
    if budget and isinstance(budget.get("minimum"), int) and isinstance(budget.get("maximum"), int):
        chars = len(content)
        if chars < budget["minimum"]:
            issues.append({"kind": "体量",
                           "detail": f"正文约 {chars} 字，低于下限 {budget['minimum']} 字",
                           "fix_hint": f"补充约 {budget['minimum'] - chars} 字有效剧情"})
        elif chars > budget["maximum"]:
            issues.append({"kind": "体量",
                           "detail": f"正文约 {chars} 字，超出上限 {budget['maximum']} 字",
                           "fix_hint": "压缩重复描写与次要支线"})
    # 3) 活跃角色点名（机械校验同口径的提前预警）。
    names = [str(n or "").strip() for n in (active_names or ()) if isinstance(n, Mapping) or True]
    names = [n.strip() for n in names if n and n.strip()]
    if names and not any(n in content for n in names):
        issues.append({"kind": "角色",
                       "detail": f"活跃角色（{'、'.join(names)}）均未被点名",
                       "fix_hint": f"让 {'、'.join(names[:2])} 参与本回合行动并给出回应"})
    return issues


__all__ = ["MAX_REVISIONS", "build_self_check_prompt", "parse_issues",
           "machine_findings", "local_findings", "build_revise_prompt"]
