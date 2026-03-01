#!/usr/bin/env python3
"""获取错误条目的标题并输出到 MD 文件."""

import subprocess
import json
import os

error_keys = """2D9B8BVG 2H8LVWA7 2QNB7TMJ 2WP3S4LW 35D52P6T 3AMXA2P2 3CP2EWLW 4ADGXLND
4LXAHBZB 52EPBSIQ 56HD353G 5NYI432Y 5T8AD64D 63V69WM4 6HNH3WCP 6VZ4INB8
7H7EHRS9 7U2NAD3U 852CSD5L 8DNTLIM4 8MIYHD6N 8W5W8U5X 9RFPK3QA A9JUG48Y
AFAMIPYL AGEWQRVE AYCCMFDM B9UVF7K9 BD2KDLRY BF3X3BPA C9FMYKTH CKQFWZPB
CNBYMQFA DCGYI72W DFXRE6YD DLFALX7V DYBNTRC3 EHRZW33P EQIGQAGL EXH3K89B
F8WS5F7G FGXXF4NG FJLTSUXF GG62856L HVFR2WCY IHIZCQ8L J6RN8GG6 JXLXGM7G
KNSPPD33 L6Q7BATI M6332845 P8YBKD7W QYLA493N RMLLDKQW RUQPLCL9 S6WLGR6R
SBXCD63C SDDRC4MG T5RS8RHC U9682K2X UBQA245D V7J47RKS VB6LRB95 VK6H7FHV
VLA9TQA9 WB43J5JF WJG6KP5V WJYF693P WVH3JNK8 X4EHBKFE X87W6EXL YNY3YK4Q
Z2N88G68 Z3DQD57A ZB6YIBD6 ZHIDLNBR ZJ8CTFJB""".split()

results = []

for key in error_keys:
    try:
        result = subprocess.run(
            ['curl', '-s', f'http://localhost:23119/api/users/0/items/{key}?format=json'],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        parent_item = data['data'].get('parentItem', '')

        title = ''
        if parent_item:
            parent_result = subprocess.run(
                ['curl', '-s', f'http://localhost:23119/api/users/0/items/{parent_item}?format=json'],
                capture_output=True, text=True, timeout=5
            )
            parent_data = json.loads(parent_result.stdout)
            title = parent_data['data'].get('title', '')

        results.append((key, title))
    except Exception as e:
        results.append((key, f'Error: {e}'))

# 写入 MD 文件
output_path = '/Users/liam/projects/pyzotero/zotero_md_output/error_titles.md'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write('# 错误条目标题列表\n\n')
    f.write('> 可在 Zotero 软件中搜索这些 Key 检查条目\n\n')
    f.write('| # | Key | Title |\n')
    f.write('|---|------|-------|\n')
    for i, (key, title) in enumerate(results, 1):
        title_str = title if title else '[无父级条目]'
        f.write(f'| {i} | {key} | {title_str} |\n')

print(f'已写入: {output_path}')
print(f'共 {len(results)} 条')
