# -*- coding: utf-8 -*-
"""公开资产门禁（D12 隔离）：打包 spec 的单一事实来源，可独立干跑测试。

``PUBLIC_BASELINE_SHA256`` 固定公开资产基线；作品目录必须为空目录版本。
``PRIVATE_LOCAL_SHA256`` 当前不登记任何覆盖，因此私有构建开关也不能
重新携带已退役的预置作品。任何哈希不匹配都会中止构建。

公开打包脚本（package_release.bat / build_windows.bat）不得设置该开关。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

#: 显式启用私有资产覆盖的环境变量（公开脚本不得设置）。
PRIVATE_ASSETS_ENV = "FATEENGINE_ALLOW_PRIVATE_ASSETS"

_DATA_FILES = (
    'layered_corpus.json', 'skills_catalog.json', 'tropes_biz.json',
    'tropes_combat.json', 'tropes_life.json', 'tropes_manifest.json',
    'tropes_mystery.json', 'tropes_romance.json',
)
_PROMPT_FILES = (
    'agent_revise.md', 'agent_self_check.md', 'answer_polish.md',
    'character_patch.md', 'directives_register.md', 'eval_archive.md',
    'nemesis_block.md', 'opening_anchor_merge.md', 'opening_anchor_verify.md',
    'opening_archive.md', 'opening_characters.md', 'opening_check.md',
    'opening_nemesis_note.md', 'opening_plot_merge.md', 'opening_plot_sample.md',
    'opening_settings.md', 'option_repair.md', 'options_gen.md', 'pacing_hint.md',
    'paper_director.md', 'paper_polish.md', 'paper_segment.md', 'quality_judge.md',
    'quality_rewrite.md', 'rounds_rule.md', 'rounds_rule_enhanced.md',
    'rounds_rule_fragment.md', 'segment_refill.md', 'structured_question.md',
    'system_header.md', 'uploaded_work.md', 'work_archive_distill.md',
)
_RULE_FILES = (
    'enhanced.md', 'golden_finger.md', 'runtime.md', 'state_memory.md',
    'work_library.md', 'worldbook.md',
)
_PAPER_FILES = tuple(
    f'{size}_l{level}_{stage}.json'
    for size, levels in (('small', range(1, 4)), ('large', range(4, 7)))
    for level in levels
    for stage in ('setup', 'climax', 'free')
)
PUBLIC_ASSET_FILES = (
    *(f'data/{name}' for name in _DATA_FILES),
    'lore/default_worldbook.json',
    *(f'papers/{name}' for name in _PAPER_FILES),
    *(f'prompts/{name}' for name in _PROMPT_FILES),
    *(f'rules/{name}' for name in _RULE_FILES),
)

#: 公开基线哈希（主门禁，永远生效）。
PUBLIC_BASELINE_SHA256 = {
    'rules/work_library.md': '921371c3a5eb70235c2e7e11b5616e2f36163bc73c2942b69c8f7ecbb32564b7',
}

#: 预置作品退役后，空目录哈希不得被私有开关绕过。
PRIVATE_LOCAL_SHA256: dict[str, str] = {}


def private_override_enabled(environ: dict | None = None) -> bool:
    """私有覆盖是否被显式开启（公开打包脚本不得调用或置位）。"""
    env = os.environ if environ is None else environ
    return str(env.get(PRIVATE_ASSETS_ENV, '')).strip() == '1'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def public_asset_datas(root: Path, *, allow_private: bool = False) -> list[tuple[str, str]]:
    """校验并返回公开资产 datas 列表；任何违规 ``SystemExit`` 中止构建。

    ``allow_private=True``（spec 侧由 :func:`private_override_enabled` 决定）
    时对 ``PRIVATE_LOCAL_SHA256`` 覆盖的文件放行本地私有副本；其余文件仍按
    公开基线校验。缺文件/符号链接/哈希不匹配一律中止，不放行、不降级。
    """
    asset_root = Path(root) / 'assets'
    result: list[tuple[str, str]] = []
    for relative_name in PUBLIC_ASSET_FILES:
        source = asset_root / relative_name
        if not source.is_file() or source.is_symlink():
            raise SystemExit(f'Required public asset is missing or unsafe: assets/{relative_name}')
        expected = PUBLIC_BASELINE_SHA256.get(relative_name)
        if allow_private and relative_name in PRIVATE_LOCAL_SHA256:
            expected = PRIVATE_LOCAL_SHA256[relative_name]
        if expected and _sha256(source) != expected:
            raise SystemExit(
                f'Public asset hash mismatch: assets/{relative_name}. '
                'Restore or explicitly review and update the approved hash before release.'
                + ('' if not allow_private else ' (private override active)')
            )
        target = (Path('assets') / Path(relative_name).parent).as_posix()
        result.append((str(source), target))
    return result
