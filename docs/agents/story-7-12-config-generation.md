# story-7-12-config-generation

## 目标
为 `story_chapter_7_v1` 到 `story_chapter_12_v1` 生成/补齐 `config.json`，格式与已有章节一致。

## 输入
- 规则：`generate_json.md`
- 章节内容：
  - `story_chapter_7_v1/content`
  - `story_chapter_8_v1/content`
  - `story_chapter_9_v1/content`
  - `story_chapter_10_v1/content`
  - `story_chapter_11_v1/content`
  - `story_chapter_12_v1/content`
- 组件 ID：
  - `story_line_7.json` -> `chapters.chapter_7`
  - `story_line_8-11.json` -> `chapters.chapter_8/9/10/11`
  - `story_line_12.json` -> `chapters.chapter_12`

## 转换规则
1. 顶层字段：`templateId` 固定 `nMghiUAU`，`componentIds` 对应章节 ID 列表。
2. 分段：
   - `结果页` 之前写入 `front`。
   - `结果页` 行后第一个带背景的行用于 `result.backgroundImg`，并从剧情节点中移除。
   - `结果页衔接` 之后写入 `behind`，衔接行本身移除。
3. 字段映射：
   - 对话 -> `dialogue`
   - 人物 -> `characterName`（`旁白` 转 `special:narration`）
   - 人物图 -> `characterImg`
   - 背景 -> `backgroundImg`
   - 位置 -> `characterPos`（居左/居中/居右 -> 0/1/2）
4. 清洗：
   - `.png` 统一替换 `.webp`
   - 空值不输出对应 key
   - 多行/引号包裹文本压平成单行
   - 整行空行跳过

## 校验
- 每章 `config.json` 可被 `jq` 正常解析。
- `componentIds` 非空。
- `front`、`behind` 为数组，`result.backgroundImg` 存在。
- 图片后缀均为 `.webp`（含 `result:*` 特殊资源不改动）。
