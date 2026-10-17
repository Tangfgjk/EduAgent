# 概率多轮追问开发评测

时间：2026-10-17T19:45:29+08:00；模式：live；提示：probability.followup.v2。

**自编开发样本，无教师金标；Fake 仅验证工程流程，不能证明真实模型理解、追问质量或教学效果。**

- 6 组对话、18 轮；两道开发练习。语料：probability.multiturn.eval.v1。
- 参考标签不发送模型；每组最多 4 次提示，历史只取已完成交流。失败仍占分母和额度。
- 与单轮脚本共用固定隔离评测账号、数据库及调用账本；每日限额模式见运行清单，恢复不重复已执行调用。

| 指标 | 结果 |
| --- | --- |
| 预约 / 执行成功 | 18 / 18 |
| 引用 / 方向权限检查通过 | 18 / 18 |
| 开发参考方向匹配 | 15 / 18 |
| 相同本轮不同历史对照 | 未通过 |
| 出站虚构身份标记全部移除 | True |

| 样本 | 上下文轮数 | 状态 | 参考方向 | 返回方向 |
| --- | --- | --- | --- | --- |
| M01-1 | 1 | succeeded | equal_likelihood, sample_space | equal_likelihood |
| M01-2 | 2 | succeeded | favorable | favorable |
| M01-3 | 3 | succeeded | ratio | ratio |
| M01-4 | 4 | succeeded | equal_likelihood | equal_likelihood |
| M02-1 | 1 | succeeded | clarify | clarify |
| M02-2 | 2 | succeeded | equal_likelihood | equal_likelihood |
| M02-3 | 3 | succeeded | sample_space | sample_space |
| M02-4 | 4 | succeeded | favorable | favorable |
| M03-1 | 1 | succeeded | sample_space | sample_space |
| M03-2 | 2 | succeeded | sample_space | clarify |
| M04-1 | 1 | succeeded | ratio | ratio |
| M04-2 | 2 | succeeded | ratio | clarify |
| M05-1 | 1 | succeeded | equal_likelihood | equal_likelihood |
| M05-2 | 2 | succeeded | clarify, equal_likelihood | clarify |
| M05-3 | 3 | succeeded | ratio | ratio |
| M06-1 | 1 | succeeded | equal_likelihood | equal_likelihood |
| M06-2 | 2 | succeeded | clarify | clarify |
| M06-3 | 3 | succeeded | ratio | favorable |

## 待复核样本

### M03-2

- 自编输入：对，我想继续弄清刚才这一点。
- 开发依据：省略内容应结合上一轮基本结果问题，引用仍只能来自本轮。
- 返回引用：对，我想继续弄清刚才这一点。
- 返回追问：你现在卡在哪一步？可以先说说你怎样理解题目中的条件。；错误码：None。

### M04-2

- 自编输入：对，我想继续弄清刚才这一点。
- 开发依据：与M03相同本轮、题目、提示等级，应由不同前文确定方向。
- 返回引用：对，我想继续弄清刚才这一点。
- 返回追问：你现在卡在哪一步？可以先说说你怎样理解题目中的条件。；错误码：None。

### M06-3

- 自编输入：回到题目，总数五个，蓝球两个，我想问哪个数放在概率分数的分子位置？
- 开发依据：回题后依据最新比例问题追问。
- 返回引用：我想问哪个数放在概率分数的分子位置？
- 返回追问：你认为哪些基本结果符合题目要求？你是怎样识别它们的？；错误码：None。


Fake 固定选择 clarify，成对上下文辨别预期不通过；不能据 Fake 结果宣称多轮语义验收通过。真实模型须另行运行此冻结语料，保留结果和失败样本。
