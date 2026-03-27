# story-templateid-and-version-bump

## 目标
1. 按章节分组更新 `config.json` 的 `templateId`。
2. `1-6` 章目录版本号整体 +1（`v1->v2`、`v2->v3`、`v3->v4`）。

## templateId 规则
- chapter `1-6`、`8-11`：`lVkjJdC1`
- chapter `7`：`hQMnM6qB`
- chapter `12`：`80EowtBu`

## 目录重命名规则
- `story_chapter_1_v2` -> `story_chapter_1_v3`
- `story_chapter_2_v3` -> `story_chapter_2_v4`
- `story_chapter_3_v1` -> `story_chapter_3_v2`
- `story_chapter_4_v1` -> `story_chapter_4_v2`
- `story_chapter_5_v1` -> `story_chapter_5_v2`
- `story_chapter_6_v1` -> `story_chapter_6_v2`

## 校验
- 确认新目录存在且旧目录不存在。
- 所有章节 `config.json` 的 `templateId` 与章节分组一致。
- JSON 可被 `jq` 正常解析。
