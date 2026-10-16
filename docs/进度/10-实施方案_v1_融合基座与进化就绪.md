# 10 · v1 实施方案：DeepTutor 基座融合与 RSI 就绪架构

| | |
|---|---|
| 版本 | v0.1（2026-09-26） |
| 状态 | 评审稿（⚑ 标记处为默认决策，可推翻） |
| 上游 | docs/01–09（v0.3 定稿）；《教育智能体应用调研情况汇报》（2026-09）；DeepTutor / OpenMAIC 源码审计（`参考项目/` 两仓库，已核实）；`platform/` 现有代码（~3.4k 行 + 678 行测试） |
| 下游 | `platform/` 后续编码；01 §8 里程碑修订；03/04 增量条款 |

**一句话定位**：v1 = "会教学的内核 + 会记录的地基"——教学引擎借 DeepTutor，课堂形态与支架表述借 OpenMAIC，治理与可验证性用自己的硬约束内核；RSI **只铺轨道、不点火**，但 v2 点火时零重构。

---

## 1. 总原则（五条，冲突时按序裁决）

1. **北极星不变**。无辅助表现增量、认知层级跃迁、autonomy_index；完课率/互动量/停留时长永远只做诊断遥测，不进任何奖励与晋升判据（04 §3 古德哈特防线照旧）。
2. **引擎可借，治理不借**。一切教育动作——无论来自自研编排还是 vendor 引擎——必须封装为动作信封过 R-01…R-10 规则引擎（03 §1）。基座代码进入"被门禁管辖"的地位，不存在绕行通道。
3. **抽取不 fork**。DeepTutor（Apache-2.0）按模块摘取进 `vendor/` 层，保留 LICENSE 与 THIRD_PARTY_NOTICES；OpenMAIC（MIT）只取模式、提示词资产与组件，逐份保留版权声明。vendor 代码不上浮：import-linter 式约束强制 `app → vendor` 单向。
4. **v1 不点火自动 RSI**，但 04 号文档的全部接口按 v1 范围实现（见 §9）。v2 的进化环只填实现、不加接口——防"以后从头开发"的保证就在这一条。
5. **一切可验证**。每个教育机制配验收测试（03 §6 风格）；每个可进化对象（软参数/模板/技能）带版本与谱系；系统级回归用冻结场景集（§8）。

---

## 2. 基座融合地图

处置动词定义：**抽取** = 摘源码进 `vendor/` 适配后使用；**移植** = 取提示词/模板/组件改写为我们的资产；**参考** = 只学设计与测试写法；**自建** = 用 01–09 已有设计实现。

| 组件 | 来源与现状 | v1 处置 | 关键改造点 |
|---|---|---|---|
| 学习引擎：掌握门槛（按知识类型，默认 0.9）、FSRS 风格间隔调度（事件重放+CAS）、Feynman 检查、错误四分类 | DeepTutor `deeptutor/learning/`，纯逻辑、带测试、留有 Protocol 替换点 | **抽取** | 掌握度与调度事件的持久化从其 SQLite 改接我们的存储适配器；`compute_mastery` 的证据输入改接我们的 Verifier Verdict（04 §2）；其 docstring 自认可换 IRT/BKT——接口保留 |
| 三层记忆 L1(trace jsonl)/L2(分表面小结)/L3(recent·profile·scope·preferences) + LLM 巩固器（含防篡改审计） | DeepTutor `services/memory/` | **抽取模式 + 自建** | 与 02 §3.4 对齐：L1=InteractionEvent 事件流、L2=可再生派生视图（标注 derived）、L3=画像；**加我们独有的 L3 禁止引用 L3 硬规则**；记忆图谱钻取链（09 已吸收）自建 |
| LLM Provider 注册表（39 家，含智谱/DeepSeek/Kimi/MiniMax/火山/DashScope） | DeepTutor `services/provider_registry.py` | **抽取** | 仅需 openai_compat/anthropic 两类后端 + 智谱与 DeepSeek 预设；structured_retry/traffic_control 一并取用 |
| 出题流水线（三阶段：idea→plan→per-question；仿题 mimic） | DeepTutor `agents/question/`（2309 行） | **参考，v1 简版自建** | 取三阶段骨架与题干验证钩子；难度字段预留 `irt_params: null`（v3 校准）；考题引用受 R-09 管辖 |
| 测验判分 | DeepTutor `quiz_judge` | **参考** | 以我们 Verifier 契约为准（rubric+Bloom 打标已在 04 §2） |
| RAG 知识库（8 条流水线，默认 llamaindex+FAISS） | DeepTutor `services/rag/` | **抽取默认线** | 只装 llamaindex 一条；embedding 签名防错配机制照搬；检索结果必须挂 KG 节点才可进 EXPLAIN（R-06 接地） |
| 课堂生成：大纲→逐页→TTS，15+ 类内容模板 | OpenMAIC `classroom-generation.ts` + `packages/@openmaic/generation/templates/` | **移植模板** | 对应 03 §2.1 模式库的 slides_lecture / quiz / interactive_sim / live_book；生成物一律走 REVIEW_RECORD 送审、大纲先审后展开 |
| 支架披露阶梯参数化（L0–L3，stuck 信号计数驱动，"禁止首答跳 L3"） | OpenMAIC `tier-guidance.ts` | **参考** | 融入 03 §4.1 提示阶梯：阶梯放行速度由 stuck-signal 计数与 StageProfile 共同决定（§4） |
| 圆桌研讨 + 导演委派协议（`call_agent` 前强制 `read_scene`，证据先行） | OpenMAIC roundtable + Pi director-loop | **参考** | 派生类型⑤（01 §5.2）的实现蓝本；证据先行写入派生委派协议（§6） |
| PBL 导师/评审/模拟器提示词（instructor-base-rules 252 行等） | OpenMAIC `lib/pbl/v2/prompts/` | **移植** | 汉化 + 过 R-04 归因分类器改造（其打分话术需按"只许努力与策略归因"重写）；60 分放行门槛保留为软参数 |
| 评测工装（LLM-as-judge 场景评测：路由/收束/答内容质量） | OpenMAIC `eval/orchestration/` | **移植结构** | 改造为我们的冻结场景回归集运行器（§8.2） |
| 白板 / 圆桌前端组件 | OpenMAIC `components/whiteboard/`、`components/roundtable/` | **按需移植** | React 19 组件，与 07 基座技术栈兼容 |
| 前端骨架 | 07 学生 14 屏高保真基座 | **自建**（v1 定案 2026-09-27：**零构建单页应用** `web/index.html`，由原型工程化 + 网关 API 对接 + 演示数据回退；Next.js 迁移推迟到出现 SSR/多端需求时——推翻原 ⚑，理由：Python 单栈零工具链、原型已验证设计、M1 优先级在内核不在前端） | DeepTutor web 的 a11y/visual 测试写法可参考 |
| 多用户存储 | DeepTutor 默认 SQLite / PocketBase（明示 single-user only） | **不用，自建 Postgres**（⚑） | 06 ER 31 实体为数据字典；DeepTutor 存储协议薄，适配层工作量可控 |
| 主动学习节奏（晨间简报） | 已有设计（03 §3.1，源自 Hyperknow 理念） | **自建** | 复习队列排序接入 FSRS `forgetting_risk`（DeepTutor 引擎输出） |

**License 合规**：`vendor/` 目录随仓库保留 Apache-2.0 LICENSE + THIRD_PARTY_NOTICES；移植的 MIT 资产在文件头保留版权声明；发布物附 OPEN_SOURCE_NOTICES.md。全线允许商用。

---

## 3. 系统架构（v1 组件与数据流）

### 3.1 分层

```text
┌ 前端：07 学生 14 屏基座（Next.js）＋白板/圆桌移植件
├ API 网关：app/gateway（已有）→ 统一 WebSocket + REST
├ 编排环：app/orchestration
│   trigger(TriggerEngine 规则族) → policy(POMDP-lite) → session(5E 状态机) → 派生器(§6)
├ 内核：app/core（已有，硬约束所在地）
│   schema(02) · actions(信封) · rules(R-01..R-10) · machines(提示阶梯/5E)
├ 能力层：
│   学习引擎(学习目标/掌握度/复习队列) ← vendor: deeptutor.learning
│   记忆服务(L1/L2/L3+巩固器) · 出题/判分 · 模式库(生成) · 学科策略库(§4)
│   派生模板注册表 · 验证器(learning/verifier.py，已有)
├ vendor 层：vendor/deeptutor/…（单向依赖，禁上浮）
└ 存储：Postgres(06 ER 31 实体) + append-only 事件流 + 对象存储(artifact)
```

### 3.2 现有代码盘点（2026-09-26，platform/ ~3.4k 行 + 8 个测试文件）

| 模块 | 现状 | 对应设计 |
|---|---|---|
| `core/schema.py, actions.py, rules.py, machines.py` | 已实现 | 02 Schema、03 信封与 R-01…R-09、提示阶梯/5E 状态机 |
| `orchestration/trigger.py, policy.py, session.py` | 已实现 | 03 §3.1 介入门、策略层、会话编排 |
| `learning/perception.py, tracer.py, verifier.py` | 已实现 | 过程感知、事件追踪、验证器 |
| `agent/{loop,tools,gates,workspace}.py` + `cli.py` | 已实现（三关口 REPL） | v2 式智能体循环，工具层门禁接 R 规则 |
| `evolution/l0_digest.py` | 雏形 | 04 Stage1-2 的手动版前驱 |
| `tests/`（rules/ladder/schema/learning/agent/e2e…） | 已实现 | 03 §6 验收测试的雏形 |

**结论：01 §8 的 M0 已基本落地。** 本方案的增量从 M1 起算。

### 3.3 请求生命周期的关键改造点

1. 编排环第 3 步（选动作）增加 `scaffold_type` 维度选择（§5.1）。
2. 编排环第 5 步（执行）接派生器：按 §6 决策流水线实例化，产物过对抗委员会后才能进入下一步动作提案。
3. 学习引擎以适配器接入：`app/learning/` 新增 `mastery_port.py`，把我们 Verifier 的 Verdict 流翻译为引擎的 evidence 事件；复习队列输出供晨间简报消费。
4. 存储层从单文件/内存切 Postgres：先迁 `InteractionEvent`（append-only 语义不变）与学习者模型表，题库/artifact 随后。

---

## 4. 教育思想落点（调研报告 → 机制 → 验收）

报告条目编号对应《教育智能体应用调研情况汇报》。每行第三列是**本方案的验收测试**——不可测的机制不进 v1。

| 报告思想 | v1 机制落点 | 验收测试 |
|---|---|---|
| 六类支架（概念/流程/策略/元认知/认识论/协作，§四） | 提示族动作新增 `scaffold_type` 参数（§5.1），POMDP 按"触发状态→支架类型"映射表选择 | 构造"知道目标但不知如何入手"状态 → 断言选中 strategic 且从 L0 起步；构造结论缺证据 → 断言 epistemic |
| 学段差异（§二） | `StageProfile` 软参数覆盖档：阶梯起始档/放行速度、支架类型权重、R-07 预算函数形状、语言负荷上限、WAIT 上限；不新增机制，只是软参数预置组合 | 小学档会话断言：句长上限生效、participation 支架可用；高中档断言：主动提示预算显著低于小学档（R-07 单调性跨档检验） |
| Pólya 四阶段 + 学科认知链（案例 A/G） | 新增**学科策略库**：数学=Pólya 四阶段、物理=情境→表征→原理→建模→计算→检验、科学探究=提问→假设→变量→实验→证据→解释；`TASK.params.pattern` 引用，节点挂 KG（R-06） | 数学任务会话断言四阶段状态位依序推进；Explore 内策略链节点 `grounded_to_kg` 非空 |
| AMS/PMS 混合（案例 B）："不是所有提示都自适应" | TriggerEngine 事件族（自适应通道）+ 5E Evaluate 固定反思关卡（节点通道）双轨并存，写入 03 设计原则 | 任务结束断言必入反思关卡，即使过程无任何触发 |
| On-the-fly 三支架（案例 C） | 参与维持 `participation` 支架类型（小学档默认可用）；聚焦/问题化归入 conceptual/epistemic 的话术库 | 注意游离状态断言触发 participation 轻推 |
| 三类干预触发（§五） | TriggerEngine 规则族三分：事件触发（已有）/ **节点触发**（任务开始、关键阶段、任务完成、单元结束）/ **求助协议**（先澄清具体困难，再从 L0 起阶梯） | 求助场景断言：首个响应必为澄清问句，不得直接 L2+ |
| Teachable Agent（案例 D） | 派生类型⑥ 可教智能体（§6）：从 KG 误区节点注入认知局限，学生教它 | 断言：误区注入与 KG 节点一致；R-09 考试模式下⑥型禁用 |
| 探究全过程支持但不替代（案例 F） | 新增硬约束 **R-10 过程所有权**（§5.2） | 无学生设计产物时，产出完整实验方案被 R-10 拒绝且留审计 |
| 策略族识别（案例 G） | 诊断器两步：先识别合法策略族（如 mole ratio/比例推理），再在策略内诊断错误；错误四分类沿用 DeepTutor 枚举 | 构造非标准但正确的比例推理解 → 断言不被标错、被识别策略族 |
| EPMAS 多智能体分工（案例 H） | 派生类型①的模板库新增 Scientist/Engineer/Math/Reflection 四职能实例，PBL 按阶段取用；结论须过对抗委员会 | PBL 会话断言四职能可被独立派生且产物带谱系 |
| 模拟嵌入式支架三段协议（跨学段补充） | interactive_sim 模式标准包装：运行前预测产物 → 运行中变量操作事件喂 TriggerEngine → 运行后证据→结论关卡 | 无预测产物断言不能启动模拟；运行后断言进入证据关卡 |

---

## 5. 动作语法与硬约束增量（对 03 的 v0.4 修订）

### 5.1 支架类型维度

```jsonc
// 提示族动作 params 扩展
"scaffold_type": "conceptual | procedural | strategic | metacognitive | epistemic | collaborative | participation",
// 强度轴不变：hint_level L0-L3（03 §4.1）；两轴正交，选择进 POMDP，门控在 ActionGovernor
```

映射表"触发状态 → 支架类型"直接采用调研报告 §四 的表（六类 × 典型触发状态 × 示例话术），汉化后入库为提示词资产。

### 5.2 R-10 过程所有权（探究保护）

```text
allow(EXPLAIN|ENV_OP 产出完整实验设计/工程方案)
  ⟺ ∃student_design_artifact(部分) ∧ 教学策略允许 ∧ ¬R-09 考试模式
```

形式复用 R-01 门控结构；"完整方案"判定器 = 变量/材料/程序三要素齐全。R-01…R-09 语义不变。

### 5.3 验收测试新增（并入 03 §6）

- [ ] `scaffold_type` 选择矩阵：5 种触发状态 → 断言对应支架类型
- [ ] R-10：无产物拒绝 / 有部分产物放行 / 考试模式一律拒绝
- [ ] 求助协议：首响应为澄清问句
- [ ] 策略族：非标准正确解不标错
- [ ] StageProfile：小学档语言负荷、高中档提示预算单调性
- [ ] 反思关卡：任务结束必入，无触发也入
- [ ] 可教智能体：R-09 下禁用
- [ ] interactive_sim：无预测产物不启动

---

## 6. 派生机制 v1 范围（01 §5.2 五型 → 六型）

**核心公式不变**：派生 = 模板实例化 + 上下文注入 + 生命周期契约；智能体无状态（P2）。

### 6.1 v1 实现取舍

| 类型 | v1 处置 | 说明 |
|---|---|---|
| ① 需求触发式角色派生 | **实现** | 模板库含：提示者/怀疑者/评审者 + EPMAS 四职能（Scientist/Engineer/Math/Reflection） |
| ② 数字孪生派生 | **降级保留** | 仅用于核心环 MENTIS 高风险动作演练（这是教学功能）；`trust_weight` 钉 0，**不作为任何晋升证据**；校准制度 v2 启动 |
| ③ 技能结晶派生 | **手动版** | SkillLibrary 状态机真实运行（candidate→active 过门禁），但结晶由人工策展：复用 DeepTutor EduHub 技能格式，"市场开放、门禁不开放" |
| ④ 对抗委员会派生 | **实现** | 关键输出旁的验证者/提问者/评价者 |
| ⑤ 圆桌研讨派生 | **实现** | OpenMAIC roundtable 为蓝本；立场由模板注入，结论必过④ |
| ⑥ 可教智能体派生（新增） | **实现（M1）** | 模板 `misconception_source` 挂 KG 误区节点；产物契约 `TeachReport`（被纠正误区数/解释质量/新暴露误区）；对接北极星：教 = 认知外化的最高阶证据 |

### 6.2 决策流水线与纪律

```text
触发源四类 ─→ 派生提案 ─→ 预算核算 ─→ ActionGovernor 门 ─→ 实例化注入 ─→ ACTIVE ─→ REPORTED ─→ TERMINATED
(TriggerEngine规则/        (会话派生预算,     (R-01..R-10 照常,   (模板五要素+      (产物契约)   (谱系入L6审计)
 编排器侦测/5E节点/          带外代价计入)      拒绝留痕)          上下文切片)
 学生请求)
```

四条纪律：**①派生体无特权通道**——一切动作照走信封与 R 规则，错误示范走白名单标记 `EXPLAIN(simulated_error)`，R-09 考试模式下⑥⑤自动禁用；**②结论必过④才见学生**，多源结论带分歧标注；**③预算谱系全程记账**，学生拒绝介入回流金标数据集；**④上下文最小注入**——切片非全量（R-08），②⑥强制第一人称观测过滤器（04 §5）。

### 6.3 模板 Schema（01 §5.3 五要素 + 三个扩展字段）

```yaml
agent_template:
  system_prompt: ...
  tools: [...]                 # 最小授权
  knowledge_slice: kg_subgraph_ref
  budget: {max_turns: 6, max_tokens: 8000}
  lifecycle: {exit_condition: ..., report_contract: ...}
  # ---- v0.4 新增 ----
  stage_overlay: stage_profile_ref      # 学段配置档覆盖
  misconception_source: kg_misconception_nodes   # ⑥型专用
  scaffold_vocab: [strategic, metacognitive]     # 可用支架类型白名单
```

委派协议采纳 OpenMAIC 证据先行规则：派生前强制 `read_scene`（读当前场景与相关状态切片），委派调用必须携带证据引用。

---

## 7. 记忆与学习者模型 v1

1. **三层落地**：L1 = InteractionEvent（04 §1，append-only）；L2 = 按"学习场景"聚合的可再生派生视图（标注 derived、可重建，故不视为事实源）；L3 = 画像/偏好/近况（对齐 02 快照+策略档案）。**L3 禁止引用 L3 为硬规则**（进 rules.py 与验收测试）。
2. **全局学习者模型**：推翻 DeepTutor"每条 mastery path 一份进度"的分片法，按融合裁决（08）改为**按人累积、按知识点索引**：`LearnerProfile(kc_id → mastery, error_records, repetition_state, evidence)`。kc 主键对齐 06 ER。
3. **巩固器**：DeepTutor consolidator 模式（LLM 分块 update/audit/dedup + 防篡改）移植，产物入 L2/L3，全部带 derived 标注与源事件回链（记忆图谱钻取）。
4. **召回邮票化**：近活动召回只给标题/时间戳 + 预计算 days_ago（防模型日期算术错误），照搬 DeepTutor 细节。
5. 晨间简报内容源 = 截止临近度 × FSRS forgetting_risk × 契约进度，排序依据可钻取到 L1 原始事件。

---

## 8. 可验证性框架（三条证据线）

### 8.1 机制验收（测试内）
§4 表第三列 + §5.3 清单，全部落 `tests/`，CI 强制。原则：**不可测的机制不进 v1**。

### 8.2 系统回归（版本间）
- **冻结场景回归集**：教学场景"考卷"（首发 30 个场景：×7 硬约束 × 支架类型 × 学段），运行器移植自 OpenMAIC eval/（LLM-as-judge + rubric）。回归集**只可由治理委员会换版**，与一切自动流程隔离——防系统"刷卷"。
- **红队越界套件**：诱导给答案/越界的对抗性学生模拟（可先用模板化提示攻击，v2 换孪生红队），R-01…R-10 拦截率必须 100%。
- 现有 678 行测试继续作为内核回归层。

### 8.3 效果证据（基线面板，v1 就开始记）
即使 v1 不进化，也必须从第一天积累**对照基线**，否则 v2 无从证明"变好"：
- autonomy_index 轨迹（按学习者，02 §2 复合值）
- mastery_delta（后测校准，GainLabel 契约 04 §3，互动指标永不入内）
- taxonomy_lift（Verifier Bloom 打标聚合）
- 教师否决率 / 学生拒绝-忽略介入率趋势（金标与事件流免费产出）
- 总验收：任意真实会话，从事件流可复现"感知→固化→选动作→裁决→执行→记录"全链路（03 §5 + 04 §10 口径）。

---

## 9. RSI 就绪契约（v1 建 / 不建清单与点火条件）

### 9.1 v1 建立（多数是产品功能，兼作 RSI 地基）

| 组件 | v1 形态 | 04 接口位 |
|---|---|---|
| 事件流 + consent_scope | append-only，全量落 Postgres；**v1 不记的数据 v2 永远找不回，此项保真度不可妥协** | §1 InteractionEvent |
| Verifier | 已有（learning/verifier.py），补 rubric/Bloom 字段 | §2 |
| 版本化配置 + 审计 | 软参数/模板/技能全部 semver + 变更留痕 | 各接口 lineage 字段 |
| 晋升门（人驱动） | 月度看板评审 → 人工改软参数/模板 → 仍走门禁七条检查 + 灰度 + 自动回滚；**"模型只能提案、harness 裁决"中提案席 v1 坐着人** | §6 PromotionGate |
| 金标通道快路径 | 教师纠正直接覆盖呈现；进化路径只是"多写一个数据集"，一并落盘 | §8 |
| 硬约束注册表 | 最小集 R-01/03/04/10 + 哈希断言（M0 已含前三） | §9 |
| SkillLibrary（手动） | 状态机与门禁真实运行，结晶人工策展 | §4 |

### 9.2 v1 明确不做（防蔓延清单）

| 组件 | 恢复时机 | 预留接口 |
|---|---|---|
| EvolutionJob 夜间自动进化（Stage 1–7） | v2 | §7 全部 Stage 契约已定 |
| 自动技能结晶 / DSPy 提示优化 | v2 / v3 | StrategyCandidate 契约已定 |
| 孪生驱动的策略优化 | v2（校准达标后） | CalibrationReport.trust_weight 已定义，v1 钉 0 |
| 模型级 LoRA（01 §8 M5） | 无限期搁置 | — |

**防重写保证**：v2 点火 = 按 04 既有接口填实现（EvolutionJob 七阶段、校准制度、自动结晶），不新增接口、不动内核。验收口径就是 04 §10 跨接口回放。

### 9.3 v2 点火条件（量化，全部满足才开工）

1. consent_scope ⊇ evolution 的活跃学习者 ≥ 200 人，持续 ≥ 8 周；
2. 冻结回归集 ≥ 100 场景且覆盖全部 R 规则与全部支架类型；
3. 孪生校准报告 `response_distribution_distance` 达标（阈值 v2 前定）且 `trust_weight` 解锁；
4. 金标数据集 ≥ 500 条教师纠正；
5. 北极星基线面板连续 4 周稳定产出。

### 9.4 里程碑重排（替代 01 §8）

| 里程碑 | v1/v2 | 内容 | 验收 |
|---|---|---|---|
| M0 MVP | **v1（已基本完成）** | 内核四件套 + 编排环 + 学习三件套 + agent CLI + 单学科题库 | e2e 已通；补 §5.3 增量测试 |
| M1 引擎融合 + 教学机制版 | v1 | §2 抽取/移植全部条目 + §4 全部机制 + R-10 + 派生①④⑤⑥ + 记忆 L1/L2/L3 + Postgres。**✅ 第一刀已落地（2026-09-27）**：R-10 过程所有权入注册表与裁决链、scaffold_type 支架轴（7 类枚举+选择矩阵+HINT 信封）、学习路径推荐 v1（`app/learning/path.py`，PathRecommendation@1）、成长证据 v1（`app/learning/evidence.py`，EvidenceReport@1）、网关 3 端点（`/api/path/recommend`、`/api/path/accept`、`/api/evidence/{id}`）、官方前端 `web/index.html` 上线；`tests/test_m1.py` 8 项验收全绿，全库 61 测试通过。**余**：DeepTutor 引擎抽取、派生①④⑤⑥、记忆 L1/L2/L3 巩固器、Postgres 迁移、冻结回归集 | §4/§5.3 验收全绿；冻结回归集 30 场景全过；红队拦截 100% |
| M2 孪生（演练级） | v1 | ②降级实现 + 校准报告雏形（不进晋升） | MENTIS 演练可用；trust_weight=0 断言 |
| M3 内容外环（手动） | v1 | 知识库周更 + 看板 + 人工参数晋升 | 首次人工晋升走完全部门禁 |
| M4 师伴与治理 | v1 | 教师驾驶舱 + 金标快路径 + 家长端（P6 聚合呈现） | 教师纠正入数据集 |
| M5 进化环点火 | **v2** | EvolutionJob + 自动结晶 + 孪生优化 + 校准制度 | 9.3 五条件满足后开工；首个策略版本经全门禁晋升 |
| M6 权重级 | 搁置 | LoRA | 治理审批 |

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| DeepTutor 抽取比预期深（learning 对其 services/storage 有隐性依赖） | 先写适配器测试再搬运（Port & Adapter）；发现纠缠过深则只抄算法重实现——learning 引擎总量约数千行，重实现成本可控；vendor 隔离由 import-linter 强制 |
| Postgres 迁移工作量 | 06 ER 已就绪；先迁事件流与学习者模型两域，其余随 M1 摊 |
| 冻结回归集编制贵（场景 + rubric 设计） | 30 场景起步，按 R 规则 × 支架 × 学段正交抽样；LLM-as-judge 抽样人审 10% |
| 提示词资产移植后的漂移（OpenMAIC 英文提示词汉化/改写失效） | 每份移植资产配"行为快照测试"（固定输入断言关键输出特征），入回归集 |
| 单学科内容深度不足 | 首发 ⚑ 初中数学（platform 题库雏形已在此），学段档默认 初中；StageProfile 机制保证横向扩科只是配置工作 |
| 依托的两上游快速演进（DeepTutor v1.6.11、OpenMAIC v1.1.0，周更） | vendor 快照锁版本；升级走 PR 评审不自动跟 |

---

## 11. 总验收清单

- [ ] vendor/ 单向依赖检查通过（import-linter）
- [ ] OPEN_SOURCE_NOTICES.md 完整（Apache-2.0 + MIT）
- [ ] §4 教育机制验收全绿（每行一条测试）
- [ ] §5.3 新增验收测试全绿
- [ ] L3 禁止引用 L3 有测试
- [ ] 派生体动作 100% 过规则引擎（审计抽样验证）
- [ ] R-09 模式下派生⑤⑥禁用有测试
- [ ] 冻结回归集 30 场景 + 红队套件全过
- [ ] 事件流全链路可回放（04 §10 口径）
- [ ] 北极星基线面板开始产出数据
- [ ] 晋升门人工流程演练一次（含灰度与回滚）
- [ ] 9.2"不做清单"未被实现蔓延（代码评审检查项）
