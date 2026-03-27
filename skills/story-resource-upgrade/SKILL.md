---
name: story-resource-upgrade
description: One-click upgrade workflow for story chapters: validate content/config/resources/componentIds(template with base ids), run fixed image pipeline (compress_images.sh png -> png_to_webp.sh), and bump chapter folder versions only when config or image outputs changed.
---

# Story Resource Upgrade

Use this skill when user asks to batch upgrade added chapters in this repo and requires strict stop-on-missing checks.

## Inputs Required
- `chapters`: chapter range/list, e.g. `7-12` or `8,9,10`.
- `images_root`: source image root (contains per-chapter subfolders like `章节7`, `章节8`, ...).
- `storyline` json files: one or more files that contain `tid`, `baseCptIds`, and `chapters.chapter_n`.

## Guarantees
- Stop immediately if any required resource is missing.
- `componentIds` are always built as `baseCptIds + chapterIds` (dedupe, keep order).
- `templateId` always follows the storyline `tid` for that chapter.
- Image pipeline order is fixed and isolated in staging:
  1. `compress_images.sh png`
  2. `png_to_webp.sh`
- Folder version bumps (`vN -> vN+1`) happen only when staged output differs from current version in config or images.

## Command
Run from repo root:

```bash
python3 skills/story-resource-upgrade/scripts/upgrade_resources.py \
  --chapters 7-12 \
  --images-root "/Users/loopq/Downloads/章节7-12切图整理" \
  --storyline "/Users/loopq/Downloads/storyline-lVkjJdC1-1-6-8-11.json" \
  --storyline "/Users/loopq/Downloads/storyline-hQMnM6qB-7.json" \
  --storyline "/Users/loopq/Downloads/storyline-80EowtBu-12.json"
```

Dry run:

```bash
python3 skills/story-resource-upgrade/scripts/upgrade_resources.py \
  --chapters 7-12 \
  --images-root "/Users/loopq/Downloads/章节7-12切图整理" \
  --storyline "/Users/loopq/Downloads/storyline-lVkjJdC1-1-6-8-11.json" \
  --storyline "/Users/loopq/Downloads/storyline-hQMnM6qB-7.json" \
  --storyline "/Users/loopq/Downloads/storyline-80EowtBu-12.json" \
  --dry-run
```

## Output Contract
- Prints per chapter: validation status, change detection, and final action.
- If any precheck fails: exits non-zero and does not write new version folders.
- On success: creates new version folders only for changed chapters.
