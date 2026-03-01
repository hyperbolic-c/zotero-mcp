#!/usr/bin/env python3
"""分析 pdf2md 处理日志中的错误条目."""

import re
import sys
from collections import defaultdict
from pathlib import Path


def analyze_errors(log_file: str):
    """分析日志文件中的错误."""
    # 存储 item key -> (title, error_type)
    errors: dict[str, tuple[str, str]] = {}

    # API 超时和错误
    api_errors: list[tuple[str, str]] = []

    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()

    # 提取所有 Processing 行来建立 key -> title 映射
    item_titles: dict[str, str] = {}
    for match in re.finditer(r"Processing: '([^']+)' \[(\w+)\]", content):
        title, key = match.groups()
        item_titles[key] = title

    # 提取 fetch errors (发生在 Processing 之前)
    for match in re.finditer(r"Failed to fetch file (\w+)", content):
        key = match.group(1)
        title = item_titles.get(key, "Unknown")
        errors[key] = (title, "fetch_error")

    # 提取 API errors (MinerU 超时)
    for match in re.finditer(r"ERROR\] MinerU API timed out processing ([^.]+)", content):
        filename = match.group(1).strip()
        # 尝试找到对应的 item key
        # 搜索附近的 Processing 行
        api_errors.append((filename, "timeout"))

    # 提取 API HTTP errors
    for match in re.finditer(r"ERROR\] MinerU API HTTP error for ([^:]+)", content):
        filename = match.group(1).strip()
        api_errors.append((filename, "http_error"))

    # 打印结果
    print("=" * 70)
    print("获取错误 (Fetch Errors) - 共 {} 条".format(len(errors)))
    print("=" * 70)
    if errors:
        for key, (title, err_type) in sorted(errors.items()):
            print(f"  [{key}] {title}")
    else:
        print("  无")

    print()
    print("=" * 70)
    print("API 错误 (API Errors) - 共 {} 条".format(len(api_errors)))
    print("=" * 70)
    if api_errors:
        for filename, err_type in api_errors:
            print(f"  - {filename} ({err_type})")
    else:
        print("  无")

    print()
    print("=" * 70)
    print("汇总")
    print("=" * 70)
    print(f"  获取错误: {len(errors)} 条")
    print(f"  API 错误: {len(api_errors)} 条")
    print(f"  总计错误: {len(errors) + len(api_errors)} 条")

    # 统计错误类型
    if api_errors:
        timeout_count = sum(1 for _, t in api_errors if t == "timeout")
        http_count = sum(1 for _, t in api_errors if t == "http_error")
        print(f"    - 超时: {timeout_count}")
        print(f"    - HTTP 错误: {http_count}")

    return errors, api_errors


def main():
    if len(sys.argv) < 2:
        # 默认使用最新的输出文件
        output_dir = Path("zotero_md_output")
        if output_dir.exists():
            md_count = len(list(output_dir.rglob("*.md")))
            print(f"已转换的 Markdown 文件: {md_count} 个")

        log_files = list(Path("logs").glob("debug-*.log")) if Path("logs").exists() else []
        if log_files:
            latest_log = max(log_files, key=lambda p: p.stat().st_mtime)
            print(f"使用最新日志文件: {latest_log}")
            analyze_errors(str(latest_log))
        else:
            print("用法: python analyze_errors.py <log_file>")
    else:
        analyze_errors(sys.argv[1])


if __name__ == "__main__":
    main()
