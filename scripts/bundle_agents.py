# -*- coding: utf-8 -*-
"""把核心代码同步进各云端的 _vendor 目录，保证部署单元自包含。

为什么需要这一层
----------------
EdgeOne Makers 按目录约定打包：

* ``agents/<name>/index.py``    是 Agent 入口
* ``cloud-functions/<dir>/...`` 是云函数入口

官方文档承诺 cloud-functions 会把非入口 ``.py`` 作为辅助模块一起打包，
但对 ``agents/`` 没有同样承诺；而 ``scripts/``、``tests/`` 两个目录
官方明确**不会**扫描和复制。

与其赌平台行为，不如直接 vendoring：把 ``app/`` ``config/`` ``assets/``
复制一份到各自的 ``_vendor/`` 下，入口只依赖 ``_vendor``，
平台拉仓库即得运行所需全部文件，不依赖构建期路径假设。

改动核心代码后跑一次本脚本即可同步；``_vendor`` 纳入版本管理。
"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 需要同步的云端目录：(父目录, vendor 目录名)
TARGETS = (
    ("agents", "_vendor"),
    ("cloud-functions", "_vendor"),
)

COPIED = ("app", "config", "assets")
# mcp_server.py 仅供本地 MCP 通道使用，云端不部署：
# 它 import fastmcp，会被平台的自动依赖扫描发现并强制安装，
# 而 fastmcp 不在云端 requirements 里 → 构建失败 → 整站 404（已踩过此坑）。
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "_selftest.png",
                                "mcp_server.py")


def main():
    for parent, vendor in TARGETS:
        dst_root = os.path.join(ROOT, parent, vendor)
        if not os.path.isdir(os.path.join(ROOT, parent)):
            print("跳过（父目录不存在）:", parent)
            continue
        os.makedirs(dst_root, exist_ok=True)
        for name in COPIED:
            src = os.path.join(ROOT, name)
            dst = os.path.join(dst_root, name)
            if not os.path.isdir(src):
                print("  跳过（不存在）:", name)
                continue
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=IGNORE)
            print("  vendored: %-8s -> %s/%s/%s" % (name, parent, vendor, name))
        print("完成：%s/%s 已与仓库核心代码同步" % (parent, vendor))


if __name__ == "__main__":
    main()
