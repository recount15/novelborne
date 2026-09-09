"""Private sidecar recovery for local Novelborne data.

This tool intentionally contains no recovery archive and no IP data. It only restores a
user-supplied archive from a private local path after validating its manifest and paths.
It is safe to ship with the application because it never downloads or embeds private data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys
import tempfile
from typing import Any
from zipfile import ZipFile, BadZipFile

PRIVATE_DIR = Path(os.environ.get(
    "NOVELBORNE_PRIVATE_RECOVERY_DIR",
    Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Novelborne" / "private-recovery",
))
DEFAULT_ARCHIVE = PRIVATE_DIR / "Novelborne-local-recovery.zip"
MANIFEST_NAMES = ("recovery_manifest.json", "manifest.json", "META-INF/recovery_manifest.json")


class RecoveryError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or path.parts[0] in {"", "."}:
        raise RecoveryError(f"恢复包含非法路径：{name!r}")
    return path


def find_manifest(archive: ZipFile) -> tuple[str | None, dict[str, Any] | None]:
    for name in MANIFEST_NAMES:
        try:
            return name, json.loads(archive.read(name).decode("utf-8"))
        except KeyError:
            continue
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RecoveryError(f"恢复清单无效：{name}: {exc}") from exc
    return None, None


def component_for(name: str) -> str:
    path = safe_member(name)
    parts = path.parts
    if parts[0] == "ip_vault" and len(parts) > 2:
        return parts[2] if parts[1] == "assets" else parts[1]
    if parts[0] == "var":
        return "database" if "db" in parts else "runtime"
    return parts[0]


def build_manifested_sidecar(source: Path, output: Path) -> dict[str, Any]:
    """Create a local-only v2 archive with a per-file SHA-256 manifest.

    The command never writes to the repository and must only be used for data the user is
    authorized to retain privately. Public release tooling must never reference its output.
    """
    from zipfile import ZIP_DEFLATED, ZipFile

    source_report = inspect(source)
    files: list[dict[str, Any]] = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(source) as source_zip, ZipFile(output, "w", compression=ZIP_DEFLATED) as target_zip:
        for entry in source_zip.infolist():
            if entry.is_dir():
                continue
            safe_member(entry.filename)
            payload = source_zip.read(entry.filename)
            target_zip.writestr(entry.filename, payload)
            files.append({
                "path": entry.filename,
                "component": component_for(entry.filename),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
        manifest = {
            "format": "novelborne-private-recovery-v2",
            "source_sha256": source_report["sha256"],
            "files": files,
        }
        target_zip.writestr("recovery_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"source": str(source), "output": str(output), "entries": len(files), "sha256": sha256_file(output)}


def inspect(archive_path: Path) -> dict[str, Any]:
    if not archive_path.is_file():
        raise RecoveryError(f"未找到私有恢复包：{archive_path}")
    try:
        with ZipFile(archive_path) as archive:
            bad = archive.testzip()
            if bad:
                raise RecoveryError(f"恢复包完整性失败：{bad}")
            manifest_name, manifest = find_manifest(archive)
            files = [entry for entry in archive.infolist() if not entry.is_dir()]
            components: dict[str, int] = {}
            for entry in files:
                safe_member(entry.filename)
                component = component_for(entry.filename)
                components[component] = components.get(component, 0) + 1
            return {
                "archive": str(archive_path),
                "sha256": sha256_file(archive_path),
                "entries": len(files),
                "components": components,
                "manifest": manifest_name,
                "manifest_valid": manifest is not None,
            }
    except BadZipFile as exc:
        raise RecoveryError(f"恢复包不是有效 ZIP：{exc}") from exc


def validate_manifest(archive: ZipFile, manifest: dict[str, Any] | None) -> None:
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        raise RecoveryError("恢复清单缺少 files 列表")
    expected = {str(item.get("path")): item for item in files if isinstance(item, dict) and item.get("path")}
    actual = {entry.filename for entry in archive.infolist() if not entry.is_dir() and entry.filename not in MANIFEST_NAMES}
    if actual != set(expected):
        raise RecoveryError("恢复清单与 ZIP 文件列表不一致")
    for name, item in expected.items():
        safe_member(name)
        payload = archive.read(name)
        if len(payload) != int(item.get("size", -1)) or hashlib.sha256(payload).hexdigest() != item.get("sha256"):
            raise RecoveryError(f"恢复文件校验失败：{name}")


def selected_members(archive: ZipFile, components: set[str]) -> list[str]:
    result: list[str] = []
    for entry in archive.infolist():
        if entry.is_dir():
            continue
        safe_member(entry.filename)
        if component_for(entry.filename) in components:
            result.append(entry.filename)
    return result


def validate_sqlite(path: Path) -> None:
    if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        return
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RecoveryError(f"SQLite 完整性检查失败：{path.name}: {row}")
    finally:
        connection.close()


def restore(archive_path: Path, target: Path, components: set[str], *, overwrite: bool, dry_run: bool) -> dict[str, Any]:
    report = inspect(archive_path)
    if not report["manifest_valid"]:
        raise RecoveryError("恢复包缺少可验证 manifest；仅允许 inspect，不允许写入恢复")
    if not components:
        raise RecoveryError("至少选择一个恢复组件")
    target = target.resolve()
    restored: list[str] = []
    skipped: list[str] = []
    with ZipFile(archive_path) as archive:
        _manifest_name, manifest = find_manifest(archive)
        validate_manifest(archive, manifest)
        names = selected_members(archive, components)
        if not names:
            raise RecoveryError("所选组件在恢复包中不存在")
        for name in names:
            # Private archive keeps data below ip_vault; restoring drops only that wrapper.
            relative = safe_member(name)
            if relative.parts[0] == "ip_vault":
                relative = PurePosixPath(*relative.parts[1:])
            destination = (target / Path(*relative.parts)).resolve()
            if not destination.is_relative_to(target):
                raise RecoveryError(f"恢复目标越界：{name}")
            if destination.exists() and not overwrite:
                skipped.append(str(relative))
                continue
            if dry_run:
                restored.append(str(relative))
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, tempfile.NamedTemporaryFile(delete=False, dir=destination.parent) as temp:
                shutil.copyfileobj(source, temp)
                temp_path = Path(temp.name)
            try:
                validate_sqlite(temp_path)
                temp_path.replace(destination)
            finally:
                temp_path.unlink(missing_ok=True)
            restored.append(str(relative))
    report.update({"target": str(target), "restored": restored, "skipped": skipped, "dry_run": dry_run})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Novelborne 私有恢复包校验与选择恢复")
    parser.add_argument("command", choices=("inspect", "restore", "make-sidecar"))
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--target", type=Path, default=Path.cwd())
    parser.add_argument("--components", default="")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            output = inspect(args.archive)
        elif args.command == "make-sidecar":
            if not args.output:
                raise RecoveryError("make-sidecar 需要 --output")
            output = build_manifested_sidecar(args.archive, args.output)
        else:
            components = {item.strip() for item in args.components.split(",") if item.strip()}
            output = restore(args.archive, args.target, components, overwrite=args.overwrite, dry_run=args.dry_run)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except RecoveryError as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
