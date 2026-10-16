# 配置与启动问题

### [BUILD-WHEEL-001] 可编辑安装掩盖分发包运行依赖遗漏

---
module: Python 分发
category: 02
tags: [hatchling, wheel, resources]
difficulty: 3
status: 已解决
date: 2026-10-15
keywords: [Wheel 缺少运行依赖, evolution/l0_digest.py, seeds/question_bank.json]
---

#### 问题现象

源码回归通过，但 wheel 不含 evolution、seeds/question_bank.json 和 web/index.html，脱离源码后无法完整运行。

#### 定位过程

2026-10-15：构建原 wheel，读取 zip 文件清单，三个依赖均不存在；check_wheel.py 对原构建产物失败，明确列出缺失资源。

#### 根本原因

pyproject 的 wheel packages 只包含 app；动态导入模块和按相邻目录读取的外部资源不会自动纳入 app 包。源码目录与可编辑安装恰好可以找到它们。

#### 解决方案

wheel 包含 app 与 evolution，通过 force-include 精确纳入题库、旧页面；sdist 显式纳入构建所需文件。Hatchling 固定为本次验证版本 1.32.4。保留旧资源相对位置，避免扩大业务重构。

#### 验证方法

从 sdist 构建 wheel；运行 `python scripts/check_wheel.py .local/dist/rsi_platform-0.1.0-py3-none-any.whl`，临时目录隔离运行真实 API，结果 wheel_smoke passed、bank_items 12、external_http blocked；新增及原有 64 项回归通过。
