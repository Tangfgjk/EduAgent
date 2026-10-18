# 规划浏览器测试专用 Fake 适配器的字段假设

状态：已解决；日期：2026-10-18；模块：浏览器测试适配器；关键词：planning、student_text、runtime_error、Fake。

## 现象

后端与独立 Fake 规划评测通过，但浏览器规划停在 model_pending，模型状态 unknown/runtime_error；旧固定模板通过。

## 定位过程

浏览器失败快照确认 unknown/runtime_error。检查测试服务 `test_provider`：识别开发超时标记时先索引 `snapshot['student_text']`，之后才分派 planning。规划快照使用 sources，没有顶层 student_text，分派前即触发 KeyError。

## 根因

仅测试适配器把陪学快照字段错误地当成所有模型用途的公共字段；生产 Provider 的规划分派不依赖它。真实专项 PL03 的 invalid_planning_proposal 不属于本问题。

## 解决方案

超时测试标记读取改为 `snapshot.get('student_text', '')`；规划分派使用自己的 sources 与受限结果。没有修改生产输入、放宽输出校验或自动重试真实调用。

## 验证方法

重新运行正确前端工作目录下的隔离浏览器服务，4 项组合验收通过，包含真实接口与 Fake 的来源显示、双关口确认、回滚。模型状态不再因测试适配器字段缺失进入 unknown。
