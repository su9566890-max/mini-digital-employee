# -*- coding: utf-8 -*-
"""打印指定交付周报的正文（调试/演示用）。

    python scripts/show_report.py <周报.docx 路径>
缺省时打印最新一次交付。
"""
import glob
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docx import Document  # noqa: E402


def latest_report():
    files = glob.glob(os.path.join(ROOT, "deliveries", "*", "*经营周报.docx"))
    return max(files, key=os.path.getmtime) if files else None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else latest_report()
    if not path or not os.path.exists(path):
        print("未找到周报文件")
        return
    print("文件：%s\n" % path)
    for p in Document(path).paragraphs:
        if p.text.strip():
            print(p.text.strip()[:160])


if __name__ == "__main__":
    main()
