#!/usr/bin/env python3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_story_br_resources as br


class StoryBrResourceTest(unittest.TestCase):
    def test_extract_dialogues_skips_title_result_row_and_repeated_header(self):
        raw_rows = [
            ["章节2-引入剧情", "", "", "", "", ""],
            ["对话", "人物", "人物图", "背景", "位置", ""],
            ["Olá, Scarlett.", "Scarlett", "result:1", "bg_01.png", "居左", ""],
            ["结果页", "", "", "", "", ""],
            ["", "", "", "bg_02.png", "居中", ""],
            ["结果页衔接", "", "", "", "", ""],
            ["对话", "人物", "人物图", "背景", "位置", ""],
            ["Vamos agora.", "Scarlett", "result:2", "bg_02.png", "居左", ""],
        ]

        canonical = br.canonicalize_dialog_sheet_rows(raw_rows)
        dialogues = br.extract_dialogues(canonical)

        self.assertEqual(dialogues.front, ["Olá, Scarlett."])
        self.assertEqual(dialogues.behind, ["Vamos agora."])

    def test_extract_dialogues_handles_missing_result_background_row(self):
        raw_rows = [
            ["对话", "人物", "人物图", "背景", "位置", ""],
            ["front text", "Scarlett", "result:1", "bg_01.png", "居左", ""],
            ["结果页", "", "", "", "", ""],
            ["结果页衔接", "", "", "", "", ""],
            ["behind text", "Scarlett", "result:1", "bg_02.png", "居左", ""],
        ]

        canonical = br.canonicalize_dialog_sheet_rows(raw_rows)
        dialogues = br.extract_dialogues(canonical)

        self.assertEqual(dialogues.front, ["front text"])
        self.assertEqual(dialogues.behind, ["behind text"])

    def test_canonicalize_finds_header_when_title_looks_like_header_text(self):
        raw_rows = [
            ["对话", "", "", "", "", ""],
            ["对话", "人物", "人物图", "背景", "位置", ""],
            ["Olá.", "Scarlett", "result:1", "bg_01.png", "居左", ""],
        ]

        canonical = br.canonicalize_dialog_sheet_rows(raw_rows)
        dialogues = br.extract_dialogues(canonical)

        self.assertEqual(dialogues.front, ["Olá."])

    def test_replace_only_dialogues_preserves_every_other_field(self):
        original = {
            "templateId": "tid",
            "componentIds": ["base", "chapter"],
            "front": [
                {
                    "dialogue": "old front",
                    "characterName": "Scarlett",
                    "characterImg": "result:y8d2n4x6",
                    "backgroundImg": "bg_01.webp",
                    "characterPos": 0,
                }
            ],
            "result": {"backgroundImg": "bg_02.webp"},
            "behind": [
                {
                    "dialogue": "old behind",
                    "characterName": "Liam",
                    "characterImg": "character_01.webp",
                    "backgroundImg": "bg_02.webp",
                    "characterPos": 2,
                    "decorationImg": "decor_01.webp",
                }
            ],
        }
        before_non_dialogue = br.config_without_dialogue_values(original)

        updated = br.replace_dialogues_in_config(
            original,
            br.Dialogues(front=["novo front"], behind=["novo behind"]),
            "story_chapter_1_v5",
        )

        self.assertEqual(updated["front"][0]["dialogue"], "novo front")
        self.assertEqual(updated["behind"][0]["dialogue"], "novo behind")
        self.assertEqual(before_non_dialogue, br.config_without_dialogue_values(updated))
        self.assertEqual(original["front"][0]["dialogue"], "old front")

    def test_replace_dialogues_rejects_count_mismatch(self):
        config = {
            "front": [{"dialogue": "a"}, {"dialogue": "b"}],
            "result": {},
            "behind": [{"dialogue": "c"}],
        }

        with self.assertRaisesRegex(br.StoryBrError, "dialogue count mismatch"):
            br.replace_dialogues_in_config(
                config,
                br.Dialogues(front=["um"], behind=["tres"]),
                "story_chapter_1_v5",
            )

    def test_find_latest_chapter_dir_ignores_br_variants(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "story_chapter_1_v1").mkdir()
            (root / "story_chapter_1_v2_br").mkdir()
            (root / "story_chapter_1_v3").mkdir()

            latest = br.find_latest_chapter_dir(root, 1)

        self.assertEqual(latest.name, "story_chapter_1_v3")

    def test_target_name_uses_existing_version_with_br_suffix(self):
        self.assertEqual(
            br.build_target_name(Path("story_chapter_24_v2")),
            "story_chapter_24_v2_br",
        )

    def test_parse_chapters_accepts_ranges_lists_and_rejects_bad_input(self):
        self.assertEqual(br.parse_chapters("1-3"), [1, 2, 3])
        self.assertEqual(br.parse_chapters("3,10,24"), [3, 10, 24])
        self.assertEqual(br.parse_chapters("1-3,3,5"), [1, 2, 3, 5])

        for value in ("abc", "1,xyz", "1-24-extra", "0", "25", "7-3"):
            with self.subTest(value=value):
                with self.assertRaises(br.StoryBrError):
                    br.parse_chapters(value)


if __name__ == "__main__":
    unittest.main()
