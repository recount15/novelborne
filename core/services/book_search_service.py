"""Read-only deterministic reader search; no application/model imports or disk cache.

The caller must resolve/authorize book_dir (never accept it from an HTTP client).
Only chapter_index.json and numeric chapters/*.txt beneath that directory are read.
Coordinates are original chapter Unicode codepoint half-open offsets, including
headings/newlines. Overlap policy: all exact starts; fuzzy selects the best end
(distance, length delta, shortest end) per normalized start. Identical original
spans produced by Unicode expansion are deduplicated. Different starts may overlap.
Run search_occurrences in a bounded worker, not on an async event loop.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata as ud
from pathlib import Path

INDEX_VERSION = "reader-occurrences-v1"
OVERLAP_POLICY = "all-starts-best-end-deduplicate-original-spans"


class SearchQueryError(ValueError):
    """Invalid query/options; HTTP integration should return 422."""


class BookSourceError(ValueError):
    """Missing, unreadable, unsafe or inconsistent book sources; return 409."""


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def normalize_with_mapping(text, *, ignore_whitespace=False, ignore_punctuation=False):
    """NFKC then casefold, with source spans for every output codepoint.

    Tagged canonical decomposition/reordering/composition handles combining marks,
    compatibility expansions and Hangul composition (per-character NFKC does not).
    Removed punctuation/whitespace between a hit's endpoints stays in its span.
    """
    decomposed = []
    for i, char in enumerate(text):
        for part in ud.normalize("NFKD", char):
            item = (part, i, i + 1)
            decomposed.append(item)
            cc = ud.combining(part)
            j = len(decomposed) - 1
            while cc and j and ud.combining(decomposed[j - 1][0]) > cc:
                decomposed[j] = decomposed[j - 1]
                j -= 1
                decomposed[j] = item
    composed = []
    starter = None
    last_cc = 0
    for char, start, end in decomposed:
        cc = ud.combining(char)
        replacement = None
        if starter is not None and (last_cc < cc or last_cc == 0):
            candidate = ud.normalize("NFC", composed[starter][0] + char)
            if len(candidate) == 1:
                replacement = candidate
        if replacement is not None:
            _, a, b = composed[starter]
            composed[starter] = (replacement, min(a, start), max(b, end))
        else:
            if cc == 0:
                starter = len(composed)
            composed.append((char, start, end))
            last_cc = cc
    chars, spans = [], []
    for char, start, end in composed:
        for folded in char.casefold():
            if ignore_whitespace and folded.isspace():
                continue
            if ignore_punctuation and ud.category(folded).startswith("P"):
                continue
            chars.append(folded)
            spans.append((start, end))
    return "".join(chars), spans


def _safe_path(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise BookSourceError("book source escapes authorized directory")
    return resolved


def _load_sources(book_dir, expected_book_id):
    try:
        root = Path(book_dir).resolve(strict=True)
        if not root.is_dir():
            raise BookSourceError("book directory is missing")
        index_path = _safe_path(root, root / "chapter_index.json")
        data = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        if not isinstance(data, dict):
            raise BookSourceError("invalid chapter index")
        book_id = str(data.get("book_id") or expected_book_id or root.name)
        if expected_book_id is not None and book_id != str(expected_book_id):
            raise BookSourceError("book identity does not match authorized book")
        folder = _safe_path(root, root / "chapters")
        files = {}
        for path in folder.glob("*.txt"):
            if not path.stem.isascii() or not path.stem.isdigit():
                raise BookSourceError("chapter filenames must be numeric")
            number = int(path.stem)
            if number < 1 or number in files:
                raise BookSourceError("duplicate or invalid chapter number")
            files[number] = _safe_path(root, path)
        inventory = data.get("chapters", [])
        if not isinstance(inventory, list):
            raise BookSourceError("invalid chapter inventory")
        numbers = []
        for pos, item in enumerate(inventory):
            if not isinstance(item, dict):
                raise BookSourceError("invalid chapter metadata")
            # Reader directory convention: idx, legacy chapter, then position + 1.
            number = item.get("idx") or item.get("chapter") or pos + 1
            if isinstance(number, bool) or not str(number).isascii() or not str(number).isdigit():
                raise BookSourceError("invalid chapter index number")
            numbers.append(int(number))
        if inventory and (len(set(numbers)) != len(numbers) or set(numbers) != set(files)):
            raise BookSourceError("chapter inventory does not match source files")
        if not files:
            raise BookSourceError("book has no chapter sources")
        sources = []
        for number in sorted(files):
            # Match existing reader/book_index universal-newline text convention.
            text = files[number].read_text(encoding="utf-8")
            sources.append((number, text))
        return book_id, sources
    except BookSourceError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise BookSourceError("cannot read valid book sources") from exc


def _distance(a, b, budget):
    """Banded Levenshtein; returns budget+1 when outside the threshold."""
    if abs(len(a) - len(b)) > budget:
        return budget + 1
    inf = budget + 1
    previous = {j: j for j in range(min(len(b), budget) + 1)}
    for i, char in enumerate(a, 1):
        current = {0: i} if i <= budget else {}
        for j in range(max(1, i - budget), min(len(b), i + budget) + 1):
            current[j] = min(previous.get(j, inf) + 1, current.get(j - 1, inf) + 1,
                             previous.get(j - 1, inf) + (char != b[j - 1]))
        if min(current.values(), default=inf) > budget:
            return inf
        previous = current
    return previous.get(len(b), inf)


def _candidate_starts(text, query, budget):
    # Pigeonhole filter: <=k edits leave at least one of k+1 disjoint
    # query n-grams intact. Account for every possible +/-k alignment shift.
    # Unlike heuristic shared-gram cutoffs this cannot discard a legal match.
    count = budget + 1
    starts = set()
    for part in range(count):
        offset = part * len(query) // count
        end = (part + 1) * len(query) // count
        gram = query[offset:end]
        at = text.find(gram)
        while at >= 0:
            for delta in range(-budget, budget + 1):
                start = at - offset + delta
                if 0 <= start < len(text):
                    starts.add(start)
            at = text.find(gram, at + 1)
    return sorted(starts)


def _matches(text, query, budget):
    if not budget:
        start = text.find(query)
        while start >= 0:
            yield start, start + len(query), 0
            start = text.find(query, start + 1)
        return
    for start in _candidate_starts(text, query, budget):
        best = None
        for length in range(max(1, len(query) - budget), len(query) + budget + 1):
            end = start + length
            if end > len(text):
                break
            distance = _distance(query, text[start:end], budget)
            if distance <= budget:
                choice = (distance, abs(length - len(query)), end)
                if best is None or choice < best:
                    best = choice
        if best is not None:
            yield start, best[2], best[0]


def search_occurrences(book_dir: str | Path, query: str, *, book_id: str | None = None,
                       mode: str = "exact", page: int = 1, page_size: int = 20,
                       ignore_punctuation: bool = False,
                       ignore_whitespace: bool = False) -> dict:
    """Search all chapters in an authorized directory; no writes or model calls.

    Stable order: score descending, actual chapter number, original start/end.
    total_hits counts verified, deduplicated matches before pagination (no top-k).
    source_hash uses the existing preparation source-list hash convention.
    index_hash additionally binds normalization/options and the algorithm version.
    """
    if not isinstance(query, str) or not 1 <= len(query) <= 200 or not query.strip():
        raise SearchQueryError("query must contain 1–200 Unicode codepoints")
    if mode not in ("exact", "fuzzy"):
        raise SearchQueryError("mode must be exact or fuzzy")
    if type(page) is not int or page < 1:
        raise SearchQueryError("page must be a positive integer")
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise SearchQueryError("page_size must be between 1 and 100")
    if type(ignore_punctuation) is not bool or type(ignore_whitespace) is not bool:
        raise SearchQueryError("normalization options must be booleans")
    options = dict(ignore_whitespace=ignore_whitespace, ignore_punctuation=ignore_punctuation)
    normalized_query, _ = normalize_with_mapping(query, **options)
    if not normalized_query:
        raise SearchQueryError("query is empty after normalization")
    identity, sources = _load_sources(book_dir, book_id)
    source_hash = _hash([{"chapter_no": no,
                          "checksum": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                          "chars": len(text)} for no, text in sources])
    index_hash = _hash([source_hash, INDEX_VERSION, ud.unidata_version, options])
    budget = min(2, len(normalized_query) // 4) if mode == "fuzzy" else 0
    hits = []
    for chapter_no, original in sources:
        normalized, mapping = normalize_with_mapping(original, **options)
        unique = {}
        for a, b, distance in _matches(normalized, normalized_query, budget):
            # Canonical reorder can make tagged source coordinates non-monotonic.
            start = min(span[0] for span in mapping[a:b])
            end = max(span[1] for span in mapping[a:b])
            score = round(1 - distance / len(normalized_query), 8)
            if (start, end) in unique and unique[start, end]["score"] >= score:
                continue
            excerpt_start = max(0, start - 60)
            excerpt_end = min(len(original), end + 60)
            # Trim only context whitespace, never whitespace inside the hit.
            while excerpt_start < start and original[excerpt_start].isspace():
                excerpt_start += 1
            while excerpt_end > end and original[excerpt_end - 1].isspace():
                excerpt_end -= 1
            unique[start, end] = {
                "hit_id": "hit_" + _hash([identity, index_hash, normalized_query, mode,
                                          chapter_no, start, end]),
                "chapter_no": chapter_no, "start": start, "end": end,
                "excerpt": original[excerpt_start:excerpt_end], "excerpt_start": excerpt_start,
                "match_type": "exact" if distance == 0 else "fuzzy", "score": score,
            }
        hits.extend(unique.values())
    hits.sort(key=lambda hit: (-hit["score"], hit["chapter_no"], hit["start"], hit["end"]))
    total = len(hits)
    offset = (page - 1) * page_size
    return {"book_id": identity, "source_hash": source_hash, "index_version": INDEX_VERSION,
            "index_hash": index_hash, "query": query, "normalized_query": normalized_query,
            "mode": mode, "effective_mode": "fuzzy" if budget else "exact",
            "edit_budget": budget, "normalization": options, "overlap_policy": OVERLAP_POLICY,
            "warnings": (["short_query_exact_only"] if mode == "fuzzy" and len(normalized_query) <= 2 else []),
            "page": page, "page_size": page_size, "total_hits": total,
            "has_more": offset + page_size < total, "hits": hits[offset:offset + page_size]}
