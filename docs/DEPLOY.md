# 上线手册：部署到 EdgeOne Makers（Git 推送路线）

> 目标：把「经营周报数字员工」以 **agents/zhoubao** 的形式部署上线，
> 线上路径 `POST /zhoubao`，断点恢复依赖平台 `context.store`（持久到 Blob，跨实例成立）。
>
> 对照《从 0 到 1 做 AI Agent》教程的路线：本地跑通 → 推 GitHub → Makers 导入 → 自动部署 → 拿到线上 URL。

---

## 一、前置条件

| 项 | 要求 | 说明 |
|---|---|---|
| Node.js | ≥ 18 | 本机已装 22.22 |
| Python | ≥ 3.10 | 云端由平台管理，本地 3.13 |
| EdgeOne Makers 账号 | 需要你登录 | **这步只能你自己做** |
| GitHub 账号 | 需要你授权 | Makers 从 GitHub 拉仓库 |

## 二、本地最终验证（推之前必跑）

```bash
cd E:\新建文件夹\mini-digital-employee
C:\Users\lenovo\.workbuddy\binaries\python\envs\default\Scripts\python.exe -m pytest tests -q
# 预期：19 passed

# 核心代码若有改动，先同步进 agents/_vendor（重要！云端靠它自包含）
C:\Users\lenovo\.workbuddy\binaries\python\envs\default\Scripts\python.exe scripts\bundle_agents.py
```

## 三、推送到 GitHub

账号：**`su9566890-max`**（GitHub，已核实存在；此前误记为 su9566890，该用户名不存在），
提交邮箱 `su9566890@gmail.com`。
20 次提交的作者信息已统一改写为该身份，无需再处理。

**第 1 步：在 GitHub 上建空仓库**

打开 https://github.com/new ，填：

- Repository name：`mini-digital-employee`
- 选 **Public**（Makers 导入私有仓库需要额外授权，公有最省事）
- **不要**勾选 Add a README file / .gitignore / license（仓库里已经有了）

**第 2 步：推送**（两种方式二选一）

> 前置说明：本项目**只在你的本机**，GitHub 上还没有。Makers 的导入页只列 GitHub 上
> **已存在**的仓库，所以在推送完成前，那个列表里不可能出现 `mini-digital-employee`，
> 刷新也没用。必须走完这一步，再回 Makers 页面。

**方式一：一键脚本（推荐，含仓库存在性自检）**

在项目目录打开 **Git Bash**，执行：

```bash
bash scripts/push_github.sh
```

脚本会先探测仓库是否可访问，没建好会直接告知，不会报一堆看不懂的错。
若改过 `app/`、`config/`、`assets/`，用 `bash scripts/push_github.sh --bundle`
（会先同步 `agents/_vendor` 再推送）。

**方式二：手动命令**

```bash
cd E:\新建文件夹\mini-digital-employee
git branch -M main                 # 本地当前是 master，统一改名为 main

# 方式 A：HTTPS + 浏览器登录（最简单）
git remote add origin https://github.com/su9566890-max/mini-digital-employee.git
git push -u origin main

# 方式 B：GitHub CLI（装了 gh 的话）
# gh auth login
# gh repo create mini-digital-employee --public --source=. --remote=origin --push

# 方式 C：SSH（配过 key 的话）
# git remote add origin git@github.com:su9566890-max/mini-digital-employee.git
# git push -u origin main
```

> **认证提示**：方式 A 会弹 GitHub 登录窗口。若之前没登录过，GitHub 现在要求用
> **Personal Access Token** 作为密码（密码登录已停用）：
> GitHub 头像 → Settings → Developer settings → Personal access tokens → Tokens (classic)
> → Generate new token → 勾选 `repo` → 生成后**当作密码粘贴**。

> 若推送时报 `! [rejected] main -> main (fetch first)`，说明建仓库时勾了 README，
> 先 `git pull origin main --rebase` 再 `git push -u origin main`。

## 四、Makers 导入与部署

1. 打开 https://edgeone.ai 并登录，进入 **Makers 控制台**；
2. 选择 **导入 Git 仓库**（首次需授权 GitHub）；
3. 选中刚推送的 `mini-digital-employee` 仓库，分支 `main`；
4. 构建配置保持默认即可 —— 仓库根已有：
   - `edgeone.json`（agents 目录与超时声明）
   - `requirements.txt`（pandas / matplotlib / python-docx / openai 等，平台自动安装）
5. 点击创建，等构建完成，拿到形如 `https://<project>.edgeone.app` 的线上 URL。

## 五、线上验证

### ⚠ 先过这一关：裸域名一定打不开，别再刷了

`*.edgeone.cool` / `*.edgeone.dev` 这类**部署域名**自带预览鉴权，**直接粘贴域名访问必然失败**：

| 现象 | 真实原因 |
|---|---|
| `401 UNAUTHORIZED` + 响应头 `X-EOP-MSG: eo_time missing` | 链接缺签名参数 `eo_token` / `eo_time` |
| `404 NOT_FOUND` The site does not exist | 该部署已失效（push 触发新部署后旧域名作废） |
| 加速区域设为「全球（**不含**中国大陆）」 | 中国大陆网络**永远 401**，与链接无关 |

**正确访问方式**：控制台 → 项目概览 → 右上角点 **「预览」** → 复制生成的**完整链接**（含 `?eo_token=...&eo_time=...`）访问。
该链接 **有效期 3 小时**，过期再点一次「预览」即可。

**答辩/长期使用请绑定自定义域名**（不受 3 小时限制、无需签名参数，全球可用区免备案）。

### 命令行验证（一键脚本）

```bash
python scripts/verify_online.py --url "https://<your-app>.edgeone.cool/?eo_token=xxx&eo_time=yyy"
```

脚本会自动把链接上的鉴权参数转成 Cookie 携带，依次验证：
探活 → 发起任务（自动上传示例材料 base64）→ 查询 → 审核通过 → 校验交付产物可还原。

### 手动 curl

请求头必须带 `Makers-Conversation-Id`（6–36 位，字母数字 `-_ .`），否则平台直接 400。

```bash
BASE=https://<project>.edgeone.app
CID=conv_demo_001

# 1) 发起任务（材料文件先转 base64）
B64_SALES=$(base64 -w0 sample_data/销售明细.xlsx)
B64_NOTES=$(base64 -w0 sample_data/周会纪要.docx)
curl -s -X POST "$BASE/zhoubao" \
  -H "Makers-Conversation-Id: $CID" -H "Content-Type: application/json" \
  -d "{\"action\":\"submit\",\"task_id\":\"zb_online_001\",
       \"period\":\"2026 年第 44 周\",\"submitter\":\"张店长\",
       \"sales_name\":\"销售明细.xlsx\",\"sales_file\":\"$B64_SALES\",
       \"notes_name\":\"周会纪要.docx\",\"notes_file\":\"$B64_NOTES\"}"
# 预期 ok:true, stage:"HUMAN_REVIEW"

# 2) 审核通过 → 交付
curl -s -X POST "$BASE/zhoubao" \
  -H "Makers-Conversation-Id: $CID" -H "Content-Type: application/json" \
  -d '{"action":"review","task_id":"zb_online_001","decision":"approve","comment":"通过"}'
# 预期 ok:true, stage:"DELIVERED"，files 里有周报 docx 与图表 PNG（base64）

# 3) 断点恢复验证：换一个新任务，只 submit 后立即（不经任何本地状态）
#    直接再次调用 action=status —— 只要带同一个 conversation_id，
#    新请求即便落到别的实例，任务状态也从平台存储恢复。
```

**模型配置**：在 Makers 项目的环境变量里设置（不设则落 Mock 模式，流程仍通）：

| 变量 | 值 |
|---|---|
| `AI_GATEWAY_BASE_URL` | `https://ai-gateway.edgeone.link/v1` |
| `AI_GATEWAY_API_KEY` | Makers Models 的 Key（控制台「模型与密钥」页可托管厂商 Key） |
| `AI_GATEWAY_MODEL` | 如 `@makers/deepseek-v4-flash`（默认）或 `deepseek/deepseek-chat` |

## 六、本地联调（可选）

```bash
npm install -g edgeone     # 官方 CLI
edgeone login              # 浏览器登录（这步只能你自己做）
edgeone makers dev         # 本地 8088 起服务，/agent-metrics 看调用链路
```

## 七、常见问题

**Q1：`agents/framework` 填了 openai-agents-sdk 但代码没用这个 SDK？**
平台枚举五选一（claude-agent-sdk / openai-agents-sdk / langgraph / crewai / deepagents），
本员工是自研状态机，只用 `context.store` 的**通用 API**（state KV + 消息历史），
这些 API 与 framework 无关。填 openai-agents-sdk 仅为满足平台枚举。若控制台校验
依赖存在，在 requirements.txt 补 `openai-agents` 即可（预留项，先不加）。

**Q2：改了核心代码，云端怎么没生效？**
`agents/_vendor/` 是核心代码的打包副本，改完 `app/`、`config/`、`assets/` 后必须跑
`python scripts/bundle_agents.py` 同步再提交。这一步写在提交前检查清单里了。

**Q3：图表中文变方块？**
不会 —— 仓库自带 `assets/fonts/NotoSansSC-subset.otf`（OFL 许可，2.9 MB），
`_set_chinese_font()` 会优先注册它，本地与云端渲染一致。若新增了字体覆盖不到的
生僻字，重新子集化（方法见 assets/fonts/LICENSE-README.md）。

**Q4：响应里 `persistence: "degraded"` 是什么意思？**
说明平台 `context.store` 不可用，本次请求内流程能跑完，但跨请求不保留。
正常部署时应为 `"ok"`；若持续 degraded，检查项目是否为 Agents 类型项目。

**Q5：提交身份要改？**
已改为 `su9566890 <su9566890@gmail.com>`（仓库局部配置，未动全局 git config）。
若日后要换成 noreply 邮箱（隐藏真实邮箱）：

```bash
git config user.name "su9566890"
git config user.email "su9566890@users.noreply.github.com"
GIT_COMMITTER_NAME="su9566890" GIT_COMMITTER_EMAIL="su9566890@users.noreply.github.com" \
  git rebase --root --exec 'git commit --amend --reset-author --no-edit --allow-empty'
git push -f origin main      # 仓库尚无协作者时 force 是安全的
```

> 已在改写前建了备份分支 `backup-before-reauthor`，改坏了可 `git reset --hard backup-before-reauthor` 回退。

## 八、如实说明：未经真实部署验证的部分

以下判断来自官方文档与模板仓库源码，但**没有真实账号跑过线上部署**，
首次部署如遇到问题按此排查：

1. **`context.request.body` 是否自动解析 JSON** —— 官方模板这样取值，代码已做
   多形态防御（dict / bytes / str 都能吃），但极端情况可能要在入口补一层；
2. **`agents/` 目录能否引用 `_vendor` 以外的仓库路径** —— 已经用 vendoring 规避，
   入口只依赖 `agents/_vendor` 与平台注入；
3. **state KV 单值大小** —— 文档未明说，交付产物内联设了单文件 4 MB 上限，
   超限只登记元信息不内联（会在 files 里标注 oversize）；
4. **冷启动时长** —— matplotlib + pandas 首次 import 较慢，agents timeout 已配 300 秒。
