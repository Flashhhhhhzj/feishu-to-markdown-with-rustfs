#!/usr/bin/env python3
"""Compatibility entrypoint for AI tools that only need a stable Feishu-link CLI."""

from __future__ import annotations

import runpy
from pathlib import Path


def main() -> None:
    target = Path(__file__).with_name("feishu_docx_to_markdown.py")
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
