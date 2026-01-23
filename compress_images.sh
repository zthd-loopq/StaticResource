#!/bin/bash

echo "================================"
echo "图片压缩工具"
echo "================================"
echo ""

TOTAL_PNG=0
TOTAL_WEBP=0
TOTAL_SAVED=0
TOTAL_NINE_PATCH=0
CHAPTER_COUNT=0

check_dependencies() {
    local has_pngquant=1
    local has_cwebp=1
    
    if ! command -v pngquant &> /dev/null; then
        has_pngquant=0
    fi
    
    if ! command -v cwebp &> /dev/null; then
        has_cwebp=0
    fi
    
    if [ $has_pngquant -eq 0 ] || [ $has_cwebp -eq 0 ]; then
        echo "缺少必要的工具:"
        if [ $has_pngquant -eq 0 ]; then
            echo "  - pngquant: brew install pngquant"
        fi
        if [ $has_cwebp -eq 0 ]; then
            echo "  - cwebp: brew install webp"
        fi
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

compress_png_file() {
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

    local temp_file="${file}.tmp.png"

    if pngquant --quality=65-80 --output "$temp_file" "$file" 2>/dev/null; then
        local new_size=$(stat -f%z "$temp_file" 2>/dev/null)

        if [ -n "$new_size" ] && [ "$new_size" -lt "$original_size" ]; then
            mv "$temp_file" "$file"
            local saved=$((original_size - new_size))
            TOTAL_SAVED=$((TOTAL_SAVED + saved))
            TOTAL_PNG=$((TOTAL_PNG + 1))
            echo "  [ok] PNG: $file $(format_size $original_size) -> $(format_size $new_size)"
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

    local original_size=$(stat -f%z "$file" 2>/dev/null)
    if [ -z "$original_size" ]; then
        return
    fi

    local temp_file="${file}.tmp"

    if cwebp -q 80 -m 6 -mt "$file" -o "$temp_file" 2>/dev/null; then
        local new_size=$(stat -f%z "$temp_file" 2>/dev/null)

        if [ -n "$new_size" ] && [ "$new_size" -lt "$original_size" ]; then
            mv "$temp_file" "$file"
            local saved=$((original_size - new_size))
            TOTAL_SAVED=$((TOTAL_SAVED + saved))
            TOTAL_WEBP=$((TOTAL_WEBP + 1))
            echo "  [ok] WebP: $file $(format_size $original_size) -> $(format_size $new_size)"
        else
            rm -f "$temp_file"
            echo "  [--] WebP not smaller: $file"
        fi
    else
        rm -f "$temp_file"
    fi
}

compress_png_files_recursive() {
    local dir=$1

    while IFS= read -r -d '' file; do
        compress_png_file "$file"
    done < <(find "$dir" -type f -name "*.png" -print0)
}

compress_webp_files_recursive() {
    local dir=$1

    while IFS= read -r -d '' file; do
        compress_webp_file "$file"
    done < <(find "$dir" -type f -name "*.webp" -print0)
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

        compress_png_files_recursive "$chapter_dir"
        compress_webp_files_recursive "$chapter_dir"

        echo ""
    done < "$temp_list"

    rm -f "$temp_list"

    echo "================================"
    echo "压缩完成"
    echo "================================"
    echo "扫描的 chapter 文件夹: $CHAPTER_COUNT"
    echo "压缩的 PNG 文件: $TOTAL_PNG"
    echo "压缩的 WebP 文件: $TOTAL_WEBP"
    echo "跳过的 .9.png 文件: $TOTAL_NINE_PATCH"
    echo "总共节省空间: $(format_size $TOTAL_SAVED)"
    echo ""
}

main