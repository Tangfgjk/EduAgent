# G07 受限执行策略与 Skill 编译

日期：2026-10-18。API 0.17.0，数据库仍为 `g05_002`。用户已确认本轮四类可调范围；仍为模拟/成人开发阶段。

## 1. 范围和设计

沿用既有 StrategyVersion、ActiveStrategy、StrategyReceipt、StrategyActivation 和双重审核，不建立第二套竞争的策略版本库。新增 Compiler/Runtime 作为统一读取位置，版本化编译产物冻结在原策略 bundle 内；新任务在不可变开始输入中冻结运行快照。无需数据库迁移。

课程内容仍由 G03 的生成/核验/审核体系管理。G07 不重新生成题目，不把 Markdown 当执行脚本；本轮课程调整限已有练习任务的排序，后续更大课程结构变化仍需新契约与审核。

## 2. 明确白名单

旧 `daily_minutes(5–60整数)`、`reflection_focus(basis|verification)` 保留；可选 execution：

```json
{
  "schema_version": "teaching-policy.v1",
  "practice_order": "original | errors_first",
  "review_order": "due_first | due_errors_first",
  "support_mode": "step_first | adaptive",
  "question_focus": "basis | representation | verification"
}
```

以上字符串中的竖线表示可选枚举，不是合法提交值。未知字段、任意代码/Markdown、检测关闭、预算字段、删除/新增任务均拒绝。

| 参数 | 消费位置 | 实际行为和限制 |
|---|---|---|
| practice_order | tutor.task_order | 开始时查本人项目、同来源、同内容版本的数值错误事实；只稳定排序已有 practice，诊断/检测及题目内容不变。按过去错误来源，不声称当前不会；没有匹配错题时保持原顺序 |
| review_order | memory.cards | 全量排序后分页；到期数值错误来源卡优先，其余按到期时间。复做数值检查、间隔1/3/7天和修订号不变，提前复做不因此推进日程 |
| support_mode | tutor.support | 仅生成课程：step_first逐级帮助；adaptive沿用已有一次未提示数值通过后下一练习先自问、可再次求助恢复。静态旧课程不自动新增渐撤，诊断与检测不提供帮助 |
| question_focus | generic_interaction.focus | 在模型合法引用及方向校验后，本机选择该优先安全模板；模型需要clarify时保留澄清。应用于通用陪学/我教AI/圆桌，不改静态概率专用提示和数学验证 |

每日时长仍是学生自主建议，不是强制计时；反思重点继续用于后续陪学反思问题。

## 3. 编译、版本和可读文件

`compiler.compile_policy` 只接收强类型 policy，返回规范配置、policy_hash、编译器版本、消费者注册表、可读 Markdown 及 markdown_sha256。Markdown 包含同一个 policy_hash 与规范 JSON；自身哈希另记录，避免自指。

新四字段提案的 bundle 保存 skill_compilation、policy_base、policy_changes、course_baseline，以及 G06 已有本机学情摘要。策略总 content_hash 覆盖 bundle、policy 和硬规则。每次校验重编译并比较产物，不能只修改 Markdown 或 DSL 单边通过。

旧两字段版本缺少 compilation 时仍按旧方式验签、可读、确认及回滚，输出不会凭空补入 execution=None 改变旧配置形状。无需改变旧 LangGraph 流程版本或重新生成旧检查点。

## 4. 运行快照与失效

新陪学/活动开始时捕获 strategy_version_id、policy_hash、编译器、执行参数、单元/内容版本、practice_task_order 和错题来源索引（按题去重）。检查任务集合完全相同，诊断/检测不交换。该快照写入 LearningInput 开始事件；后续使用同一快照，不因激活新策略或回滚而改动在途任务。

复习页面是新一次列表读取，按当前生效版本排序；已打开的复做条目仍以其card_id/revision提交，不改变日程。

审批/确认继续校验证据、资料、基线与期限；新增课程绑定基线检查。课程在待审核期间变更则拒绝旧提案。策略引用资料停用、硬规则变更、账号来源变更等令后续新任务采用默认；历史指针和来源保留。课程本身的撤回门禁继续由教学服务检查。

审核角色使用已有启用管理员身份和真实回执；学生不能走管理员批准接口。这里只是开发审核，没有将普通管理员自动认定为具备专业教师资格。专业审核、资格与真实学生试点仍属G09。

## 5. 模型范围

本轮四项由学生选择作为受限调整请求，再经管理员和学生确认。模型规划仍只生成原有时长/反思两项；不能自行添加execution。四项保留在本机冻结元数据中，在合法两字段模型候选验证后合并，重新编译与生成差异。

通用追问的优先方向也在本机使用，不把运行快照、错题来源、策略 Markdown 或新增学情摘要发送模型。已有数据、tokens、调用次数和各用途门禁保持原授权。本轮验收只用Fake，没有新增真实服务调用。

## 6. 后续

本轮证明四个消费者确实读取批准策略，不证明调整带来教学收益。G08 才建设 Skill 候选产生、冻结/未见题族评测、拒绝/晋升和回滚；不启用自动真实 RSI。

更广的课程目标/任务增删、生成新复习题、量规、学习效果与自动策略字段建议尚待独立设计、验证及所需确认，不能因本轮接上四个枚举就宣称完整自进化。
