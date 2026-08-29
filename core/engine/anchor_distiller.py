# -*- coding: utf-8 -*-
"""后台渐进式单章锚点蒸馏。

只依赖标准库。``model`` 是调用方注入的 callable，接收单章 prompt，返回九字段
JSON（dict 或 JSON 字符串）。合格结果写入 ``anchors/NNNN.json``。
"""
from __future__ import annotations

import json
import re
import threading
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

# 失败自动重试：队列层退避重入队（_distill_one 内部另有 3 次连续尝试）。
RETRY_LIMIT = 3
RETRY_DELAYS = (2.0, 8.0, 20.0)

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
    """单线程后台队列；可重复启动，已落盘章节自动跳过。"""

    def __init__(self, chapters_path: Union[str, Path], model: Optional[Model], output_dir: Optional[Union[str, Path]] = None):
        self.chapters_path = Path(chapters_path)
        self.output_dir = Path(output_dir) if output_dir else self._default_output()
        self.model = model
        self._queue: PriorityQueue[Tuple[int, int]] = PriorityQueue()
        self._queued = set()
        self._status: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._disabled = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sequence = 0
        self._retry_counts: Dict[int, int] = {}

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
            self._queue.put((abs(number - current), self._sequence, number))
            self._set_status(number, "pending")
            added.append(number)
        return added

    def start(self) -> "AnchorDistiller":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="anchor-distiller", daemon=True)
        self._thread.start()
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
            self._distill_one(number)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._set_status(number, "failed", str(exc))
            raise
        with self._lock:
            self._set_status(number, "done")
        return json.loads(target.read_text(encoding="utf-8"))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                _, _, number = self._queue.get(timeout=0.1)
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
                self._distill_one(number)
                self._set_status(number, "done")
                with self._lock:
                    self._retry_counts.pop(number, None)
            except Exception as exc:
                # 失败自动快速重试：退避后重新入队（优先级降低，不挤占新章节），
                # 总重试次数封顶；耗尽后标记失败，等开局/下回合的 enqueue 再触发。
                message = str(exc)
                with self._lock:
                    attempt = self._retry_counts.get(number, 0)
                    if attempt < RETRY_LIMIT:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        self._retry_counts[number] = attempt + 1
                        seq = self._sequence = self._sequence + 1
                    else:
                        delay = None
                if delay is not None:
                    self._set_status(number, "failed",
                                     "%s；%.0f 秒后自动重试（第 %d/%d 次）"
                                     % (message, delay, attempt + 1, RETRY_LIMIT))
                    # 可中断等待（stop 时立即退出线程），醒后重新入队。
                    if not self._stop.wait(delay):
                        self._queue.put((90 + attempt, seq, number))
                else:
                    self._set_status(number, "failed",
                                     "%s；已自动重试 %d 次仍失败，将在下一回合或开局时再次尝试"
                                     % (message, RETRY_LIMIT))
            finally:
                self._queue.task_done()

    def _distill_one(self, number: int, attempts: int = 3) -> None:
        if self.model is None:
            raise ValueError("未配置模型调用函数")
        full_text = _chapter_path(self.chapters_path, number).read_text(encoding="utf-8")
        # 单章输入上限（放宽到 3500：2000 只能覆盖长章前 1/3，事件提取不完整）。
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
            target = self.output_dir / ("%04d.json" % number)
            temp = target.with_suffix(".json.tmp")
            temp.write_text(json.dumps(anchor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(target)
            return
        raise last_err

    def disable(self) -> None:
        self._disabled.set()

    def enable(self) -> None:
        self._disabled.clear()

    def stop(self, join: bool = True) -> None:
        self._stop.set()
        if join and self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def is_disabled(self) -> bool:
        return self._disabled.is_set()


# 便于简单调用方使用的别名。
DistillationQueue = AnchorDistiller
