# -*- coding: utf-8 -*-
"""内部子调用统一通道（蒸馏/自检/托管/任务判定）。

从 legacy Gradio 版 ``app._distill_model`` 提炼为引擎层公共函数，
供新版 FastAPI（api_server）与老版界面共用，避免新服务反向依赖旧 UI。
"""
from __future__ import annotations

from typing import Any


#: 内部子调用的单请求读超时（秒）。客户端级默认 300s 对**持锁**子调用过长：
#: 托管选线、任务/碎锚结算、宿敌回合、压缩、自检都在持会话锁状态下发生，
#: 慢响应会让其他端点一直 409；且降级阶梯会把等待放大数倍。
#: 调用方显式传 timeout 的（quest_offer 120s、break_anchor_offer 30s）不受影响。
DEFAULT_SUBCALL_TIMEOUT = 120.0

#: 后台线程子调用超时（秒）：锚点蒸馏等**不持会话锁**的通道用。主流式生成
#: 与后台蒸馏共用同一 Key 时上游会排队，120s 会把排队中的合法请求掐死
#: （实测：agent 模式长回合期间蒸馏连续超时→重试封顶→停滞）；300s 与
#: openai 客户端级读超时一致，即恢复本通道 2026-08-30 之前的行为。
BACKGROUND_SUBCALL_TIMEOUT = 300.0


def distill_model(client, model: str, prompt: str,
                  extra_kwargs: dict | None = None,
                  provider: str = "deepseek", timeout: float | None = None) -> str:
    """兼容性阶梯调用：完整参数 → 剥思考参数 → 剥采样参数 → 流式累积。

    别家服务的 OpenAI 兼容层参数支持差异很大，按四级降级重试直到某级成功；
    「成功」必须是拿到非空正文——响应成功但 content 为空串（典型：思考型模型
    在 max_tokens 预算内思考链耗尽 tokens，正文一个字没写）视为本级失败，
    继续降级到下一级；级别 2 的裸参数用提供商默认输出预算，天然不受影响。
    全部失败才向上抛错（调用方自行降级处理）。

    每级请求都带 ``DEFAULT_SUBCALL_TIMEOUT`` 读超时（调用方已指定则沿用其值）。
    """
    # 延迟导入：fate_engine 属于老版接入层，仅在源码运行时位于项目根或 legacy/ 下。
    from core import fate_engine as fe

    thinking: dict[str, Any] = dict(extra_kwargs or fe.thinking_kwargs(provider))
    if provider == "zhipu" and isinstance(thinking.get("thinking"), dict):
        # 蒸馏/托管/自检等内部子调用压低思考链：glm-5.3 系永远思考、
        # 不支持 disabled（会报 1210），用 low 控制思考预算，避免思考链
        # 吃光输出预算导致正文为空。
        thinking["thinking"] = {"type": "enabled"}
    # 输出预算：九字段锚点 JSON 等结构化输出 2000 tokens 偏紧（思考型模型
    # 还会占用思考链），放宽到 4000；空正文仍会降级到无预算的裸参数级。
    # 超时优先级：显式形参 > extra_kwargs 携带 > 持锁默认 120s。
    if timeout is None:
        timeout = thinking.get("timeout", DEFAULT_SUBCALL_TIMEOUT)
    thinking.pop("timeout", None)
    full = dict(model=model, messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=4000)
    # 级别 1：剥思考参数；级别 2：连 temperature/max_tokens 也剥掉。
    last_error: Exception = ValueError("distill_model 所有级别均未返回有效正文")
    for kwargs in (
        {**full, **thinking},
        full,
        {"model": model, "messages": [{"role": "user", "content": prompt}]},
    ):
        try:
            response = client.chat.completions.create(timeout=timeout, **kwargs)
            content = response.choices[0].message.content if getattr(response, "choices", None) else ""
            if str(content or "").strip():
                return str(content)
            # 空正文≠成功：思考链可能吃光了 max_tokens 预算，降级重试。
            last_error = ValueError(
                "模型响应成功但正文为空（可能被思考链耗尽输出预算）")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            is_param_reject = (isinstance(exc, TypeError)
                               or fe._is_unsupported_parameter_error(exc))
            if not is_param_reject:
                raise
            last_error = TypeError(str(exc))  # 归一为参数类错误，供流式回退判定
    # 级别 3：非流式彻底不可用时改走流式累积（流式是兼容面最广的协议）。
    # stream_reply 的 yield 是累积缓冲，最后一个 yield 即全文。
    try:
        acc = ""
        # 流式级同样带读超时：extra_kwargs 会被 stream_reply 展开进 create 调用。
        stream_kwargs = dict(extra_kwargs or fe.thinking_kwargs(provider))
        stream_kwargs.setdefault("timeout", timeout)
        for acc in fe.stream_reply(client, model,
                                   "", [{"role": "user", "content": prompt}],
                                   extra_kwargs=stream_kwargs, provider=provider):
            pass
        if str(acc or "").strip():
            return str(acc)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
    raise last_error
