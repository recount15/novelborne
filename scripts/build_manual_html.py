# -*- coding: utf-8 -*-
"""把 docs/USER_MANUAL.md 转成单文件 HTML（图片 base64 内嵌），输出到 docs/USER_MANUAL.html。"""
import base64
import mimetypes
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "USER_MANUAL.md"
OUT = ROOT / "docs" / "USER_MANUAL.html"

CSS = """
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; line-height: 1.75;
       max-width: 860px; margin: 2rem auto; padding: 0 1.2rem; color: #1e2021; }
h1 { border-bottom: 2px solid #1a7898; padding-bottom: .4rem; }
h2 { border-bottom: 1px solid #d8dde2; padding-bottom: .3rem; margin-top: 2.2rem; }
img { max-width: 100%; height: auto; display: block; margin: 1rem auto;
      border: 1px solid #d8dde2; border-radius: 4px; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th { background: #1a7898; color: #fff; }
th, td { border: 1px solid #c9ced3; padding: .45rem .7rem; text-align: left; }
tr:nth-child(even) td { background: #f2f4f6; }
code { background: #eef1f3; padding: .1rem .35rem; border-radius: 3px; }
pre { background: #f2f4f6; padding: .8rem 1rem; border-radius: 6px; overflow-x: auto; }
blockquote { border-left: 4px solid #1a7898; margin-left: 0; padding: .2rem 1rem;
             color: #4a5055; background: #f4f8fa; }
p em:only-child { display: block; text-align: center; color: #6e747a; font-size: .92em; }
"""

def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    html_body = markdown.markdown(text, extensions=["tables", "fenced_code"])

    def embed(match: re.Match) -> str:
        rel = match.group(1)
        path = SRC.parent / rel
        if not path.exists():
            print(f"警告: 图片不存在 {path}", file=sys.stderr)
            return match.group(0)
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'src="data:{mime};base64,{data}"'

    html_body = re.sub(r'src="(images/[^"]+)"', embed, html_body)
    html = ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>书中织梦（Novelborne）用户手册 · v3.0.0</title>\n"
            f"<style>{CSS}</style>\n</head>\n<body>\n{html_body}\n</body>\n</html>\n")
    OUT.write_text(html, encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print(f"OK {OUT} ({size:.0f} KB)")

if __name__ == "__main__":
    main()
