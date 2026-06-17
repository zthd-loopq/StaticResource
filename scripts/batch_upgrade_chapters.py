#!/usr/bin/env python3
"""Batch upgrade story chapters 1-24: new dialog + new images, bump version."""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".claude" / "skills" / "story-resource-upgrade" / "scripts"))
from upgrade_resources import (  # noqa: E402
    UpgradeError,
    collect_local_image_refs,
    parse_rows,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPRESS_SH = REPO_ROOT / "png_compress_convert_webp.sh"
GLOBAL_DOC_URL = "https://stickerstyle.feishu.cn/wiki/A3n2wf4nai2WYkkSQn8c3hscnQg"
SPREADSHEET_TOKEN = "Qy6Fs4U5NhnMtLtcpHKc6xGbngf"

RESULT_PLACEHOLDER = re.compile(r"^result:(\d+)$")
DIR_PATTERN = re.compile(r"^story_chapter_(\d+)_v(\d+)$")
JSON_BLOCK_RE = re.compile(r"(```JSON\n)(.*?)(\n```)", re.DOTALL)

DIALOG_OUTPUT_ORDER = ("对话", "人物", "人物图", "背景", "位置", "装饰")


class BatchUpgradeError(Exception):
    pass


def run_cmd(cmd: List[str], cwd: Path = REPO_ROOT) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise BatchUpgradeError(f"command failed: {' '.join(cmd)}\n{proc.stdout}")
    return proc.stdout


def _run_lark_cli(cmd: List[str], stdin: str = None) -> dict:
    try:
        proc = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise BatchUpgradeError("lark-cli not found")
    out = proc.stdout or ""
    if proc.returncode != 0 and not out.strip():
        err = proc.stderr or ""
        raise BatchUpgradeError(f"lark-cli failed (exit {proc.returncode}): {' '.join(cmd)}\n{err}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as e:
        raise BatchUpgradeError(f"lark-cli output not JSON: {e}\n{out[:500]}")
    if not payload.get("ok"):
        err = payload.get("error") or {}
        msg = err.get("message") or "unknown error"
        hint = err.get("hint")
        console_url = err.get("console_url")
        parts = [f"lark-cli failed: {msg}", f"cmd: {' '.join(cmd)}"]
        if hint:
            parts.append(f"hint: {hint}")
        if console_url:
            parts.append(f"console_url: {console_url}")
        raise BatchUpgradeError("\n".join(parts))
    return payload


def fetch_global_doc() -> Tuple[str, dict]:
    print(f"→ fetch global doc: {GLOBAL_DOC_URL}")
    payload = _run_lark_cli([
        "lark-cli", "docs", "+fetch",
        "--api-version", "v2",
        "--doc", GLOBAL_DOC_URL,
        "--doc-format", "markdown",
        "--as", "user",
    ])
    content = (((payload.get("data") or {}).get("document") or {})).get("content")
    if not isinstance(content, str) or not content:
        raise BatchUpgradeError("global doc fetch: data.document.content missing")
    m = JSON_BLOCK_RE.search(content)
    if not m:
        raise BatchUpgradeError("global doc fetch: no ```JSON``` block found")
    try:
        index = json.loads(m.group(2))
    except json.JSONDecodeError as e:
        raise BatchUpgradeError(f"global doc fetch: JSON block parse failed: {e}")
    if not isinstance(index.get("chapters"), list):
        raise BatchUpgradeError("global doc fetch: chapters is not a list")
    return content, index


def write_global_doc(original_md: str, new_index: dict) -> None:
    new_json = json.dumps(new_index, ensure_ascii=False, indent=2)
    new_md, n = JSON_BLOCK_RE.subn(
        lambda mm: mm.group(1) + new_json + mm.group(3),
        original_md,
        count=1,
    )
    if n != 1:
        raise BatchUpgradeError("write_global_doc: JSON block replacement failed")
    print("→ write global doc back to Feishu")
    _run_lark_cli(
        [
            "lark-cli", "docs", "+update",
            "--api-version", "v2",
            "--doc", GLOBAL_DOC_URL,
            "--command", "overwrite",
            "--content", "-",
            "--doc-format", "markdown",
            "--as", "user",
        ],
        stdin=new_md,
    )


def fetch_sheet_metadata() -> Dict[int, str]:
    """Return mapping chapter_no -> sheet_id."""
    print(f"→ fetch sheet metadata: {SPREADSHEET_TOKEN}")
    payload = _run_lark_cli([
        "lark-cli", "sheets", "+workbook-info",
        "--spreadsheet-token", SPREADSHEET_TOKEN,
        "--as", "user",
    ])
    sheets = (payload.get("data") or {}).get("sheets") or []
    mapping: Dict[int, str] = {}
    for s in sheets:
        name = s.get("sheet_name", "").strip()
        if name.isdigit():
            mapping[int(name)] = s["sheet_id"]
    return mapping


def fetch_dialog_sheet(sheet_id: str) -> List[List[str]]:
    url = f"https://stickerstyle.feishu.cn/sheets/{SPREADSHEET_TOKEN}?sheet={sheet_id}"
    cmd = [
        "lark-cli", "sheets", "+read",
        "--url", url,
        "--range", f"{sheet_id}!A:F",
        "--as", "user",
    ]
    payload = _run_lark_cli(cmd)
    values = ((payload.get("data") or {}).get("valueRange") or {}).get("values")
    if not values:
        raise BatchUpgradeError(f"sheet {sheet_id}: no data")
    return values


def normalize_cell(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def canonicalize_sheet_rows(raw_rows: List[List]) -> List[List[str]]:
    if not raw_rows:
        raise BatchUpgradeError("sheet is empty")

    # Some sheets have a title row before the header row (e.g. "章节1-引入剧情").
    start_idx = 0
    first_row = [normalize_cell(c) for c in raw_rows[0]]
    required = ("对话", "人物", "人物图", "背景", "位置")
    if not any(name in first_row for name in required):
        start_idx = 1
        if len(raw_rows) < 2:
            raise BatchUpgradeError("sheet has only title row, no header")

    header = [normalize_cell(c) for c in raw_rows[start_idx]]
    for name in required:
        if name not in header:
            raise BatchUpgradeError(f"sheet header missing column: {name} (actual: {header})")

    indices = [header.index(name) if name in header else -1 for name in DIALOG_OUTPUT_ORDER]

    out: List[List[str]] = [list(DIALOG_OUTPUT_ORDER)]
    for row in raw_rows[start_idx + 1:]:
        cells = []
        for i in indices:
            if i == -1:
                cells.append("")
            else:
                v = row[i] if i < len(row) else ""
                cells.append(normalize_cell(v))
        # Skip repeated header rows that sometimes appear after "结果页衔接"
        if cells[:5] == list(DIALOG_OUTPUT_ORDER)[:5]:
            continue
        out.append(cells)
    return out


def replace_result_placeholders(parsed_config: dict, chapters: List[dict], chapter_no: int):
    def resolve(value: str) -> str:
        m = RESULT_PLACEHOLDER.match(value)
        if not m:
            return value
        n = int(m.group(1))
        if n < 1 or n > len(chapters):
            raise BatchUpgradeError(f"result:{n} references missing chapter (only {len(chapters)} chapters)")
        return f"result:{chapters[n - 1]['id']}"

    for block in ("front", "behind"):
        for item in parsed_config.get(block, []):
            for k in ("characterImg", "decorationImg"):
                if k in item:
                    item[k] = resolve(item[k])


def find_latest_chapter_dir(chapter: int) -> Tuple[Path, int]:
    dirs = [p for p in REPO_ROOT.glob(f"story_chapter_{chapter}_v*") if p.is_dir()]
    if not dirs:
        raise BatchUpgradeError(f"chapter {chapter}: no folder found")
    candidates: List[Tuple[int, Path]] = []
    for d in dirs:
        m = DIR_PATTERN.match(d.name)
        if m:
            candidates.append((int(m.group(2)), d))
    if not candidates:
        raise BatchUpgradeError(f"chapter {chapter}: no valid versioned folder found")
    candidates.sort(key=lambda x: x[0])
    ver, path = candidates[-1]
    return path, ver


def extract_wiki_version(chapter_entry: dict) -> int:
    url = chapter_entry.get("resourceUrl", "")
    m = re.search(r"story_chapter_\d+_v(\d+)\.zip", url)
    if m:
        return int(m.group(1))
    return 0


def decide_new_version(chapter: int, local_ver: int, chapter_entry: dict) -> int:
    wiki_ver = extract_wiki_version(chapter_entry)
    base = max(local_ver, wiki_ver)
    print(f"  local_ver={local_ver} wiki_ver={wiki_ver} -> new_ver={base + 1}")
    return base + 1


def load_json(path: Path, label: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BatchUpgradeError(f"{label} invalid JSON: {e}")


def write_json(path: Path, obj: dict):
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def check_images_exist(refs: Set[str], source_dir: Path, chapter: int):
    source_names = {p.name for p in source_dir.iterdir() if p.is_file() and p.name != ".DS_Store"}
    missing: List[str] = []
    for name in sorted(refs):
        if name in source_names:
            continue
        if name.lower().endswith(".webp"):
            alt = name[:-5] + ".png"
            if alt in source_names:
                continue
        missing.append(name)
    if missing:
        preview = ", ".join(missing[:10])
        if len(missing) > 10:
            preview += f" ...(+{len(missing)-10})"
        raise BatchUpgradeError(f"chapter {chapter}: missing required images: {preview}")


def copy_and_compress_images(source_dir: Path, target_images_dir: Path, chapter: int):
    if target_images_dir.exists():
        shutil.rmtree(target_images_dir)
    target_images_dir.mkdir(parents=True)

    png_copied = 0
    for p in source_dir.iterdir():
        if not p.is_file() or p.name == ".DS_Store":
            continue
        if p.suffix.lower() == ".png":
            shutil.copy2(p, target_images_dir / p.name)
            png_copied += 1
        elif p.suffix.lower() == ".webp":
            shutil.copy2(p, target_images_dir / p.name)

    if png_copied == 0:
        raise BatchUpgradeError(f"chapter {chapter}: no PNG images to compress in {source_dir}")

    if not COMPRESS_SH.exists():
        raise BatchUpgradeError(f"missing script: {COMPRESS_SH}")
    run_cmd([str(COMPRESS_SH), str(target_images_dir)])


def remove_unreferenced_images(images_dir: Path, refs: Set[str]):
    for p in list(images_dir.iterdir()):
        if not p.is_file() or p.name == ".DS_Store":
            continue
        if p.suffix.lower() == ".webp" and p.name not in refs:
            p.unlink()
            print(f"  removed unreferenced: {p.name}")


def pack_zip(folder_name: str):
    zip_path = REPO_ROOT / f"{folder_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    run_cmd(
        ["zip", "-r", zip_path.name, folder_name, "-x", "*.DS_Store", "-x", "__MACOSX/*"],
        cwd=REPO_ROOT,
    )
    print(f"  packed {zip_path.name}")


def delete_old_version(old_dir: Path, old_zip: Path):
    if old_dir.exists():
        shutil.rmtree(old_dir)
        print(f"  deleted old dir: {old_dir.name}")
    if old_zip.exists():
        old_zip.unlink()
        print(f"  deleted old zip: {old_zip.name}")


def upgrade_chapter(chapter: int, sheet_id: str, chapters: List[dict], dry_run: bool) -> Tuple[int, str, str]:
    print(f"\n[chapter {chapter}]")

    old_dir, local_ver = find_latest_chapter_dir(chapter)
    chapter_entry = chapters[chapter - 1]
    new_ver = decide_new_version(chapter, local_ver, chapter_entry)
    new_folder = f"story_chapter_{chapter}_v{new_ver}"
    new_dir = REPO_ROOT / new_folder

    print(f"  old={old_dir.name} -> new={new_folder}")

    old_config = load_json(old_dir / "config.json", f"{old_dir.name}/config.json")
    template_id = old_config.get("templateId")
    component_ids = old_config.get("componentIds")
    if not template_id or not component_ids:
        raise BatchUpgradeError(f"chapter {chapter}: templateId or componentIds missing in old config")

    print(f"→ fetch dialog sheet {sheet_id}")
    raw_rows = fetch_dialog_sheet(sheet_id)
    canonical_rows = canonicalize_sheet_rows(raw_rows)
    print(f"  rows={len(raw_rows)} canonical={len(canonical_rows)}")

    try:
        front, result, behind = parse_rows(canonical_rows, f"chapter {chapter}")
    except UpgradeError as e:
        raise BatchUpgradeError(f"chapter {chapter}: dialog parse failed: {e}")

    parsed_config = {
        "templateId": template_id,
        "componentIds": component_ids,
        "front": front,
        "result": result,
        "behind": behind,
    }

    replace_result_placeholders(parsed_config, chapters, chapter)

    refs = collect_local_image_refs(parsed_config)
    print(f"  image refs: {len(refs)}")

    source_dir = Path("/Users/loopq/Downloads/简化章节资源") / str(chapter)
    if not source_dir.is_dir():
        raise BatchUpgradeError(f"chapter {chapter}: source image dir not found: {source_dir}")
    check_images_exist(refs, source_dir, chapter)

    if dry_run:
        print(f"  DRY-RUN: would create {new_folder}, {len(refs)} images, {len(front)} front / {len(behind)} behind")
        return chapter, old_dir.name, new_folder

    if new_dir.exists():
        raise BatchUpgradeError(f"chapter {chapter}: target dir already exists: {new_folder}")

    new_dir.mkdir(parents=True)
    target_images_dir = new_dir / "images"

    copy_and_compress_images(source_dir, target_images_dir, chapter)
    remove_unreferenced_images(target_images_dir, refs)

    write_json(new_dir / "config.json", parsed_config)
    print(f"  wrote config.json")

    pack_zip(new_folder)

    old_zip = REPO_ROOT / f"{old_dir.name}.zip"
    delete_old_version(old_dir, old_zip)

    return chapter, old_dir.name, new_folder


def update_wiki_resource_urls(md_text: str, index: dict, renames: List[Tuple[int, str, str]]) -> dict:
    new_index = json.loads(json.dumps(index))
    chapters = new_index.get("chapters", [])
    updated = 0
    for chapter_no, old_folder, new_folder in renames:
        idx = chapter_no - 1
        if idx >= len(chapters):
            continue
        ch = chapters[idx]
        resource_url = ch.get("resourceUrl", "")
        # Replace the file name portion (story_chapter_N_vX.zip) with new_folder.zip
        new_url = re.sub(r"story_chapter_\d+_v\d+\.zip$", f"{new_folder}.zip", resource_url)
        if new_url != resource_url:
            ch["resourceUrl"] = new_url
            updated += 1
    print(f"→ updated {updated} resourceUrl entries")
    return new_index


def get_image_refs_for_chapter(chapter: int, sheet_id: str, chapters: List[dict]) -> Set[str]:
    raw_rows = fetch_dialog_sheet(sheet_id)
    canonical_rows = canonicalize_sheet_rows(raw_rows)
    front, result, behind = parse_rows(canonical_rows, f"chapter {chapter}")
    parsed_config = {
        "templateId": "",
        "componentIds": [],
        "front": front,
        "result": result,
        "behind": behind,
    }
    replace_result_placeholders(parsed_config, chapters, chapter)
    return collect_local_image_refs(parsed_config)


def parse_chapters(expr: str) -> List[int]:
    out: List[int] = []
    for token in expr.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            s, e = token.split("-", 1)
            out.extend(range(int(s), int(e) + 1))
        else:
            out.append(int(token))
    return sorted(set(out))


def check_all_images(chapters_to_check: List[int]):
    print(f"=== Check missing images for chapters {chapters_to_check} ===\n")
    sheet_mapping = fetch_sheet_metadata()
    missing_sheets = [i for i in chapters_to_check if i not in sheet_mapping]
    if missing_sheets:
        raise BatchUpgradeError(f"missing sheets for chapters: {missing_sheets}")

    _, index = fetch_global_doc()
    chapters = index.get("chapters", [])
    if len(chapters) < max(chapters_to_check):
        raise BatchUpgradeError(f"global index has only {len(chapters)} chapters")

    total_missing = 0
    for chapter in chapters_to_check:
        source_dir = Path("/Users/loopq/Downloads/简化章节资源") / str(chapter)
        if not source_dir.is_dir():
            print(f"[chapter {chapter}] SOURCE DIR MISSING: {source_dir}")
            total_missing += 1
            continue

        try:
            refs = get_image_refs_for_chapter(chapter, sheet_mapping[chapter], chapters)
        except BatchUpgradeError as e:
            print(f"[chapter {chapter}] PARSE ERROR: {e}")
            total_missing += 1
            continue

        source_names = {p.name for p in source_dir.iterdir() if p.is_file() and p.name != ".DS_Store"}
        missing: List[str] = []
        for name in sorted(refs):
            if name in source_names:
                continue
            if name.lower().endswith(".webp"):
                alt = name[:-5] + ".png"
                if alt in source_names:
                    continue
            missing.append(name)

        if missing:
            total_missing += len(missing)
            print(f"[chapter {chapter}] missing {len(missing)}:")
            for m in missing:
                print(f"    - {m}")
        else:
            print(f"[chapter {chapter}] ok ({len(refs)} refs)")

    print(f"\n=== total missing: {total_missing} ===")
    return total_missing


def main():
    parser = argparse.ArgumentParser(description="Batch upgrade story chapters 1-24")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write files or update wiki")
    parser.add_argument("--check-images", action="store_true", help="Only check which referenced images are missing in source dirs")
    parser.add_argument("--chapters", default="1-24", help="Chapter range to process, e.g. 3,18 or 1-24")
    args = parser.parse_args()

    chapters_to_check = parse_chapters(args.chapters)

    if args.check_images:
        missing = check_all_images(chapters_to_check)
        sys.exit(1 if missing > 0 else 0)

    if not COMPRESS_SH.exists():
        raise BatchUpgradeError(f"missing script: {COMPRESS_SH}")

    md_text, index = fetch_global_doc()
    chapters = index.get("chapters", [])
    if len(chapters) < 24:
        raise BatchUpgradeError(f"global index has only {len(chapters)} chapters, expected 24")

    sheet_mapping = fetch_sheet_metadata()
    missing_sheets = [i for i in range(1, 25) if i not in sheet_mapping]
    if missing_sheets:
        raise BatchUpgradeError(f"missing sheets for chapters: {missing_sheets}")

    renames: List[Tuple[int, str, str]] = []

    for chapter in range(1, 25):
        chapter_no, old_folder, new_folder = upgrade_chapter(chapter, sheet_mapping[chapter], chapters, args.dry_run)
        renames.append((chapter_no, old_folder, new_folder))

    new_index = update_wiki_resource_urls(md_text, index, renames)

    if args.dry_run:
        print("\n=== DRY RUN COMPLETE ===")
        print("Renames:")
        for _, old, new in renames:
            print(f"  {old} -> {new}")
        print("\nWiki resourceUrl diff preview:")
        old_urls = [c.get("resourceUrl", "") for c in index.get("chapters", [])]
        new_urls = [c.get("resourceUrl", "") for c in new_index.get("chapters", [])]
        for i, (o, n) in enumerate(zip(old_urls, new_urls)):
            if o != n:
                print(f"  chapters[{i}]: {o} -> {n}")
        return

    write_global_doc(md_text, new_index)
    print("\n=== UPGRADE COMPLETE ===")
    for _, old, new in renames:
        print(f"  {old} -> {new}")


if __name__ == "__main__":
    try:
        main()
    except BatchUpgradeError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] command failed: {e}", file=sys.stderr)
        sys.exit(1)
