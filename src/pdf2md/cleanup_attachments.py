#!/usr/bin/env python3
"""删除 Zotero 中无效的附件条目（孤立附件）.

用法:
    uv run python3 src/pdf2md/cleanup_attachments.py --dry-run  # 预览
    uv run python3 src/pdf2md/cleanup_attachments.py            # 删除
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, '/Users/liam/projects/pyzotero/src')

from pyzotero import Zotero

# 77 个无效附件的 Key
INVALID_KEYS = """2D9B8BVG 2H8LVWA7 2QNB7TMJ 2WP3S4LW 35D52P6T 3AMXA2P2 3CP2EWLW 4ADGXLND
4LXAHBZB 52EPBSIQ 56HD353G 5NYI432Y 5T8AD64D 63V69WM4 6HNH3WCP 6VZ4INB8
7H7EHRS9 7U2NAD3U 852CSD5L 8DNTLIM4 8MIYHD6N 8W5W8U5X 9RFPK3QA A9JUG48Y
AFAMIPYL AGEWQRVE AYCCMFDM B9UVF7K9 BD2KDLRY BF3X3BPA C9FMYKTH CKQFWZPB
CNBYMQFA DCGYI72W DFXRE6YD DLFALX7V DYBNTRC3 EHRZW33P EQIGQAGL EXH3K89B
F8WS5F7G FGXXF4NG FJLTSUXF GG62856L HVFR2WCY IHIZCQ8L J6RN8GG6 JXLXGM7G
KNSPPD33 L6Q7BATI M6332845 P8YBKD7W QYLA493N RMLLDKQW RUQPLCL9 S6WLGR6R
SBXCD63C SDDRC4MG T5RS8RHC U9682K2X UBQA245D V7J47RKS VB6LRB95 VK6H7FHV
VLA9TQA9 WB43J5JF WJG6KP5V WJYF693P WVH3JNK8 X4EHBKFE X87W6EXL YNY3YK4Q
Z2N88G68 Z3DQD57A ZB6YIBD6 ZHIDLNBR ZJ8CTFJB""".split()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='清理 Zotero 中的无效附件')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不实际删除')
    parser.add_argument('--library-id', default='0', help='Zotero library ID')
    args = parser.parse_args()

    print(f"无效附件数量: {len(INVALID_KEYS)}")

    # 连接 Zotero（使用 local=True 连接本地 API）
    zot = Zotero(args.library_id, 'user', local=True)
    print(f"已连接到 Zotero: {args.library_id}")

    if args.dry_run:
        print("=" * 60)
        print("预览模式 - 以下条目将被删除:")
        print("=" * 60)
        for key in INVALID_KEYS:
            try:
                item = zot.item(key)
                if item:
                    parent_key = item.get('data', {}).get('parentItem', '')
                    title = ''
                    if parent_key:
                        parent = zot.item(parent_key)
                        if parent:
                            title = parent.get('data', {}).get('title', '')[:50]
                    print(f"  {key} | v{item.get('version', '?')} | {title}")
                else:
                    print(f"  {key} | (未找到)")
            except Exception as e:
                print(f"  {key} | 错误: {e}")
        print()
        print("运行不带 --dry-run 参数来执行删除")
    else:
        print("=" * 60)
        print("删除模式 - 确认删除? (Ctrl+C 取消)")
        print("=" * 60)
        import time
        time.sleep(3)

        success = 0
        failed = 0
        for key in INVALID_KEYS:
            try:
                # 获取完整的 item 数据
                item = zot.item(key)
                if item:
                    # delete_item 需要完整的 item dict
                    zot.delete_item(item)
                    print(f"[DELETED] {key}")
                    success += 1
                else:
                    print(f"[NOT FOUND] {key}")
            except Exception as e:
                print(f"[FAILED] {key}: {e}")
                failed += 1

        print()
        print(f"完成: 成功 {success}, 失败 {failed}")


if __name__ == '__main__':
    main()
