# 04 业务代码

### [WENJIN-UX-002] 学生端发送错路由与工作空间初始化中断

---
module: EduAgent-frontend
category: 04
tags: [frontend, routing, workspace, regression]
difficulty: 3
status: 已解决
date: 2026-10-09
keywords: [发送并验证, null onclick, 输入区遮挡]
---

#### 问题现象

题干为 2x + 1 = 7，聊天却使用 3x + 5 = 14；“发送并验证”不产生题目验证结果。临时会话与规划按钮初始化失败，更多操作覆盖输入框。

#### 定位过程

真实页面逐项点击，浏览器记录 features.js 的 Cannot set properties of null (setting onclick)。独立内存库运行 scripts/study_usage_repro.cjs，稳定复现未支持目标仍显示旧题、作答选项遮挡输入框。检查发现发送按钮绑定自由会话接口，页面不存在 new-temporary-context 和 sidebar-contexts 元素。

#### 根本原因

展示任务与自由会话共用输入框但调用不同协议；新增脚本假设不存在的 DOM 元素一定存在。刷新概览还调用 setProject，意外清除临时上下文。

#### 解决方案

主发送操作路由到当前题目验证，临时消息走独立接口；换题清空视图并重置提示状态。可选 DOM 元素显式检查；概览刷新不改变学习路线。更多操作采用正常文档流；成长与待办卡片仅在对应页面展示。题号改为题型及序号，保留真实 ID 用于请求。

#### 验证方法

node scripts/study_usage_repro.cjs：4 项陪学回归及项目创建、ToDo、项目证据、复习预览、离线圆桌、候选审核/回滚、临时消息隔离、设置全部通过。测试使用 scripts.browser_fixture 内存数据库及 FakeLLM，不代表真实模型质量或全量测试通过。JavaScript 语法检查通过。

### [WENJIN-GOAL-001] 自由目标与课程目录脱节导致错误路径

---
module: EduAgent
category: 04
tags: [goal, curriculum, recommendation, frontend]
difficulty: 3
status: 已解决
date: 2026-10-08
keywords: [unsupported goal, 路径建议, 洛必达法则]
---

#### 问题现象

- 学习者输入“我想学习洛必达法则”，页面显示目标已建立；刷新路径后却出现等式和一元一次方程，初始计划草案也是方程内容。

#### 定位过程

- 用隔离 `TestClient` 保存同样的目标，`GET /api/path/recommend` 原先返回 200 和方程节点；两个针对性断言稳定失败。
- 核对 `POST /api/contracts`：原实现接受任意目标文字，却固定写入方程成功标准与 `bank_scope`。
- 核对推荐路由与目录：推荐器只遍历当前课程知识点与先修图，没有读取最新目标；默认课程目录只包含等式／一元一次方程。
- 排除浏览器缓存：原始 API 响应就是方程路径；修复后在真实 8001 服务的已保存目标上返回 422。

#### 根本原因

- 自由文本目标没有与已导入课程范围建立契约。目标保存、计划初始化与路径推荐彼此脱节，导致系统把未支持的主题错误地包装成可执行的方程学习计划。

#### 解决方案

- `app/learning/goal_scope.py` 对本地演示课程做保守的目标范围检查，并给出当前可用主题。
- `app/gateway/routes.py` 在新建目标、刷新路径、采纳路径和签署带课程范围的旧草案时阻止不匹配操作；`app/gateway/learning_routes.py` 将已有目标的支持状态返回给工作台。
- 工作台对旧目标显示“当前课程不支持”，清除旧路径展示并禁用不匹配草案的签署，保留拒绝旧草案的能力；原有目标和数据库记录不删除。
- 真正支持洛必达法则需要新增并审核其知识点、先修、题目、提示和评测资产，不能靠改目标文本或模型临时生成冒充课程。

#### 验证方法

- `python -m pytest -q tests/test_integrated_learning.py tests/test_gateway.py tests/test_learning_api_v2.py tests/test_workbench_v4.py`：39 passed。
- 全量测试：796 passed、33 skipped、1 failed；唯一失败为此前已知的 V3 冻结合成课程包哈希不一致，与本次目标检查无关。
- 重启本地 8001 后，已保存目标仍为“我想学习洛必达法则”；workspace 返回 `goal_supported=false`，路径接口返回 422。新浏览器页面显示不支持提示、无方程路径、旧草案签署按钮禁用。
