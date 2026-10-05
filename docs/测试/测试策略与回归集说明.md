# 测试与回归

机制测试位于 `platform/tests/`，覆盖 Schema、规则引擎、提示阶梯、学习追踪、Agent、网关和 M1 验收。冻结回归场景和效果评测属于后续阶段，不与单元测试混放。

执行入口：

```powershell
cd platform
python -m pytest -q
```

阶段一新增能力必须先写 Adapter Test，再引入外部实现；`unverifiable` 结果不得进入长期学习状态。
