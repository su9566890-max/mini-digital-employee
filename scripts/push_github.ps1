# 一键推送到 GitHub（PowerShell 版）
# 用法：在项目目录下执行
#     powershell -ExecutionPolicy Bypass -File scripts\push_github.ps1
# 若改过 app/ config/ assets/，加 -Bundle 参数先同步 agents/_vendor。

param([switch]$Bundle)

$ErrorActionPreference = 'Stop'

$Repo = 'su9566890-max/mini-digital-employee'
$Url  = "https://github.com/$Repo.git"

Set-Location (Join-Path $PSScriptRoot '..')

if ($Bundle) {
    Write-Host '==> 同步核心代码到 agents/_vendor ...'
    & python scripts/bundle_agents.py
    if ($LASTEXITCODE -ne 0) { Write-Host '[x] 同步失败，已中止推送'; exit 1 }
}

Write-Host "==> 探测 GitHub 仓库 $Repo ..."
git ls-remote $Url 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host "[x] 访问不到 $Url"
    Write-Host '    可能原因：'
    Write-Host '    1) 仓库还没建 —— 打开 https://github.com/new ，'
    Write-Host '       仓库名填 mini-digital-employee，选 Public，三个勾选框都不要勾'
    Write-Host '    2) 登录的 GitHub 账号不是 su9566890-max'
    Write-Host '    3) 仓库是私有且本机没配凭据（建议先建为 Public）'
    Write-Host '    处理好后再跑一次本脚本。'
    exit 1
}
Write-Host '    OK，仓库可访问。'

git branch -M main

$hasOrigin = $false
try { git remote get-url origin 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $hasOrigin = $true } } catch {}
if ($hasOrigin) { git remote set-url origin $Url } else { git remote add origin $Url }

Write-Host '==> 推送中（会弹 GitHub 登录；密码栏请粘贴 PAT）...'
git push -u origin main
if ($LASTEXITCODE -eq 0) {
    Write-Host ''
    Write-Host "[OK] 推送成功：https://github.com/$Repo"
    Write-Host '     下一步：打开 https://edgeone.ai → Makers 控制台 → 导入 Git 仓库 →'
    Write-Host '     刷新列表，选中 mini-digital-employee，分支 main，构建配置默认，点创建。'
} else {
    Write-Host ''
    Write-Host '[x] 推送失败。请把上面这段报错原文发我，我按报错定位。'
    exit 1
}
