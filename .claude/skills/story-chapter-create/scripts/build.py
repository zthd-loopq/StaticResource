#!/usr/bin/env python3
"""Validate a filled chapter workspace, build config + webp + zip, update global index."""
import argparse
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
COMPRESS_SH = REPO_ROOT / "png_compress_convert_webp.sh"

sys.path.insert(0, str(REPO_ROOT / ".claude" / "skills" / "story-resource-upgrade" / "scripts"))
from upgrade_resources import parse_rows, collect_local_image_refs, UpgradeError  # noqa: E402

DEFAULT_RESOURCE_URL_TEMPLATE = "https://github.com/zthd-loopq/StaticResource/raw/refs/heads/master/{folder}.zip"
GLOBAL_DOC_URL = "https://stickerstyle.feishu.cn/wiki/A3n2wf4nai2WYkkSQn8c3hscnQg"
ID_LEN = 8
ID_RETRIES = 32
DIR_PATTERN = re.compile(r"^story_chapter_(\d+)_v(\d+)$")
RESULT_PLACEHOLDER = re.compile(r"^result:(\d+)$")
SPREADSHEET_RE = re.compile(r"/sheets/([A-Za-z0-9]+)")
SHEET_ID_RE = re.compile(r"[?&]sheet=([A-Za-z0-9]+)")
DIALOG_HEADERS_REQUIRED = ("对话", "人物", "人物图", "背景", "位置")
DIALOG_OUTPUT_ORDER = ("对话", "人物", "人物图", "背景", "位置", "装饰")
JSON_BLOCK_RE = re.compile(r"(```JSON\n)(.*?)(\n```)", re.DOTALL)


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


def build_chapter_entry(chapter_id: str, chapter_cfg: dict, folder_name: str, resource_url_template: str) -> dict:
    return {
        "id": chapter_id,
        "name": chapter_cfg["name"],
        "coverUrl": chapter_cfg["coverUrl"],
        "unlockCoverUrl": chapter_cfg["unlockCoverUrl"],
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


def parse_dialog_url(url: str) -> Tuple[str, str]:
    if not url:
        raise BuildError("chapter.json: dialogSheetUrl is empty")
    m1 = SPREADSHEET_RE.search(url)
    if not m1:
        raise BuildError(f"chapter.json: dialogSheetUrl 无法解析出 spreadsheet token: {url}")
    m2 = SHEET_ID_RE.search(url)
    if not m2:
        raise BuildError(f"chapter.json: dialogSheetUrl 必须包含 ?sheet=<sheet_id> 参数: {url}")
    return m1.group(1), m2.group(1)


def fetch_dialog_sheet(url: str, sheet_id: str) -> List[List[str]]:
    cmd = [
        "lark-cli", "sheets", "+read",
        "--url", url,
        "--range", f"{sheet_id}!A:F",
        "--as", "user",
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise BuildError("未找到 lark-cli 命令，请先安装并 PATH 中可执行")

    out = proc.stdout or ""
    if proc.returncode != 0 and not out.strip():
        err = proc.stderr or ""
        raise BuildError(f"lark-cli sheets +read 失败 (exit {proc.returncode}):\n{err}")

    try:
        payload = json.loads(out)
    except json.JSONDecodeError as e:
        raise BuildError(f"lark-cli 输出非 JSON: {e}\n{out[:500]}")

    if not payload.get("ok"):
        err = payload.get("error") or {}
        msg = err.get("message") or "unknown error"
        hint = err.get("hint")
        console_url = err.get("console_url")
        parts = [f"lark-cli sheets +read 失败: {msg}"]
        if hint:
            parts.append(f"hint: {hint}")
        if console_url:
            parts.append(f"console_url: {console_url}")
        raise BuildError("\n".join(parts))

    values = ((payload.get("data") or {}).get("valueRange") or {}).get("values")
    if not values:
        raise BuildError(f"sheet {sheet_id} 没有数据（valueRange.values 为空）")
    return values


def normalize_cell(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _run_lark_cli(cmd: List[str], stdin: str = None) -> dict:
    try:
        proc = subprocess.run(
            cmd,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        raise BuildError("未找到 lark-cli 命令，请先安装并 PATH 中可执行")
    out = proc.stdout or ""
    if proc.returncode != 0 and not out.strip():
        err = proc.stderr or ""
        raise BuildError(f"lark-cli 失败 (exit {proc.returncode}): {' '.join(cmd)}\n{err}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as e:
        raise BuildError(f"lark-cli 输出非 JSON: {e}\n{out[:500]}")
    if not payload.get("ok"):
        err = payload.get("error") or {}
        msg = err.get("message") or "unknown error"
        hint = err.get("hint")
        console_url = err.get("console_url")
        parts = [f"lark-cli 失败: {msg}", f"cmd: {' '.join(cmd)}"]
        if hint:
            parts.append(f"hint: {hint}")
        if console_url:
            parts.append(f"console_url: {console_url}")
        raise BuildError("\n".join(parts))
    return payload


def fetch_global_doc() -> Tuple[str, dict]:
    print(f"→ 拉取全局索引: {GLOBAL_DOC_URL}")
    payload = _run_lark_cli([
        "lark-cli", "docs", "+fetch",
        "--api-version", "v2",
        "--doc", GLOBAL_DOC_URL,
        "--doc-format", "markdown",
        "--as", "user",
    ])
    content = (((payload.get("data") or {}).get("document") or {})).get("content")
    if not isinstance(content, str) or not content:
        raise BuildError("global doc fetch: data.document.content 缺失")
    m = JSON_BLOCK_RE.search(content)
    if not m:
        raise BuildError("global doc fetch: 未找到 ```JSON``` 代码块")
    try:
        index = json.loads(m.group(2))
    except json.JSONDecodeError as e:
        raise BuildError(f"global doc fetch: JSON 块解析失败: {e}")
    if not isinstance(index.get("chapters"), list):
        raise BuildError("global doc fetch: chapters 字段不是 list")
    return content, index


def write_global_doc(original_md: str, new_index: dict) -> None:
    new_json = json.dumps(new_index, ensure_ascii=False, indent=2)
    new_md, n = JSON_BLOCK_RE.subn(
        lambda mm: mm.group(1) + new_json + mm.group(3),
        original_md,
        count=1,
    )
    if n != 1:
        raise BuildError("write_global_doc: JSON 代码块替换失败")
    print("→ 写回全局索引（飞书 docx overwrite）")
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


def print_diff(old_index: dict, new_index: dict, current_chapter_no: int) -> None:
    print("--- diff ---")
    old_chs = old_index.get("chapters") or []
    new_chs = new_index.get("chapters") or []
    idx = current_chapter_no - 1
    if idx < len(old_chs):
        print(f"  chapters[{idx}] (rebuild) ← {new_chs[idx].get('name')}")
        for k in ("id", "name", "coverUrl", "unlockCoverUrl", "resourceUrl"):
            ov, nv = old_chs[idx].get(k), new_chs[idx].get(k)
            if ov != nv:
                print(f"    {k}: {ov!r} → {nv!r}")
    else:
        print(f"  chapters[{idx}] (new) ← {new_chs[idx].get('name')} id={new_chs[idx].get('id')}")
    old_lim = old_index.get("limitChapterId")
    new_lim = new_index.get("limitChapterId")
    if old_lim != new_lim:
        print(f"  limitChapterId: {old_lim} → {new_lim}")
    print()
    print("--- new global index (will write back) ---")
    print(json.dumps(new_index, ensure_ascii=False, indent=2))


def canonicalize_sheet_rows(raw_rows: List[List]) -> List[List[str]]:
    header = [normalize_cell(c) for c in raw_rows[0]]
    for name in DIALOG_HEADERS_REQUIRED:
        if name not in header:
            raise BuildError(f"sheet header 缺列: {name}（实际 header: {header}）")

    indices = [header.index(name) if name in header else -1 for name in DIALOG_OUTPUT_ORDER]

    out: List[List[str]] = [list(DIALOG_OUTPUT_ORDER)]
    for row in raw_rows[1:]:
        cells = []
        for i in indices:
            if i == -1:
                cells.append("")
            else:
                v = row[i] if i < len(row) else ""
                cells.append(normalize_cell(v))
        out.append(cells)
    return out


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

    # Check 4: chapter.json exists + valid JSON
    chapter_json_path = chapter_dir / "chapter.json"
    if not chapter_json_path.exists():
        raise BuildError("chapter.json missing")
    chapter_cfg = load_json(chapter_json_path, "chapter.json")

    # Check 5: chapter.json field validation
    for k in ("name", "coverUrl", "unlockCoverUrl", "dialogSheetUrl", "templateId"):
        v = chapter_cfg.get(k)
        if not isinstance(v, str) or not v:
            raise BuildError(f"chapter.json: {k} is empty")
    component_ids = chapter_cfg.get("componentIds")
    if not isinstance(component_ids, list) or not component_ids:
        raise BuildError("chapter.json: componentIds is empty")

    template_id = chapter_cfg["templateId"]
    dialog_url = chapter_cfg["dialogSheetUrl"]

    # Check 6: parse dialogSheetUrl
    _, sheet_id = parse_dialog_url(dialog_url)

    # Check 7: global index fetched from Feishu wiki docx
    md_text, global_index = fetch_global_doc()
    chapters = global_index["chapters"]

    # Check 8: chapter number gap
    if chapter_no > len(chapters) + 1:
        raise BuildError(f"chapter number gap: N={chapter_no} but global has only {len(chapters)}")

    is_new_chapter = chapter_no == len(chapters) + 1

    # Fetch dialog rows from Feishu sheet
    print(f"→ 拉取对话 sheet: {sheet_id}")
    raw_rows = fetch_dialog_sheet(dialog_url, sheet_id)
    canonical_rows = canonicalize_sheet_rows(raw_rows)
    print(f"  拉到 {len(raw_rows)} 行，规范化后 {len(canonical_rows)} 行（含 header）")

    # Parse rows -> front/result/behind
    try:
        front, result, behind = parse_rows(canonical_rows, f"sheet:{sheet_id}")
    except UpgradeError as e:
        raise BuildError(f"dialog 解析失败: {e}")
    parsed_config = {
        "templateId": template_id,
        "componentIds": component_ids,
        "front": front,
        "result": result,
        "behind": behind,
    }

    # Check 9: result:M references valid
    referenced_ms = collect_result_chapter_numbers(parsed_config)
    for n in referenced_ms:
        if n == chapter_no:
            continue
        if n < 1 or n > len(chapters):
            raise BuildError(f"sheet: result:{n} references missing chapter (only {len(chapters)} chapters in global index)")

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

    # Check 10: image refs all present
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

    new_entry = build_chapter_entry(chapter_id, chapter_cfg, folder_name, resource_url_template)

    # Prepare new global_index in memory (used by both dry-run preview and real write)
    old_index_snapshot = json.loads(json.dumps(global_index))
    if chapter_no <= len(chapters):
        chapters[chapter_no - 1] = new_entry
    else:
        chapters.append(new_entry)
    if chapter_cfg.get("limitCurrent"):
        global_index["limitChapterId"] = chapter_id

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
        print_diff(old_index_snapshot, global_index, chapter_no)
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

    # Pipeline step 6: diff + write back to Feishu docx
    print_diff(old_index_snapshot, global_index, chapter_no)
    write_global_doc(md_text, global_index)
    print(f"✓ updated global doc (chapters[{chapter_no - 1}].id={chapter_id})")
    if chapter_cfg.get("limitCurrent"):
        print(f"  limitChapterId = {chapter_id}")

    # Pipeline step 7: cleanup
    chapter_json_path.unlink()
    print("✓ cleaned chapter.json")


def main():
    parser = argparse.ArgumentParser(description="Build chapter from filled workspace and update global index")
    parser.add_argument("chapter_folder", help="Chapter folder name, e.g. story_chapter_17_v1")
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
