---
name: story-chapter-create
description: Two-step workflow to create a brand-new story chapter — scaffold copies story_chapter_template/ into story_chapter_{N}_v{V}/, then build validates the filled workspace, parses dialog.txt with result:M id resolution, runs pngquant+cwebp pipeline, packs zip, and updates story_chapter_config.json. Strict fail-fast: any missing input stops the run.
---

# Story Chapter Create

Use when the user asks to create a brand-new story chapter (first-time, not version-upgrading existing chapters).

## Two-step flow

### Step 1: scaffold

```bash
python3 .claude/skills/story-chapter-create/scripts/scaffold.py --chapter N [--version V]
```

Copies `story_chapter_template/` into `story_chapter_{N}_v{V}/` and prints a fill-in checklist. `--version` defaults to `1`.

### Step 2: build

```bash
python3 .claude/skills/story-chapter-create/scripts/build.py story_chapter_{N}_v{V} [--dry-run]
```

Runs 14 strict checks → parses dialog.txt → resolves `result:M` to chapter ids → writes `config.json` → image pipeline → packs `<folder>.zip` → updates `story_chapter_config.json` → cleans intermediate files.

`--dry-run` runs all checks plus an in-memory preview without writing.

## Required workspace

After scaffold, the user must fill (see `story_chapter_template/README.md`):

- `images/` — chapter PNGs
- `dialog.txt` — 5-column TSV
- `setting.json` — `{templateId, componentIds}`
- `meta.json` — `{name, coverUrl, unlockCoverUrl, limitCurrent}`

Plus the global index `story_chapter_config.json` must exist at repo root.

## Fail-fast guarantees

Build performs all checks before any write. Any failure → no `config.json`, no zip, no global index change.

## Outputs (on success)

1. `<folder>/config.json`
2. `<folder>/images/*.webp` (unreferenced removed)
3. `<folder>.zip` at repo root
4. `story_chapter_config.json` updated (chapter entry append/update; `limitChapterId` set if `meta.limitCurrent`)
5. `dialog.txt`, `setting.json`, `meta.json` deleted

## Scope

This skill is for **first-time chapter creation only**. Legacy batch upgrade lives in the deprecated `story-resource-upgrade` skill.
