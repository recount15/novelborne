# -*- coding: utf-8 -*-
"""后台渐进式单章锚点蒸馏。

只依赖标准库。``model`` 是调用方注入的 callable，接收单章 prompt，返回九字段
JSON（dict 或 JSON 字符串）。合格结果写入 ``anchors/NNNN.json``。
"""
from __future__ import annotations

import heapq
import json
import os
import re
import threading
import time
from pathlib import Path
from queue import PriorityQueue
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

ANCHOR_FIELDS = (
    "chapter", "title", "summary", "events", "characters",
    "world", "foreshadowing", "quotes", "ripple",
)
_TEXT_FIELDS = ("title", "summary", "world", "ripple")
_LIST_FIELDS = ("events", "characters", "foreshadowing", "quotes")
_POLLUTION = re.compile(r"(?:待填写|待补充|TODO|TBD|示例文本|placeholder|<[^>]+>)", re.I)

# 失败自动重试：队列层延迟重入队（_distill_one 内部另有 3 次连续尝试）。
RETRY_LIMIT = 3
RETRY_DELAYS = (2.0, 8.0, 20.0)

#: 蒸馏 worker 池默认大小（重构 M0：章间并行；实际 API 并发仍受
#: engine.parallel 动态控制器全局约束，worker 数只是并行度上限之一）。
DEFAULT_WORKERS = 3
MAX_WORKERS = 4


def default_workers() -> int:
    """worker 数：环境变量 FATE_DISTILL_WORKERS 优先，默认 3，钳制 1–4。"""
    raw = str(os.environ.get("FATE_DISTILL_WORKERS") or "").strip()
    try:
        number = int(raw) if raw else DEFAULT_WORKERS
    except ValueError:
        number = DEFAULT_WORKERS
    return max(1, min(MAX_WORKERS, number))

Model = Callable[[str], Any]


def _chapter_path(chapters_path: Union[str, Path], number: int) -> Path:
    path = Path(chapters_path)
    directory = path / "chapters" if (path / "chapters").is_dir() else path
    return directory / ("%04d.txt" % number)


def _parse_model_output(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        raise ValueError("模型输出必须是 dict 或 JSON 字符串")
    text = value.strip()
    if not text:
        raise ValueError("模型返回了空内容，无法生成锚点")
    # 兼容 markdown 围栏：```json ... ``` / ``` ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        # 模型常在 JSON 前后夹带说明文字（如"以下是锚点："或思考型模型的
        # 推理残留）：提取首个 "{" 到最后一个 "}" 的片段再解析一次。
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型输出中未找到 JSON 对象")
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError as exc:
            raise ValueError("模型输出的 JSON 无法解析") from exc
    if not isinstance(parsed, dict):
        raise ValueError("模型输出 JSON 顶层必须是对象")
    return parsed


def _align_quote_to_text(quote: str, chapter_text: str,
                        min_ratio: float = 0.55) -> Optional[str]:
    """把模型输出的一条引文对齐到原文最接近的连续片段。

    模型常对原文做轻微改写（省略、替换标点），导致逐字命中校验失败。
    此处用滑动窗口 + 相似度评分寻找原文中最接近的等长片段；最佳相似度
    达到 min_ratio 即采用原文片段（保证落盘引文永远逐字命中原文），
    否则返回 None（该条引文放弃，交由上层处理）。
    """
    quote = quote.strip()
    if not quote or quote in chapter_text:
        return quote if quote else None
    from difflib import SequenceMatcher

    qlen = len(quote)
    if qlen < 4 or not chapter_text:
        return None
    best_text: Optional[str] = None
    best_ratio = 0.0
    for start in range(0, max(1, len(chapter_text) - qlen + 1)):
        candidate = chapter_text[start:start + qlen]
        # quick_ratio 预筛，避免逐对全量计算。
        if SequenceMatcher(None, quote, candidate).quick_ratio() < min_ratio:
            continue
        ratio = SequenceMatcher(None, quote, candidate).ratio()
        if ratio > best_ratio:
            best_ratio, best_text = ratio, candidate
    if best_text is not None and best_ratio >= min_ratio:
        return best_text
    return None


def _align_quotes(quotes: List[str], chapter_text: str) -> List[str]:
    """对齐引文列表；全部无法对齐时返回空列表（由上层判失败）。"""
    aligned: List[str] = []
    for quote in quotes:
        fixed = _align_quote_to_text(quote, chapter_text)
        if fixed:
            aligned.append(fixed)
    return aligned


def _chapter_sentences(text: str, limit: int = 40) -> List[str]:
    """按中文句末标点切出原文句子（弹性兜底的数据源）。"""
    sentences = []
    for piece in re.split(r"(?<=[。！？])", str(text or "")):
        piece = piece.strip()
        if len(piece) >= 8:
            sentences.append(piece)
        if len(sentences) >= limit:
            break
    return sentences


def synthesize_anchor_from_text(chapter_text: str, chapter_number: int,
                                chapter_title: str = "") -> Dict[str, Any]:
    """弹性兜底：从原文确定性合成「原文摘录式锚点」（九字段合规）。

    模型多次蒸馏仍不过校验时的保底路径——内容全部取自原文，不虚构：
    - quotes：逐字取原文句子（天然通过逐字命中校验，截到 40–200 字）
    - title：章节标题（蒸馏输入自带）或首句前 20 字
    - summary/events：原文前几句（事件描述即原文摘录）
    - characters：从对话/人名高频出现的句子提取含名字的短句
    - world/foreshadowing/ripple：从原文中后段句子各取一条
    输出必须再过 validate_anchor；合成结果加 origin 标记前先返回标准九字段
    （多余字段会破坏严格九字段校验，标记由调用方在落盘时另行处理）。
    """
    sentences = _chapter_sentences(chapter_text)
    if not sentences:
        raise ValueError("原文无可用的完整句子，无法合成兜底锚点")

    def clip(text: str, lo: int = 10, hi: int = 200) -> str:
        value = text.strip()
        if len(value) > hi:
            value = value[:hi].rstrip("，。；、")
        return value if len(value) >= min(lo, len(value)) else value

    title = str(chapter_title or "").strip() or clip(sentences[0], 8, 20)
    # 摘要取前 2–3 句拼接；事件取前几句（每句一事，原文即事实）。
    summary = clip("".join(sentences[:3]), 20, 200)
    events = [clip(s, 6, 200) for s in sentences[:4]]
    # 人物：含引号的句子多为对话（说话人在场）；再补含「他/她/其名」的短句。
    characters: List[str] = []
    for s in sentences:
        if "“" in s or "」" in s:
            characters.append(clip(s, 4, 100))
        if len(characters) >= 3:
            break
    if not characters:
        characters = [clip(s, 4, 60) for s in sentences[1:4]]
    # 世界观/伏笔/涟漪：从中后段各取一句，与摘要错开。
    mid = sentences[len(sentences) // 2:] or sentences
    world = clip(mid[0], 10, 200)
    foreshadowing = [clip(s, 6, 200) for s in (mid[1:3] or sentences[2:4]) or [world]]
    ripple = clip((mid[2:4] or sentences[3:5] or [world])[0], 8, 200)
    quotes = [clip(s, 8, 200) for s in sentences[:3]]
    return {
        "chapter": int(chapter_number),
        "title": title,
        "summary": summary,
        "events": events,
        "characters": characters[:3],
        "world": world,
        "foreshadowing": foreshadowing[:3],
        "quotes": quotes,
        "ripple": ripple,
    }


def sanitize_anchor(anchor: Dict[str, Any], chapter_number: Optional[int] = None) -> Dict[str, Any]:
    """形式修正：把模型输出的锚点尽量修成合法形式，减少无谓的失败。

    只修"形式"不修"事实"（不改语义内容）：
    - chapter 强制对齐任务章号（模型常写错）；
    - 多余字段直接丢弃（保留九字段内键，缺失字段交给 validate 判失败）；
    - 文本字段转 str、去首尾空白、截断到 500 字符；
    - 数组字段每项去空白、**空项过滤**、**超长项按中文标点拆句**
      （拆后仍超长的截断）、去重保序、截到 12 项。
    """
    if not isinstance(anchor, dict):
        return anchor
    result = {key: value for key, value in anchor.items() if key in ANCHOR_FIELDS}
    if chapter_number is not None:
        result["chapter"] = int(chapter_number)
    for field in _TEXT_FIELDS:
        if field in result:
            result[field] = str(result[field] or "").strip()[:500]
    for field in _LIST_FIELDS:
        result[field] = _sanitize_list(result.get(field))
    return result


_ITEM_MAX = 240


def _sanitize_list(value: Any) -> List[str]:
    """数组字段形式修正：空项过滤、超长拆句、去重、限 12 项。"""
    if isinstance(value, str):
        value = [line for line in re.split(r"[；;\n]+", value) if line.strip()]
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for raw in value:
        if isinstance(raw, dict):
            # 模型偶发输出 {"事件": "..."} 这类对象项：取最长字符串值当正文。
            texts = [str(v) for v in raw.values() if isinstance(v, str) and v.strip()]
            raw = max(texts, key=len) if texts else ""
        if not isinstance(raw, str):
            raw = str(raw)
        for piece in _split_long_item(raw.strip()):
            if piece and not _POLLUTION.search(piece):
                items.append(piece)
    deduped: List[str] = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped[:12]


def _split_long_item(item: str) -> List[str]:
    """超长项按中文标点拆句；拆后仍超长的截断。"""
    if len(item) <= _ITEM_MAX:
        return [item] if item else []
    pieces: List[str] = []
    for part in re.split(r"(?<=[。！？；])", item):
        part = part.strip()
        if not part:
            continue
        if len(part) <= _ITEM_MAX:
            pieces.append(part)
            continue
        # 单句仍超长：按逗号/顿号再拆；仍超长截断。
        for sub in re.split(r"(?<=[，、])", part):
            sub = sub.strip()
            if sub:
                pieces.append(sub[:_ITEM_MAX])
    return pieces


def validate_anchor(anchor: Dict[str, Any], chapter_text: str, chapter_number: Optional[int] = None) -> Dict[str, Any]:
    """校验并规范九字段锚点；失败抛 ValueError。"""
    if not isinstance(anchor, dict):
        raise ValueError("锚点必须是对象")
    missing = [field for field in ANCHOR_FIELDS if field not in anchor]
    extra = [field for field in anchor if field not in ANCHOR_FIELDS]
    if missing or extra:
        raise ValueError("字段必须严格为九字段，缺少=%s，多余=%s" % (missing, extra))
    try:
        number = int(anchor["chapter"])
    except (TypeError, ValueError):
        raise ValueError("chapter 必须为整数")
    if chapter_number is not None and number != chapter_number:
        raise ValueError("chapter 与任务章号不一致")
    clean: Dict[str, Any] = {"chapter": number}
    for field in _TEXT_FIELDS:
        value = anchor[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("字段 %s 必须非空文本" % field)
        if len(value) > 500:
            raise ValueError("字段 %s 超过 500 字符" % field)
        if _POLLUTION.search(value):
            raise ValueError("字段 %s 含模板污染内容" % field)
        clean[field] = value.strip()
    for field in _LIST_FIELDS:
        value = anchor[field]
        if not isinstance(value, list) or not value:
            raise ValueError("字段 %s 必须为非空数组" % field)
        if len(value) > 12:
            raise ValueError("字段 %s 项数过多" % field)
        items = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item) > 240:
                raise ValueError("字段 %s 含空项或超长项" % field)
            item = item.strip()
            if _POLLUTION.search(item):
                raise ValueError("字段 %s 含模板污染内容" % field)
            items.append(item)
        clean[field] = items
    for quote in clean["quotes"]:
        if quote not in chapter_text:
            raise ValueError("quotes 未逐字命中原文: %s" % quote)
    return clean


class AnchorDistiller:
    """多 worker 后台蒸馏池（重构 M0）；可重复启动，已落盘章节自动跳过。

    队列元素为 ``(priority, seq, chapter, ready_at, force)``：
    ``ready_at`` 用于失败章的**非阻塞**延迟重入队（worker 不再整队等待退避）；
    ``force`` 标记「文件已存在也要重蒸馏」（救援兜底落盘后，后台模型版
    稍后覆盖精化）。
    """

    def __init__(self, chapters_path: Union[str, Path], model: Optional[Model],
                 output_dir: Optional[Union[str, Path]] = None,
                 workers: Optional[int] = None):
        self.chapters_path = Path(chapters_path)
        self.output_dir = Path(output_dir) if output_dir else self._default_output()
        self.model = model
        self.workers = default_workers() if workers is None else max(1, int(workers))
        # (priority, seq, chapter) -> (priority, seq, chapter, ready_at, force)
        self._queue: PriorityQueue = PriorityQueue()
        # (ready_at, priority, seq, chapter)：退避到期后由 _promote_delayed 搬回主队列。
        self._delayed: List[Tuple[float, int, int, int]] = []
        self._queued = set()
        self._status: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._disabled = threading.Event()
        self._threads: List[threading.Thread] = []
        self._sequence = 0
        self._retry_counts: Dict[int, int] = {}
        # 章级互斥：distill_now 与 worker 池并发蒸馏同一章时不双写、不重复调用。
        self._chapter_locks: Dict[int, threading.Lock] = {}


    def _default_output(self) -> Path:
        return (self.chapters_path / "anchors") if (self.chapters_path / "chapters").is_dir() else self.chapters_path.parent / "anchors"

    def _set_status(self, number: int, status: str, error: str = "") -> None:
        with self._lock:
            self._status[number] = {"chapter": number, "status": status, **({"error": error} if error else {})}

    def status(self) -> Dict[int, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._status.items()}

    def get_status(self) -> Dict[int, Dict[str, Any]]:
        return self.status()

    def enqueue(self, current_chapter: int, lookahead: int = 2, total: Optional[int] = None,
                lookback: Optional[int] = None) -> List[int]:
        """加入当前章蒸馏窗口；当前章优先，其余按距离排序。

        ``lookback`` 默认 ``None`` 表示向前看 ``lookahead`` 章（前后对称的旧行为）；
        显式传 ``0`` 时从当前章起向后；传 ``-1`` 时从下一章起向后——当前章正在
        进行、其锚点已蒸馏，窗口只覆盖其后 ``lookahead`` 章，再往后不蒸馏以节约
        token。
        """
        current = int(current_chapter)
        radius = max(0, int(lookahead))
        back = radius if lookback is None else int(lookback)
        start = max(1, current - back)
        if lookback is not None and int(lookback) < 0:
            # 向前看模式默认假定当前章锚点已蒸馏；若首章同步蒸馏失败
            # （锚点文件缺失），仍须把当前章纳入队列重试，否则开局永久卡死。
            if not (self.output_dir / ("%04d.json" % current)).is_file():
                start = current
        candidates = [n for n in range(start, current + radius + 1)]
        if total is not None:
            candidates = [n for n in candidates if n <= int(total)]
        added = []
        for number in sorted(set(candidates), key=lambda n: (abs(n - current), n)):
            path = _chapter_path(self.chapters_path, number)
            if not path.is_file() or (self.output_dir / ("%04d.json" % number)).is_file():
                if path.is_file() and (self.output_dir / ("%04d.json" % number)).is_file():
                    self._set_status(number, "done")
                continue
            with self._lock:
                if number in self._queued or self._status.get(number, {}).get("status") == "in_progress":
                    continue
                self._queued.add(number)
                self._sequence += 1
            self._queue.put((abs(number - current), self._sequence, number, 0.0, False))
            self._set_status(number, "pending")
            added.append(number)
        return added

    def _promote_delayed(self) -> None:
        """把退避到期的章节搬回主队列（worker 空闲即抢，不再整队等待）。"""
        now = time.monotonic()
        with self._lock:
            matured = []
            while self._delayed and self._delayed[0][0] <= now:
                _, priority, seq, number = heapq.heappop(self._delayed)
                if (self.output_dir / ("%04d.json" % number)).is_file():
                    self._set_status(number, "done")
                    continue
                self._queued.add(number)
                matured.append((priority, seq, number, 0.0, False))
            for item in matured:
                self._queue.put(item)

    def _chapter_guard(self, number: int) -> threading.Lock:
        """章级互斥锁（按需创建）：同一章并发蒸馏串行化 + 落盘去重。"""
        with self._lock:
            lock = self._chapter_locks.get(number)
            if lock is None:
                lock = threading.Lock()
                self._chapter_locks[number] = lock
            return lock

    def start(self) -> "AnchorDistiller":
        live = [t for t in self._threads if t.is_alive()]
        if len(live) >= self.workers:
            self._threads = live
            return self
        self._stop.clear()
        for index in range(self.workers - len(live)):
            thread = threading.Thread(target=self._run,
                                      name="anchor-distiller-%d" % (len(live) + index + 1),
                                      daemon=True)
            self._threads.append(thread)
            thread.start()
        return self

    def distill_now(self, number: int) -> Dict[str, Any]:
        """同步蒸馏单章锚点（阻塞调用）。

        已落盘则直接读取返回；否则立即调用模型生成并写入，成功返回锚点对象，
        失败抛异常。用于开局时同步补齐首章锚点，避免玩家在后台蒸馏完成前
        确认开局而被拒绝。
        """
        number = int(number)
        target = self.output_dir / ("%04d.json" % number)
        if target.is_file():
            with self._lock:
                self._set_status(number, "done")
            return json.loads(target.read_text(encoding="utf-8"))
        if self.model is None:
            raise ValueError("未配置模型调用函数")
        with self._lock:
            self._set_status(number, "in_progress")
        try:
            origin = self._distill_one(number)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._set_status(number, "failed", str(exc))
            raise
        self._mark_result(number, origin)
        return json.loads(target.read_text(encoding="utf-8"))

    def _mark_result(self, number: int, origin: str) -> None:
        """按蒸馏来源落状态；兜底版标记 origin 并入队 force 精化。"""
        if origin == "fallback":
            note = "模型蒸馏未通过校验，已用原文摘录锚点兜底，后台将自动精化"
            with self._lock:
                self._status[number] = {"chapter": number, "status": "done",
                                        "origin": "fallback", "note": note}
                self._sequence += 1
                seq = self._sequence
                self._queued.add(number)
                self._queue.put((95, seq, number, 0.0, True))
        elif origin == "model":
            with self._lock:
                self._set_status(number, "done")

    def rescue_now(self, number: int) -> Dict[str, Any]:
        """同步救援（重构 M0）：模型一卷 → 失败则原文摘录兜底落盘放行。

        任何成功路径都以「锚点文件落盘」为终点；兜底版在状态里标记
        ``origin=fallback``（文件本体保持严格九字段），并低优先级重新入队
        （force=True）让后台模型版稍后覆盖精化——保底比空缺好。
        """
        number = int(number)
        target = self.output_dir / ("%04d.json" % number)
        if target.is_file():
            return json.loads(target.read_text(encoding="utf-8"))
        try:
            return self.distill_now(number)
        except Exception as exc:  # noqa: BLE001 模型路径失败 → 确定性兜底
            chapter_file = _chapter_path(self.chapters_path, number)
            if not chapter_file.is_file():
                raise
            full_text = chapter_file.read_text(encoding="utf-8")
            anchor = validate_anchor(
                synthesize_anchor_from_text(full_text, number), full_text, number)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(".json.tmp")
            temp.write_text(json.dumps(anchor, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            temp.replace(target)
            note = "模型蒸馏失败，已用原文摘录锚点兜底放行：%s" % str(exc)[:120]
            with self._lock:
                self._status[number] = {"chapter": number, "status": "done",
                                        "origin": "fallback", "note": note[:200]}
                # 后台模型版精化：文件已存在故 force=True；低优先级不挤占新章。
                self._sequence += 1
                seq = self._sequence
                self._queued.add(number)
                self._queue.put((95, seq, number, 0.0, True))
            return anchor

    def _run(self) -> None:
        while not self._stop.is_set():
            self._promote_delayed()
            try:
                _, _, number, _ready, force = self._queue.get(timeout=0.1)
            except Exception:
                continue
            with self._lock:
                self._queued.discard(number)
            if self._disabled.is_set():
                self._set_status(number, "failed", "蒸馏已禁用")
                self._queue.task_done()
                continue
            self._set_status(number, "in_progress")
            try:
                origin = self._distill_one(number, force=force)
                self._mark_result(number, origin)
                with self._lock:
                    self._retry_counts.pop(number, None)
            except Exception as exc:
                # 失败延迟重入队（重构 M0：退避不再阻塞 worker，其他章节继续），
                # 优先级压低不挤占新章；总重试次数封顶后等 enqueue 再触发。
                message = str(exc)
                with self._lock:
                    attempt = self._retry_counts.get(number, 0)
                    if attempt < RETRY_LIMIT:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        self._retry_counts[number] = attempt + 1
                        seq = self._sequence = self._sequence + 1
                        requeue = True
                    else:
                        delay, seq, requeue = None, None, False
                if requeue:
                    self._set_status(number, "failed",
                                     "%s；%.0f 秒后自动重试（第 %d/%d 次）"
                                     % (message, delay, attempt + 1, RETRY_LIMIT))
                    with self._lock:
                        heapq.heappush(
                            self._delayed,
                            (time.monotonic() + delay, 90 + attempt, seq, number))
                else:
                    self._set_status(number, "failed",
                                     "%s；已自动重试 %d 次仍失败，将在下一回合或开局时再次尝试"
                                     % (message, RETRY_LIMIT))
            finally:
                self._queue.task_done()

    def _distill_one(self, number: int, attempts: int = 3, force: bool = False) -> str:
        """蒸馏单章；返回来源 ``"model"|"fallback"|"exists"``。

        章级互斥：distill_now / worker 池 / force 精化并发同一章时串行化；
        先到者落盘后，后到者（非 force）直接返回，模型只调一次。
        """
        if self.model is None:
            raise ValueError("未配置模型调用函数")
        with self._chapter_guard(number):
            target = self.output_dir / ("%04d.json" % number)
            if target.is_file() and not force:
                return "exists"
            return self._distill_one_locked(number, attempts, target)

    def _distill_one_locked(self, number: int, attempts: int, target: Path) -> str:
        full_text = _chapter_path(self.chapters_path, number).read_text(encoding="utf-8")
        # 单章输入上限（放宽到 3500：2000 只能覆盖长章前 1/3，事件提取不完整）。
        # 更长章节的全文覆盖由开局流水线的 map-reduce 切块蒸馏（M1）接管。
        source = full_text[:3500]
        prompt = (
            "请仅依据以下单章原文输出严格 JSON，必须包含且只能包含九字段：%s。"
            "quotes 必须是原文逐字连续片段，不能改写；"
            "events/characters/foreshadowing/quotes 四个数组字段必须至少 1 项，不得为空数组，"
            "数组内不要出现空字符串项，单项不超过 200 字；"
            "title/summary/world/ripple 必须为非空字符串。每个字段都要具体、非空。\n"
            "chapter=%d\n原文：\n%s" % (", ".join(ANCHOR_FIELDS), number, source)
        )
        last_err: Exception | None = None
        for _ in range(max(1, int(attempts))):
            try:
                parsed = _parse_model_output(self.model(prompt))
                # 形式修正：空项过滤、超长拆句、章号对齐、多余字段丢弃——
                # 只修形式不修事实，减少病态形态导致的整轮报废。
                parsed = sanitize_anchor(parsed, number)
                # 引文容错：模型常对原文轻微改写（省略、标点替换），
                # 先把每条引文对齐到完整原文中最接近的连续片段再校验；
                # 落盘引文永远逐字命中原文。全部无法对齐时保留原样，
                # 由 validate 的逐字校验按老路径判失败重试。
                if isinstance(parsed.get("quotes"), list):
                    aligned = _align_quotes(
                        [q for q in parsed["quotes"] if isinstance(q, str)], full_text)
                    if aligned:
                        parsed["quotes"] = aligned
                anchor = validate_anchor(parsed, full_text, number)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
            self.output_dir.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(".json.tmp")
            temp.write_text(json.dumps(anchor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(target)
            return "model"
        # 弹性兜底（2026-08-30）：模型多次蒸馏仍不过校验时，从原文确定性
        # 合成「原文摘录式锚点」——quotes 逐字取自原文（天然通过逐字校验），
        # 其余字段从原文句子与章标题派生，最后仍过 validate_anchor 严校验。
        # 保底比空缺好：主线推进需要锚点数据在场，摘录式内容完全来自原文，
        # 不违反「锚点数据必须按规范」的红线；标记 origin=fallback 供区分。
        try:
            anchor = synthesize_anchor_from_text(full_text, number)
            anchor = validate_anchor(anchor, full_text, number)
        except Exception as exc:  # noqa: BLE001  兜底合成也失败才真正抛错
            raise last_err or exc
        self.output_dir.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(anchor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(target)
        return "fallback"

    def disable(self) -> None:
        self._disabled.set()

    def enable(self) -> None:
        self._disabled.clear()

    def stop(self, join: bool = True) -> None:
        self._stop.set()
        if join:
            for thread in list(self._threads):
                if thread.is_alive():
                    thread.join(timeout=5)
            with self._lock:
                self._threads = [t for t in self._threads if t.is_alive()]

    def is_disabled(self) -> bool:
        return self._disabled.is_set()


# 便于简单调用方使用的别名。
DistillationQueue = AnchorDistiller
