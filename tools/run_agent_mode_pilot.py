# -*- coding: utf-8 -*-
"""向后兼容薄包装：真实实现已收编至 tools/playtest_kit/agent_pilot.py。

旧调用方式 `python tools/run_agent_mode_pilot.py` 仍然可用，
等价于以默认参数（zhipu / glm-5.3-flash / 2 回合）运行新模块。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.playtest_kit.agent_pilot import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] if len(sys.argv) > 1 else None))
