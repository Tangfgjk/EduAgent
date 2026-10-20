# 业务代码问题

## 2026-10-18：OpenMAIC SDK文本分片与SSE协议不兼容

状态：已解决；模块：resources/proxy；关键词：`unsupported_resource_capability`、SSE、text parts、AI SDK。

### 现象

上游SDK会发送文本数组并请求流式输出，初版代理只接受字符串并返回JSON。付费验收前的Mock回归返回422。

### 定位过程

检查固定上游的 `streamText` 使用位置，构造实际消息分片和 `stream=true` 请求；回归精确断言SSE、文本delta、结束标记及预算一次结算。

### 根因

“支持文本模型”没有覆盖SDK线上协议。数据内容同为文本，并不意味着请求格式和响应传输格式相同。

### 解决方案

仅规范化text分片，不接受图片/工具；对上游仍发一次受预算保护的非流式请求，结算与取消门禁之后包装SSE，无额外调用。没有声称它是逐token低延迟流式生成。

### 验证方法

`test_resource_sdk_text_parts_and_sse_keep_single_budgeted_call` 红/绿实测；SQLite/PostgreSQL通过，真实课堂8次请求全部用量已知，未触及上限。

## 2026-10-18：真实OpenMAIC课堂转换只有标题

状态：已解决；模块：resources/import；关键词：`content.canvas`、`canvas-text.v2`、重新导入、不可变资源。

### 现象

远端生成三个完整幻灯片场景，但平台初次资源只有三行标题。不能以任务成功或资源数量判断内容完整。

### 定位过程

比较同owner读取的真实课堂文档与本地候选，真实正文在 `content.canvas.elements`，旧白名单只读普通text/content字段。

### 根因

Mock导出样例没有覆盖真实上游canvas格式，生产转换器漏掉了嵌套结构与latex元素。

### 解决方案

新增只读canvas文字/LaTeX源码转换，丢弃脚本、样式、媒体与动作，拒绝空正文。旧不可变标题产物退回；对已知课堂提供无模型重新导入，按哈希去重，保留同一远端任务和关闭预算；审核后任务同步completed。

### 验证方法

真实格式Mock验证脚本不执行、正文/公式不丢、重复导入不重复资源且调用数不变；实际同课堂重导入后仍为8次，正文审核与学生桌面/手机空sandbox预览通过。公式当前为纯文本，不宣称还原原版排版。

## 2026-10-18：动态活动 ID 超过旧发布字段长度

状态：已解决；模块：content/governance；关键词：`ContentApproval`、`VARCHAR(64)`、PostgreSQL、动态圆桌发布。

### 现象

SQLite 的主题课程生成与审核成功，但 PostgreSQL 在发布动态圆桌时返回通用503。

### 定位过程

以相同自编课程经过真实管理员审核入口复现；SQLite/PostgreSQL对照。检查类型限制和动态 ID 实际长度，发现圆桌 ID65字符，旧发布表64字符；不是模型内容或权限分支失败。

### 根因

新内容/活动契约允许100字符，旧 `ContentApproval.unit_id` 仍为64。SQLite字符类型不强制限制长度，掩盖了 PostgreSQL 的真实约束。

### 解决方案

模型与迁移统一为100字符；保持旧 ID、内容版本不变，按前向迁移升级，不删旧审批记录。

### 验证方法

`tests/test_topic_generation.py` 的生成→审核→绑定→活动路径同时运行于 SQLite/PostgreSQL，确认新私有课程能发布、他人不能读取或绑定；静态发布流程继续回归。

## 2026-10-18：看过验证题后取消未被计入曝光

状态：已解决；模块：learning/evidence；关键词：`AssessmentExposure`、`seen_before`、取消、重复检测。

### 现象

学生进入活动验证、看到题目后直接取消，没有作答事实；再开活动使用同题时，旧逻辑仍可把结果记作新独立检测。

### 定位过程

构造“进入验证→取消→重开→同题验证”的真实 API 回归。只按已保存 `LearningFact` 查曝光时，最后断言 `independent == False` 失败；增加页面展示记录后，展示标签先修复，但验证提交仍查询旧事实入口，回归再次发现此遗漏。

### 根因

把“提交过答案”混同为“见过题目”，且展示和最终判定使用不同数据来源。只修显示标签不够。

### 解决方案

新增不可变 `assessment_exposures`，展示诊断/检测时追加；展示和可信服务端验证判定共用 `seen_before`，并兼容查询旧事实。前向迁移 `g05_002`；取消不删除曝光。

### 验证方法

`tests/test_generation_boundaries.py::test_cancel_after_presentation_still_marks_repeated_assessment` 修复前能失败，修复后 SQLite/PostgreSQL 均通过。平台外曝光无法自动识别，工程通过不等于测量等值性或学习效果得到验证。


## 2026-10-18：曝光元数据补入后，历史回填错误拒绝匹配

状态：已解决；模块：memory/backfill；关键词：`exposure_status`、`eligible=0`、确定性回放、派生视图。

### 现象

规划扩展回归中，旧运行的事实回填预期 eligible=1 却为0，在 SQLite/PostgreSQL 均失败。

### 定位过程

复用最小“启动→提交诊断→模拟旧策略视图→回填”的回归，比较保存视图与回放视图的不同字段。仅 task.exposure_status 存在 fresh/null 差异；题干、作答反馈和其余状态一致。

### 根因

G05 从数据库补入题目曝光元数据，纯图回放不生成该字段。回填使用整个展示视图等值比较，将派生状态当成了可确定回放的教学状态。

### 解决方案

回填对比较双方的深拷贝仅移除 task.exposure_status，保留其余完整核对；不改保存视图，不从回放生成不存在的 fresh 证据。缺失历史曝光仍归为未知。

### 验证方法

原 test_s09 旧策略回填双数据库回归通过。新增 tests/test_g06.py 的回填边界测试证明正常派生差异允许核对，但题干不同仍 incomplete，不插入猜测事实。相关81项回归通过。


## 2026-10-18：执行策略表单的精确标签与复选框布局

状态：已解决；模块：frontend/planner；关键词：`getByLabel`、`selectOption timeout`、`label.inline`、四项执行策略。

### 现象

展开新策略表单后，下拉框已显示，Playwright的精确标签选择仍超时；截图还显示复选框被表单输入宽度撑开、文字挤到右侧。

### 定位过程

核对失败的隔离页面快照，四个combobox均存在。对照既有反思重点字段，发现新字段没有独立aria-label；隐式label包含select选项文本，使标签精确匹配不稳定。截图确认通用表单input宽度规则同样作用到了checkbox。

### 根因

复用表单样式和隐式标签时，没有分别检查原生下拉框的精确名称以及checkbox与文本输入的尺寸差异。

### 解决方案

四个select增加明确aria-label；form-stack中的inline checkbox设置固定16px并禁止撑满。保留原语义label与键盘可操作性。

### 验证方法

桌面四参数→审核→确认→新任务冻结→回滚的原失败路径通过；390px手机展开选择、无横向溢出与实际截图复核通过。


## 2026-10-18：手机登录后立即打开导航被路由清理关闭

状态：已解决；模块：ui/Shell；关键词：`menu-open`、`useLayoutEffect`、登录后导航、手机侧栏。

### 现象

手机四参数表单此前可通过，收尾复测却在登录后的导航点击超时，尚未进入表单。

### 定位过程

只提取隔离trace最后操作与可见性日志：打开导航成功后，项目链接由不稳定变成不可见；页面停在首页。Shell在location.pathname变更后用普通effect关闭menu，存在用户先点击、延迟清理随后关闭的窗口。

### 根因

路由切换后的导航清理使用绘制后的effect，与立即打开导航的交互竞争。

### 解决方案

仅将Shell的路由侧栏清理与主区焦点移至useLayoutEffect，确保绘制前完成；保留其余effect及导航行为，不增加固定sleep或绕过真实点击。

### 验证方法

复用原手机登录→创建项目→展开四参数真实浏览器路径，并复测策略/复习桌面与手机场景。无测试专用导航后门。


## 2026-10-20：课堂异步审核的取消与待复核出口

状态：已解决；模块：resources/quality、generation、model_tasks；关键词：`checking`、`resource_cancelled`、第二次检查、晚回包。

### 现象

课堂检查要求修改或第二次检查额度不足时，学生可能一直看到checking。取消课堂时，关联机器检查还可能继续运行；需确保完成落库不能覆盖取消。

### 定位过程

对照OpenMAICRunner只扫描pending/running/import_pending及quality.accept的早退路径；以模拟HTTP和双数据库重放pass、revise、quota、timeout及学生/管理员取消后的late complete。

### 根因

资源审核结果与课堂任务状态不是同一对象，早退缺少任务终态转换；取消需要覆盖关联模型操作和结果提交栅栏，不能只更新一个页面状态。

### 解决方案

集中hold记录理由并将checking转pending_review；两端取消关联waiting/pending/running检查，保留在途未知计费；完成前重查任务取消；不撤销已开放旧资源，也不重新请求收费模型。

### 验证方法

`tests/test_private_classroom_release.py`在SQLite与隔离PostgreSQL覆盖六种结局；取消后late complete必须False，资源不能自动批准；失败检查必须显示待复核并保留原因。

## 2026-10-20：恢复与取消的行锁顺序不一致

状态：已解决；模块：runtime/model_tasks、resources/openmaic；关键词：`FOR UPDATE`、Run/Operation、Workspace/Job、恢复死锁。

### 现象

静态并发复查发现：恢复先锁Operation再修改Run，而完成先锁Run再锁Operation；课堂导入先锁Job再创建会话锁Workspace，取消顺序相反。并发时存在循环等待。

### 定位过程

沿恢复、提交、取消、导入的事务边界列出实际行锁顺序，不用单线程绿灯替代并发分析；检查_end隐式ORM flush对Run/Job的写锁。

### 根因

局部使用skip_locked不能消除不同入口之间的锁序反转；调用链中的后续写入也属于锁序。

### 解决方案

恢复先无锁搜候选，再经_locked以Account→Workspace→Run→Policy→Operation统一顺序锁定、复查租约；课堂导入先Workspace后Job；取消在Workspace内先停止关联操作，再锁Job并重读取消状态。

### 验证方法

模型恢复测试观察真实PG FOR UPDATE顺序，并跑原租约、取消、迟回包和课堂双数据库回归。生产负载/并发容量仍需单独压测。

## 2026-10-20：动态诊断与模型输入快照不同步

状态：已解决；模块：generation/service；关键词：`diagnostic_counts`、追加探测、`excluded_parameters`。

### 现象

追加第3/4道探测后，普通课程生成仍报告总数2且只排除初始题目，导致分母失真，可能浪费生成/修复额度。

### 定位过程

比较诊断持久记录、freeze_request发出的JSON和数学编译器排除参数。最终编译仍能拦截曝光复用，但模型输入不准确。

### 根因

新增自适应诊断后保留了固定两题的请求构造常量。

### 解决方案

使用实际attempts数量及diagnostic_parameters的完整曝光清单；有理数专用流程保留各自契约。

### 验证方法

`test_additional_diagnostic_is_in_model_denominator_and_excluded_parameters`经真实诊断接口累计三次观察，核对发送快照总数3及三组排除参数；SQLite/PostgreSQL同验。
