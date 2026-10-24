# 迷你数字员工 · 经营周报员工（场景 A）

> 赛题：《迷你数字员工——可审核、可交付的岗位业务 Agent》· 锐软科技（深圳）有限公司
> 场景 A：读取门店销售数据 + 周会纪要 → 汇总分析、定位异常 → 生成带图表和结论的经营周报（Word）

**设计主线**：把一个"会出错的大模型"做成流程可控、结果可检、出错能停的系统。
所有关键控制（准入、能否送审、能否交付、非法流转、超限暂停）**由程序判定**，模型只负责"叙事"，不参与控制决策。

---

## 一、一键运行

环境：Python 3.10+（本项目在 3.13 验证通过）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 生成模拟业务数据（销售明细.xlsx / 周会纪要.docx / 任务表单样例.json）
python main.py init-data

# 3. 端到端演示：发起 → 执行 → 质检 → 人工退回 → 采纳重做 → 通过 → 交付
python main.py demo
```

> 默认**离线 Mock 模式**运行（不需任何 API Key），全流程可跑通；
> 配置 Key 后自动切换到真实模型。

### 分步操作流程

```bash
python main.py submit --form sample_data/任务表单样例.json   # ① 表单发起任务
python main.py resume  <task_id>                              # ② 推进（准入→执行→质检）
python main.py review  <task_id> approve --comment "可以使用"   # ③ 人工审核通过 → 交付
python main.py review  <task_id> reject  --comment "结论偏少"   # ③' 退回（意见自动被采纳重做）
python main.py add-material <task_id> sales_file <路径>        # 缺材料时补充后继续
python main.py status  <task_id> --events                      # 查看状态/质检记录/事件流
python main.py tools                                           # 查看工具清单与入参出参
python main.py init-data                                       # 生成模拟数据
```

**Mock vs 真实模型**：在 `config/employee_zhoubao.yaml` 里配 `model.base_url / model / api_key_env`，
设置对应环境变量即可切换到 DeepSeek / 通义千问 / GLM 等任意 OpenAI 兼容接口；无 Key 时自动降级 Mock。

---

## 二、目录结构

```
mini-digital-employee/
├─ main.py                       命令行入口（submit/resume/review/status/tools/demo/web）
├─ requirements.txt
├─ config/employee_zhoubao.yaml  员工能力配置（职责/步骤/上限/工具白名单/质检规则）
├─ app/
│  ├─ state.py                   状态机：流转控制、断点恢复、超限暂停
│  ├─ storage.py                 持久化抽象层：Disk / Memory / Makers 三后端
│  ├─ events.py                  事件日志（薄封装，写入位置由 Storage 决定）
│  ├─ llm.py                     模型层：OpenAI 兼容 + Makers 网关 + 离线 Mock 兜底
│  ├─ qa.py                      自动质检 4 类检查 + 检验记录
│  ├─ mcp_server.py              MCP 服务端（FastMCP）
│  └─ tools/
│     ├─ __init__.py             工具注册表：白名单/重试/越权拒绝/留痕
│     └─ implementations.py      工具实现（5 个）
├─ cloud-functions/
│  ├─ zhoubao-web/index.py       云端网页工单台 Flask 入口（GET /zhoubao-web）
│  ├─ requirements.txt           云端依赖（优先于根目录）
│  └─ _vendor/                   核心代码打包副本（scripts/bundle_agents.py 同步）
├─ agent_source/                 早期 Agent 版入口（保留参考，不再部署）
├─ assets/fonts/                 自带中文字体（Noto Sans CJK SC 子集，OFL 许可）
├─ scripts/
│  ├─ make_sample_data.py        生成模拟业务数据
│  ├─ bundle_agents.py           同步核心代码进 agents/_vendor 与 cloud-functions/_vendor
│  ├─ web_smoke.py               Web 工单台端到端冒烟（8 步）
│  └─ show_report.py             打印最新交付周报正文
├─ docs/DEPLOY.md                上线手册（Git 推送路线，逐条命令）
├─ sample_data/                  模拟数据（无隐私、无真实商业信息）
├─ tasks/<task_id>/              任务运行态：state.json + events.jsonl + 图表
├─ deliveries/<task_id>/         交付成果：周报 + 图表 + 检验记录 + 事件日志
└─ tests/                        24 条自测用例（test_all 14 + test_storage 5 + test_cloud_function 5）
```

**一份代码，两地运行**：`TaskRunner` 只依赖 `storage.py` 的同步接口 ——
本地写磁盘（`tasks/<id>/`），云端写平台 `context.store`（持久到 Blob，跨实例不丢），
因此「断点恢复」在 Serverless 环境同样成立；Web 工单台与云端 Agent 跑的是同一套状态机与质检。

---

### Web 工单台（可选，答辩演示更直观）

```bash
pip install flask
python main.py web            # 默认 http://127.0.0.1:5000
python main.py web --port 8080
```

| 页面 | 能力 |
|---|---|
| 任务列表 `/` | 所有任务及其当前状态、质检通过数 |
| 发起工单 `/new` | 填周期/发起人，上传销售数据与周会纪要（故意少传一份可演示缺材料） |
| 任务详情 `/task/<id>` | 状态卡片、操作区、质检结果、事件流时间线、产物下载 |

操作按钮的**可用性由程序按当前状态决定**，网页不做判断：

- `PAUSED_MISSING` → 只显示「补充材料」入口，补充后自动继续
- `HUMAN_REVIEW` → 审核通过/退回（退回必填意见），可预览周报
- `SUSPENDED` → 显示暂停原因，人工处理后「恢复执行」
- `DELIVERED` → 交付物下载（周报/图表/事件日志）
- 其余状态 → 「继续推进」

配套脚本：`python scripts/web_smoke.py --port 5000` 一键冒烟（发起上传→送审→退回→通过→交付→缺材料→补件恢复，共 8 步）。

---

## 三、状态流转表（程序把关）

| 当前状态 | 合法目标 | 触发条件（全部由程序判定） |
|---|---|---|
| FORM_SUBMITTED 表单发起 | ADMIT_CHECK | 提交结构化表单 |
| ADMIT_CHECK 准入校验 | EXECUTING / PAUSED_MISSING / FAILED | 字段齐全且材料存在 → 执行；材料缺失 → 暂停待补；字段缺失 → 判失败 |
| PAUSED_MISSING 缺材料暂停 | ADMIT_CHECK | 发起人补充材料后重新校验 |
| EXECUTING 执行 | AUTO_QA / SUSPENDED | 工具调用+叙事成功 → 质检；工具重试仍失败或轮次超限 → 暂停转人工 |
| AUTO_QA 自动质检 | HUMAN_REVIEW / EXECUTING / SUSPENDED | 全部通过 → 待审；未通过且重试未达上限 → 自动修正重做；超上限 → 暂停 |
| HUMAN_REVIEW 人工审核 | DELIVERED / EXECUTING | 通过 → 交付；退回（须附意见）→ 重做，意见写入提示词并被采纳 |
| SUSPENDED 异常暂停 | EXECUTING | 人工处理后恢复 |
| DELIVERED / FAILED | （终态） | 任何再流转均被拒绝并记 `ILLEGAL_TRANSITION` |

关键控制点：**能否进审核、能否交付由程序流转表决定**；越权调用工具被拒绝并记录 `SECURITY_DENY`。

---

## 四、员工能力配置（不改代码即可调整）

`config/employee_zhoubao.yaml` —— 业务人员可直接修改：

- `employee`：岗位职责、工作步骤、注意事项（写进提示词，不写死在代码里）
- `limits`：`max_llm_rounds=12`、`max_llm_seconds=300`、`max_qa_retry=2`、`max_tool_retry=2`
  - **取值理由**：单任务叙事 + 质检修正最多约 8 次调用，取 1.5 倍余量防死循环；300s 覆盖慢速接口单次生成，避免任务无限挂起
- `tools_allowed`：工具白名单（员工只能调用被开放的工具）
- `qa_rules`：质检规则开关（**现场小变更演示点**：加一条规则 = 改一行配置 + 一个检查函数）
- `sensitive_words`：敏感词表

---

## 五、工具（5 个，两套通道）

| 工具 | 入参 | 出参 |
|---|---|---|
| `validate_form` | form(period/sales_file/notes_file/submitter) | missing_fields, missing_materials |
| `read_sales_data` | path(xlsx/csv) | columns, rows |
| `read_meeting_notes` | path(docx/txt) | text |
| `analyze_sales` | sales | total_amount、by_store、by_category、by_date、wow_change_pct、anomalies |
| `generate_weekly_report` | period/analysis/narrative/out_path/charts_dir | report, charts, word_count |

- **进程内通道**（默认）：`app/tools/__init__.py`，含白名单校验、失败重试（上限 2）、调用留痕。
- **MCP 通道**（可选）：`pip install fastmcp` 后执行 `python -m app.mcp_server`，同一批函数以 MCP 协议暴露。
- **工程加固**：`generate_weekly_report` 写文件采用"临时文件 + 原子替换 + 固定路径"，重试不会重复产出文件。

---

## 六、自动质检（4 类检查，≥3 类已满足）

| 检查项 | 内容 |
|---|---|
| `structure_complete` | 周报必须含"一、本周经营概况 / 二、异常波动分析 / 三、结论与建议" |
| `data_consistency` | 周报中的总销售额必须与工具分析结果逐字符一致（防篡改/防幻觉） |
| `sensitive_words` | 交付文档不得出现敏感词 |
| `format_check` | 至少 1 张图表、中文标签、正文 200–5000 字 |

不通过 → 自动修正重试（上限 2 次）→ 仍不通过转人工；每次质检写入 `deliveries/<task_id>/检验记录.json`，随成果交付。

---

## 七、加分项实现情况

- **事件流与断点恢复**：每次状态变化 / 模型调用 / 工具调用 / 质检 / 审核都写入 `events.jsonl`；`state.json` 原子落盘。
  程序中断后重新执行 `python main.py resume <task_id>`，从断点继续，已完成步骤不重复执行（见 `test_06`）。
- **工程加固**：写文件幂等（同上）；越权调用拒绝并留痕（见 `test_07`）。

---

## 八、自测用例（24 条，全部通过）

```bash
python -m pytest tests -q
```

| # | 用例 | 类型 |
|---|---|---|
| 1 | 正常流程跑到待人工审核 | 正常 |
| 2 | 审核通过→交付，交付物齐全（周报/图表/检验记录/事件日志） | 正常 |
| 3 | 缺销售数据→暂停→补充后继续 | 缺材料 |
| 4 | 表单必填字段缺失→直接判失败 | 异常 |
| 5 | 审核退回→意见被采纳→重新送审 | 边界 |
| 6 | 断点恢复：中断后重建 Runner 继续跑 | 边界 |
| 7 | 越权调用工具被拒绝并留痕 | 异常 |
| 8 | 工具失败重试后任务暂停，不崩溃不编造 | 异常 |
| 9 | 模型调用轮次超限→暂停转人工 | 边界 |
| 10 | 非法流转被程序拒绝并留痕 | 异常 |
| 11 | 质检：敏感词不通过 → 自动修正 | 边界 |
| 12 | 质检：数据一致性抓到数值被篡改 | 异常 |
| 13 | 工具入参/出参定义与白名单一致 | 正常 |
| 14 | Mock 与真实模型返回结构一致 | 边界 |

---

## 九、部署上线（EdgeOne Makers，Git 推送路线）

云端采用**纯 Web 运行时**（`cloud-functions/zhoubao-web`），浏览器可直接打开。
**注意**：`agents/` 与 `cloud-functions/` 不能共存于同一部署分支，否则平台构建时
运行时冲突 → 部署失败 → 整站 404。早期 Agent 版入口已改名为 `agent_source/` 保留。

上线三步（详见 [docs/DEPLOY.md](docs/DEPLOY.md) 逐条命令）：

1. `git push` 到 GitHub（需要你的账号）；
2. Makers 控制台导入该仓库，自动构建部署，拿到线上 URL；
3. 浏览器打开 `https://<域名>/zhoubao-web` 即可使用。
   模型走平台 AI Gateway（环境变量三件套，不设则自动落 Mock）。

云端与本地共用同一状态机；Serverless 无持久化，故网页版采用**无状态同步模式**：
状态与材料打包成 blob 随表单回传，产物内联进页面点击下载。


---

## 十、参考的开源项目（已核对存在）

- https://github.com/openai/openai-agents-python — OpenAI Agents SDK（Agent 编排、工具、Guardrails、Handoffs）
- https://github.com/jlowin/fastmcp — FastMCP（把工具暴露为 MCP 服务端）
- https://github.com/modelcontextprotocol/python-sdk — MCP 官方 Python SDK

---

## 十一、已知边界（如实说明）

- Mock 模式下的叙事为模板化输出，**接真实模型后语言表达能力才完整**；所有数值始终来自工具统计，不含模型编造。
- 异常检测使用"偏离该店日均值 ±40%"的统计规则，未做季节性/促销日程建模。
- 交付格式为 Word；如需 PDF/邮件推送，只需新增一个工具并在白名单登记。
- 尚未实现：多员工协同、并行任务队列。
- 云端部署的真实链路（context.request.body 解析、平台依赖校验等）按官方文档与模板源码对齐，**尚未用真实账号完成一次线上部署**，排查清单见 docs/DEPLOY.md 第八节。
