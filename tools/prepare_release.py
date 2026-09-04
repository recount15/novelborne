#!/usr/bin/env python3
"""发布前清理和打包脚本。

清理所有敏感信息、运行数据和个人文件，准备干净的发布包。

Usage:
    python tools/prepare_release.py --version 2.1.0
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


# 必须清理的目录（包含运行数据和敏感信息）
CLEAN_DIRS = [
    "var",
    "outputs", 
    "ip_vault",
    "build/FateEngine",
    "build/FateEngineWindowed",
    "build/pkg",
    "build/pkg_win",
    "frontend/node_modules",
    "frontend/android",
    "frontend/.capacitor",
    "assets/data/characters/user",  # 用户自建角色卡
    ".pytest_cache",
    ".zcode",
    "gui-test-screenshots",
    "private-recovery",
    "archive/private-recovery",
]


# 必须清理的文件模式
CLEAN_FILES = [
    "*.pyc",
    "*.pyo",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "*.log",
    ".DS_Store",
    "Thumbs.db",
    "*.local",
    ".env",
    ".env.*",
    "config.json",
    "server.log",
    "WORKLOG_*.md",
    "m3_*.py",
    "verify_*.py",
    "M3_TEST_CHECKLIST.md",
    "*.spec.bak",
    "Novelborne-local-recovery*.zip",
]


def clean_directory(path: Path, dry_run: bool = False) -> int:
    """清理目录"""
    if not path.exists():
        return 0
    
    if dry_run:
        print(f"  Would delete directory: {path.relative_to(ROOT)}")
        return 1
    else:
        print(f"  Deleting directory: {path.relative_to(ROOT)}")
        shutil.rmtree(path, ignore_errors=True)
        return 1


def clean_files(pattern: str, dry_run: bool = False) -> int:
    """清理匹配模式的文件"""
    count = 0
    for path in ROOT.rglob(pattern):
        if path.is_file():
            if dry_run:
                print(f"  Would delete: {path.relative_to(ROOT)}")
            else:
                print(f"  Deleting: {path.relative_to(ROOT)}")
                path.unlink(missing_ok=True)
            count += 1
    return count


def verify_version_consistency(version: str) -> list[str]:
    """验证版本号一致性"""
    issues: list[str] = []
    
    # 检查 package.json
    pkg_json = ROOT / "frontend" / "package.json"
    if pkg_json.exists():
        with pkg_json.open("r", encoding="utf-8") as f:
            pkg_data = json.load(f)
            pkg_version = pkg_data.get("version", "")
            if pkg_version != version:
                issues.append(f"frontend/package.json version mismatch: {pkg_version} != {version}")
    
    # 检查 server.py
    server_py = ROOT / "core" / "server.py"
    if server_py.exists():
        content = server_py.read_text(encoding="utf-8")
        if f'version="{version}"' not in content:
            issues.append(f"core/server.py version not found: {version}")
    
    return issues


def run_security_scan() -> bool:
    """运行安全扫描"""
    print("\n" + "="*70)
    print("Running security scan...")
    print("="*70)
    
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "security_scan.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        # 只显示关键问题
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr)
            return False
        
        print("✓ Security scan passed")
        return True
        
    except subprocess.TimeoutExpired:
        print("✗ Security scan timed out")
        return False
    except Exception as e:
        print(f"✗ Security scan failed: {e}")
        return False


def run_tests() -> bool:
    """运行测试套件"""
    print("\n" + "="*70)
    print("Running tests...")
    print("="*70)
    
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "-x"],
            cwd=ROOT,
            timeout=300,
        )
        
        if result.returncode != 0:
            print("✗ Tests failed")
            return False
        
        print("✓ All tests passed")
        return True
        
    except subprocess.TimeoutExpired:
        print("✗ Tests timed out")
        return False
    except Exception as e:
        print(f"✗ Tests failed: {e}")
        return False


def create_release_archive(version: str, output_dir: Path) -> Path:
    """创建发布压缩包"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    archive_name = f"Novelborne-v{version}-source"
    archive_path = output_dir / f"{archive_name}.zip"
    
    print(f"\nCreating release archive: {archive_path}")
    
    # 使用 git archive 创建干净的源码包（自动排除 .gitignore 的文件）
    try:
        subprocess.run(
            ["git", "archive", "-o", str(archive_path), "--prefix", f"{archive_name}/", "HEAD"],
            cwd=ROOT,
            check=True,
        )
        print(f"✓ Created: {archive_path}")
        print(f"  Size: {archive_path.stat().st_size / 1024 / 1024:.2f} MB")
        return archive_path
        
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to create archive: {e}")
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare release package")
    parser.add_argument("--version", required=True, help="Release version (e.g., 2.1.0)")
    parser.add_argument("--skip-tests", action="store_true", help="Skip test suite")
    parser.add_argument("--skip-scan", action="store_true", help="Skip security scan")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--output", default="release", help="Output directory for release archive")
    args = parser.parse_args()
    
    version = args.version.strip()
    output_dir = ROOT / args.output
    
    print("="*70)
    print(f"Novelborne Release Preparation v{version}")
    print("="*70)
    
    # 1. 验证版本号一致性
    print("\n1. Checking version consistency...")
    issues = verify_version_consistency(version)
    if issues:
        print("✗ Version inconsistencies found:")
        for issue in issues:
            print(f"  - {issue}")
        if not args.dry_run:
            return 1
    else:
        print("✓ Version numbers are consistent")
    
    # 2. 清理敏感数据和运行文件
    print("\n2. Cleaning sensitive and runtime data...")
    total_cleaned = 0
    
    for dir_name in CLEAN_DIRS:
        dir_path = ROOT / dir_name
        total_cleaned += clean_directory(dir_path, args.dry_run)
    
    for pattern in CLEAN_FILES:
        total_cleaned += clean_files(pattern, args.dry_run)
    
    print(f"✓ Cleaned {total_cleaned} items")
    
    if args.dry_run:
        print("\n[DRY RUN] No files were actually deleted")
        return 0
    
    # 3. 运行测试
    if not args.skip_tests:
        if not run_tests():
            print("\n✗ Tests failed. Fix issues before release.")
            return 1
    else:
        print("\n⚠ Skipping tests")
    
    # 4. 安全扫描
    if not args.skip_scan:
        if not run_security_scan():
            print("\n✗ Security scan failed. Fix issues before release.")
            return 1
    else:
        print("\n⚠ Skipping security scan")
    
    # 5. 创建发布包
    print("\n5. Creating release archive...")
    archive_path = create_release_archive(version, output_dir)
    
    print("\n" + "="*70)
    print("✓ Release preparation complete!")
    print("="*70)
    print(f"\nRelease archive: {archive_path}")
    print(f"\nNext steps:")
    print(f"  1. Test the archive in a clean environment")
    print(f"  2. Create a GitHub/Gitee release with tag v{version}")
    print(f"  3. Upload the archive and user manual")
    print(f"  4. Verify the release is downloadable and functional")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
