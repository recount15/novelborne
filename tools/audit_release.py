"""Fail closed when a public release contains private or runtime data."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
from typing import Iterable
import zipfile


MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 3

PRIVATE_DIR_NAMES = {".zcode", "ip_vault", "private-recovery"}
RUNTIME_ROOT_NAMES = {"archive", "outputs", "var"}
RUNTIME_DIR_NAMES = {"books", "db", "logs", "saves", "sessions", "uploads"}
RUNTIME_SUFFIXES = {".db", ".jsonl", ".log", ".shm", ".sqlite", ".sqlite3", ".wal"}
ARCHIVE_SUFFIXES = {".7z", ".tar", ".tgz", ".zip"}
CREDENTIAL_FILE_NAMES = {
    ".env",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
}
CREDENTIAL_SUFFIXES = {".key", ".p12", ".pfx"}

# Keep the literal marker out of this source file so a source-release audit does
# not identify its own detector as a credential.
_PRIVATE_KEY_MARKER = b"-----BEGIN " + b"PRIVATE KEY-----"
_CREDENTIAL_PATTERNS = (
    re.compile(rb"(?i)(?:github_pat_|gh[pousr]_|glpat-|sk-)[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(
        rb"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|secret[_-]?key)"
        rb"\s*[\"']?\s*[:=]\s*[\"']([^\"'\s,;}]{8,})"
    ),
    re.compile(rb"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s/:]+:[^\s/@]+@"),
)
_PRIVATE_PATH_PATTERNS = (
    re.compile(rb"(?i)[A-Z]:[\\/]Users[\\/][^\\/:*?\"<>|\r\n]+[\\/]"),
    re.compile(rb"/(?:home|Users)/[^/\x00\r\n]+/"),
    re.compile(rb"\\\\[^\\\s]+\\[^\\\s]+\\"),
)
_PLACEHOLDERS = {
    b"changeme",
    b"dummy",
    b"example",
    b"none",
    b"null",
    b"placeholder",
    b"redacted",
    b"test",
    b"your_api_key",
    b"your_token",
}
_CHINESE_CHAPTER = re.compile(r"(?m)^\s*第[0-9一二三四五六七八九十百千零〇两]{1,10}[章节回卷]\s*[^\n]{0,80}$")
_ENGLISH_CHAPTER = re.compile(r"(?im)^\s*chapter\s+(?:[0-9]{1,5}|[ivxlcdm]{1,12})\b[^\n]{0,80}$")
_MANUSCRIPT_SOURCE_MARKERS = tuple(
    marker.encode("utf-8")
    for marker in (
        "校对版全本",
        "本书由",
        "更多精校小说",
        "仅供个人学习交流",
    )
)


@dataclass(frozen=True, order=True)
class Finding:
    """A redacted release-audit finding."""

    location: str
    rule: str
    sha256: str | None = None


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalise_parts(name: str) -> tuple[str, ...]:
    cleaned = name.replace("\\", "/")
    return tuple(part.casefold() for part in PurePosixPath(cleaned).parts if part not in {"", "."})


def _contains_parts(parts: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    width = len(sequence)
    return any(parts[index:index + width] == sequence for index in range(len(parts) - width + 1))


def _path_rule(name: str, *, is_dir: bool = False) -> str | None:
    parts = _normalise_parts(name)
    if not parts:
        return None
    if any(part == ".." for part in parts) or name.startswith(("/", "\\")):
        return "unsafe archive/path traversal"
    if any(part in PRIVATE_DIR_NAMES for part in parts):
        return "private path"
    if any(part in RUNTIME_ROOT_NAMES for part in parts):
        return "runtime data path"
    if parts[0] in RUNTIME_DIR_NAMES or (len(parts) > 1 and parts[0] == "_internal" and parts[1] in RUNTIME_DIR_NAMES):
        return "runtime data path"
    if _contains_parts(parts, ("assets", "data", "characters", "user")):
        return "user-generated asset path"
    if _contains_parts(parts, ("assets", "data", "samples")):
        return "sample/manuscript asset path"
    if is_dir:
        return None

    basename = parts[-1]
    suffix = Path(basename).suffix.casefold()
    if basename in CREDENTIAL_FILE_NAMES or basename.startswith(".env.") or suffix in CREDENTIAL_SUFFIXES:
        return "credential-bearing filename"
    if suffix in RUNTIME_SUFFIXES:
        return "runtime data file"
    if suffix in {".epub", ".mobi"}:
        return "manuscript file"
    if re.search(r"第[0-9一二三四五六七八九十百千零〇两]+[章节回卷]", basename):
        return "manuscript filename marker"
    if any(marker in basename for marker in ("全本", "校对版", "精校版")):
        return "manuscript filename marker"
    return None


def _credential_content_rule(data: bytes) -> str | None:
    if _PRIVATE_KEY_MARKER in data:
        return "private key content"
    for index, pattern in enumerate(_CREDENTIAL_PATTERNS):
        match = pattern.search(data)
        if match is None:
            continue
        if index == 2 and match.group(1).strip().casefold() in _PLACEHOLDERS:
            continue
        return "credential content"
    return None


def _content_rules(data: bytes) -> list[str]:
    rules: list[str] = []
    credential_rule = _credential_content_rule(data)
    if credential_rule:
        rules.append(credential_rule)
    if any(pattern.search(data) for pattern in _PRIVATE_PATH_PATTERNS):
        rules.append("private local path content")
    if data.startswith(b"SQLite format 3\x00"):
        rules.append("SQLite runtime data content")
    if any(marker in data for marker in _MANUSCRIPT_SOURCE_MARKERS):
        rules.append("manuscript distribution marker")

    if len(data) <= MAX_TEXT_BYTES:
        text = _decode_text(data)
        if text:
            chapter_count = len(_CHINESE_CHAPTER.findall(text)) + len(_ENGLISH_CHAPTER.findall(text))
            if chapter_count >= 8:
                rules.append("manuscript chapter sequence")
    return rules


def _decode_text(data: bytes) -> str | None:
    if not data:
        return ""
    if b"\x00" in data[:4096]:
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _archive_kind(name: str, data: bytes | None = None) -> str | None:
    lowered = name.casefold()
    if lowered.endswith(".tar.gz") or lowered.endswith(".tgz"):
        return "tar"
    suffix = Path(lowered).suffix
    if suffix in ARCHIVE_SUFFIXES:
        return suffix.lstrip(".")
    if data is not None:
        if data.startswith(b"PK\x03\x04"):
            return "zip"
        if data.startswith(b"7z\xbc\xaf'\x1c"):
            return "7z"
    return None


def _join_location(container: str, member: str) -> str:
    return f"{container}!{member.replace(chr(92), '/')}"


def _scan_payload(data: bytes, location: str, depth: int) -> list[Finding]:
    digest = _fingerprint(data)
    found = [Finding(location, rule, digest) for rule in _content_rules(data)]
    kind = _archive_kind(location, data)
    if kind:
        if depth >= MAX_ARCHIVE_DEPTH:
            found.append(Finding(location, "archive nesting limit exceeded", digest))
        else:
            found.extend(_scan_archive(io.BytesIO(data), location, kind, depth + 1, digest))
    return found


def _scan_archive(source: Path | io.BytesIO, location: str, kind: str, depth: int, digest: str) -> list[Finding]:
    try:
        if kind == "zip":
            members = _read_zip_members(source)
        elif kind == "tar":
            members = _read_tar_members(source)
        elif kind == "7z":
            members = _read_7z_members(source)
        else:
            return [Finding(location, "unsupported archive format", digest)]
        return _scan_archive_members(location, members, depth)
    except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile):
        return [Finding(location, "unreadable archive", digest)]
    except ImportError:
        return [Finding(location, "7z audit dependency unavailable", digest)]
    except Exception:
        # Archive parser errors can include member data in their messages. Keep
        # output redacted and fail closed.
        return [Finding(location, "unreadable archive", digest)]


def _scan_archive_members(location: str, members: Iterable[tuple[str, bytes | None]], depth: int) -> list[Finding]:
    found: list[Finding] = []
    total = 0
    for name, data in members:
        member_location = _join_location(location, name)
        path_rule = _path_rule(name, is_dir=data is None)
        if path_rule:
            found.append(Finding(member_location, path_rule, _fingerprint(data) if data is not None else None))
        if data is None or path_rule:
            continue
        total += len(data)
        if len(data) > MAX_ARCHIVE_MEMBER_BYTES or total > MAX_ARCHIVE_TOTAL_BYTES:
            found.append(Finding(member_location, "archive extraction limit exceeded", _fingerprint(data)))
            continue
        found.extend(_scan_payload(data, member_location, depth))
    return found


def _read_zip_members(source: Path | io.BytesIO) -> Iterable[tuple[str, bytes | None]]:
    with zipfile.ZipFile(source) as archive:
        declared_total = sum(info.file_size for info in archive.infolist())
        if declared_total > MAX_ARCHIVE_TOTAL_BYTES:
            raise ValueError("archive too large")
        for info in archive.infolist():
            if info.is_dir():
                yield info.filename, None
            elif info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                raise ValueError("archive member too large")
            else:
                yield info.filename, archive.read(info)


def _read_tar_members(source: Path | io.BytesIO) -> Iterable[tuple[str, bytes | None]]:
    kwargs = {"fileobj": source, "mode": "r:*"} if isinstance(source, io.BytesIO) else {"name": str(source), "mode": "r:*"}
    with tarfile.open(**kwargs) as archive:
        declared_total = sum(member.size for member in archive.getmembers() if member.isfile())
        if declared_total > MAX_ARCHIVE_TOTAL_BYTES:
            raise ValueError("archive too large")
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                yield member.name, b""
            elif not member.isfile():
                yield member.name, None
            elif member.size > MAX_ARCHIVE_MEMBER_BYTES:
                raise ValueError("archive member too large")
            else:
                handle = archive.extractfile(member)
                yield member.name, handle.read() if handle is not None else b""


def _read_7z_members(source: Path | io.BytesIO) -> Iterable[tuple[str, bytes | None]]:
    import py7zr
    from py7zr.io import BytesIOFactory

    with py7zr.SevenZipFile(source, mode="r") as archive:
        infos = archive.list()
        declared_total = sum(info.uncompressed or 0 for info in infos if not info.is_directory)
        if declared_total > MAX_ARCHIVE_TOTAL_BYTES:
            raise ValueError("archive too large")
        factory = BytesIOFactory(limit=MAX_ARCHIVE_TOTAL_BYTES)
        archive.extractall(factory=factory)
        for info in infos:
            if info.is_directory:
                yield info.filename, None
            elif (info.uncompressed or 0) > MAX_ARCHIVE_MEMBER_BYTES:
                raise ValueError("archive member too large")
            else:
                buffer = factory.get(info.filename)
                buffer.seek(0)
                yield info.filename, buffer.read()


def audit(root: Path) -> list[Finding]:
    """Return redacted findings for a directory or release archive."""

    root = root.resolve()
    if root.is_file():
        rule = _path_rule(root.name)
        if rule:
            return [Finding(root.name, rule, _hash_file(root))]
        return _scan_file(root, root.name)

    found: list[Finding] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        rule = _path_rule(relative, is_dir=path.is_dir())
        if path.is_symlink():
            found.append(Finding(relative, "symbolic link in release"))
        elif rule:
            found.append(Finding(relative, rule, _hash_file(path) if path.is_file() else None))
        elif path.is_file():
            found.extend(_scan_file(path, relative))
    return sorted(set(found))


def _scan_file(path: Path, location: str) -> list[Finding]:
    kind = _archive_kind(location)
    if kind:
        digest = _hash_file(path)
        return _scan_archive(path, location, kind, 0, digest)
    if path.stat().st_size > MAX_TEXT_BYTES:
        return _scan_large_file(path, location)
    return _scan_payload(path.read_bytes(), location, 0)


def _scan_large_file(path: Path, location: str) -> list[Finding]:
    digest = hashlib.sha256()
    overlap = b""
    rules: set[str] = set()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            window = overlap + chunk
            credential_rule = _credential_content_rule(window)
            if credential_rule:
                rules.add(credential_rule)
            if any(pattern.search(window) for pattern in _PRIVATE_PATH_PATTERNS):
                rules.add("private local path content")
            if any(marker in window for marker in _MANUSCRIPT_SOURCE_MARKERS):
                rules.add("manuscript distribution marker")
            overlap = window[-4096:]
    fingerprint = digest.hexdigest()
    return [Finding(location, rule, fingerprint) for rule in sorted(rules)]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def violations(root: Path) -> list[Path]:
    """Backward-compatible path-only audit API."""

    return [Path(item.location) for item in audit(root)]


def version_violations(root: Path, expected: str) -> list[str]:
    """Check release metadata used by the Windows build without importing the app."""

    problems: list[str] = []
    package_path = root / "frontend" / "package.json"
    lock_path = root / "frontend" / "package-lock.json"
    server_path = root / "core" / "server.py"
    notes_path = root / "docs" / f"RELEASE_NOTES_v{expected}.md"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        if package.get("version") != expected:
            problems.append("frontend/package.json")
    except (OSError, ValueError):
        problems.append("frontend/package.json")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock.get("version") != expected or lock.get("packages", {}).get("", {}).get("version") != expected:
            problems.append("frontend/package-lock.json")
    except (OSError, ValueError):
        problems.append("frontend/package-lock.json")
    try:
        server_source = server_path.read_text(encoding="utf-8")
        match = re.search(r"FastAPI\([^\n]*\bversion\s*=\s*[\"']([^\"']+)[\"']", server_source)
        if match is None or match.group(1) != expected:
            problems.append("core/server.py FastAPI version")
    except OSError:
        problems.append("core/server.py FastAPI version")
    if not notes_path.is_file():
        problems.append(notes_path.relative_to(root).as_posix())
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit release content without printing matched values")
    parser.add_argument("path", nargs="?", type=Path, help="release directory or archive")
    parser.add_argument("--check-version", metavar="VERSION", help="check build-facing version metadata")
    args = parser.parse_args()
    root = (args.path or Path(__file__).resolve().parents[1]).resolve()

    if args.check_version:
        bad_versions = version_violations(root, args.check_version)
        if bad_versions:
            print(f"Version check failed for {args.check_version}:", file=sys.stderr)
            for item in bad_versions:
                print(f"- {item}", file=sys.stderr)
            return 1
        print(f"Version check passed: {args.check_version}")
        return 0

    if not root.exists() or not (root.is_dir() or root.is_file()):
        print(f"Release target does not exist: {root}", file=sys.stderr)
        return 2
    bad = audit(root)
    if bad:
        print("Release audit failed; matched values are redacted:", file=sys.stderr)
        for item in bad:
            fingerprint = f" sha256={item.sha256}" if item.sha256 else ""
            print(f"- {item.location} [{item.rule}]{fingerprint}", file=sys.stderr)
        return 1
    print(f"Release audit passed: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
