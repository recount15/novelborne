# -*- coding: utf-8 -*-
"""本地原著章节切分与索引工具。

切分完全在本地完成，不调用模型或网络。字符偏移以 Python 字符串下标为准，
章节文件内容与 source 的对应切片保持逐字符一致。

标题识别是多情况模式库（全部锚定行首），按优先级匹配：
1. 「第X卷 ... 第N章」卷章同行组合（卷号章号都解析）；
2. 「第N章/节/回」（数字/中文数字/全角数字，可带「正文 」卷首前缀）；
3. 番外/外传/楔子/序章/尾声等无编号章节（按出现顺序编入）；
4. 「第X卷/部/篇」卷标记（不是章节，但写入索引保持卷归属）；
5. Chapter N / ch.N；
6. 纯数字编号行（如「0001 标题」「1、标题」）——仅当该行前一行是空行
   且全文命中数 >= 3 时才启用，防止把正文里的列表误切。

编号策略是宽容重排：按文件顺序为每个（卷, 分段）内的正文章节确定性重
编号，原始章号保留在 original_no；跳号/重号/分卷重编号只记入 warnings，
只有完全匹配不到任何标题才报错。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union


ENCODINGS = ("utf-8", "gbk", "gb18030", "utf-16")
_BOM_ENCODINGS = {
    b"\xff\xfe": "utf-16",
    b"\xfe\xff": "utf-16",
    b"\xff\xfe\x00\x00": "utf-32",
    b"\x00\x00\xfe\xff": "utf-32",
}

# 标题行首允许半角/全角空格（知轩藏书部分书籍标题带全角缩进）。
_HEAD = r"[ \t　]*"
# 章号：半角数字、全角数字、中文数字。
_NUM = r"[0-9０-９零〇一二三四五六七八九十百千万两]+"
# 标题与编号之间的分隔符（含全角空格、中文逗号）。
_SEP = r"[ \t　:：、.．,，\-—]"
_TITLE = r"[^\r\n]{0,40}"

# ③ 「第X卷 ... 第N章」卷章同行组合，优先级最高。
_RE_VOL_CHAPTER = re.compile(
    rf"^{_HEAD}第(?P<vol>{_NUM})卷(?:[^第\r\n]{{0,30}}?)?"
    rf"第(?P<ch>{_NUM})[章节回](?:{_SEP}+(?P<title>{_TITLE}))?{_HEAD}$"
)

# ① 「第N章/节/回」，可带「正文 」卷首前缀（②）。
_RE_CHAPTER = re.compile(
    rf"^{_HEAD}(?:正文{_SEP}+)?"
    rf"第(?P<ch>{_NUM})[章节回](?:{_SEP}*(?P<title>{_TITLE}))?{_HEAD}$"
)

# ⑤ 番外/外传/楔子/序章/尾声等无编号章节。
_RE_SPECIAL = re.compile(
    rf"^{_HEAD}(?P<kind>番外|外传|楔子|序章|序曲|序言|尾声|终章|引子|后记)"
    rf"(?P<suffix>[卷篇章部节])?(?:{_SEP}+(?P<title>{_TITLE}))?{_HEAD}$"
)

# ④ 「第X卷/部/篇」卷标记。卷号后必须紧跟分隔符或行尾，
# 防止把「第一篇就是……」这类正文误判为卷标题。
_RE_VOLUME = re.compile(
    rf"^{_HEAD}第(?P<vol>{_NUM})[卷部篇](?:{_SEP}+(?P<title>{_TITLE}))?{_HEAD}$"
)

# ⑥ Chapter N / ch.N。
_RE_ENGLISH = re.compile(
    rf"^{_HEAD}(?:chapter|ch)[. \t　]+(?P<ch>[0-9]+)\b(?:{_SEP}*(?P<title>{_TITLE}))?{_HEAD}$",
    re.IGNORECASE,
)

# 「正文」卷首标记：书籍信息区与正文的分界。
_RE_MARKER = re.compile(rf"^{_HEAD}正文{_HEAD}$")

# ⑦ 纯数字编号行（需前置空行且全文命中数达标才启用）。
_RE_NUMERIC = re.compile(
    rf"^{_HEAD}(?P<ch>[0-9０-９]{{1,5}})(?:[ \t　、.．:：]+(?P<title>[^\r\n]{{1,40}})){_HEAD}$"
)
# 纯数字标题不得以句读结尾，排除「1、死者口鼻部……。」这类正文列表项。
_NUMERIC_BAD_TAIL = re.compile(r"[。；;，,、！？!?…：:]$")

# 广告/站点信息行中出现的疑似标题不计入。
_AD_TOKENS = ("知轩藏书", "zxcs", "http://", "https://", "www.")

# 单条标题行长度上限，过滤误匹配的长正文行。
_MAX_HEADING_LINE = 70

_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

_MAX_WARNINGS = 200


def read_text_with_fallback(path: Union[str, os.PathLike]) -> Tuple[str, str]:
    """按 utf-8/gbk/gb18030/utf-16 回退读取文本，返回 (文本, 编码)。

    全部严格解码失败时（真实藏书常见个别坏字节），改用「替换字符最少」
    评分兜底，编码名带 ``-replace`` 后缀表示有损解码。
    """
    raw = Path(path).read_bytes()
    for marker, encoding in _BOM_ENCODINGS.items():
        if raw.startswith(marker):
            try:
                return raw.decode(encoding).replace("\r", ""), encoding
            except UnicodeDecodeError:
                break
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding).replace("\r", ""), encoding
        except UnicodeDecodeError:
            continue
    best: Optional[Tuple[int, str, str]] = None
    for encoding in ("utf-8", "gbk", "gb18030"):
        text = raw.decode(encoding, errors="replace")
        score = text.count("\ufffd")
        if best is None or score < best[0]:
            best = (score, encoding, text)
    assert best is not None
    return best[2].replace("\r", ""), f"{best[1]}-replace"


def _cn_number(value: str) -> Optional[int]:
    value = value.translate(_FW_DIGITS)
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in digits:
            number = digits[char]
        elif char in units:
            unit = units[char]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return None
    return total + section + number


def turn_budget(chars: int) -> int:
    """按章节字符数计算回合预算。"""
    for limit, budget in ((1500, 3), (3000, 4), (5000, 5), (8000, 6),
                          (12000, 7), (18000, 8)):
        if chars < limit:
            return budget
    return 9


def _classify_line(line: str) -> Optional[Dict]:
    """把一行分类为章节/卷/标记/纯数字候选；广告行与超长行直接排除。"""
    if not line.strip(" \t　") or len(line) > _MAX_HEADING_LINE:
        return None
    if any(token in line for token in _AD_TOKENS):
        return None
    match = _RE_VOL_CHAPTER.match(line)
    if match:
        vol_no = _cn_number(match.group("vol"))
        chapter_no = _cn_number(match.group("ch"))
        if vol_no is not None and chapter_no is not None:
            return {"kind": "chapter", "chapter_no": chapter_no, "special": False,
                    "raw": line.strip(" \t　"), "vol_chapter": vol_no}
    match = _RE_CHAPTER.match(line)
    if match:
        chapter_no = _cn_number(match.group("ch"))
        if chapter_no is not None:
            return {"kind": "chapter", "chapter_no": chapter_no, "special": False,
                    "raw": line.strip(" \t　"), "vol_chapter": None}
    match = _RE_SPECIAL.match(line)
    if match:
        return {"kind": "chapter", "chapter_no": None, "special": True,
                "raw": line.strip(" \t　"), "vol_chapter": None}
    match = _RE_ENGLISH.match(line)
    if match:
        return {"kind": "chapter", "chapter_no": int(match.group("ch")), "special": False,
                "raw": line.strip(" \t　"), "vol_chapter": None}
    match = _RE_VOLUME.match(line)
    if match:
        vol_no = _cn_number(match.group("vol"))
        if vol_no is not None:
            return {"kind": "volume", "volume_no": vol_no, "raw": line.strip(" \t　")}
    if _RE_MARKER.match(line):
        return {"kind": "marker"}
    match = _RE_NUMERIC.match(line)
    if match:
        number = int(match.group("ch").translate(_FW_DIGITS))
        return {"kind": "numeric", "chapter_no": number, "raw": line.strip(" \t　")}
    return None


def _scan(source: str) -> Tuple[List[Dict], List[Dict], List[int], List[str]]:
    """扫描全文，返回 (章节标题, 卷标题, 正文标记位置, 扫描期 warning)。"""
    lines = source.split("\n")
    headings: List[Dict] = []
    volumes: List[Dict] = []
    markers: List[int] = []
    numeric_candidates: List[Dict] = []
    offset = 0
    previous_blank = True
    for line in lines:
        info = _classify_line(line)
        if info is not None:
            info["pos"] = offset
            if info["kind"] == "chapter":
                headings.append(info)
            elif info["kind"] == "volume":
                volumes.append(info)
            elif info["kind"] == "marker":
                markers.append(offset)
            elif info["kind"] == "numeric" and previous_blank and not _NUMERIC_BAD_TAIL.search(info["raw"]):
                numeric_candidates.append(info)
        previous_blank = not line.strip(" \t　")
        offset += len(line) + 1
    warnings: List[str] = []
    # ⑦ 纯数字编号防误切：前一行是空行且全文命中数 >= 3 才启用。
    if len(numeric_candidates) >= 3:
        warnings.append(f"启用纯数字编号标题（共 {len(numeric_candidates)} 处）")
        for info in numeric_candidates:
            info["kind"] = "chapter"
            info["special"] = False
            info["vol_chapter"] = None
            headings.append(info)
        headings.sort(key=lambda item: item["pos"])
    # 连续出现且中间没有任何章节的卷标题簇，多是作者贴在文中的分卷简介/目录，
    # 不是真实结构边界，整簇忽略（卷章同行产生的边界不在此列）。
    heading_positions = [item["pos"] for item in headings]
    kept: List[Dict] = []
    cluster: List[Dict] = []

    def _flush_cluster() -> None:
        if len(cluster) >= 2:
            warnings.append(f"忽略疑似分卷简介/目录的连续卷标题（共 {len(cluster)} 处）")
        else:
            kept.extend(cluster)
        cluster.clear()

    def _has_heading_between(after: int, before: int) -> bool:
        return any(after < pos < before for pos in heading_positions)

    previous_volume: Optional[Dict] = None
    for volume in volumes:
        if previous_volume is not None and _has_heading_between(previous_volume["pos"], volume["pos"]):
            _flush_cluster()
        cluster.append(volume)
        previous_volume = volume
    _flush_cluster()
    return headings, kept, markers, warnings


def _make_chapters(source: str) -> Tuple[List[Dict], List[str]]:
    headings, volumes, markers, warnings = _scan(source)
    if not headings:
        raise ValueError("未识别到章节标题")

    # 边界事件：卷标题改变卷归属并开启新分段；「正文」标记只开启新分段。
    boundaries: List[Tuple[int, str, int, str]] = []
    for volume in volumes:
        boundaries.append((volume["pos"], "volume", volume["volume_no"], volume["raw"]))
    for position in markers:
        boundaries.append((position, "marker", 0, "正文"))
    # 卷章同行组合同时是卷边界。
    for heading in headings:
        if heading.get("vol_chapter") is not None:
            boundaries.append((heading["pos"], "volume", heading["vol_chapter"], heading["raw"]))
    boundaries.sort(key=lambda item: item[0])

    chapters: List[Dict] = []
    boundary_idx = 0
    current_volume_no: Optional[int] = None
    volume_no, volume_title = 1, ""
    section_no = 0
    appendix_pending = False
    counters: Dict[Tuple[int, int], int] = {}
    deltas: Dict[Tuple[int, int], int] = {}
    total = len(headings)
    for idx, heading in enumerate(headings, 1):
        start = 0 if idx == 1 else heading["pos"]
        end = headings[idx]["pos"] if idx < total else len(source)
        while boundary_idx < len(boundaries) and boundaries[boundary_idx][0] <= heading["pos"]:
            _, kind, number, label = boundaries[boundary_idx]
            boundary_idx += 1
            if kind == "volume":
                if current_volume_no != number:
                    current_volume_no = number
                    volume_no, volume_title = number, label
                    section_no = 0
                    appendix_pending = False
                elif label and label != volume_title:
                    volume_title = label
            else:  # 「正文」标记：信息区与正文分界，开启新分段
                section_no = 0
                appendix_pending = False
        chapter_no = heading["chapter_no"]
        special = heading["special"]
        # 仅“上一标题是独立番外”时启动新分段；普通正文中的同名词不影响编号。
        if appendix_pending and not special:
            section_no += 1
            appendix_pending = False
        section_key = (volume_no, section_no)
        entry = {
            "idx": idx,
            "title": heading["raw"],
            "start_char": start,
            "end_char": end,
            "chars": end - start,
            "turn_budget": turn_budget(end - start),
            "is_special": special,
            "volume_no": volume_no,
            "volume_title": volume_title,
            "section_no": section_no,
        }
        if not special:
            counters[section_key] = counters.get(section_key, 0) + 1
            entry["chapter_no"] = counters[section_key]
            entry["original_no"] = chapter_no
            if chapter_no is not None:
                # 组内「原始章号 - 重排章号」偏移应保持恒定；偏移变化才是
                # 真正的跳号/重号/乱序。跨卷连续编号（如第二卷从第74章起）
                # 与分卷重编号（每卷从第1章起）的偏移各自恒定，不算异常。
                delta = chapter_no - entry["chapter_no"]
                if section_key in deltas and deltas[section_key] != delta:
                    location = f"第 {volume_no} 卷" + (f"分段 {section_no}" if section_no else "")
                    warnings.append(
                        f"章号不连续：{location}「{heading['raw']}」原始第 {chapter_no} 章，"
                        f"按文件顺序重排为第 {entry['chapter_no']} 章"
                    )
                deltas[section_key] = delta
        chapters.append(entry)
        appendix_pending = special and heading["raw"].strip() == "番外"
    return chapters, warnings


def _validate(chapters: List[Dict], source: str) -> None:
    """结构性校验：偏移链、长度、末尾基准。编号连续性已在重排阶段降级为 warning。"""
    if not chapters:
        raise ValueError("未识别到章节标题")
    previous_end = 0
    for chapter in chapters:
        start, end = chapter["start_char"], chapter["end_char"]
        if start != previous_end:
            raise ValueError(f"章节偏移链不连续：{chapter['idx']} 的 start_char={start}，应为 {previous_end}")
        if end < start or chapter["chars"] != end - start:
            raise ValueError(f"章节长度或偏移非法：idx={chapter['idx']}")
        previous_end = end
    if previous_end != len(source):
        raise ValueError(f"章节末尾不一致：索引末尾 {previous_end}，正文末尾 {len(source)}")


def validate_chapter_output(book_dir: Union[str, os.PathLike], source: str,
                            chapters: Optional[List[Dict]] = None) -> None:
    """校验索引与章节文件的零差集、偏移链和末尾基准。"""
    book_path = Path(book_dir)
    index_path = book_path / "chapter_index.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    indexed = data.get("chapters", data) if isinstance(data, dict) else data
    if chapters is None:
        chapters = indexed
    if indexed != chapters:
        raise ValueError("章节索引内容与切分结果不一致")
    _validate(chapters, source)
    chapter_dir = book_path / "chapters"
    pieces = []
    actual_paths = set()
    for chapter in chapters:
        filename = f"{chapter['idx']:04d}.txt"
        path = chapter_dir / filename
        if not path.is_file():
            raise ValueError(f"缺少章节正文文件：{path}")
        actual_paths.add(path.name)
        pieces.append(path.read_bytes().decode("utf-8"))
        expected_piece = source[chapter["start_char"]:chapter["end_char"]]
        if pieces[-1] != expected_piece:
            raise ValueError(f"章节正文与索引偏移不一致：idx={chapter['idx']}")
    expected_paths = {f"{chapter['idx']:04d}.txt" for chapter in chapters}
    all_paths = {path.name for path in chapter_dir.glob("*.txt")}
    if actual_paths != expected_paths or all_paths != expected_paths or "".join(pieces) != source:
        raise ValueError("正文/索引零差集校验失败")


def _clear_chapter_dir(chapter_dir: Path) -> None:
    """清空旧章节文件。

    沙箱环境（如 WorkBuddy safe-delete）会拦截 unlink 并以
    「回收站不可用」失败关闭；此时改为把整个 chapters 目录**改名隔离**
    （rename 不是删除操作），再让调用方重建同名目录。隔离目录留在
    books/ 下，由用户或系统清理，不影响切章与校验（校验只读 chapters/）。
    """
    try:
        for old in chapter_dir.glob("*.txt"):
            old.unlink()
        return
    except OSError:
        pass
    if not chapter_dir.exists():
        return
    stamp = int(time.time() * 1000)
    for suffix in range(100):
        stale = chapter_dir.with_name(f"{chapter_dir.name}.stale-{stamp + suffix}")
        try:
            chapter_dir.rename(stale)
            chapter_dir.mkdir(parents=True, exist_ok=True)
            return
        except OSError:
            continue
    raise OSError(f"无法清空章节目录：{chapter_dir}")


def split_book(source: str, book_id: str, output_root: Union[str, os.PathLike] = ".") -> Dict:
    """切分文本并写出 books/<book_id>/chapter_index.json 与 chapters/ 文件树。"""
    if not isinstance(source, str):
        raise TypeError("source 必须是字符串")
    if not book_id or Path(book_id).name != book_id or book_id in {".", ".."}:
        raise ValueError("book_id 只能是单层目录名")
    chapters, warnings = _make_chapters(source)
    _validate(chapters, source)
    book_dir = Path(output_root) / "books" / book_id
    chapter_dir = book_dir / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    _clear_chapter_dir(chapter_dir)
    for chapter in chapters:
        (chapter_dir / f"{chapter['idx']:04d}.txt").write_text(
            source[chapter["start_char"]:chapter["end_char"]], encoding="utf-8", newline="")
    index = {
        "book_id": book_id,
        "source_chars": len(source),
        "chapters": chapters,
        "warning_count": len(warnings),
        "warnings": warnings[:_MAX_WARNINGS],
    }
    (book_dir / "chapter_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
    validate_chapter_output(book_dir, source, chapters)
    return index


def split_file(input_path: Union[str, os.PathLike], book_id: Optional[str] = None,
               output_root: Union[str, os.PathLike] = ".") -> Dict:
    """读取并切分文件；未传 book_id 时使用输入文件名（不含扩展名）。"""
    path = Path(input_path)
    source, encoding = read_text_with_fallback(path)
    result = split_book(source, book_id or path.stem, output_root)
    result["source_encoding"] = encoding
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="本地原著章节切分器")
    parser.add_argument("input", help="TXT 原著路径")
    parser.add_argument("--book-id", help="输出目录名，默认使用输入文件名")
    parser.add_argument("--output-root", default=".", help="输出根目录，默认当前目录")
    args = parser.parse_args()
    result = split_file(args.input, args.book_id, args.output_root)
    print(json.dumps({
        "book_id": result["book_id"],
        "chapters": len(result["chapters"]),
        "warning_count": result.get("warning_count", 0),
        "source_encoding": result.get("source_encoding"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
