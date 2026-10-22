#!/usr/bin/env python3
"""抓取 OceanBase 大赛 Yuque 文档(公开 API, body 在 sourcecode 字段)。

用法:
    python3 crawl_yuque.py            # 抓取并在本目录生成 md 文件
    python3 crawl_yuque.py --check    # 只对比本地文件与线上 word_count, 不写文件
"""

import json
import os
import sys
import time
import urllib.request

BOOK_ID = 81051108
HOST = "https://oceanbase.yuque.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0"

# (文件名, slug, 标题)   slug 来自 https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio
DOCS = [
    ("00-总览.md", "gp9hi8ikdnef8ryc", "PowerContext 赛题总览"),
    ("01-fork仓库.md", "ygfg2iskh62zntl5", "赛题一: Fork PowerContext 仓库"),
    ("02-CSV行级采集与原始记录回查.md", "yt9ctvr4n9lc0p1l", "赛题二: CSV 行级采集与原始记录回查"),
    ("03-Pi会话分支采集与断点恢复.md", "oi71gg4g80uda2ls", "赛题三: Pi 会话分支采集与断点恢复"),
    ("04-财报PDF全文解析与证据页检索.md", "ohy6lqb9wshgmrar", "赛题四: 财报 PDF 全文解析与证据页检索"),
    ("05-HTTP重排服务接入与跨批次排序.md", "te7ynwpbeg0cbufs", "赛题五: HTTP 重排服务接入与跨批次排序"),
    ("06-上下文增量传输与完整重建.md", "pw6pl9siofvh3h9k", "赛题六: 上下文增量传输与完整重建"),
    ("07-按新增来源条目数触发记忆处理.md", "kv2g0mm2fdfdda7x", "赛题七: 按新增来源条目数触发记忆处理"),
    ("08-Embedding并发合并与向量缓存.md", "gt75mi3l407t2ccn", "赛题八: Embedding 并发请求合并与向量缓存"),
    ("09-来源撤回后的记忆停用与恢复.md", "vru5ymzl6lmi701c", "赛题九: 来源撤回后的记忆停用与恢复"),
    ("10-按查询和预算选择原文证据.md", "kcgxoqb2h9up4ufu", "赛题十: 按查询和预算选择原文证据"),
    ("11-seekdb混合检索单SQL候选查询.md", "hqkh5u47mf5nv8r5", "赛题十一: seekdb 混合检索的单 SQL 候选查询"),
]

HERE = os.path.dirname(os.path.abspath(__file__))


def fetch(slug):
    url = f"{HOST}/api/docs/{slug}?book_id={BOOK_ID}&mode=markdown"
    req = urllib.request.Request(url, headers={"User-Agent": UA})  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))["data"]


def main():
    check = "--check" in sys.argv
    manifest = []
    for name, slug, _title in DOCS:
        data = fetch(slug)
        body = data.get("sourcecode") or ""
        meta = {
            "file": name,
            "slug": slug,
            "title": data["title"],
            "url": f"{HOST}/org-wiki-obtech-vh7w9r/hc3pio/{slug}",
            "updated_at": data.get("content_updated_at"),
            "words": data.get("word_count"),
            "chars": len(body),
        }
        manifest.append(meta)
        status = f"{name}: {meta['title']}  words={meta['words']} chars={meta['chars']} updated={meta['updated_at']}"
        print(status)
        if check:
            path = os.path.join(HERE, name)
            if not os.path.exists(path):
                print(f"  !! 本地缺失 {path}")
            continue
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            f.write(body if body.endswith("\n") else body + "\n")
        time.sleep(0.5)
    if not check:
        with open(os.path.join(HERE, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"book_id": BOOK_ID, "docs": manifest}, f, ensure_ascii=False, indent=2)
        print(f"\n写入 {len(manifest)} 个文件到 {HERE}")


if __name__ == "__main__":
    main()
