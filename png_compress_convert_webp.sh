#!/bin/bash

# png_compress_convert_webp.sh
#
# 合并 png 压缩 + png→webp 转换为一步：
#   Step 1: pngquant --quality=65-80 (更小才覆盖原图)
#   Step 2: cwebp -lossless -m 6 -mt → 删除原 PNG
#
# 跳过 *.9.png（Android 9-patch 资源）。
#
# 用法:
#   ./png_compress_convert_webp.sh <file|dir>
#     <file>  仅处理该 .png 文件
#     <dir>   递归处理目录下所有 .png（除 .9.png 外）

set -u

show_usage() {
    cat <<EOF
用法: $0 <file|dir>
  <file>   单个 .png 文件
  <dir>    递归处理目录下所有 .png（自动跳 .9.png）

示例:
  $0 ~/Downloads/result/                # 目录（递归）
  $0 ~/Downloads/13_gray.png            # 单文件
EOF
}

if [ $# -lt 1 ]; then
    show_usage
    exit 1
fi

TARGET="$1"

if [ ! -e "$TARGET" ]; then
    echo "错误: 路径不存在: $TARGET" >&2
    exit 1
fi

check_dependencies() {
    local missing=0
    if ! command -v pngquant &> /dev/null; then
        echo "缺少 pngquant: brew install pngquant" >&2
        missing=1
    fi
    if ! command -v cwebp &> /dev/null; then
        echo "缺少 cwebp: brew install webp" >&2
        missing=1
    fi
    [ $missing -eq 1 ] && exit 2
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

TOTAL_FILES=0
TOTAL_CONVERTED=0
TOTAL_SKIP_NINE=0
TOTAL_FAILED=0
TOTAL_SAVED=0

# 处理单个 png 文件：pngquant 压缩 (更小才覆盖) → cwebp -lossless 转 webp → 删除原 PNG
process_png() {
    local file=$1

    if [[ "$file" == *".9.png" ]]; then
        echo "  [skip] .9.png: $(basename "$file")"
        TOTAL_SKIP_NINE=$((TOTAL_SKIP_NINE + 1))
        return
    fi

    local original_size
    original_size=$(stat -f%z "$file" 2>/dev/null)
    if [ -z "$original_size" ]; then
        echo "  [fail] stat: $(basename "$file")" >&2
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        return
    fi

    # ---- Step 1: pngquant 有损压缩，更小才覆盖（与 compress_images.sh 完全一致） ----
    local tmp_png="${file}.tmp.png"
    local png_size=$original_size
    if pngquant --quality=65-80 --output "$tmp_png" "$file" 2>/dev/null; then
        local new_size
        new_size=$(stat -f%z "$tmp_png" 2>/dev/null)
        if [ -n "$new_size" ] && [ "$new_size" -lt "$original_size" ]; then
            mv "$tmp_png" "$file"
            png_size=$new_size
        else
            rm -f "$tmp_png"
        fi
    else
        rm -f "$tmp_png"
    fi

    # ---- Step 2: cwebp -lossless 转 webp，删除原 PNG（与 png_to_webp.sh 完全一致） ----
    local webp_file="${file%.png}.webp"
    if cwebp -lossless -m 6 -mt "$file" -o "$webp_file" 2>/dev/null; then
        local webp_size
        webp_size=$(stat -f%z "$webp_file" 2>/dev/null)
        if [ -n "$webp_size" ]; then
            rm -f "$file"
            local saved=$((original_size - webp_size))
            TOTAL_SAVED=$((TOTAL_SAVED + saved))
            TOTAL_CONVERTED=$((TOTAL_CONVERTED + 1))
            echo "  [ok] $(basename "$file"): $(format_size $original_size) → png $(format_size $png_size) → webp $(format_size $webp_size)"
        else
            rm -f "$webp_file"
            echo "  [fail] webp: $(basename "$file")" >&2
            TOTAL_FAILED=$((TOTAL_FAILED + 1))
        fi
    else
        rm -f "$webp_file"
        echo "  [fail] cwebp: $(basename "$file")" >&2
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
    fi
}

check_dependencies

echo "================================"
echo "compress (png → pngquant → webp)"
echo "================================"
echo "目标: $TARGET"
echo ""

if [ -f "$TARGET" ]; then
    if [[ "$TARGET" != *.png ]]; then
        echo "错误: 单文件必须是 .png" >&2
        exit 1
    fi
    TOTAL_FILES=1
    process_png "$TARGET"
elif [ -d "$TARGET" ]; then
    TMP_LIST=$(mktemp)
    find "$TARGET" -type f -name "*.png" > "$TMP_LIST"
    TOTAL_FILES=$(wc -l < "$TMP_LIST" | tr -d ' ')
    echo "找到 $TOTAL_FILES 个 .png 文件"
    echo ""
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        process_png "$f"
    done < "$TMP_LIST"
    rm -f "$TMP_LIST"
else
    echo "错误: 既不是文件也不是目录: $TARGET" >&2
    exit 1
fi

echo ""
echo "================================"
echo "完成"
echo "================================"
echo "扫描文件:     $TOTAL_FILES"
echo "成功转换:     $TOTAL_CONVERTED"
echo "跳过 .9.png:  $TOTAL_SKIP_NINE"
echo "失败:         $TOTAL_FAILED"
echo "节省空间:     $(format_size $TOTAL_SAVED)"
echo ""

if [ "$TOTAL_FAILED" -gt 0 ]; then
    exit 3
fi
exit 0
