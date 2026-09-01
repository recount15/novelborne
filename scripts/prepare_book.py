from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.services.book_prepare_service import prepare_book


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a book for bounded retrieval")
    parser.add_argument("book_dir", help="Book directory containing chapters/")
    parser.add_argument("--leaf-chars", type=int, default=1200)
    parser.add_argument("--arc-size", type=int, default=10)
    parser.add_argument("--opening-chapters", type=int, default=3)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    result = prepare_book(args.book_dir, leaf_chars=args.leaf_chars, arc_size=args.arc_size,
                          opening_chapters=args.opening_chapters, resume=not args.no_resume)
    print(json.dumps({"book_id": result["book_id"], "chapters": len(result["chapters"]),
                      "stats": result["stats"], "extractive": result["extractive"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
