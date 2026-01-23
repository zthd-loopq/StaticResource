#!/bin/bash

echo "================================"
echo "PNG 转 WebP 工具 (无损)"
echo "================================"
echo ""

TOTAL_CONVERTED=0
TOTAL_SAVED=0
TOTAL_NINE_PATCH=0
CHAPTER_COUNT=0

check_dependencies() {
    if ! command -v cwebp &> /dev/null; then
        echo "缺少必要的工具:"
        echo "  - cwebp: brew install webp"
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
        printf "%d.%02dMB" $mb_int $mb_dec
    fi
}

convert_png_file() {
    local file=$1

    if [[ "$file" == *".9.png" ]]; then
        echo "  [skip] .9.png: $file"
        TOTAL_NINE_PATCH=$((TOTAL_NINE_PATCH + 1))
        return
    fi

    local original_size=$(stat -f%z "$file" 2>/dev/null)
    if [ -z "$original_size" ]; then
        return
    fi

    local webp_file="${file%.png}.webp"

    if cwebp -lossless -m 6 -mt "$file" -o "$webp_file" 2>/dev/null; then
        local new_size=$(stat -f%z "$webp_file" 2>/dev/null)

        if [ -n "$new_size" ]; then
            rm -f "$file"
            local saved=$((original_size - new_size))
            TOTAL_SAVED=$((TOTAL_SAVED + saved))
            TOTAL_CONVERTED=$((TOTAL_CONVERTED + 1))
            echo "  [ok] $file -> $(basename "$webp_file") $(format_size $original_size) -> $(format_size $new_size)"
        else
            rm -f "$webp_file"
            echo "  [fail] $file"
        fi
    else
        echo "  [fail] $file"
    fi
}

convert_png_to_webp_recursive() {
    local dir=$1

    while IFS= read -r -d '' file; do
        convert_png_file "$file"
    done < <(find "$dir" -type f -name "*.png" -print0)
}

main() {
    check_dependencies

    echo "开始扫描 chapter 文件夹..."
    echo ""

    local temp_list=$(mktemp)
    find . -type d -name "*chapter*" > "$temp_list"

    while IFS= read -r chapter_dir; do
        [ -z "$chapter_dir" ] && continue
        CHAPTER_COUNT=$((CHAPTER_COUNT + 1))
        echo "处理目录 (递归): $chapter_dir"

        convert_png_to_webp_recursive "$chapter_dir"

        echo ""
    done < "$temp_list"

    rm -f "$temp_list"

    echo "================================"
    echo "转换完成"
    echo "================================"
    echo "扫描的 chapter 文件夹: $CHAPTER_COUNT"
    echo "转换的 PNG 文件: $TOTAL_CONVERTED"
    echo "跳过的 .9.png 文件: $TOTAL_NINE_PATCH"
    echo "总共节省空间: $(format_size $TOTAL_SAVED)"
    echo ""
}

main