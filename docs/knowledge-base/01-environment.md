# 环境问题

### [ENV-SANDBOX-001] Windows 沙箱权限错误影响依赖与测试

---
module: 开发环境
category: 01
tags: [windows, sandbox, pytest]
difficulty: 2
status: 已解决
date: 2026-10-15
keywords: [os error 10013, could not create numbered dir, WinError 5]
---

#### 问题现象

uv 下载出现套接字权限错误，pytest 的 tmp_path 和 tempfile 创建/写入失败，首轮 50 passed、1 failed、10 errors。

#### 定位过程

2026-10-15：失败路径集中在沙箱重定向的 AppData 临时目录。改用项目内缓存和临时路径解决部分错误，但沙箱内 TestClient 仍阻塞；同一代码在正常本机进程完成 61 项原回归，新增检查后共 64 项通过。

#### 根本原因

本次执行器的文件、网络和线程限制影响检查过程；错误不能直接证明教学逻辑有问题。

#### 解决方案

缓存与临时输出放在忽略的 `.uv-cache/`、`.local/` 下；下载依赖及需要回环网络/线程的测试经允许在正常本机进程执行。结束本次挂起的 pytest 进程，不放宽应用本身的权限。

#### 验证方法

`pytest -q` 得到 64 passed；`scripts/check_server.py` 实际 HTTP 启动通过。普通开发环境仍可使用默认临时目录，文档记录受限运行器的变量配置。

### [ENV-PROCESS-002] Windows 启动检查关闭启动器后资源未释放

---
module: 启动检查
category: 01
tags: [windows, subprocess, venv]
difficulty: 2
status: 已解决
date: 2026-10-15
keywords: [WinError 32, server.log]
---

#### 问题现象

HTTP 检查已通过，但 TemporaryDirectory 清理 server.log 时短暂报文件正在使用。

#### 定位过程

2026-10-15：原脚本只 terminate Popen 直接进程；Windows 虚拟环境 python 启动器可能派生实际解释器，等待启动器退出不保证服务子进程已释放日志。

#### 根本原因

检查脚本的子进程生命周期管理不覆盖 Windows 启动器派生的服务进程树。

#### 解决方案

`scripts/check_server.py` 在 Windows 结束其自己创建的服务 PID 树（taskkill /T /F），随后 wait；其他系统按 terminate/wait 清理。不扫描或终止其他开发服务。

#### 验证方法

同一启动检查返回 exit code 0，startup passed、15 个操作，临时目录成功清理。
