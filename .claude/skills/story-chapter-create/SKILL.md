---
name: story-chapter-create
description: Two-step workflow to create a brand-new story chapter — scaffold copies story_chapter_template/ into story_chapter_{N}_v{V}/, then build reads chapter.json, pulls dialog rows from the Feishu sheet pointed to by dialogSheetUrl via lark-cli, runs strict validation, parses with result:M id resolution, runs pngquant+cwebp pipeline, packs zip, and updates the global chapter index stored in a Feishu wiki docx. Strict fail-fast: any missing input stops the run.
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

Runs strict checks → parses `chapter.json` → pulls dialog from Feishu sheet via `lark-cli sheets +read` → canonicalizes by column name → fetches global index from Feishu wiki docx → resolves `result:M` to chapter ids → writes `config.json` → image pipeline → packs `<folder>.zip` → diff + writes global index back to Feishu docx → cleans `chapter.json`.

`--dry-run` runs all checks plus an in-memory preview (config.json + diff + final global index), no Feishu write, no local file write.

## Required workspace

After scaffold, the user must fill:

- `chapter.json` — `{name, coverUrl, unlockCoverUrl, limitCurrent, templateId, dialogSheetUrl, componentIds}`
- `images/` — chapter PNGs (`bg_*.png`, `character_*.png`)

`dialogSheetUrl` must be a full Feishu sheet URL containing `?sheet=<sheet_id>`. Build pulls the A:E columns and matches headers by name (`对话/人物/人物图/背景/位置`).

## Dialog sheet contract

Sheet header row (first row) must contain these 5 columns (order doesn't matter, names must match exactly):

| 对话 | 人物 | 人物图 | 背景 | 位置 |
| --- | --- | --- | --- | --- |

- **对话** (dialogue): conversation line; can be empty for image-only transitions.
- **人物** (character_name): speaker name; can be empty; `旁白` maps to `special:narration`.
- **人物图** (character_img): local image name like `character_01.png`, OR `result:M` placeholder where M is a 1-based chapter number — build resolves it to that chapter's id (e.g. `result:5` → `result:t5u2j7k4`). M may equal the current chapter (self-reference).
- **背景** (background_img): local image name like `bg_01.png`.
- **位置** (character_pos): `居左` / `居中` / `居右`; can be empty.

**Marker rows** (special first-column values):

- `结果页` — the **next** row's `背景` becomes `result.backgroundImg`.
- `结果页衔接` — switches subsequent rows from `front[]` to `behind[]`.

## Global chapter index

Stored in a Feishu wiki docx (single source of truth, no local copy):

- URL: <https://stickerstyle.feishu.cn/wiki/A3n2wf4nai2WYkkSQn8c3hscnQg>
- doc_id: `O07udynUxoEMaqxoTUcc1YsNnQe`
- Location: the first ` ```JSON ... ``` ` code block under `# 配置 > ## V1 > ### Value > #### 示例`.

Build flow:

1. Fetch markdown via `lark-cli docs +fetch --api-version v2 --doc-format markdown --as user`.
2. Extract JSON code block, parse, merge new chapter entry in memory.
3. Print diff + full final json to stdout.
4. Replace JSON code block contents, `lark-cli docs +update --api-version v2 --mode overwrite --markdown -` to write back.

**Concurrency**: build is not idempotent under concurrent runs — run builds serially.

## Prerequisites for build

- `lark-cli` installed and in `$PATH`
- Authenticated as user with scopes: `sheets:spreadsheet:readonly sheets:spreadsheet.meta:read docs:document docs:document:readonly`
- Feishu account has read access to the dialog sheet AND read/write access to the global doc

## Fail-fast guarantees

Build performs all checks before any write. Any failure → no `config.json`, no zip, no global doc change, no `chapter.json` deletion.

## Outputs (on success)

1. `<folder>/config.json`
2. `<folder>/images/*.webp` (unreferenced removed)
3. `<folder>.zip` at repo root
4. Feishu wiki docx updated (chapter entry append/update in JSON code block; `limitChapterId` set if `chapter.limitCurrent`)
5. `chapter.json` deleted

## Scope

This skill is for **first-time chapter creation only**. Legacy batch upgrade lives in the deprecated `story-resource-upgrade` skill.
