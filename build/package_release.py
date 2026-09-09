"""Fail-closed local public packaging; does not build, run, upload, or delete files."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import zipfile

from asset_gate import PUBLIC_ASSET_FILES, public_asset_datas

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PARTS = {'var', 'backups', 'evidence', 'novels', 'uploads', 'saves', '.git', '.mimosa'}
BLOCKED_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.log', '.pem', '.key', '.env'}
PUBLIC_CERTIFICATES = {'_internal/certifi/cacert.pem', '_internal/grpc/_cython/_credentials/roots.pem'}


def private_path(path: Path, source: Path) -> bool:
    relative = path.relative_to(source)
    parts = tuple(part.lower() for part in relative.parts)
    # SDK source modules named uploads are code, not user uploads.
    sdk_upload = (len(parts) >= 4 and parts[:2] == ('_internal', 'openai')
                  and parts[2] in {'resources', 'types'} and parts[3] == 'uploads'
                  and (path.suffix == '.py' or (len(parts) == 4 and path.is_dir())))
    if set(parts) & BLOCKED_PARTS and not sdk_upload:
        return True
    if relative.as_posix() in PUBLIC_CERTIFICATES:
        return b'PRIVATE KEY' in path.read_bytes()
    return path.suffix.lower() in BLOCKED_SUFFIXES or path.name.lower().startswith('.env')


def checked_files(source: Path) -> list[Path]:
    source = source.resolve()
    source.relative_to(ROOT)
    if not (source / 'FateEngine.exe').is_file():
        raise ValueError('FateEngine.exe is missing')
    # Always public mode, even when the caller inherited a private override.
    approved = public_asset_datas(ROOT, allow_private=False)
    asset_root = source / '_internal' / 'assets'
    actual_assets = {p.relative_to(asset_root).as_posix() for p in asset_root.rglob('*') if p.is_file()}
    if actual_assets != set(PUBLIC_ASSET_FILES):
        raise ValueError('Bundled asset inventory differs from allowlist')
    for original, target in approved:
        original = Path(original)
        bundled = source / '_internal' / target / original.name
        if hashlib.sha256(original.read_bytes()).digest() != hashlib.sha256(bundled.read_bytes()).digest():
            raise ValueError('Bundled asset differs from approved workspace asset')
    files = []
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if path.is_symlink():
            raise ValueError('Symlinks are not packageable')
        if private_path(path, source):
            raise ValueError('Runtime or credential path in package')
        if path.is_file():
            files.append(path)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New workspace .zip path')
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    output.relative_to(ROOT)
    if output == source or source in output.parents:
        raise ValueError('Archive must be outside the input tree')
    if output.suffix.lower() != '.zip' or output.exists():
        raise ValueError('Output must be a new .zip file; overwriting is forbidden')
    files = checked_files(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, Path('FateEngine') / path.relative_to(source))
    print('Local package created. No executable was started; nothing was uploaded.')


if __name__ == '__main__':
    main()
