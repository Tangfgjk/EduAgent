# 中间件问题



## 2026-10-20：跨日Docker停止导致数据库及worker不可用

状态：已解决；模块：本机Docker/PostgreSQL；关键词：`dockerDesktopLinuxEngine`、`OperationalError`、`worker_cycle_unavailable`。

### 现象

API/worker日志出现cycle_unavailable，数据库连接失败；并不是迁移结构不匹配。

### 定位过程

先执行无模型调用的assert_ready与worker housekeeping；只打印异常类别/安全帧，不泄露连接串。Docker查询表明Linux引擎管道不存在、服务停止；启动后现有容器尚未运行。

### 根因

跨日恢复时Docker Desktop及本项目PostgreSQL容器停止，原数据库卷仍在。

### 解决方案

后台恢复Docker Desktop，执行`docker compose -f compose.dev.yml up -d db`复用原卷；不删除、不重建数据库。确认r14_003与文本/媒体启用预算均0后再启动4003 API及教学/模型worker。

### 验证方法

assert_ready和Worker.reap通过、原库版本正确、HTTP health/ready与双端静态入口可用；独立媒体worker保持关闭。
