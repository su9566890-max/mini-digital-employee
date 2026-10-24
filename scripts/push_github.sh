#!/usr/bin/env bash
# 一键推送到 GitHub
# 用法（在项目目录下打开 Git Bash）：bash scripts/push_github.sh
# 若想在推送前自动刷新 agents/_vendor，加 --bundle 参数。

set -uo pipefail

REPO="su9566890-max/mini-digital-employee"
URL="https://github.com/${REPO}.git"
cd "$(dirname "$0")/.." || exit 1

PY="${PYTHON:-python}"

if [ "${1:-}" = "--bundle" ]; then
  echo "==> 同步核心代码到 agents/_vendor ..."
  "$PY" scripts/bundle_agents.py || { echo "[x] 同步失败，已中止推送"; exit 1; }
fi

echo "==> 探测 GitHub 仓库 ${REPO} ..."
if ! git ls-remote "$URL" >/dev/null 2>&1; then
  echo ""
  echo "[x] 访问不到 ${URL}"
  echo "    可能原因："
  echo "    1) 仓库还没建 —— 打开 https://github.com/new ，"
  echo "       仓库名填 mini-digital-employee，选 Public，三个勾选框都不要勾"
  echo "    2) 登录的 GitHub 账号不是 su9566890-max"
  echo "    3) 仓库是私有且本机没配凭据（建议先建为 Public）"
  echo "    处理好后再跑一次本脚本。"
  exit 1
fi
echo "    OK，仓库可访问。"

git branch -M main
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$URL"
else
  git remote add origin "$URL"
fi

echo "==> 推送中（会弹 GitHub 登录；密码栏请粘贴 PAT）..."
if git push -u origin main; then
  echo ""
  echo "[√] 推送成功：https://github.com/${REPO}"
  echo "    下一步：打开 https://edgeone.ai → Makers 控制台 → 导入 Git 仓库 →"
  echo "    刷新列表，选中 mini-digital-employee，分支 main，构建配置默认，点创建。"
else
  echo ""
  echo "[x] 推送失败。请把上面这段报错原文发我，我按报错定位。"
  echo "    常见：Authentication failed（PAT 无效/缺 repo 权限）"
  echo "         ! [rejected] (fetch first)（建仓库时勾了 README）"
  exit 1
fi
