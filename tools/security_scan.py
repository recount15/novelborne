#!/usr/bin/env python3
"""发布前安全扫描：检查密钥泄露、敏感信息和隐私数据。

Usage:
    python tools/security_scan.py [--fix]
    
Options:
    --fix    自动修复可修复的问题（删除敏感文件）
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = ROOT / "outputs"
VAR_DIR = ROOT / "var"


# 密钥特征模式
KEY_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{32,}", "OpenAI-style API key"),
    (r"[a-f0-9]{32}", "MD5/UUID-style key"),
    (r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"]([^'\"]{10,})['\"]", "Explicit API key assignment"),
    (r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*", "Bearer token"),
]


# 敏感路径模式
SENSITIVE_PATHS = [
    r"[C-Z]:\\Users\\[^\\]+\\",  # Windows 用户路径
    r"/Users/[^/]+/",  # macOS 用户路径
    r"/home/[^/]+/",  # Linux 用户路径
]


# 应排除扫描的文件模式
EXCLUDE_PATTERNS = [
    r"\.git/",
    r"node_modules/",
    r"__pycache__/",
    r"\.pytest_cache/",
    r"\.venv/",
    r"venv/",
    r"dist/",
    r"build/",
    r"\.egg-info/",
]


# 敏感文件扩展名（应从发布中排除）
SENSITIVE_EXTENSIONS = {
    ".db", ".sqlite", ".sqlite3",  # 数据库
    ".log", ".jsonl",  # 日志
    ".env", ".env.local", ".env.production",  # 环境变量
    ".key", ".pem", ".crt",  # 证书和密钥
}


# 敏感目录（应从发布中排除）
SENSITIVE_DIRS = {
    "outputs", "var", ".git", "__pycache__", ".pytest_cache",
    "node_modules", ".venv", "venv",
}


def should_exclude(path: Path, relative: Path) -> bool:
    """检查路径是否应排除"""
    rel_str = str(relative).replace("\\", "/")
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, rel_str):
            return True
    return False


def scan_file_for_keys(path: Path) -> list[dict[str, Any]]:
    """扫描文件中的密钥"""
    issues: list[dict[str, Any]] = []
    
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return issues
    
    lines = content.splitlines()
    for pattern, description in KEY_PATTERNS:
        for match in re.finditer(pattern, content, re.IGNORECASE):
            # 查找行号
            pos = match.start()
            line_no = content[:pos].count("\n") + 1
            line_text = lines[line_no - 1] if line_no <= len(lines) else ""
            
            # 排除明显的占位符和注释
            if any(marker in line_text.lower() for marker in ["example", "test", "fake", "placeholder", "redacted", "xxx"]):
                continue
            
            issues.append({
                "file": str(path.relative_to(ROOT)),
                "line": line_no,
                "type": "potential_key",
                "description": description,
                "context": line_text.strip()[:100],
            })
    
    return issues


def scan_file_for_paths(path: Path) -> list[dict[str, Any]]:
    """扫描文件中的个人路径"""
    issues: list[dict[str, Any]] = []
    
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return issues
    
    lines = content.splitlines()
    for pattern in SENSITIVE_PATHS:
        for match in re.finditer(pattern, content):
            pos = match.start()
            line_no = content[:pos].count("\n") + 1
            line_text = lines[line_no - 1] if line_no <= len(lines) else ""
            
            # 排除文档中的示例
            if "example" in line_text.lower() or "#" in line_text:
                continue
            
            issues.append({
                "file": str(path.relative_to(ROOT)),
                "line": line_no,
                "type": "personal_path",
                "description": "Personal filesystem path",
                "context": line_text.strip()[:100],
            })
    
    return issues


def scan_sensitive_files() -> list[dict[str, Any]]:
    """扫描敏感文件"""
    issues: list[dict[str, Any]] = []
    
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        
        relative = path.relative_to(ROOT)
        if should_exclude(path, relative):
            continue
        
        # 检查敏感扩展名
        if path.suffix.lower() in SENSITIVE_EXTENSIONS:
            issues.append({
                "file": str(relative),
                "type": "sensitive_file",
                "description": f"Sensitive file extension: {path.suffix}",
                "action": "exclude_from_release",
            })
        
        # 检查敏感目录
        if any(part in SENSITIVE_DIRS for part in relative.parts):
            issues.append({
                "file": str(relative),
                "type": "sensitive_directory",
                "description": f"File in sensitive directory",
                "action": "exclude_from_release",
            })
    
    return issues


def scan_codebase() -> dict[str, Any]:
    """扫描代码库"""
    report: dict[str, Any] = {
        "scanned_files": 0,
        "issues": [],
    }
    
    print("Scanning for API keys and tokens...")
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        
        relative = path.relative_to(ROOT)
        if should_exclude(path, relative):
            continue
        
        # 只扫描代码和配置文件
        if path.suffix not in {".py", ".js", ".ts", ".vue", ".json", ".yaml", ".yml", ".toml", ".ini", ".env", ".md", ".txt"}:
            continue
        
        report["scanned_files"] += 1
        report["issues"].extend(scan_file_for_keys(path))
        report["issues"].extend(scan_file_for_paths(path))
    
    print("Scanning for sensitive files...")
    report["issues"].extend(scan_sensitive_files())
    
    return report


def print_report(report: dict[str, Any]) -> None:
    """打印扫描报告"""
    print(f"\n{'='*70}")
    print(f"Security Scan Report")
    print(f"{'='*70}")
    print(f"Scanned files: {report['scanned_files']}")
    print(f"Issues found: {len(report['issues'])}")
    print()
    
    # 按类型分组
    by_type: dict[str, list[dict[str, Any]]] = {}
    for issue in report["issues"]:
        issue_type = issue["type"]
        by_type.setdefault(issue_type, []).append(issue)
    
    for issue_type, issues in sorted(by_type.items()):
        print(f"\n{issue_type.upper().replace('_', ' ')} ({len(issues)} issues):")
        print("-" * 70)
        
        for issue in issues[:10]:  # 只显示前10个
            print(f"  File: {issue['file']}")
            if "line" in issue:
                print(f"  Line: {issue['line']}")
            print(f"  Description: {issue['description']}")
            if "context" in issue:
                print(f"  Context: {issue['context']}")
            if "action" in issue:
                print(f"  Action: {issue['action']}")
            print()
        
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more\n")
    
    print(f"\n{'='*70}")


def fix_issues(report: dict[str, Any], dry_run: bool = True) -> int:
    """修复可修复的问题"""
    fixed = 0
    
    for issue in report["issues"]:
        if issue.get("action") == "exclude_from_release":
            file_path = ROOT / issue["file"]
            if file_path.exists():
                if not dry_run:
                    print(f"Would delete: {issue['file']}")
                    # file_path.unlink()
                else:
                    print(f"Would delete: {issue['file']}")
                fixed += 1
    
    return fixed


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for security issues before release")
    parser.add_argument("--fix", action="store_true", help="Fix issues automatically (dry run for now)")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()
    
    report = scan_codebase()
    
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)
    
    if args.fix:
        print("\n" + "="*70)
        print("FIX MODE (DRY RUN)")
        print("="*70)
        fixed = fix_issues(report, dry_run=True)
        print(f"\nWould fix {fixed} issues")
        print("\nNote: Actual deletion is disabled. Manually review and delete sensitive files.")
    
    # 返回非零退出码如果发现严重问题
    critical_types = {"potential_key", "personal_path"}
    critical_issues = [i for i in report["issues"] if i["type"] in critical_types]
    
    if critical_issues:
        print(f"\n⚠️  Found {len(critical_issues)} critical issues that must be addressed before release!")
        return 1
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
