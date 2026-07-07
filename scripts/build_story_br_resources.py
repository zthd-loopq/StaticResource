#!/usr/bin/env python3
import argparse
import copy
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SHEET_URL = "https://stickerstyle.feishu.cn/sheets/SB0jsAqbnhvFOutM5hgccy7znBh?sheet=3WGioe"
DIR_PATTERN = re.compile(r"^story_chapter_(\d+)_v(\d+)$")
REQUIRED_HEADERS = ("对话", "人物", "人物图", "背景", "位置")
OUTPUT_HEADERS = ("对话", "人物", "人物图", "背景", "位置", "装饰")
READ_COLUMNS = ("A", "B", "C", "D", "E", "F")


class StoryBrError(Exception):
    pass


@dataclass(frozen=True)
class Dialogues:
    front: List[str]
    behind: List[str]


@dataclass(frozen=True)
class SheetInfo:
    chapter: int
    sheet_id: str
    row_count: int


@dataclass(frozen=True)
class ChapterWork:
    chapter: int
    source_dir: Path
    target_dir: Path
    target_zip: Path
    original_config: dict
    br_config: dict
    dialogues: Dialogues


def clean_cell(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).strip()


def parse_chapters(expr: str) -> List[int]:
    chapters: List[int] = []
    for token in expr.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_s, end_s = token.split("-", 1)
            start = parse_chapter_number(start_s, token)
            end = parse_chapter_number(end_s, token)
            if start > end:
                raise StoryBrError(f"invalid chapter range: {token}")
            chapters.extend(range(start, end + 1))
        else:
            chapters.append(parse_chapter_number(token, token))
    unique = sorted(set(chapters))
    if not unique:
        raise StoryBrError("chapter list is empty")
    for chapter in unique:
        if chapter < 1 or chapter > 24:
            raise StoryBrError(f"chapter out of supported range 1-24: {chapter}")
    return unique


def parse_chapter_number(value: str, token: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise StoryBrError(f"invalid chapter number in {token!r}: {value!r}")


def run_lark_cli(args: List[str]) -> dict:
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise StoryBrError("lark-cli not found in PATH")
    if proc.returncode != 0:
        raise StoryBrError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout:\n{proc.stdout[:2000]}\n"
            f"stderr:\n{proc.stderr[:2000]}"
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise StoryBrError(f"lark-cli output is not JSON: {exc}\n{proc.stdout[:500]}")
    if not payload.get("ok"):
        error = payload.get("error") or {}
        message = error.get("message") or "unknown error"
        hint = error.get("hint")
        console_url = error.get("console_url")
        parts = [f"lark-cli failed: {message}", f"cmd: {' '.join(args)}"]
        if hint:
            parts.append(f"hint: {hint}")
        if console_url:
            parts.append(f"console_url: {console_url}")
        raise StoryBrError("\n".join(parts))
    return payload


def ensure_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise StoryBrError(f"required tool not found in PATH: {name}")


def preflight_tools() -> None:
    for name in ("lark-cli", "zip", "zipinfo"):
        ensure_tool(name)


def fetch_sheet_infos(sheet_url: str) -> Dict[int, SheetInfo]:
    payload = run_lark_cli([
        "lark-cli",
        "sheets",
        "+workbook-info",
        "--url",
        sheet_url,
        "--as",
        "user",
    ])
    mapping: Dict[int, SheetInfo] = {}
    for sheet in (payload.get("data") or {}).get("sheets") or []:
        name = str(sheet.get("title") or sheet.get("sheet_name") or "").strip()
        if not name.isdigit():
            continue
        chapter = int(name)
        sheet_id = sheet.get("sheet_id")
        if not sheet_id:
            continue
        mapping[chapter] = SheetInfo(
            chapter=chapter,
            sheet_id=sheet_id,
            row_count=int(sheet.get("row_count") or 200),
        )
    missing = [chapter for chapter in range(1, 25) if chapter not in mapping]
    if missing:
        raise StoryBrError(f"missing sheet tabs for chapters: {missing}")
    return mapping


def fetch_sheet_rows(sheet_url: str, info: SheetInfo) -> List[List[str]]:
    last_row = max(info.row_count, 200)
    payload = run_lark_cli([
        "lark-cli",
        "sheets",
        "+csv-get",
        "--url",
        sheet_url,
        "--sheet-id",
        info.sheet_id,
        "--range",
        f"A1:F{last_row}",
        "--rows-json",
        "--as",
        "user",
    ])
    data = payload.get("data") or {}
    if data.get("data_not_fully_read"):
        raise StoryBrError(f"sheet {info.chapter}: data not fully read: {data['data_not_fully_read']}")
    rows = []
    for row in data.get("rows") or []:
        values = row.get("values") or {}
        rows.append([values.get(col, "") for col in READ_COLUMNS])
    if not rows:
        raise StoryBrError(f"sheet {info.chapter}: no rows")
    return rows


def canonicalize_dialog_sheet_rows(raw_rows: List[List[str]]) -> List[List[str]]:
    if not raw_rows:
        raise StoryBrError("dialog sheet is empty")
    start_index = find_header_row_index(raw_rows)
    header = [clean_cell(cell) for cell in raw_rows[start_index]]
    indices = [header.index(name) if name in header else -1 for name in OUTPUT_HEADERS]
    rows = [list(OUTPUT_HEADERS)]
    for raw in raw_rows[start_index + 1:]:
        cells = []
        for idx in indices:
            value = raw[idx] if idx >= 0 and idx < len(raw) else ""
            cells.append(clean_cell(value))
        if is_repeated_header_row(cells):
            continue
        rows.append(cells)
    return rows


def find_header_row_index(raw_rows: List[List[str]]) -> int:
    for index, row in enumerate(raw_rows[:3]):
        cells = [clean_cell(cell) for cell in row]
        if all(name in cells for name in REQUIRED_HEADERS):
            return index
    preview = [[clean_cell(cell) for cell in row] for row in raw_rows[:3]]
    raise StoryBrError(f"dialog sheet header row not found in first 3 rows: {preview}")


def is_repeated_header_row(cells: List[str]) -> bool:
    padded = list(cells[:5])
    if len(padded) < 5:
        padded.extend([""] * (5 - len(padded)))
    return padded == list(REQUIRED_HEADERS)


def extract_dialogues(canonical_rows: List[List[str]]) -> Dialogues:
    front: List[str] = []
    behind: List[str] = []
    in_behind = False
    waiting_result_bg = False
    for row in canonical_rows[1:]:
        cells = list(row[:6])
        if len(cells) < 6:
            cells.extend([""] * (6 - len(cells)))
        dialogue = clean_cell(cells[0])
        if dialogue == "结果页":
            waiting_result_bg = True
            continue
        if dialogue == "结果页衔接":
            in_behind = True
            waiting_result_bg = False
            continue
        if all(not clean_cell(cell) for cell in cells):
            continue
        if waiting_result_bg:
            waiting_result_bg = False
            continue
        if not dialogue:
            continue
        if in_behind:
            behind.append(dialogue)
        else:
            front.append(dialogue)
    if not front and not behind:
        raise StoryBrError("dialog sheet has no dialogue text")
    return Dialogues(front=front, behind=behind)


def find_latest_chapter_dir(repo_root: Path, chapter: int) -> Path:
    candidates: List[Tuple[int, Path]] = []
    for path in repo_root.glob(f"story_chapter_{chapter}_v*"):
        if not path.is_dir():
            continue
        match = DIR_PATTERN.match(path.name)
        if not match:
            continue
        candidates.append((int(match.group(2)), path))
    if not candidates:
        raise StoryBrError(f"chapter {chapter}: no source directory found")
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def build_target_name(source_dir: Path) -> str:
    if not DIR_PATTERN.match(source_dir.name):
        raise StoryBrError(f"invalid source chapter directory: {source_dir.name}")
    return f"{source_dir.name}_br"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StoryBrError(f"invalid JSON: {path}: {exc}")


def write_json(path: Path, value: dict) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def dialogue_slots(items: Iterable[dict]) -> List[dict]:
    return [item for item in items if isinstance(item, dict) and "dialogue" in item]


def replace_dialogues_in_config(config: dict, dialogues: Dialogues, label: str) -> dict:
    result = copy.deepcopy(config)
    front_slots = dialogue_slots(result.get("front") or [])
    behind_slots = dialogue_slots(result.get("behind") or [])
    if len(front_slots) != len(dialogues.front) or len(behind_slots) != len(dialogues.behind):
        raise StoryBrError(
            f"{label}: dialogue count mismatch: "
            f"config front/behind={len(front_slots)}/{len(behind_slots)}, "
            f"sheet front/behind={len(dialogues.front)}/{len(dialogues.behind)}"
        )
    for item, text in zip(front_slots, dialogues.front):
        item["dialogue"] = text
    for item, text in zip(behind_slots, dialogues.behind):
        item["dialogue"] = text
    return result


def config_without_dialogue_values(config: dict) -> dict:
    stripped = copy.deepcopy(config)
    for section in ("front", "behind"):
        for item in stripped.get(section) or []:
            if isinstance(item, dict) and "dialogue" in item:
                item["dialogue"] = "<dialogue>"
    return stripped


def build_work(repo_root: Path, chapter: int, dialogues: Dialogues) -> ChapterWork:
    source_dir = find_latest_chapter_dir(repo_root, chapter)
    target_name = build_target_name(source_dir)
    target_dir = repo_root / target_name
    target_zip = repo_root / f"{target_name}.zip"
    original_config = load_json(source_dir / "config.json")
    br_config = replace_dialogues_in_config(original_config, dialogues, source_dir.name)
    return ChapterWork(
        chapter=chapter,
        source_dir=source_dir,
        target_dir=target_dir,
        target_zip=target_zip,
        original_config=original_config,
        br_config=br_config,
        dialogues=dialogues,
    )


def ensure_target_clean(work_items: List[ChapterWork], overwrite: bool) -> None:
    conflicts = []
    for work in work_items:
        if work.target_dir.exists():
            conflicts.append(str(work.target_dir.relative_to(REPO_ROOT)))
        if work.target_zip.exists():
            conflicts.append(str(work.target_zip.relative_to(REPO_ROOT)))
    if conflicts and not overwrite:
        raise StoryBrError(
            "target already exists; pass --overwrite to replace generated BR outputs: "
            + ", ".join(conflicts)
        )


def remove_generated_target(path: Path) -> None:
    if not path.name.endswith("_br") and not path.name.endswith("_br.zip"):
        raise StoryBrError(f"refuse to remove non-BR target: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def pack_zip(repo_root: Path, folder_name: str) -> None:
    zip_path = repo_root / f"{folder_name}.zip"
    proc = subprocess.run(
        ["zip", "-r", zip_path.name, folder_name, "-x", "*.DS_Store", "-x", "__MACOSX/*"],
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        raise StoryBrError(f"zip failed for {folder_name}:\n{proc.stdout}")


def verify_work(work: ChapterWork) -> None:
    generated = load_json(work.target_dir / "config.json")
    if config_without_dialogue_values(work.original_config) != config_without_dialogue_values(generated):
        raise StoryBrError(f"{work.target_dir.name}: non-dialogue config changed")
    if generated.get("templateId") != work.original_config.get("templateId"):
        raise StoryBrError(f"{work.target_dir.name}: templateId changed")
    if generated.get("componentIds") != work.original_config.get("componentIds"):
        raise StoryBrError(f"{work.target_dir.name}: componentIds changed")
    if generated.get("result") != work.original_config.get("result"):
        raise StoryBrError(f"{work.target_dir.name}: result changed")
    if not work.target_zip.exists():
        raise StoryBrError(f"{work.target_zip.name}: zip missing")
    listing = subprocess.run(
        ["zipinfo", "-1", work.target_zip.name],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if listing.returncode != 0:
        raise StoryBrError(f"zipinfo failed for {work.target_zip.name}:\n{listing.stdout}")
    if f"{work.target_dir.name}/config.json" not in listing.stdout.splitlines():
        raise StoryBrError(f"{work.target_zip.name}: missing top-level {work.target_dir.name}/config.json")


def fetch_dialogues_for_chapters(sheet_url: str, chapters: List[int]) -> Dict[int, Dialogues]:
    infos = fetch_sheet_infos(sheet_url)
    result: Dict[int, Dialogues] = {}
    for chapter in chapters:
        raw_rows = fetch_sheet_rows(sheet_url, infos[chapter])
        canonical = canonicalize_dialog_sheet_rows(raw_rows)
        result[chapter] = extract_dialogues(canonical)
    return result


def generate_resources(repo_root: Path, sheet_url: str, chapters: List[int], dry_run: bool, overwrite: bool) -> None:
    preflight_tools()
    print(f"chapters: {chapters}")
    dialogues_by_chapter = fetch_dialogues_for_chapters(sheet_url, chapters)
    work_items = [build_work(repo_root, chapter, dialogues_by_chapter[chapter]) for chapter in chapters]
    ensure_target_clean(work_items, overwrite)
    for work in work_items:
        print(
            f"[preflight] chapter {work.chapter}: {work.source_dir.name} -> {work.target_dir.name}; "
            f"dialogues front/behind={len(work.dialogues.front)}/{len(work.dialogues.behind)}"
        )
    if dry_run:
        print("DRY-RUN complete; no files written")
        return
    for work in work_items:
        remove_generated_target(work.target_dir)
        remove_generated_target(work.target_zip)
        shutil.copytree(work.source_dir, work.target_dir)
        write_json(work.target_dir / "config.json", work.br_config)
        pack_zip(repo_root, work.target_dir.name)
        verify_work(work)
        print(f"[ok] {work.target_dir.name} and {work.target_zip.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate PT-BR story chapter resources without changing English resources")
    parser.add_argument("--chapters", default="1-24", help="Chapter range/list, e.g. 1-8 or 3,10,24")
    parser.add_argument("--sheet-url", default=SHEET_URL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing generated *_br dirs/zips only")
    args = parser.parse_args()
    generate_resources(
        repo_root=REPO_ROOT,
        sheet_url=args.sheet_url,
        chapters=parse_chapters(args.chapters),
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    try:
        main()
    except StoryBrError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
