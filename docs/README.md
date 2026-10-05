# 文档中心

本文档目录是项目唯一的文档入口。设计、需求、进度、研究材料和工程指南按用途分层，文件名统一采用 `主题-YYYYMMDD.md` 或 `序号-主题-YYYYMMDD.md`。

## 阅读路线

| 顺序 | 入口 | 目的 |
|---|---|---|
| 1 | [项目总览与开发路线](overview/project-overview-roadmap-20261005.md) | 理解产品定位、当前基线和后续路线 |
| 2 | [项目交接文档](overview/project-handover-20260928.md) | 掌握运行方法、设计锚点和风险 |
| 3 | [阶段一需求](requirements/phase-1-learning-integration-20261005.md) | 开始当前阶段的开发 |
| 4 | [架构设计](architecture/01-overall-architecture-20260916.md) | 理解系统分层和教育机制 |
| 5 | [实施方案](architecture/10-v1-implementation-plan-20260926.md) | 理解 v1 的取舍和验收 |
| 6 | [平台开发指南](guides/platform-development-guide-20260920.md) | 运行平台和测试 |

## 目录职责

- `overview/`：项目级总览、交接、基线说明。
- `architecture/`：长期有效的架构与领域规格；修改必须同步代码和测试。
- `requirements/`：可执行需求、验收条件和范围边界。
- `progress/`：里程碑、完成度、风险和下一步。
- `decisions/`：需要长期保留的架构决策及其理由。
- `guides/`：运行、开发、部署和新成员指南。
- `research/`：原始研究资料、外部参考和实验设计；原始资料不直接作为实现规范。
- `testing/`：测试策略、冻结回归场景和质量门槛。
- `logs/`：按日期记录开发日志；运行时数据库和本地日志不提交到 Git。

## 命名规范

- Markdown 文档：`主题-YYYYMMDD.md`；有稳定顺序的规格使用 `NN-主题-YYYYMMDD.md`。
- 研究资料：`主题-来源或用途-YYYYMMDD.扩展名`。
- 代码包使用 Python 标准小写下划线命名；测试文件使用 `test_主题.py`。
- 时间后缀表示文档版本或归档日期，不代表内容自动最新；最新状态以 `overview/` 和 `progress/` 为准。
