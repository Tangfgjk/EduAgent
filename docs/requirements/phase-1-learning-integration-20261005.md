# 阶段一 Learning 闭环需求

更新时间：2026-10-05

## 目标

把一次作答的 `Verdict` 转成可长期计算的 `LearningEvidence`，并让掌握门控、保持度调度和学习路径推荐共享同一条证据链。阶段一不做自动进化，不把 DeepTutor 的产品、Web、Agent Runtime 或 per-path 学习者状态整体搬入。

## 目标模块

```text
platform/app/learning/mastery_port.py
platform/app/learning/gates.py
platform/app/learning/review_port.py
```

### `mastery_port.py`

- `passed` 映射为 `correct`。
- `partial` 映射为 `partial`。
- `failed` 映射为 `incorrect`。
- `unverifiable` 只审计，不更新 mastery 或 retention。

### `gates.py`

- MEMORY/PROCEDURE 以 BKT `p_mastery` 为定量依据。
- CONCEPT/DESIGN 需要额外的解释或 Feynman-style qualitative pass。
- Gate 与教学策略解耦。
- 结果必须携带 evidence 和 provenance。

### `review_port.py`

- LearningEvidence 转成 RepetitionState。
- 计算 retrievability、forgetting risk 和 next review。
- 输出可供 PathRecommendation 消费的 ReviewTask。

## 必须先写的测试

1. 四种 Verdict 映射测试。
2. BKT 阈值前后掌握门控测试。
3. CONCEPT/DESIGN 在 quiz 全对但没有 qualitative pass 时不得通过。
4. 成功回忆提高 stability，失败增加 lapse 并提前复习。
5. 时间流逝使 retrievability 下降。
6. 相同 evidence replay 得到相同状态。
7. unverifiable 样本不改变长期状态。

## 完成标准

- 适配器测试先于外部源码导入。
- 所有新增接口有单元测试和最小集成测试。
- 事件字段能追溯到 learner、KC、verdict、策略版本和状态前后版本。
- 既有 R-01 至 R-10 测试不回归。
- 文档和代码路径使用 `platform/` 与 `docs/` 新结构。
