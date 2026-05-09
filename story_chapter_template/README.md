# Story Chapter Template

Template folder used by the `story-chapter-create` skill. **Do not edit chapter content here** — this is the source-of-truth skeleton that scaffold copies into `story_chapter_{N}_v{V}/`.

## Workflow

```
# 1. Scaffold (copies this template into a new chapter folder)
python3 .claude/skills/story-chapter-create/scripts/scaffold.py --chapter 15

# 2. Fill in the 4 pieces inside story_chapter_15_v1/

# 3. Build
python3 .claude/skills/story-chapter-create/scripts/build.py story_chapter_15_v1
```

## Files to fill

### `images/`

Drop chapter PNGs:

- Backgrounds: `bg_01.png`, `bg_02.png`, ...
- Characters: `character_01.png`, `character_02.png`, ...

The build pipeline runs `pngquant` then `cwebp` to produce optimized webp. Images not referenced by `dialog.txt` are deleted automatically.

### `dialog.txt`

Tab-separated, 5 columns. Header line stays as-is; add one row per dialogue line.

| 对话  | 人物  | 人物图 | 背景  | 位置  |
| --- | --- | --- | --- | --- |

- **对话** (dialogue): conversation line; can be empty for image-only transitions
- **人物** (character_name): speaker name; can be empty; `旁白` maps to `special:narration`
- **人物图** (character_img): local image name like `character_01.png`, OR `result:M` placeholder where M is a 1-based chapter number — build resolves it to that chapter's id (e.g. `result:5` → `result:t5u2j7k4`). M may equal the current chapter (self-reference).
- **背景** (background_img): local image name like `bg_01.png`
- **位置** (character_pos): `居左` / `居中` / `居右`; can be empty

**Marker rows** (special first-column values):

- `结果页` — the **next** row's `背景` becomes `result.backgroundImg`
- `结果页衔接` — switches subsequent rows from `front[]` to `behind[]`

**Example**:

```
对话    人物    人物图    背景    位置
Hello!    Alice    character_01.png    bg_01.png    居左
        character_02.png    bg_01.png    居中
结果页                    
            bg_02.png    
结果页衔接                    
Wow!    Alice    result:15    bg_02.png    居左
```

### `setting.json`

```json
{
  "templateId": "<from backend>",
  "componentIds": ["<merged base + chapter ids list>"]
}
```

Both fields required. `componentIds` should already be the full merged list (no further merging in build).

### `meta.json`

```json
{
  "name": "<English chapter title>",
  "coverUrl": "https://...",
  "unlockCoverUrl": "https://...",
  "limitCurrent": false
}
```

All four fields required. `limitCurrent: true` makes build set this chapter's id as global `limitChapterId`.

## Build outputs

After successful build:

- `<folder>/config.json` — final structured config
- `<folder>/images/*.webp` — optimized, unreferenced removed
- `<folder>.zip` (repo root) — packaged for distribution
- `story_chapter_config.json` (repo root) — chapter entry appended/updated
- Intermediate files (`dialog.txt`, `setting.json`, `meta.json`) deleted
