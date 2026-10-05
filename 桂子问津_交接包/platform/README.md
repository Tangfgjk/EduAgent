# 桂子问津 · RSI 教育智能体平台（v1 正式版 Web 工作台 + v2 智能体 CLI）

> **桂子问津（Wenjin）**：华师桂子山 × 武汉问津书院 ×「问津，而后自渡」——
> 平台指渡口，过河靠学生自己。学伴角色名 **津津**。
> **v1 Web 工作台**：`web/index.html`（docs/11 Codex 式三栏工作台，项目优先 IA），
> 由 FastAPI 网关 `app/gateway/routes.py` 服务，网关在线接实时数据、离线回落演示数据。
> **v2 = 自主学习定制版 ZCode**：`app/agent/`（多步智能体循环 + 11 个学习工具 + 三关口）+ `app/cli.py`（终端 REPL）。
> 设计文档见 `../docs/01–11`（10=实施方案，11=UI 设计）。

## v2 快速开始（智能体 CLI）

```bash
cd platform
uv run python -m app.cli
```

```
你> 我要两周学会一元一次方程应用题，期中考试 2026-11-03
  ▶ learner_model.read            ✓ 新学习者，暂无掌握记录
  ▶ bank.search 难度≤0.45 × 3     ✓ 取得 3 道
  ▶ task.issue EQ-001             ✓ 通过门禁
⏸ （诊断 1/3）请作答：解方程：3x + 5 = 14
你> x=8
  ✗ verify.answer — 解与标准答案不等价
  ✓ workspace.write 错题本.md      （含重练排期）
  ✓ hint.ladder — 第一步：3x+5-5=14-5，得到 3x=9。下一步交给你。
⏸ 关口①（计划签署）：计划已写入 计划.md …回复「确认」或说要改什么
…
⏸ 关口②（反思）：策略=…；预计=90；存=是
✓ 单元总结：作答 N 次，错题 M 道已归档排期；掌握 SOLVE 0.10→0.61；自主性 0.43
```

- **单元级自主 + 三关口**：给目标后智能体自主跑完 诊断→计划→练习→错题归档→计划修订；
  只有 计划签署 / 反思关卡 / 红线升级 三个关口必须停下等人，随时 Ctrl+C 中断（进度实时落盘）。
- **门禁在工具层**：每个工具调用（布题/提示/讲解/验证）都过 R-01…R-10 规则引擎；
  要答案会被 R-01 拦下并转为阶梯提示；挫败时 R-03 禁止加难度；考试模式 R-09 锁死帮助；
  探究任务 R-10 不代做完整方案。
- **学习工作区**：`workspace/<学习者>/计划.md、错题本.md、笔记/、作品/`——智能体真实读写，
  等价于 ZCode 的代码仓库。
- **LLM 驱动 + 硬骨架**：阶段推进是硬状态机（防跑飞、步数预算 MAX_STEPS），
  阶段内的题量规划、补救工具选择、话术由 GLM 驱动；LLM 不可用时全部回落确定性默认值。

会话内命令：`/mirror` 我的镜子 · `/plan` 看计划 · `/quit` 退出。

## v1 Web 服务（保留）

```bash
uv run python -m app.main   # http://127.0.0.1:8000（五屏 SRL 闭环 + REST/SSE API）
```
## 官方 Web 工作台（桂子问津 v1，2026-09-27 上线）

```bash
uv run python -m app.main        # http://127.0.0.1:8000/ 即工作台（web/index.html）
```

也可以纯静态打开 `web/index.html`（无网关时自动回落演示数据，界面照常可用）。
高保真原型与六张截图在 `web/prototype_v2_workbench.html` 与 `web/screenshots/`。

**五个视图**（顶栏切换，`#plan` `#study` `#roundtable` `#evidence` `#settings` 直达）：

| 视图 | 看什么 | 背后机制 |
|---|---|---|
| 计划 | 进度环/里程碑轴/子目标条 + **学习路径推荐**（津津的提案：信号卡→路径流→采纳/我改/换思路）+ diff 签署 | `/api/path/recommend`（PathRecommendation@1 规则版）· R-02 只提案不代签 |
| 陪学 | 会话流 + 工具卡 + 苏格拉底徽章 + 问题链 + 话语占比 + 右栏阶梯/预算/5E/镜子 | 提示阶梯（L0-L3×scaffold_type）· R-01/R-07 门禁 · TriggerEngine |
| 圆桌 | 教师+不同立场"同学"多席辩论，结论带分歧标注 | 派生⑤（设计态，v1 为界面演示） |
| 成长证据 | 九张证据卡：掌握轨迹/Bloom 右移/脚手架递减/无辅助增量/遗忘曲线/元认知校准/错误演变 + **认知诊断·知识追踪预留位** | `/api/evidence/{learner_id}`（EvidenceReport@1 聚合版） |
| 设置 | 学段档/支架自调（带外代价）/无障碍/数据同意/平台级约定 | StageProfile · 同意范围进事件流 consent_scope |

左栏 = **项目导航**（一级目录，全视图常驻）；顶栏两枚面板图标或 `[` `]` 收纳左右栏（教学面板仅陪学视图）。

**新增 API（M1 第一刀）**：

```bash
curl "http://127.0.0.1:8000/api/path/recommend?learner_id=$RSI_LEARNER_ID"  # 路径提案（无快照→404）
curl -X POST http://127.0.0.1:8000/api/path/accept -H 'content-type: application/json' \
     -d '{"learner_id":"…","path":{…}}'        # 采纳 → 落 PlanVersion（须有契约，R-02）
curl http://127.0.0.1:8000/api/evidence/…      # 成长证据面板数据
curl http://127.0.0.1:8000/api/governance/hard-rules   # 硬约束注册表（10 条 + 哈希）
```

## 打开页面后怎么玩（5 屏 = SRL 闭环）

1. **① 学习契约**：写目标 → 学生签署（R-02：AI 只能提议）→ 自动生成计划版本。
2. **② 探究会话**：答错 → 提示阶梯升级（L0 轻推→L3 完整讲解）+ 归因安全反馈；
   说"直接告诉我答案" → **R-01 拦截**并转为阶梯提示（拦截留审计）；
   连续卡壳 → 触发器主动提示（占 R-07 预算，自主性越高预算越少）；
   挫败连续超阈 → **R-03** 禁止加难度，先安抚。
3. **② 阶段测试**：R-09 考试隔离——AI 只讲规则不给答案，提交后统一解析。
4. **③ 反思关卡**：归因选择 + 元认知校准（预测 vs 实际）+ 策略入库 → 自主性指数微升。
5. **④ 我的镜子**：掌握图谱（含置信区间）、误区**假设云**（概率非判决）、自主性仪表、
   元认知校准、硬约束注册表哈希。
6. **⑤ 进化演示**：事件流 → 过滤 unverifiable → 按 KC 聚合 → 沉淀建议（L0 最小闭环）。

## 架构对照（文档 → 代码）

| 文档 | 模块 |
|---|---|
| 02 状态Schema | `app/core/schema.py`（Pydantic，extra=forbid 禁特质字段） |
| 03 动作语法/状态机/规则 | `app/core/actions.py`（信封+ScaffoldType）· `app/core/machines.py` · `app/core/rules.py`（R-01…R-10） |
| 04 进化环接口 | `app/learning/verifier.py`（Verdict）· `evolution/l0_digest.py`（Stage1-3 极简版） |
| 编排环 | `app/orchestration/session.py`（step0 触发→5 记录）· `trigger.py` · `policy.py` |
| **v2 智能体运行时** | `app/agent/loop.py`（阶段状态机+LLM 工具选择+可重入关口）· `tools.py`（11 工具×门禁）· `transcript.py` · `workspace.py` · `gates.py` |
| 智能体 CLI | `app/cli.py`（终端 REPL：转录渲染 + 关口交互） |
| 派生机制 | `app/agents/templates.py`（怀疑者 + 生命周期契约） |
| 网关/治理 | `app/gateway/routes.py`（/api/governance/hard-rules 哈希锁定） |
| 存储 | `app/storage/db.py`（SQLite；events/snapshots 严格 append-only） |

## 开发与测试

```bash
uv venv --python 3.11 && uv pip install -e ".[dev]"   # 首次
uv run pytest -q                                       # 61 个用例全绿（含 tests/test_m1.py M1 验收 8 项）
uv run python -m evolution.l0_digest                   # L0 沉淀演示
```

LLM 配置：`cp .env.example .env` 后填 OpenAI 兼容端点（智谱 GLM 默认）；已验证的内网 GLM 示例见 `.env`。
未配置 Key 时自动用 FakeLLM——全部教学机制照常运行，零 API 成本回归测试。
环境变量：`RSI_LLM_API_KEY` / `RSI_LLM_BASE_URL` / `RSI_LLM_MODEL` / `RSI_DB_PATH` / `RSI_LEARNER_ID` /
`RSI_LLM_EXTRA_BODY`（透传 chat/completions 私有字段的 JSON；推理模型关思考链示例见 .env，36s→4.5s/回合）。

## 后续路线（接口已留桩）

M1 余项：DeepTutor 学习引擎抽取（掌握门槛/FSRS）· 派生①④⑤⑥ · 记忆 L1/L2/L3 巩固器 · Postgres · 冻结回归集。
后续：Web 转录界面（v2.1）· 孪生模拟器与晋升门（M2/M3=RSI 点火，见 docs/10 §9.3 五条件）·
技能库结晶与市场 · 师伴与金标通道 · 认知诊断/知识追踪模型接入（docs/11 §6.4 预留位）。
