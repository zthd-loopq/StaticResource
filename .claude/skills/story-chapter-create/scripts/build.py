#!/usr/bin/env python3
"""Validate a filled chapter workspace, build config + webp + zip, update global index."""
import argparse
import csv
import json
import re
import secrets
import string
import subprocess
import sys
from pathlib import Path
from typing import List, Set, Tuple

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[4]
GLOBAL_CONFIG = REPO_ROOT / "story_chapter_config.json"
COMPRESS_SH = REPO_ROOT / "png_compress_convert_webp.sh"

sys.path.insert(0, str(REPO_ROOT / ".claude" / "skills" / "story-resource-upgrade" / "scripts"))
from upgrade_resources import parse_content, collect_local_image_refs, UpgradeError  # noqa: E402

DEFAULT_RESOURCE_URL_TEMPLATE = "https://github.com/zthd-loopq/StaticResource/raw/refs/heads/master/{folder}.zip"
ID_LEN = 8
ID_RETRIES = 32
DIR_PATTERN = re.compile(r"^story_chapter_(\d+)_v(\d+)$")
RESULT_PLACEHOLDER = re.compile(r"^result:(\d+)$")


class BuildError(Exception):
    pass


def parse_dir_name(folder_name: str) -> Tuple[int, int]:
    m = DIR_PATTERN.match(folder_name)
    if not m:
        raise BuildError(f"invalid chapter folder name: {folder_name}")
    return int(m.group(1)), int(m.group(2))


def gen_id(existing: Set[str]) -> str:
    alphabet = string.ascii_lowercase + string.digits
    for _ in range(ID_RETRIES):
        new = "".join(secrets.choice(alphabet) for _ in range(ID_LEN))
        if new not in existing:
            return new
    raise BuildError(f"failed to generate unique id after {ID_RETRIES} retries")


def replace_result_placeholders(config: dict, chapters: List[dict], current_chapter_no: int, current_id: str):
    def resolve(value: str) -> str:
        m = RESULT_PLACEHOLDER.match(value)
        if not m:
            return value
        n = int(m.group(1))
        if n == current_chapter_no:
            return f"result:{current_id}"
        return f"result:{chapters[n - 1]['id']}"

    for block in ("front", "behind"):
        for item in config.get(block, []):
            for k in ("characterImg", "decorationImg"):
                if k in item:
                    item[k] = resolve(item[k])


def collect_result_chapter_numbers(parsed_config: dict) -> Set[int]:
    found: Set[int] = set()
    for block in ("front", "behind"):
        for item in parsed_config.get(block, []):
            for k in ("characterImg", "decorationImg"):
                v = item.get(k, "")
                m = RESULT_PLACEHOLDER.match(v)
                if m:
                    found.add(int(m.group(1)))
    return found


def run_cmd(cmd: List[str], cwd: Path):
    proc = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise BuildError(f"command failed: {' '.join(cmd)}\n{proc.stdout}")


def build_chapter_entry(chapter_id: str, meta: dict, folder_name: str, resource_url_template: str) -> dict:
    return {
        "id": chapter_id,
        "name": meta["name"],
        "coverUrl": meta["coverUrl"],
        "unlockCoverUrl": meta["unlockCoverUrl"],
        "resourceUrl": resource_url_template.format(folder=folder_name),
    }


def load_json(path: Path, label: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BuildError(f"{label} invalid: {e}")


def write_json(path: Path, obj: dict):
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def build(chapter_folder: str, dry_run: bool, resource_url_template: str):
    folder_name = chapter_folder.rstrip("/").split("/")[-1]
    chapter_no, version = parse_dir_name(folder_name)
    chapter_dir = REPO_ROOT / folder_name

    # Check 1: chapter folder exists
    if not chapter_dir.is_dir():
        raise BuildError(f"chapter folder not found: {chapter_dir}")

    # Check 2: images/ directory exists
    images_dir = chapter_dir / "images"
    if not images_dir.is_dir():
        raise BuildError("images/ directory missing")

    # Check 3: images/ has at least one png/webp
    image_files = [
        p for p in images_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".png", ".webp") and p.name != ".DS_Store"
    ]
    if not image_files:
        raise BuildError("images/ has no png or webp files")

    # Check 4: dialog.txt exists
    dialog_path = chapter_dir / "dialog.txt"
    if not dialog_path.exists():
        raise BuildError("dialog.txt missing")

    # Check 5: dialog.txt has data rows beyond header
    with dialog_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t", quotechar='"'))
    data_rows_nonempty = [r for r in rows[1:] if any(c.strip() for c in r)]
    if not data_rows_nonempty:
        raise BuildError("dialog.txt has no data rows (still template?)")

    # Check 6: setting.json exists and is valid JSON
    setting_path = chapter_dir / "setting.json"
    if not setting_path.exists():
        raise BuildError("setting.json missing")
    setting = load_json(setting_path, "setting.json")

    # Check 7: setting.json.templateId non-empty
    template_id = setting.get("templateId")
    if not isinstance(template_id, str) or not template_id:
        raise BuildError("setting.json: templateId is empty")

    # Check 8: setting.json.componentIds is non-empty array
    component_ids = setting.get("componentIds")
    if not isinstance(component_ids, list) or not component_ids:
        raise BuildError("setting.json: componentIds is empty")

    # Check 9: meta.json exists and is valid JSON
    meta_path = chapter_dir / "meta.json"
    if not meta_path.exists():
        raise BuildError("meta.json missing")
    meta = load_json(meta_path, "meta.json")

    # Check 11: global index exists
    if not GLOBAL_CONFIG.exists():
        raise BuildError(f"global index not found: {GLOBAL_CONFIG.name}")
    global_index = load_json(GLOBAL_CONFIG, GLOBAL_CONFIG.name)
    chapters = global_index.get("chapters")
    if not isinstance(chapters, list):
        raise BuildError(f"{GLOBAL_CONFIG.name}: chapters is not a list")

    # Check 12: chapter number gap
    if chapter_no > len(chapters) + 1:
        raise BuildError(f"chapter number gap: N={chapter_no} but global has only {len(chapters)}")

    # Check 10: meta.json fields non-empty (only required for new chapters)
    is_new_chapter = chapter_no == len(chapters) + 1
    if is_new_chapter:
        for k in ("name", "coverUrl", "unlockCoverUrl"):
            v = meta.get(k)
            if not isinstance(v, str) or not v:
                raise BuildError(f"meta.json: {k} is empty")
    else:
        # Re-build of an existing chapter: still require meta to be filled
        # (intermediate files would have been deleted on prior success).
        for k in ("name", "coverUrl", "unlockCoverUrl"):
            v = meta.get(k)
            if not isinstance(v, str) or not v:
                raise BuildError(f"meta.json: {k} is empty")

    # Parse dialog.txt -> front/result/behind
    try:
        front, result, behind = parse_content(dialog_path)
    except UpgradeError as e:
        raise BuildError(f"dialog.txt parse failed: {e}")
    parsed_config = {
        "templateId": template_id,
        "componentIds": component_ids,
        "front": front,
        "result": result,
        "behind": behind,
    }

    # Check 13: result:M references valid
    referenced_ms = collect_result_chapter_numbers(parsed_config)
    for n in referenced_ms:
        if n == chapter_no:
            continue
        if n < 1 or n > len(chapters):
            raise BuildError(f"dialog.txt: result:{n} references missing chapter (only {len(chapters)} chapters in global index)")

    # Pipeline step 1: id prep
    if chapter_no <= len(chapters):
        existing_id = chapters[chapter_no - 1].get("id")
        if not existing_id:
            raise BuildError(f"existing chapters[{chapter_no - 1}] has empty id")
        chapter_id = existing_id
    else:
        existing_ids = {c.get("id") for c in chapters if c.get("id")}
        chapter_id = gen_id(existing_ids)

    # Pipeline step 2: replace result:M with concrete ids
    replace_result_placeholders(parsed_config, chapters, chapter_no, chapter_id)

    # Check 14: image refs all present
    refs = collect_local_image_refs(parsed_config)
    image_names = {p.name for p in image_files}
    missing_refs = []
    for r in sorted(refs):
        if r in image_names:
            continue
        if r.lower().endswith(".webp"):
            png_alt = r[:-5] + ".png"
            if png_alt in image_names:
                continue
        missing_refs.append(r)
    if missing_refs:
        preview = ", ".join(missing_refs[:5])
        if len(missing_refs) > 5:
            preview += f" ... (+{len(missing_refs) - 5})"
        raise BuildError(f"missing image: {preview}")

    new_entry = build_chapter_entry(chapter_id, meta, folder_name, resource_url_template)

    if dry_run:
        print("=== DRY RUN ===")
        print(f"chapter_no={chapter_no} version={version} {'(new)' if is_new_chapter else '(rebuild existing)'}")
        print(f"chapter_id={chapter_id}")
        print(f"templateId={template_id}")
        print(f"componentIds: {len(component_ids)} items")
        print(f"front: {len(front)} entries, behind: {len(behind)} entries")
        print(f"result: {result}")
        print(f"image refs: {sorted(refs)}")
        print()
        print("--- config.json preview ---")
        print(json.dumps(parsed_config, ensure_ascii=False, indent=2))
        print()
        print(f"--- chapters[{chapter_no - 1}] (would write) ---")
        print(json.dumps(new_entry, ensure_ascii=False, indent=2))
        if meta.get("limitCurrent"):
            print(f"--- limitChapterId would be set to: {chapter_id} ---")
        return

    # Pipeline step 3: write config.json
    config_path = chapter_dir / "config.json"
    write_json(config_path, parsed_config)
    print(f"✓ wrote {config_path.relative_to(REPO_ROOT)}")

    # Pipeline step 4: image pipeline (only if there are pngs)
    if not COMPRESS_SH.exists():
        raise BuildError(f"missing script: {COMPRESS_SH}")

    has_png = any(p.suffix.lower() == ".png" for p in image_files)
    if has_png:
        run_cmd([str(COMPRESS_SH), str(images_dir)], cwd=REPO_ROOT)
        print(f"✓ image pipeline (pngquant + cwebp) done")

    # Delete unreferenced webp
    for p in list(images_dir.iterdir()):
        if not p.is_file() or p.name == ".DS_Store":
            continue
        if p.suffix.lower() == ".webp" and p.name not in refs:
            p.unlink()
            print(f"  removed unreferenced: {p.name}")

    # Pipeline step 5: pack zip
    zip_path = REPO_ROOT / f"{folder_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    run_cmd(
        ["zip", "-r", zip_path.name, folder_name, "-x", "*.DS_Store", "-x", "__MACOSX/*"],
        cwd=REPO_ROOT,
    )
    print(f"✓ packed {zip_path.relative_to(REPO_ROOT)}")

    # Pipeline step 6: update global index
    if chapter_no <= len(chapters):
        chapters[chapter_no - 1] = new_entry
    else:
        chapters.append(new_entry)
    if meta.get("limitCurrent"):
        global_index["limitChapterId"] = chapter_id
    write_json(GLOBAL_CONFIG, global_index)
    print(f"✓ updated {GLOBAL_CONFIG.name} (chapters[{chapter_no - 1}].id={chapter_id})")
    if meta.get("limitCurrent"):
        print(f"  limitChapterId = {chapter_id}")

    # Pipeline step 7: cleanup
    dialog_path.unlink()
    setting_path.unlink()
    meta_path.unlink()
    print("✓ cleaned dialog.txt / setting.json / meta.json")


def main():
    parser = argparse.ArgumentParser(description="Build chapter from filled workspace and update global index")
    parser.add_argument("chapter_folder", help="Chapter folder name, e.g. story_chapter_15_v1")
    parser.add_argument("--dry-run", action="store_true", help="Validate + preview, do not write")
    parser.add_argument(
        "--resource-url-template",
        default=DEFAULT_RESOURCE_URL_TEMPLATE,
        help=f"URL template with {{folder}} placeholder; default: {DEFAULT_RESOURCE_URL_TEMPLATE}",
    )
    args = parser.parse_args()
    build(args.chapter_folder, args.dry_run, args.resource_url_template)


if __name__ == "__main__":
    try:
        main()
    except BuildError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
