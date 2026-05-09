#!/usr/bin/env python3
"""Scaffold a new chapter workspace by copying story_chapter_template/."""
import argparse
import shutil
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[4]
TEMPLATE_DIR = REPO_ROOT / "story_chapter_template"


class ScaffoldError(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description="Scaffold a new chapter workspace from template")
    parser.add_argument("--chapter", type=int, required=True, help="Chapter number (1-based)")
    parser.add_argument("--version", type=int, default=1, help="Version number (default: 1)")
    args = parser.parse_args()

    if args.chapter < 1:
        raise ScaffoldError(f"--chapter must be >= 1, got {args.chapter}")
    if args.version < 1:
        raise ScaffoldError(f"--version must be >= 1, got {args.version}")

    if not TEMPLATE_DIR.is_dir():
        raise ScaffoldError(f"template not found: {TEMPLATE_DIR}")

    target_name = f"story_chapter_{args.chapter}_v{args.version}"
    target = REPO_ROOT / target_name
    if target.exists():
        raise ScaffoldError(f"target already exists: {target}")

    shutil.copytree(TEMPLATE_DIR, target)

    print(f"✓ Created {target_name}/")
    print()
    print("To complete this chapter, please fill:")
    print("  □ images/        — drop chapter pngs (bg_*.png, character_*.png)")
    print("  □ dialog.txt     — fill conversation rows")
    print("  □ setting.json   — set templateId + componentIds")
    print("  □ meta.json      — set name / coverUrl / unlockCoverUrl")
    print()
    print("Template guide: story_chapter_template/README.md")
    print()
    print("When ready:")
    print(f"  python3 .claude/skills/story-chapter-create/scripts/build.py {target_name}")


if __name__ == "__main__":
    try:
        main()
    except ScaffoldError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
