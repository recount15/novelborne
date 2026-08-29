# -*- coding: utf-8 -*-
"""书中行 · 命运引擎 — 模型接入与规则装配。

职责：
- 读取作品库（rules/work_library.md）与运行时规则（rules/runtime.md、强化时叠加 rules/enhanced.md）。
- 解析作品库（当前约 1099 个 W 编号条目）供界面选择。
- 扫描 personas/standard 与 personas/enhanced 作为魂穿性格模型。
- 按开局设定装配 system prompt，并通过 OpenAI 兼容协议调用模型（流式输出）。

提示词文案不内嵌在本文件，统一放在 prompts/ 下的小文件里，经 prompts.load/render 装配。
"""
from core import engine
import json
import os
import re
import sys

from core import prompts


def _resource_dir():
    """只读资源（规则、角色模型）所在目录。
    PyInstaller 打包后取捆绑目录（_MEIPASS 或 exe 同级），源码运行取本文件目录。
    本文件位于 core/，资源在项目根 assets/：向上回退一级查找。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(os.path.join(here, "assets", "rules")):
        return here
    parent = os.path.dirname(here)  # 项目根（core/ 的上一级）
    if os.path.isdir(os.path.join(parent, "assets", "rules")):
        return parent
    return here


def _writable_dir():
    """可写目录（config.json / .env / 全部运行数据统一归 var/）。

    优先级：FATE_VAR_DIR 环境变量（多实例并发时每实例独立数据目录，
    避免 SQLite / 存档 / 会话互踩）> 打包 exe 同级 var/ > 项目根 var/。
    """
    override = os.getenv("FATE_VAR_DIR", "").strip()
    if override:
        return os.path.abspath(override)
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "var")
    # 本文件位于 core/：项目根即 core 的上一级。
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var")


BASE_DIR = _resource_dir()
WRITABLE_DIR = _writable_dir()
WORK_LIBRARY_PATH = os.path.join(BASE_DIR, "assets", "rules", "work_library.md")
RUNTIME_RULES_PATH = os.path.join(BASE_DIR, "assets", "rules", "runtime.md")
ENHANCED_RULES_PATH = os.path.join(BASE_DIR, "assets", "rules", "enhanced.md")
RULES_PATH = WORK_LIBRARY_PATH

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# OpenAI 兼容服务预设；API key 只从环境变量、配置文件或 UI 输入读取。
PROVIDERS = {
    "deepseek": {"label": "DeepSeek", "base_url": DEEPSEEK_BASE_URL,
                 "models": ["deepseek-chat", "deepseek-reasoner"], "env_key": "DEEPSEEK_API_KEY"},
    "qwen": {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "models": ["qwen-plus", "qwen-max", "qwen-turbo"], "env_key": "DASHSCOPE_API_KEY"},
    "kimi": {"label": "Kimi", "base_url": "https://api.moonshot.cn/v1",
             "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"], "env_key": "MOONSHOT_API_KEY"},
    "zhipu": {"label": "智谱", "base_url": "https://open.bigmodel.cn/api/paas/v4",
              "models": ["glm-5.3-flash", "glm-5.3", "glm-5.2", "glm-5.1",
                         "glm-5-turbo", "glm-5", "glm-4.7", "glm-4.6",
                         "glm-4.5-air", "glm-4-plus"], "env_key": "ZHIPUAI_API_KEY"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
               "models": ["gpt-4o-mini", "gpt-4o"], "env_key": "OPENAI_API_KEY"},
    "custom": {"label": "自定义 OpenAI 兼容", "base_url": "", "models": [], "env_key": ""},
}
PROVIDER_CHOICES = [(item["label"], key) for key, item in PROVIDERS.items()]
MODELS = PROVIDERS["deepseek"]["models"]
DEFAULT_MODEL = MODELS[0]


def _parse_thinking_params(value):
    """解析 UI 中的 key=value[,key=value]，尽量保留数字和布尔值。"""
    pairs = {}
    for item in (value or "").split(","):
        if "=" not in item:
            continue
        key, raw = item.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if not key:
            continue
        try:
            pairs[key] = json.loads(raw)
        except (TypeError, ValueError):
            pairs[key] = raw
    return pairs


def thinking_kwargs(provider="deepseek", mode="auto", param=""):
    """把统一思考档位映射为各 OpenAI 兼容服务的请求参数。

    档位：auto / off / low / mid / high（on 视为 high）。
    显式参数优先；未知提供商/DeepSeek/Kimi 默认不发送额外字段。
    """
    explicit = _parse_thinking_params(param)
    if explicit:
        return explicit
    alias = {"on": "high", "off": "off", "low": "low", "mid": "mid", "medium": "mid", "high": "high", "auto": "auto"}
    mode = alias.get(str(mode or "auto").lower(), "auto")
    if mode == "auto":
        return {}
    effort = {"off": "low", "low": "low", "mid": "medium", "high": "high"}[mode]
    enabled = mode != "off"
    if provider == "openai":
        return {"reasoning_effort": effort}
    if provider == "deepseek":
        # DeepSeek V4 flash/pro 通过 reasoning_effort 开启思考链；off 时不发送。
        return {} if mode == "off" else {"reasoning_effort": effort}
    if provider == "qwen":
        return {"extra_body": {"enable_thinking": enabled}}
    if provider == "zhipu":
        # 智谱 OpenAI 兼容端点的思考开关必须经 extra_body 透传；
        # 顶层 thinking 是智谱自有 SDK 的参数，openai 客户端会直接 TypeError。
        return {"extra_body": {"thinking": {"type": "enabled" if enabled else "disabled"}}}
    return {}


def provider_config(provider="deepseek", base_url=None):
    """返回提供商配置副本，避免 UI 修改全局预设。"""
    key = provider if provider in PROVIDERS else "deepseek"
    cfg = dict(PROVIDERS[key])
    if base_url is not None:
        cfg["base_url"] = (base_url or "").strip()
    return cfg


def normalize_profile(profile=None):
    """将新 profile 或旧 config.json 的 deepseek_api_key 归一化为模型配置。"""
    profile = dict(profile or {})
    if "api_key" not in profile and profile.get("deepseek_api_key"):
        profile.update({"provider": "deepseek", "api_key": profile["deepseek_api_key"]})
    provider = profile.get("provider", "deepseek")
    cfg = provider_config(provider, profile.get("base_url"))
    profile["provider"] = provider if provider in PROVIDERS else "deepseek"
    profile["base_url"] = cfg["base_url"]
    profile["model"] = profile.get("model") or cfg["models"][0] if cfg["models"] else profile.get("model", "")
    profile.setdefault("thinking_mode", "auto")
    profile.setdefault("thinking_param", "")
    profile.setdefault("api_key", "")
    return profile


def load_profiles(path):
    """读取非敏感 profiles；兼容旧格式但丢弃所有长期凭据字段。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, TypeError):
        raw = {}

    def safe_profile(value):
        profile = normalize_profile(value)
        for key in ("api_key", "deepseek_api_key", "token", "secret"):
            profile.pop(key, None)
        return profile

    if isinstance(raw, dict) and isinstance(raw.get("profiles"), dict):
        profiles = {name: safe_profile(value) for name, value in raw["profiles"].items()}
        active = raw.get("active_profile") or (next(iter(profiles), "默认") if profiles else "默认")
        return profiles or {"默认": safe_profile(raw)}, active
    return {"默认": safe_profile(raw)}, "默认"


def save_profiles(path, profiles, active_profile="默认"):
    """保存非敏感模型设置；API Key 必须通过环境变量或本次界面输入提供。"""
    safe_profiles = {}
    for name, value in profiles.items():
        profile = normalize_profile(value)
        safe_profiles[name] = {key: item for key, item in profile.items()
                               if key not in {"api_key", "deepseek_api_key", "token", "secret"}}
    payload = {"active_profile": active_profile, "profiles": safe_profiles}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def make_client(api_key, provider="deepseek", base_url=None):
    """带读写超时与连接级重试的客户端（连接 15s / 读 300s / 写 60s）。

    读超时兜底「流式挂起不吐字」的服务端故障，否则回合会无限期卡死；
    max_retries=3 兜底网关偶发的连接重置（WinError 10054 等），openai
    SDK 只对连接建立与可安全重放的请求重试，流中途断开不重放。
    个别旧版 SDK 不认 timeout 元组时回退默认客户端。
    """
    from openai import OpenAI
    cfg = provider_config(provider, base_url)
    if not cfg["base_url"]:
        raise ValueError("请先填写 OpenAI 兼容服务的 Base URL")
    api_key = (api_key or "").strip()
    try:
        return OpenAI(api_key=api_key, base_url=cfg["base_url"],
                      timeout=300.0, connect=15.0, max_retries=3)
    except TypeError:
        return OpenAI(api_key=api_key, base_url=cfg["base_url"], max_retries=3)


def _model_sort_key(model_id: str):
    """实时模型列表排序：flash/turbo（便宜快速）优先，其余按名称倒序（新版本在前）。"""
    name = str(model_id)
    fast = 0 if ("flash" in name or "turbo" in name or "air" in name) else 1
    return (fast, name)


def fetch_models(api_key, provider="deepseek", base_url=None):
    """通过 OpenAI 兼容 /models 获取实时模型 ID 列表（拉取失败由调用方回退预设）。"""
    client = make_client(api_key, provider, base_url)
    result = client.models.list()
    ids = [item.id for item in getattr(result, "data", []) if getattr(item, "id", None)]
    return sorted(ids, key=_model_sort_key)


def test_connection(api_key, provider="deepseek", base_url=None, model=None):
    """测试 /models；若服务不支持则尝试一次最小 chat 请求。"""
    client = make_client(api_key, provider, base_url)
    try:
        models = client.models.list()
        ids = [item.id for item in getattr(models, "data", []) if getattr(item, "id", None)]
        return True, f"连接成功，可用模型 {len(ids)} 个"
    except Exception:
        if not model:
            raise
        client.chat.completions.create(model=model, messages=[{"role": "user", "content": "ping"}],
                                       max_tokens=1)
        return True, "连接成功"

DIFFICULTIES = [
    "D1 极易（开局无敌）", "D2 很易", "D3 较易", "D4 普通", "D5 较难",
    "D6 困难", "D7 很困难", "D8 极难", "D9 炼狱（一步失误即万劫不复）",
]

GOLDEN_FINGERS = [
    "无（凡人开局）", "系统流（面板/任务/抽奖）", "先知/记忆（预知节点）",
    "天赋/体质变异（血脉灵根）", "空间/物资仓库（随身空间）", "复制/学习（瞬掌技能）",
    "气运/魅力（好运人际）", "武力碾压（战力拉满）", "智谋/全知分析（算无遗策）",
    "治疗/不死（快速恢复）", "沟通/契约（驭使万物）",
]

PERSONAS = [
    "自定义（在下方文本框描述）", "莽夫（正面硬刚、风险偏好高）",
    "苟道（稳健发育、保命优先）", "谋士（权谋布局、借力打力）",
    "规则解构（钻研机制、寻找缝隙）", "义士（道义优先、护短）",
    "乐子人（混沌随性、追求趣味）", "探索者（好奇求知、踏遍未知）",
    "深情（羁绊优先、以情驱动）", "学徒（日拱一卒、越挫越强）",
]

# 上传原著节选的最大字符数（防止超出模型上下文；超出部分截断并标注）
MAX_NOVEL_EXCERPT = 15000

# 基础模式注入的作品档案最大字符数（超长时保留开头设定与结尾近期段落）
MAX_BASIC_WORK_CHARS = 4000

# 基础/普通片段模式的精简运行时规则（从 rules/runtime.md 提炼的要点；
# 完整规则仅强化模式注入，rules/ 文件本身不改动）。
BASIC_RULES = """# Fate Engine 基础运行规则（精简版）

> 本摘要适用于基础/普通片段模式；强化模式另行注入完整规则集。

## 铁律
1. 原文依附：作品档案与上传原文中的明确事实优先；无法确认的新增内容标注为【待证伪】，不得伪装成原文事实。
2. 锚点尽力发生：主线锚点在世界允许时尽力发生；玩家改变的是路径、代价、见证者与呈现形式，不得无理由删除锚点。
3. 选择有回响：每次玩家行动都产生可观察的状态变化，或明确记录为无效、被阻挡及其原因。
4. 角色受约束：角色行为服从其目标、已知信息、能力边界与关系状态，不得全知全能。
5. 能力守恒：金手指必须有作用域、代价、冷却或风险，不得用空泛升级绕过世界上限。
6. 收束可解释：回弹与阻挡须由角色意志、环境阻力或既有因果支撑，不得以作者、神谕或系统意志作为唯一理由。

## 涟漪与相容性
- 涟漪分级 L0–L4：L0–L2 的原创行动真实留存，不得因其非原文而清除；L3（较大关系、资源流或事件顺序改变）需多回合积势与可解释代价；L4（改动锚点因果核或世界级结构）默认阻挡，仅在积势达标且相容性 K≥60 时评估兼容变体。
- K 为 0–100 的行动与锚点共存评分：K≥60 必须同时保留玩家行动与锚点因果核；K<60 不得强行共存，转化为旁支、代价、未遂或延迟影响，并说明世界内阻力。
- 回弹等级：轻（局部巧合修正）/中（因果拉回并附轻微代价）/重（回到最近合法锚点窗口并附可追溯损失）；灭级仅用于恶意取消锚点，须由多个世界内因解释。

## 风格与范围
- 九风格固定集合：行动型、谋略型、苟稳型、规则型、义守型、乐趣型、探索型、情感型、成长型；最高分为主风格，是行动偏好与呈现倾向，不是身份标签。
- 基础/普通片段模式：只在指定片段内推进，10–30 回合内收束本片段，不做全书进度承诺；不启用伙伴、女主、宿敌三类人物机制；不提供全局存档与工作记录。
- 开局先输出核对清单（作品、模式、难度、金手指、穿越角色、时间点、性格要点），玩家确认后才生成第一幕；留空项先给 2–3 个候选，不得悄悄替换玩家设定。

## 输出校验
每幕入账前检查：内容与当前片段、事实、玩家行动或合法涟漪相符；至少保留一个原文依附点；锚点状态更新正确；非原文事件标注涟漪级别；角色不越出知情范围；进度单调不倒退。任一项未通过则修正后重写当前幕，不得带病输出。"""

# 角色性格模型目录：标准层 + 超高还原增强层
STANDARD_MODEL_DIR = os.path.join(BASE_DIR, "assets", "personas", "standard")
ENHANCED_MODEL_DIR = os.path.join(BASE_DIR, "assets", "personas", "enhanced")
# 作为穿越性格注入时的最大字符数（防止超大模型超出模型上下文）
MAX_PERSONA_CHARS = 30000

_rules_cache = None
_runtime_rules_cache = {}


def read_character_model(path, cap=MAX_PERSONA_CHARS):
    """读取角色性格模型内容，剥离 YAML frontmatter，返回正文（作为穿越性格注入）。
    对超大（超高还原）模型按 cap 截断以控制上下文。"""
    txt = read_upload_text(path)
    if txt.startswith("---"):
        lines = txt.split("\n")
        seen = 0
        for i, ln in enumerate(lines):
            if ln.strip() == "---":
                seen += 1
                if seen == 2:
                    txt = "\n".join(lines[i + 1:]).strip()
                    break
    txt = txt.strip()
    if cap and len(txt) > cap:
        txt = txt[:cap] + "\n……（超高还原模型核心节选，完整版见模型文件）"
    return txt


def _to_path(f):
    """把 Gradio File 组件的返回值统一为本地路径字符串。
    兼容 str 路径、带 .path 的 FileData、带 .name 的对象。"""
    if f is None:
        return None
    if isinstance(f, str):
        return f
    return getattr(f, "path", None) or getattr(f, "name", None)


def read_upload_text(path, cap=None):
    """读取用户上传的文本文件，自动尝试 utf-8 / gbk 编码；按 cap 截断。"""
    path = _to_path(path)
    if not path or not os.path.exists(path):
        return ""
    raw = None
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            with open(path, encoding=enc) as f:
                raw = f.read()
            break
        except Exception:
            continue
    if raw is None:
        with open(path, encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    raw = raw.strip()
    if cap and len(raw) > cap:
        raw = raw[:cap] + "\n……（节选截断，后续文本未包含）"
    return raw


def load_rules():
    """读取作品库资产（带缓存，不修改内容）。"""
    global _rules_cache
    if _rules_cache is None:
        with open(WORK_LIBRARY_PATH, encoding="utf-8") as f:
            _rules_cache = f.read()
    return _rules_cache


def invalidate_rules_cache():
    """作品库被写入（上传蒸馏入库）后调用：下次 list_works/get_work_block 重新读盘。"""
    global _rules_cache
    _rules_cache = None


def load_runtime_rules(enhanced=False):
    """读取泛化运行时规则；强化模式再叠加章节同步规则。按 enhanced 分键缓存。"""
    key = bool(enhanced)
    cached = _runtime_rules_cache.get(key)
    if cached is not None:
        return cached
    parts = []
    if os.path.isfile(RUNTIME_RULES_PATH):
        with open(RUNTIME_RULES_PATH, encoding="utf-8") as f:
            parts.append(f.read())
    if key and os.path.isfile(ENHANCED_RULES_PATH):
        with open(ENHANCED_RULES_PATH, encoding="utf-8") as f:
            parts.append(f.read())
    text = "\n\n".join(parts)
    _runtime_rules_cache[key] = text
    return text


def list_works():
    """返回作品库条目列表，形如 'W01 《青云试剑录》'。过滤掉原创题材占位条目。"""
    text = load_rules()
    return [f"{m.group(1)} 《{m.group(2)}》"
            for m in re.finditer(r"^### (W\d+) · 《([^》]+)》", text, re.M)
            if "原创题材" not in m.group(2)]


def _model_label(head, fallback):
    """从模型文件头部解析显示标签：优先 frontmatter description，其次 H1『# X思维模型』+《作品》。"""
    m = re.search(r"description:\s*([^\n（(]+?)[（(]([^）)]*)[）)]", head)
    if m:
        char = m.group(1).strip()
        tag = m.group(2).strip()
        book = re.search(r"《[^》]+》", tag)
        return f"{char}{book.group(0)}" if book else (f"{char}·{tag}" if tag else char)
    m = re.search(r"^#\s*([^·#\n]+?)思维模型", head, re.M)
    if m:
        char = m.group(1).strip()
        book = re.search(r"《[^》]+》", head)
        return char + (book.group(0) if book else "")
    return fallback


def _read_head(path, size=4096):
    """只读文件头部若干字符（编码回退顺序与 read_upload_text 一致）。

    用于只需要 frontmatter / 首个 H1 的场景（如 _scan_models 取展示标签）：
    性格模型单文件可达数十 KB，bootstrap 每次实时扫描全部文件时全文读入是
    纯浪费。不缓存，保留「保存后立即可见」语义。
    """
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            with open(path, encoding=enc) as fh:
                return fh.read(size)
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, encoding="utf-8", errors="ignore") as fh:
        return fh.read(size)


def _scan_models(dirpath, suffix=""):
    out = []
    if not os.path.isdir(dirpath):
        return out
    for fn in sorted(os.listdir(dirpath)):
        if not fn.lower().endswith(".md"):
            continue
        path = os.path.join(dirpath, fn)
        try:
            head = _read_head(path, 4096)
        except Exception:
            continue
        label = _model_label(head, os.path.splitext(fn)[0].replace("_SKILL", ""))
        out.append((label + suffix, path))
    return out


# 角色性格模型的展示顺序（按此排序，未收录的新模型排最后）。
# 性格库清空重建后不再预置任何具体角色名；如需置顶展示，在此按显示标签前缀追加。
PREFERRED_ORDER: list = []


def _order_key(label):
    for i, name in enumerate(PREFERRED_ORDER):
        if label.startswith(name):
            return (0, i, label)
    return (1, 0, label)


def _unique_model_labels(items):
    """保留全部模型；同显示标签按文件大小确定主版本，其余加稳定版本号。"""
    groups = {}
    for label, path in items:
        groups.setdefault(label, []).append(path)
    result = []
    for label, paths in groups.items():
        def rank(path):
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            return (-size, os.path.basename(path).casefold())

        for index, path in enumerate(sorted(paths, key=rank), start=1):
            display = label if index == 1 else f"{label}（版本{index}）"
            result.append((display, path))
    return result


def list_character_models():
    """扫描角色性格模型，返回 [(显示标签, 文件路径), ...]。
    超高还原增强层（personas/enhanced）在前并标注（超高还原），
    标准层（personas/standard）在后；同层同名版本以稳定后缀区分。"""
    enhanced = _unique_model_labels(_scan_models(ENHANCED_MODEL_DIR, suffix="（超高还原）"))
    standard = _unique_model_labels(_scan_models(STANDARD_MODEL_DIR))
    enhanced.sort(key=lambda lp: _order_key(lp[0]))
    standard.sort(key=lambda lp: _order_key(lp[0]))
    return enhanced + standard


def _extract(text, start_marker, level):
    """截取从 start_marker 到下一个同级标题之间的文本块。"""
    i = text.find(start_marker)
    if i < 0:
        return ""
    nxt = text.find("\n" + level, i + len(start_marker))
    end = nxt if nxt >= 0 else len(text)
    return text[i:end].strip()


def get_work_block(work_label):
    """按 'W01 《青云试剑录》' 中的编号取出该作品档案块。"""
    if not work_label:
        return ""
    wid = work_label.split(" ")[0]
    return _extract(load_rules(), f"### {wid} ·", "### ")


def _truncate_middle(text, cap):
    """超长文本保留开头与结尾、省略中段（尽量按段落边界对齐，并加省略标注）。"""
    text = (text or "").strip()
    if not cap or len(text) <= cap:
        return text
    head_cap = int(cap * 0.65)
    tail_cap = cap - head_cap
    head = text[:head_cap]
    cut = head.rfind("\n")
    if cut > head_cap // 2:
        head = head[:cut]
    tail = text[-tail_cap:]
    cut = tail.find("\n")
    if 0 < cut < tail_cap // 2:
        tail = tail[cut + 1:]
    return (head.rstrip() + "\n\n……（档案中段节选略去，完整版见作品库）……\n\n"
            + tail.lstrip())


def _difficulty_num(difficulty):
    """从难度字符串（如 'D4 普通'）解析难度编号 1–9。"""
    m = re.search(r"D\s*(\d)", difficulty or "")
    d = int(m.group(1)) if m else 4
    return max(1, min(9, d))


def build_nemesis_block(nemesis_label, nemesis_persona, player_difficulty="D4 普通",
                         nemesis_difficulty=None, faction_gap=None):
    """装配宿敌规则；宿敌仅一名，难度由玩家难度与阵营势差非线性计算。"""
    d = _difficulty_num(player_difficulty)
    nd = round(max(0.01, min(9.99, float(nemesis_difficulty or (10 - d)))), 2)
    gap = faction_gap or {}
    return "\n\n" + prompts.render(
        "nemesis_block.md",
        label=nemesis_label,
        persona=(nemesis_persona or "").strip(),
        player_d=d,
        nemesis_d=nd,
        delta=gap.get("delta", 0),
        bonus=gap.get("nemesis_bonus", 0),
    )


def build_system_prompt(mode, difficulty, golden_finger, persona_text,
                        work_label=None, novel_name=None, novel_excerpt=None,
                        role="", timepoint="", nemesis_label=None, nemesis_persona=None,
                        companion_configs=None, heroine_configs=None, faction_gap=None,
                        nemesis_difficulty=None, gender_constraints=""):
    """装配 system prompt：运行时规则 + 作品档案/上传节选 + 人物机制 + 开局设定。

    基础/普通片段模式使用精简规则（BASIC_RULES）、作品档案截断到
    MAX_BASIC_WORK_CHARS，且一律不注入伙伴/女主/宿敌机制块。
    gender_constraints 为穿越性别铁律（gender_guard 产出），注入在开局
    设定之前——穿越安排由模型完成，约束是同性别穿越的第一道保险。"""
    enhanced = bool(mode and str(mode).startswith("强化"))
    secs = []
    runtime = load_runtime_rules(enhanced=True) if enhanced else BASIC_RULES
    if runtime:
        secs.append(runtime.strip())

    # 作品来源：玩家上传原著 优先于 作品库
    if novel_name:
        excerpt = (novel_excerpt or "").strip()
        work_title = f"《{novel_name}》（玩家上传原著）"
        work_part = "\n\n" + prompts.render("uploaded_work.md",
                                            novel_name=novel_name, excerpt=excerpt)
    else:
        wb = get_work_block(work_label)
        if wb and not enhanced:
            wb = _truncate_middle(wb, MAX_BASIC_WORK_CHARS)
        work_title = work_label or "（未指定）"
        work_part = f"\n\n# 当前作品档案\n\n{wb}" if wb else ""

    role = (role or "").strip() or "由系统依性格设定建议 2–3 个候选，玩家确认"
    timepoint = (timepoint or "").strip() or "故事开篇"

    nemesis_part = ""
    nemesis_note = ""
    # 三类人物机制仅强化模式启用；基础模式即使调用方误传配置也一律跳过。
    if enhanced and companion_configs:
        try:
            import core.engine.runtime_mechanics
            secs.append(engine.runtime_mechanics.build_companion_block(companion_configs))
        except (ImportError, ValueError):
            pass
    if enhanced and heroine_configs:
        try:
            import core.engine.runtime_mechanics
            secs.append(engine.runtime_mechanics.build_heroine_block(heroine_configs))
        except (ImportError, ValueError):
            pass
    if enhanced and nemesis_label:
        nemesis_part = build_nemesis_block(
            nemesis_label, nemesis_persona, player_difficulty=difficulty,
            nemesis_difficulty=nemesis_difficulty, faction_gap=faction_gap)
        nd = round(max(0.01, min(9.99, float(nemesis_difficulty or (10 - _difficulty_num(difficulty))))), 2)
        nemesis_note = prompts.render("opening_nemesis_note.md", nemesis_d=nd)

    opening = "\n\n" + prompts.render(
        "opening_settings.md", work_title=work_title, mode=mode, difficulty=difficulty,
        golden_finger=golden_finger, role=role, timepoint=timepoint,
        persona_text=persona_text, nemesis_note=nemesis_note)

    # 回合推进规则（常驻）：确保每一回合都按固定 6+1 结构给出可选行动。
    rounds_rule = "\n\n" + prompts.load("rounds_rule.md")
    if mode and mode.startswith("强化"):
        rounds_rule += "\n" + prompts.load("rounds_rule_enhanced.md")
    if mode and (str(mode).startswith("基础") or str(mode).startswith("普通")):
        rounds_rule += "\n" + prompts.load("rounds_rule_fragment.md")

    header = prompts.load("system_header.md") + "\n\n"
    gender_part = ("\n\n" + gender_constraints.strip()) if gender_constraints and gender_constraints.strip() else ""
    return header + "\n\n".join(secs) + work_part + nemesis_part + gender_part + opening + rounds_rule


def opening_user_message():
    """开局第一条 user 消息：要求先做开局核对，不直接进正文。"""
    return prompts.load("opening_check.md")


# ---------- 选项结构的代码级校验（固定 6+1：4 金手指向 + 2 性格向 + 自由输入） ----------

# 匹配编号选项行：支持 "1." "1、" "1．"(全角点) "1)" "1：" "- 1." "（1）" "**1.**" 等常见编号
_OPTION_LINE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:\*\*|__)?[（(]?\s*([1-9])\s*[）)]?(?:\*\*|__)?\s*[\.、．\)）:：]\s*\S",
    re.M)

# 处于开局确认阶段的回复不含选项，属正常，不做补发
_CONFIRM_MARKERS = ("是否确认", "请确认", "开局核对", "确认后开始", "待你确认", "等你确认",
                    "候选", "是否开始")


def count_options(reply):
    """统计回复中编号选项的个数（取 1–9 的去重编号数）。"""
    nums = {int(m.group(1)) for m in _OPTION_LINE.finditer(reply or "")}
    return len(nums)


def is_confirmation_stage(reply):
    """该回复是否处于开局确认阶段（此时不要求 6 个选项）。"""
    return any(m in (reply or "") for m in _CONFIRM_MARKERS)


def options_ok(reply, need=6):
    """代码级校验：叙述回合是否给出了至少 need 个编号选项。确认阶段直接视为合格。"""
    if is_confirmation_stage(reply):
        return True
    return count_options(reply) >= need


def truncate_partial_options(reply):
    """回复中选项不完整时，截掉末尾残缺的选项块，保留剧情部分（供补发后合并为同一气泡展示）。
    仅当选项块位于回复后半段时才截断，避免误伤正文中的编号。"""
    txt = strip_hidden(reply)
    matches = list(_OPTION_LINE.finditer(txt))
    if not matches:
        return txt
    first = matches[0]
    if first.start() < len(txt) // 2:
        return txt
    cut = txt.rfind("\n", 0, first.start())
    return txt[:cut if cut > 0 else first.start()].rstrip()


OPTION_REPAIR_MESSAGE = prompts.load("option_repair.md")


# ---------- 运行日志 / 十回合评价 / 历史压缩（代码级保证） ----------

LOG_RE = re.compile(r"<<<LOG>>>(.*?)<<<END>>>", re.S)
ARCHIVE_RE = re.compile(r"<<<ARCHIVE>>>(.*?)<<<END>>>", re.S)


def extract_log(reply):
    """提取引擎回复末尾的回合日志段；没有则返回空串。"""
    m = LOG_RE.search(reply or "")
    return m.group(1).strip() if m else ""


def extract_archive(reply):
    """提取压缩存档段；没有则返回空串。"""
    m = ARCHIVE_RE.search(reply or "")
    return m.group(1).strip() if m else ""


def strip_hidden(reply):
    """展示给玩家前剥离日志/存档等隐藏段（含流式中途未闭合的段）。

    流式渲染热路径（每个 chunk 调用一次）：隐藏段一律以 "<<<" 开头，
    文本中没有 "<<<" 时直接返回，跳过两次正则替换与两次查找。
    """
    reply = reply or ""
    if "<<<" not in reply:
        return reply.rstrip()
    txt = LOG_RE.sub("", reply)
    txt = ARCHIVE_RE.sub("", txt)
    for mark in ("<<<LOG>>>", "<<<ARCHIVE>>>"):
        i = txt.find(mark)
        if i != -1:
            txt = txt[:i]
    return txt.rstrip()


def pacing_hint(round_no, chapter=1, chapter_round=1, turn_budget=0, chapter_text="", anchor_text=""):
    """强化模式的机械章节提示；原文和锚点作为运行时消息注入。"""
    cap = 2500 if int(chapter_round or 1) <= 1 else 800
    return prompts.render(
        "pacing_hint.md",
        chapter=chapter,
        chapter_round=chapter_round,
        turn_budget=turn_budget or "?",
        source=(chapter_text or "")[:cap],
        anchors=(anchor_text or "")[:1200],
    )


def eval_archive_message(round_no):
    """每 10 回合：玩家评价（展示）+ 压缩存档（隐藏，供历史压缩）。"""
    return prompts.render("eval_archive.md", round_no=round_no)


def _is_unsupported_parameter_error(exc):
    """识别常见的参数不支持错误；仅用于思考参数的一次性回退。"""
    text = str(exc).lower()
    markers = ("unsupported", "unknown parameter", "unrecognized parameter", "invalid parameter",
               "extra_body", "reasoning_effort", "enable_thinking", "thinking")
    if any(marker in text for marker in markers) and any(
            code in text for code in ("400", "bad request", "invalid")):
        return True
    # openai 客户端本地校验抛的 TypeError（未发 HTTP）：
    # "Completions.create() got an unexpected keyword argument 'xxx'"
    return "unexpected keyword argument" in text


def trim_history(history, max_messages=8):
    """把对话历史裁剪为「首条 + 最近 max_messages 条」，供省 token 的调用侧使用。

    首条通常是开局 user 消息（系统摘要性质），其余保留最近回合。
    返回新列表，不改动入参；历史不超过上限时原样返回副本。"""
    history = list(history or [])
    if max_messages is None or max_messages < 0 or len(history) <= max_messages + 1:
        return history
    if max_messages == 0:
        return history[:1]
    return history[:1] + history[-max_messages:]


def stream_reply(client, model, system_prompt, history, usage_box=None, extra_kwargs=None,
                 provider="deepseek", thinking_mode="auto", thinking_param=""):
    """流式生成助手回复；思考参数不兼容时自动去掉后重试一次。"""
    messages = [{"role": "system", "content": system_prompt}] + history
    thinking = dict(extra_kwargs or thinking_kwargs(provider, thinking_mode, thinking_param))
    kwargs = dict(model=model, messages=messages, stream=True, temperature=0.8)
    kwargs.update(thinking)
    if usage_box is not None:
        kwargs["stream_options"] = {"include_usage": True}
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:
        if not thinking or not _is_unsupported_parameter_error(exc):
            raise
        kwargs = dict(model=model, messages=messages, stream=True, temperature=0.8)
        if usage_box is not None:
            kwargs["stream_options"] = {"include_usage": True}
        resp = client.chat.completions.create(**kwargs)
        if usage_box is not None:
            usage_box["thinking_fallback"] = True
    buf = ""
    for chunk in resp:
        if usage_box is not None and getattr(chunk, "usage", None):
            u = chunk.usage
            usage_box["prompt"] = getattr(u, "prompt_tokens", 0) or 0
            usage_box["completion"] = getattr(u, "completion_tokens", 0) or 0
            usage_box["cache_hit"] = getattr(u, "prompt_cache_hit_tokens", 0) or 0
        if not getattr(chunk, "choices", None):
            continue
        delta = chunk.choices[0].delta
        piece = getattr(delta, "content", None)
        if piece:
            buf += piece
            yield buf


def stream_reply_with_retry(client, model, system_prompt, history, usage_box=None,
                            extra_kwargs=None, provider="deepseek",
                            thinking_mode="auto", thinking_param="", retries=1):
    """stream_reply 的网络重试包装：连接类故障自动整体重放一次。

    网关偶发重置连接（WinError 10054 / APIConnectionError）时从头重试整个
    请求（流式中途断开无法续传）；重试额度用尽才把异常抛给调用方。产出
    语义与 stream_reply 完全一致（累积缓冲）。
    """
    try:
        from openai import APIConnectionError as _ConnError
    except ImportError:  # pragma: no cover  无 SDK 环境退化为不重试
        yield from stream_reply(client, model, system_prompt, history,
                                usage_box=usage_box, extra_kwargs=extra_kwargs,
                                provider=provider, thinking_mode=thinking_mode,
                                thinking_param=thinking_param)
        return
    for attempt in range(max(0, int(retries)) + 1):
        try:
            for acc in stream_reply(client, model, system_prompt, history,
                                    usage_box=usage_box, extra_kwargs=extra_kwargs,
                                    provider=provider, thinking_mode=thinking_mode,
                                    thinking_param=thinking_param):
                yield acc
            return
        except _ConnError:
            if attempt >= max(0, int(retries)):
                raise


def extract_progress(reply):
    """从回合日志段解析剧情进度百分比（0–100）；没有则返回 None。"""
    m = LOG_RE.search(reply or "")
    if not m:
        return None
    p = re.search(r"进度\s*[:：]\s*(\d{1,3})", m.group(1))
    if not p:
        return None
    return max(0, min(100, int(p.group(1))))
