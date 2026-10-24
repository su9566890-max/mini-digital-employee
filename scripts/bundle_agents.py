# -*- coding: utf-8 -*-
"""把核心代码同步进 agents/_vendor，保证云端 agents 目录自包含。

为什么需要
----------
EdgeOne Makers 的「文件即路由」约定里，agents/<name>/index.py 是入口。
官方文档明确保证 cloud-functions/ 会把非入口 .py 作为辅助模块一起打包，
但对 agents/ 目录没有同样承诺。为了不赌平台行为，用 vendoring：

    agents/
    ├── zhoubao/index.py      入口（引用 _vendor 里的包）
    └── _vendor/              私有模块（下划线前缀不生成路由）
        ├── app/              核心代码副本
        ├── config/           员工配置副本
        └── assets/fonts/     中文字体副本

改动核心代码后跑一次本脚本即可同步；_vendor 纳入版本管理，
平台拉仓库即得完整运行所需文件，不依赖构建期路径假设。
"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "agents", "_vendor")

COPIED = ("app", "config", "assets")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "_selftest.png")


def main():
    os.makedirs(VENDOR, exist_ok=True)
    for name in COPIED:
        src = os.path.join(ROOT, name)
        dst = os.path.join(VENDOR, name)
        if not os.path.isdir(src):
            print("跳过（不存在）:", name)
            continue
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=IGNORE)
        print("vendored: %-8s -> agents/_vendor/%s" % (name, name))
    print("完成。agents/_vendor 与仓库核心代码已同步。")


if __name__ == "__main__":
    main()
