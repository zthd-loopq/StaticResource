#!/bin/bash

MODE="${1:-all}"
SCAN_PATTERN="${2:-*chapter*}"

usage() {
    echo "用法: $0 [png|webp|all] [scan_pattern]"
    echo "  png          - 只处理 PNG 图片"
    echo "  webp         - 只处理 WebP 图片"
    echo "  all          - 处理 PNG 和 WebP 图片 (默认)"
    echo "  scan_pattern - 目录匹配模式，默认 '*chapter*'"
    echo ""
    echo "示例:"
    echo "  $0"
    echo "  $0 webp"
    echo "  $0 all '*chapter_7*'"
}

if [[ "$MODE" == "-h" || "$MODE" == "--help" ]]; then
    usage
    exit 0
fi

if [[ "$MODE" != "png" && "$MODE" != "webp" && "$MODE" != "all" ]]; then
    usage
    exit 1
fi

echo "================================"
echo "图片压缩工具"
echo "================================"
echo "模式: $MODE"
echo "扫描目录匹配: $SCAN_PATTERN"
echo ""

TOTAL_PNG=0
TOTAL_WEBP=0
TOTAL_SAVED=0
TOTAL_NINE_PATCH=0
SCAN_DIR_COUNT=0

check_dependencies() {
    local need_pngquant=0
    local need_cwebp=0

    if [[ "$MODE" == "png" || "$MODE" == "all" ]]; then
        need_pngquant=1
    fi
    if [[ "$MODE" == "webp" || "$MODE" == "all" ]]; then
        need_cwebp=1
    fi

    if [[ $need_pngquant -eq 1 ]] && ! command -v pngquant &> /dev/null; then
        echo "缺少必要工具: pngquant"
        echo "安装命令: brew install pngquant"
        exit 1
    fi

    if [[ $need_cwebp -eq 1 ]] && ! command -v cwebp &> /dev/null; then
        echo "缺少必要工具: cwebp"
        echo "安装命令: brew install webp"
        exit 1
    fi

    echo "依赖检查通过"
    echo ""
}

format_size() {
    local size=$1
    if [ "$size" -lt 1024 ]; then
        echo "${size}B"
    elif [ "$size" -lt 1048576 ]; then
        local kb=$((size * 10 / 1024))
        local kb_int=$((kb / 10))
        local kb_dec=$((kb % 10))
        echo "${kb_int}.${kb_dec}KB"
    else
        local mb=$((size * 100 / 1048576))
        local mb_int=$((mb / 100))
        local mb_dec=$((mb % 100))
        printf "%d.%02dMB" "$mb_int" "$mb_dec"
    fi
}

compress_png_file() {
    local file=$1

    if [[ "$file" == *".9.png" ]]; then
        echo "  [skip] .9.png: $file"
        TOTAL_NINE_PATCH=$((TOTAL_NINE_PATCH + 1))
        return
    fi

    local original_size
    original_size=$(stat -f%z "$file" 2>/dev/null) || return
    [ -n "$original_size" ] || return

    local temp_file="${file}.tmp.png"

    if pngquant --quality=65-80 --output "$temp_file" "$file" 2>/dev/null; then
        local new_size
        new_size=$(stat -f%z "$temp_file" 2>/dev/null)
        if [ -n "$new_size" ] && [ "$new_size" -lt "$original_size" ]; then
            mv "$temp_file" "$file"
            local saved=$((original_size - new_size))
            TOTAL_SAVED=$((TOTAL_SAVED + saved))
            TOTAL_PNG=$((TOTAL_PNG + 1))
            echo "  [ok] PNG: $file $(format_size "$original_size") -> $(format_size "$new_size")"
        else
            rm -f "$temp_file"
            echo "  [--] PNG not smaller: $file"
        fi
    else
        rm -f "$temp_file"
    fi
}

compress_webp_file() {
    local file=$1

    local original_size
    original_size=$(stat -f%z "$file" 2>/dev/null) || return
    [ -n "$original_size" ] || return

    local temp_file="${file}.tmp"

    if cwebp -q 80 -m 6 -mt "$file" -o "$temp_file" 2>/dev/null; then
        local new_size
        new_size=$(stat -f%z "$temp_file" 2>/dev/null)
        if [ -n "$new_size" ] && [ "$new_size" -lt "$original_size" ]; then
            mv "$temp_file" "$file"
            local saved=$((original_size - new_size))
            TOTAL_SAVED=$((TOTAL_SAVED + saved))
            TOTAL_WEBP=$((TOTAL_WEBP + 1))
            echo "  [ok] WebP: $file $(format_size "$original_size") -> $(format_size "$new_size")"
        else
            rm -f "$temp_file"
            echo "  [--] WebP not smaller: $file"
        fi
    else
        rm -f "$temp_file"
    fi
}

process_dir_recursive() {
    local dir=$1

    if [[ "$MODE" == "png" || "$MODE" == "all" ]]; then
        while IFS= read -r -d '' file; do
            compress_png_file "$file"
        done < <(find "$dir" -type f -name "*.png" -print0)
    fi

    if [[ "$MODE" == "webp" || "$MODE" == "all" ]]; then
        while IFS= read -r -d '' file; do
            compress_webp_file "$file"
        done < <(find "$dir" -type f -name "*.webp" -print0)
    fi
}

main() {
    check_dependencies

    echo "开始扫描目录..."
    echo ""

    local temp_list
    temp_list=$(mktemp)
    trap 'rm -f "$temp_list"' EXIT

    find . -type d -name "$SCAN_PATTERN" > "$temp_list"

    while IFS= read -r scan_dir; do
        [ -z "$scan_dir" ] && continue
        SCAN_DIR_COUNT=$((SCAN_DIR_COUNT + 1))
        echo "处理目录 (递归): $scan_dir"
        process_dir_recursive "$scan_dir"
        echo ""
    done < "$temp_list"

    if [ "$SCAN_DIR_COUNT" -eq 0 ]; then
        echo "未匹配到目录，扫描模式: $SCAN_PATTERN"
    fi

    echo "================================"
    echo "压缩完成"
    echo "================================"
    echo "模式: $MODE"
    echo "扫描目录匹配: $SCAN_PATTERN"
    echo "扫描到的目录数: $SCAN_DIR_COUNT"
    echo "压缩的 PNG 文件: $TOTAL_PNG"
    echo "压缩的 WebP 文件: $TOTAL_WEBP"
    echo "跳过的 .9.png 文件: $TOTAL_NINE_PATCH"
    echo "总共节省空间: $(format_size "$TOTAL_SAVED")"
    echo ""
}

main
