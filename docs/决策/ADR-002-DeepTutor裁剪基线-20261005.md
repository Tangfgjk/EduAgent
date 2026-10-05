# ADR-002：DeepTutor 裁剪基线

日期：2026-10-05  
状态：已接受

## 背景

桂子问津需要借鉴 DeepTutor 的 Learning、Memory、RAG 和 Provider 工程实现，但不能把 DeepTutor 的产品壳、Web、Agent Runtime 或学习者状态模型整体引入。为了让后续裁剪可复现，必须锁定远端来源、提交版本和抽取边界。

## 决策

1. 本地参考仓库固定保存于 `../DeepTutor`，远端为 `https://github.com/HKUDS/DeepTutor.git`。
2. GitHub 当前观察到的 `main` 基线提交为 `f07029cfcf2c8dfccdb671cdfc343db8334f5741`。由于本机 GitHub TLS 通道不稳定，本地完整工作树通过 `https://gitee.com/mirrors/DeepTutor.git` 获取，当前本地提交为 `897fce52f24bf22e6e50d8a3e4df532632a26322`。后续裁剪必须从已记录的提交开始，不直接跟随未记录的 `main` 变化。
3. Phase 1 只研究并裁剪 Learning 相关能力：Evidence 适配、掌握门控、保持度/复习调度和必要的测试/数据结构。
4. 暂不抽取 DeepTutor 的产品 Web、Agent Runtime、会话记忆、按路径隔离的学习者状态和供应商绑定逻辑。
5. 所有被抽取能力必须通过桂子问津自己的端口、Schema、ActionGovernor 和事件审计链；DeepTutor 代码不能成为第二个控制器。
6. 外部代码进入主仓库前，先建立 Adapter Test，再进行最小代码移植，并保留来源文件、提交 SHA、许可证和本地改动说明。

## 版本与许可证核查

- 版本类型：Git 提交基线，而不是浮动分支版本。
- GitHub 提交 SHA：`f07029cfcf2c8dfccdb671cdfc343db8334f5741`。
- 本地工作树提交 SHA：`897fce52f24bf22e6e50d8a3e4df532632a26322`。
- 本地镜像远端：`https://gitee.com/mirrors/DeepTutor.git`；原始远端：`https://github.com/HKUDS/DeepTutor.git`。
- 许可证：裁剪前必须在本地仓库根目录和拟抽取模块目录核对许可证文本及版权声明；未完成核查前不得复制代码进入主仓库。
- 升级流程：新提交先在 `DeepTutor` 本地参考仓库中审计，再更新本 ADR，最后通过适配器测试和回归测试。

## 影响

- 桂子问津的学习状态所有权仍属于 Learner Model 与 Storage，Agent Runtime 只通过端口读写。
- DeepTutor 的分类和算法是可替换实现参考，不成为桂子问津的核心知识本体。
- 保持度接口使用通用的 `stability`、`retrievability`、`lapse_count` 和 `next_review_at` 语义，不在架构层绑定某个具体调度算法。
