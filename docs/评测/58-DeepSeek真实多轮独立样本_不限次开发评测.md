# 概率多轮追问开发评测

时间：2026-10-17T19:46:30+08:00；模式：live；提示：probability.followup.v2。

**自编开发样本，无教师金标；Fake 仅验证工程流程，不能证明真实模型理解、追问质量或教学效果。**

- 4 组对话、10 轮；两道开发练习。语料：probability.multiturn.holdout.v1。
- 参考标签不发送模型；每组最多 4 次提示，历史只取已完成交流。失败仍占分母和额度。
- 与单轮脚本共用固定隔离评测账号、数据库及调用账本；每日限额模式见运行清单，恢复不重复已执行调用。

| 指标 | 结果 |
| --- | --- |
| 预约 / 执行成功 | 10 / 10 |
| 引用 / 方向权限检查通过 | 10 / 10 |
| 开发参考方向匹配 | 8 / 10 |
| 相同本轮不同历史对照 | 未通过 |
| 出站虚构身份标记全部移除 | True |

| 样本 | 上下文轮数 | 状态 | 参考方向 | 返回方向 |
| --- | --- | --- | --- | --- |
| M07-1 | 1 | succeeded | sample_space, equal_likelihood | sample_space |
| M07-2 | 2 | succeeded | favorable | favorable |
| M07-3 | 3 | succeeded | ratio | ratio |
| M08-1 | 1 | succeeded | equal_likelihood | equal_likelihood |
| M08-2 | 2 | succeeded | clarify, equal_likelihood | clarify |
| M08-3 | 3 | succeeded | ratio | ratio |
| M09-1 | 1 | succeeded | favorable | favorable |
| M09-2 | 2 | succeeded | favorable | clarify |
| M10-1 | 1 | succeeded | ratio | ratio |
| M10-2 | 2 | succeeded | ratio | clarify |

## 待复核样本

### M09-2

- 自编输入：我仍然想请你围绕上一个问题继续追问。
- 开发依据：由前文识别符合条件的结果。
- 返回引用：我仍然想请你围绕上一个问题继续追问。
- 返回追问：你现在卡在哪一步？可以先说说你怎样理解题目中的条件。；错误码：None。

### M10-2

- 自编输入：我仍然想请你围绕上一个问题继续追问。
- 开发依据：本轮与M09相同，前文不同，应选不同方向。
- 返回引用：我仍然想请你围绕上一个问题继续追问。
- 返回追问：你现在卡在哪一步？可以先说说你怎样理解题目中的条件。；错误码：None。


Fake 固定选择 clarify，成对上下文辨别预期不通过；不能据 Fake 结果宣称多轮语义验收通过。真实模型须另行运行此冻结语料，保留结果和失败样本。
