#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

POS_MAP = {"居左": 0, "居中": 1, "居右": 2}
IMAGE_FIELDS = ("characterImg", "backgroundImg", "decorationImg")


class UpgradeError(Exception):
    pass


@dataclass
class ChapterPlan:
    chapter: int
    current_dir: Path
    next_dir: Path
    source_dir: Path
    expected_config: dict
    required_images: Set[str]
    stage_dir: Path
    stage_images_dir: Path
    config_changed: bool = False
    image_changed: bool = False



def parse_chapters(expr: str) -> List[int]:
    out: List[int] = []
    for token in expr.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            s, e = token.split("-", 1)
            start = int(s)
            end = int(e)
            if start > end:
                raise UpgradeError(f"invalid chapter range: {token}")
            out.extend(range(start, end + 1))
        else:
            out.append(int(token))
    uniq = sorted(set(out))
    if not uniq:
        raise UpgradeError("chapters is empty")
    return uniq



def extract_version(chapter_dir: Path, chapter: int) -> int:
    m = re.match(rf"^story_chapter_{chapter}_v(\d+)$", chapter_dir.name)
    if not m:
        raise UpgradeError(f"invalid chapter dir name: {chapter_dir.name}")
    return int(m.group(1))



def find_latest_chapter_dir(repo_root: Path, chapter: int) -> Tuple[Path, int]:
    dirs = [p for p in repo_root.glob(f"story_chapter_{chapter}_v*") if p.is_dir()]
    if not dirs:
        raise UpgradeError(f"chapter_{chapter}: no folder found")
    candidates: List[Tuple[int, Path]] = []
    for d in dirs:
        try:
            candidates.append((extract_version(d, chapter), d))
        except UpgradeError:
            continue
    if not candidates:
        raise UpgradeError(f"chapter_{chapter}: no valid versioned folder found")
    candidates.sort(key=lambda x: x[0])
    ver, path = candidates[-1]
    return path, ver



def clean_text(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    return re.sub(r"\s+", " ", v).strip()



def png_to_webp_name(v: str) -> str:
    if v.lower().endswith(".png"):
        return v[:-4] + ".webp"
    return v



def merge_component_ids(base_ids: List[str], chapter_ids: List[str]) -> List[str]:
    merged: List[str] = []
    seen: Set[str] = set()
    for cid in base_ids + chapter_ids:
        if cid not in seen:
            merged.append(cid)
            seen.add(cid)
    return merged



def parse_content(content_path: Path) -> Tuple[List[dict], dict, List[dict]]:
    with content_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t", quotechar='"'))

    if not rows:
        raise UpgradeError(f"{content_path}: content is empty")

    data_rows = rows[1:]
    front: List[dict] = []
    behind: List[dict] = []
    result: Dict[str, str] = {}
    in_behind = False
    waiting_result_bg = False

    for row in data_rows:
        cols = list(row)
        if len(cols) < 6:
            cols.extend([""] * (6 - len(cols)))
        cols = [clean_text(c) for c in cols[:6]]
        dialogue, character_name, character_img, background_img, character_pos, decoration_img = cols

        if dialogue == "结果页":
            waiting_result_bg = True
            continue

        if dialogue == "结果页衔接":
            in_behind = True
            waiting_result_bg = False
            continue

        if all(not c for c in cols):
            continue

        if waiting_result_bg and background_img:
            result["backgroundImg"] = png_to_webp_name(background_img)
            waiting_result_bg = False
            continue

        entry = {}
        if dialogue:
            entry["dialogue"] = dialogue
        if character_name:
            entry["characterName"] = "special:narration" if character_name == "旁白" else character_name
        if character_img:
            entry["characterImg"] = png_to_webp_name(character_img)
        if background_img:
            entry["backgroundImg"] = png_to_webp_name(background_img)
        if character_pos in POS_MAP:
            entry["characterPos"] = POS_MAP[character_pos]
        if decoration_img:
            entry["decorationImg"] = png_to_webp_name(decoration_img)

        if not entry:
            continue

        if in_behind:
            behind.append(entry)
        else:
            front.append(entry)

    if "backgroundImg" not in result:
        raise UpgradeError(f"{content_path}: result.backgroundImg missing")

    has_dialogue = any("dialogue" in x and x["dialogue"] for x in (front + behind))
    if not has_dialogue:
        raise UpgradeError(f"{content_path}: no dialogue rows found")

    return front, result, behind



def collect_local_image_refs(config_obj: dict) -> Set[str]:
    refs: Set[str] = set()

    def add_image(v: str):
        if not v:
            return
        if ":" in v:
            return
        refs.add(v)

    result = config_obj.get("result", {})
    if isinstance(result, dict):
        add_image(result.get("backgroundImg", ""))

    for block in ("front", "behind"):
        items = config_obj.get(block, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for field in IMAGE_FIELDS:
                if field in item:
                    add_image(item[field])

    return refs



def resolve_source_chapter_dir(images_root: Path, chapter: int) -> Path:
    preferred = [
        images_root / f"章节{chapter}",
        images_root / f"chapter_{chapter}",
        images_root / f"story_chapter_{chapter}",
        images_root / str(chapter),
    ]
    for p in preferred:
        if p.is_dir():
            return p

    candidates = [
        p for p in images_root.iterdir()
        if p.is_dir() and re.search(rf"(?:章节|chapter_?|_)?{chapter}(?:$|[^0-9])", p.name)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = ", ".join(sorted(p.name for p in candidates))
        raise UpgradeError(f"chapter_{chapter}: multiple source dirs matched: {names}")

    raise UpgradeError(f"chapter_{chapter}: source image dir not found under {images_root}")



def build_source_name_set(source_dir: Path) -> Set[str]:
    names: Set[str] = set()
    for p in source_dir.iterdir():
        if not p.is_file():
            continue
        if p.name == ".DS_Store":
            continue
        lower = p.suffix.lower()
        if lower in (".png", ".webp"):
            names.add(p.name)
    return names



def check_required_images_exist(required_images: Set[str], source_names: Set[str], chapter: int):
    missing: List[str] = []
    for name in sorted(required_images):
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
        raise UpgradeError(f"chapter_{chapter}: missing required images: {preview}")



def copy_source_images(source_dir: Path, target_images_dir: Path):
    target_images_dir.mkdir(parents=True, exist_ok=True)
    for p in source_dir.iterdir():
        if not p.is_file() or p.name == ".DS_Store":
            continue
        if p.suffix.lower() not in (".png", ".webp"):
            continue
        shutil.copy2(p, target_images_dir / p.name)



def run_cmd(cmd: List[str], cwd: Path):
    proc = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise UpgradeError(f"command failed: {' '.join(cmd)}\n{proc.stdout}")



def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()



def build_image_hash_map(images_dir: Path) -> Dict[str, str]:
    if not images_dir.exists():
        return {}
    out: Dict[str, str] = {}
    for p in sorted(images_dir.iterdir()):
        if not p.is_file() or p.name == ".DS_Store":
            continue
        out[p.name] = sha256_file(p)
    return out



def write_json(path: Path, obj: dict):
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")



def load_storyline_mapping(paths: List[Path], target_chapters: Set[int]) -> Dict[int, dict]:
    mapping: Dict[int, dict] = {}
    for p in paths:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        tid = data.get("tid")
        base = data.get("baseCptIds", [])
        chapters = data.get("chapters", {})
        if not isinstance(chapters, dict):
            raise UpgradeError(f"{p}: chapters is not an object")
        for k, v in chapters.items():
            m = re.match(r"^chapter_(\d+)$", str(k))
            if not m:
                continue
            ch = int(m.group(1))
            if ch not in target_chapters:
                continue
            if not isinstance(v, list):
                raise UpgradeError(f"{p}: chapter_{ch} ids is not a list")

            candidate = {
                "tid": tid,
                "base": base,
                "chapter_ids": v,
                "storyline_path": str(p),
            }
            if ch not in mapping:
                mapping[ch] = candidate
                continue

            existing = mapping[ch]
            existing_len = len(existing.get("chapter_ids", []))
            candidate_len = len(v)

            # Empty chapter entries are placeholders in many storyline files.
            # Prefer non-empty mapping if one side is empty.
            if existing_len == 0 and candidate_len > 0:
                mapping[ch] = candidate
                continue
            if existing_len > 0 and candidate_len == 0:
                continue
            if existing_len == 0 and candidate_len == 0:
                continue

            # Both non-empty and different => ambiguous mapping.
            if existing["tid"] != tid or existing["base"] != base or existing["chapter_ids"] != v:
                raise UpgradeError(
                    f"chapter_{ch} has conflicting non-empty definitions: "
                    f"{existing['storyline_path']} vs {p}"
                )
    return mapping



def plan_chapter(
    repo_root: Path,
    chapter: int,
    images_root: Path,
    story_map: Dict[int, dict],
    stage_root: Path,
    compress_script: Path,
    png_to_webp_script: Path,
) -> ChapterPlan:
    if chapter not in story_map:
        raise UpgradeError(f"chapter_{chapter}: not found in storyline inputs")

    meta = story_map[chapter]
    tid = meta["tid"]
    base_ids = meta["base"]
    chapter_ids = meta["chapter_ids"]

    current_dir, cur_ver = find_latest_chapter_dir(repo_root, chapter)
    content_path = current_dir / "content"
    config_path = current_dir / "config.json"

    if not content_path.exists():
        raise UpgradeError(f"chapter_{chapter}: missing content file: {content_path}")
    if not config_path.exists():
        raise UpgradeError(f"chapter_{chapter}: missing config file: {config_path}")

    front, result, behind = parse_content(content_path)
    expected = {
        "templateId": tid,
        "componentIds": merge_component_ids(base_ids, chapter_ids),
        "front": front,
        "result": result,
        "behind": behind,
    }

    source_dir = resolve_source_chapter_dir(images_root, chapter)
    source_names = build_source_name_set(source_dir)
    if not source_names:
        raise UpgradeError(f"chapter_{chapter}: no source image files in {source_dir}")

    required_images = collect_local_image_refs(expected)
    check_required_images_exist(required_images, source_names, chapter)

    next_dir = repo_root / f"story_chapter_{chapter}_v{cur_ver + 1}"
    if next_dir.exists():
        raise UpgradeError(f"chapter_{chapter}: target already exists: {next_dir.name}")

    stage_dir = stage_root / next_dir.name
    stage_images = stage_dir / "images"
    stage_dir.mkdir(parents=True, exist_ok=True)
    copy_source_images(source_dir, stage_images)
    write_json(stage_dir / "config.json", expected)

    # Fixed order: compress_images.sh png -> png_to_webp.sh
    run_cmd([str(compress_script), "png", stage_dir.name], cwd=stage_root)
    run_cmd([str(png_to_webp_script)], cwd=stage_root)

    # Validate staged required images exist after conversion.
    staged_names = set(p.name for p in stage_images.iterdir() if p.is_file())
    missing_after_stage = [x for x in sorted(required_images) if x not in staged_names]
    if missing_after_stage:
        raise UpgradeError(
            f"chapter_{chapter}: missing required images after pipeline: {', '.join(missing_after_stage[:10])}"
        )

    # Change detection.
    with config_path.open("r", encoding="utf-8") as f:
        current_cfg = json.load(f)
    config_changed = current_cfg != expected

    stage_hash = build_image_hash_map(stage_images)
    current_hash = build_image_hash_map(current_dir / "images")
    image_changed = stage_hash != current_hash

    plan = ChapterPlan(
        chapter=chapter,
        current_dir=current_dir,
        next_dir=next_dir,
        source_dir=source_dir,
        expected_config=expected,
        required_images=required_images,
        stage_dir=stage_dir,
        stage_images_dir=stage_images,
        config_changed=config_changed,
        image_changed=image_changed,
    )
    return plan



def apply_plan(plan: ChapterPlan):
    if plan.next_dir.exists():
        raise UpgradeError(f"chapter_{plan.chapter}: target exists before apply: {plan.next_dir}")

    shutil.copytree(plan.current_dir, plan.next_dir)

    write_json(plan.next_dir / "config.json", plan.expected_config)

    target_images = plan.next_dir / "images"
    if target_images.exists():
        shutil.rmtree(target_images)
    shutil.copytree(plan.stage_images_dir, target_images)



def main():
    parser = argparse.ArgumentParser(description="One-click chapter resource upgrade with strict validation")
    parser.add_argument("--chapters", required=True, help="Chapter list/range, e.g. 7-12 or 8,9,10")
    parser.add_argument("--images-root", required=True, help="Source images root path")
    parser.add_argument("--storyline", action="append", required=True, help="Storyline json path; pass multiple")
    parser.add_argument("--repo-root", default=".", help="Repo root (default: current directory)")
    parser.add_argument("--dry-run", action="store_true", help="Validate and diff only; do not write new versions")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    images_root = Path(args.images_root).resolve()
    storyline_paths = [Path(x).resolve() for x in args.storyline]

    if not repo_root.exists():
        raise UpgradeError(f"repo root not found: {repo_root}")
    if not images_root.exists():
        raise UpgradeError(f"images root not found: {images_root}")

    compress_script = repo_root / "compress_images.sh"
    png_to_webp_script = repo_root / "png_to_webp.sh"
    if not compress_script.exists():
        raise UpgradeError(f"missing script: {compress_script}")
    if not png_to_webp_script.exists():
        raise UpgradeError(f"missing script: {png_to_webp_script}")

    chapters = parse_chapters(args.chapters)
    story_map = load_storyline_mapping(storyline_paths, set(chapters))

    print("=== Story Resource Upgrade ===")
    print(f"repo_root: {repo_root}")
    print(f"images_root: {images_root}")
    print(f"chapters: {chapters}")
    print(f"dry_run: {args.dry_run}")

    plans: List[ChapterPlan] = []

    with tempfile.TemporaryDirectory(prefix="story_upgrade_stage_") as td:
        stage_root = Path(td)

        # Phase 1: strict precheck + staging + diff
        for ch in chapters:
            print(f"\n[check] chapter_{ch}")
            plan = plan_chapter(
                repo_root=repo_root,
                chapter=ch,
                images_root=images_root,
                story_map=story_map,
                stage_root=stage_root,
                compress_script=compress_script,
                png_to_webp_script=png_to_webp_script,
            )
            changed = plan.config_changed or plan.image_changed
            print(
                f"  current={plan.current_dir.name} next={plan.next_dir.name} "
                f"config_changed={plan.config_changed} image_changed={plan.image_changed} changed={changed}"
            )
            plans.append(plan)

        changed_plans = [p for p in plans if p.config_changed or p.image_changed]

        if not changed_plans:
            print("\nNo changes detected for selected chapters. Nothing to upgrade.")
            return

        if args.dry_run:
            print("\nDry-run only. Planned upgrades:")
            for p in changed_plans:
                print(f"  chapter_{p.chapter}: {p.current_dir.name} -> {p.next_dir.name}")
            return

        # Phase 2: apply only after all checks pass
        print("\n[apply] all prechecks passed, creating new versions...")
        for p in changed_plans:
            apply_plan(p)
            print(f"  chapter_{p.chapter}: created {p.next_dir.name}")

        print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except UpgradeError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] command failed: {e}")
        sys.exit(1)
