# 桂子问津阿里云部署
日期：2026-11-23。目标服务器 47.111.38.174，Ubuntu 22.04 x86_64，2 核 4G，40GB 系统盘。

本次按用户选择使用 HTTP 8088 验收，不启用 HTTPS 或证书自动续期。此前 Let's Encrypt 免费 IP 证书已经签发，但未用于应用，也没有购买证书。HTTP 不加密登录信息，正式面向学生使用前应启用 HTTPS 与 Secure Cookie。

## 服务与文件
- 代码和运行根目录 /opt/guiziwenjin；配置 /etc/guiziwenjin；备份 /var/backups/guiziwenjin。
- 公网 Nginx 8088 → 回环地址 3782 → 单应用容器内前端和后端（8001）。
- 独立 Compose 项目 guiziwenjin，PostgreSQL 17.11 不向主机发布数据库端口，使用 wenjin_app 非超级用户。
- 保留原有 HTTP 80 human-study 网站。8088 站点来自 deploy/wenjin/aliyun-nginx.conf。
- 前端和 Python 依赖在本地构建，服务器仅从本地基础镜像组装最新源码层。不要复制 Windows .venv/node_modules。
- 应用以 UID/GID 10001 运行，根文件系统只读，资料目录可写；单后端进程、应用内存限额 2300MB、数据库 640MB。服务器另有 2GB 专用交换文件。
- 当前磁盘和内存规格仅供小规模使用；并发与持续视频生成能力尚需压测。

## 一致性迁移
先停止本地前后端写入，数据库继续运行。执行 scripts/backup_wenjin_release.py --directory data/backups/releases/新的目录，生成 database.dump、runtime.tar.gz 和 manifest.json。

备份记录所有业务 schema 的逐表行数/摘要及文件 SHA-256。排除历史备份、部署 SSH 私钥以及数据库管理员 compose.env/init.sql。七个已迁移资源的 schema 和 scope_id 必须保留，不能根据新路径重建。

首次目标必须为空，并确认 PostgreSQL 集群标识不同。以 wenjin_app 创建目标对象，使用 --exit-on-error --single-transaction --no-owner --no-acl --role=wenjin_app 恢复。

目标 connection.json 改为 postgres:5432 与新的应用密码；数据库管理员密码与初始化 SQL 位于 /etc/guiziwenjin，不能进入 Git。运行 verify_wenjin_release.py 完整核对后，再运行 relocate_wenjin_release.py --apply，只迁移实际存在的文件路径元数据，不改对话正文或用户标识。

管理员 tzk 保留用户标识，服务器使用独立强密码；审核账号 wenjin_audit 为普通学习用户，不复制管理员模型密钥。运行 provision_wenjin_deployment.py --apply 前安全提供临时部署密码环境变量。原本地账户和文件保持不变。

## 构建启动
本地 Dockerfile 为 deploy/wenjin/Dockerfile，生成 guiziwenjin:20261123-base。依赖受 requirements/wenjin-linux-constraints.txt 约束，媒体要求已对齐本地验证的 ReportLab 5.1 和 Pillow 13。

服务器导入镜像后，使用 deploy/wenjin/Dockerfile.overlay 组装代码；该步骤不需要 npm、pip 下载或重新编译前端。

生产 Compose：deploy/wenjin/compose.production.yaml。独立配置包括 /etc/guiziwenjin/app.env、database.env、postgres-init.sql。app.env 以 deploy/wenjin/aliyun-app.env.example 为模板。配置权限 600；初始化 SQL 文件在受限父目录内以只读方式挂载。

启动前执行 docker compose -f deploy/wenjin/compose.production.yaml config --quiet。首次只启动数据库，完成恢复与校验、路径迁移和账户设置后再启动应用。最后测试 Nginx 配置并 reload，保留原站点。

## 验收
检查登录页、Cookie、管理员/审核账号、原项目/对话/诊断/媒体文件、跨账户拒绝访问、WebSocket、模型接口鉴权、公式和 FFmpeg，以及容器重启后的数据。

测试结果与服务器实际状态应单独记录；本文件是部署流程，不表示所有功能或真实学生学习效果已验收。媒体 TTS 依赖各用户配置的可用语音服务，不将 Windows 本地语音视为 Linux 已可用。

备份必须同时覆盖 PostgreSQL 与 data 目录，并复制到独立位置。不要执行 down --volumes、删除数据或重新用旧 SQLite 覆盖 PostgreSQL。

## 本次执行结果

- 已将分支 V23-20261118-Deploy 的已推送代码部署到 /opt/guiziwenjin；GitHub 直连拉取先出现 HTTP/2 错误，改为 HTTP/1.1 后仍连接超时，因此使用同一提交的 Git bundle，经 SHA-256 校验后导入，origin 仍指向原 GitHub 仓库。
- 应用与 PostgreSQL 镜像在本地构建/导出，运行包、数据库转储、资料包和清单共五个文件的传输 SHA-256 均已核对。
- 首次恢复核验：182 张表、172270 条记录、1297 个文件一致。目标数据库连接文件按部署环境有意更换；七个资源的 schema/scope_id 保留。
- 管理员 tzk 和普通审核账号 wenjin_audit 可登录，HTTP Cookie 可用且退出后清除。审核账号看不到管理员项目，越权访问项目返回 404，未登录访问项目接口返回 401。
- 管理员接口读取到 13 个项目、11 条会话；项目详情、诊断事实、证据、训练记录和计划历史可访问。
- 二元一次方程组项目的两个历史课堂各有 74 页。七个已有 PPTX/音频/视频资源下载测试通过，Range 请求返回 206。
- 经 Nginx 和 Next 转发的已认证 WebSocket 升级返回 101；未登录页面跳转到相对登录地址。
- 容器内使用迁移后的原有 DeepSeek 配置请求模型列表，鉴权成功返回 200、两个模型。该检查未生成新的教学内容，不代表已经进行全套学生学习或并发压测。
- 已制作目标部署的数据库/资料配对备份：/var/backups/guiziwenjin/initial-verified-20261123/，包含 SHA256SUMS；数据库和应用重启后重复登录与数据检查通过。原本地应用保持停止，本地 PostgreSQL 与原资料保留。
- 服务器原有 80 端口网站仍返回 200；应用 3782 只绑定回环，8001 不对宿主机开放，数据库不发布端口。
- 服务器内部 HTTP 8088 登录页返回 200，但从公网访问 8088 失败。宿主机 UFW 未启用、INPUT 为 ACCEPT，Nginx 已监听 0.0.0.0:8088；尚需用户在阿里云安全组入方向放行 TCP 8088，然后复验公网入口。当前不能宣称公网访问验收通过。
- 未配置应用 HTTPS 或证书自动续期，没有启用证书续期服务/定时器。此前签发的免费证书未用于该应用。

## 尚需处理

1. 阿里云安全组放行 TCP 8088（公网来源 0.0.0.0/0），再检查公网登录与播放。
2. Linux 无 Windows 本机配音能力；当前未配置可用 TTS。已有音视频完整保留，生成新配音及配音视频前，需要在设置中配置各用户可用的语音服务。
3. HTTP 下浏览器录音等依赖安全上下文的能力可能受限；本阶段仅按用户要求使用 HTTP，后续若需要这些功能再启用 HTTPS。
4. 小规格实例尚未完成并发、长时间媒体生成与学生大规模使用验收。备份现有本地原件作为独立副本；后续新增服务器数据也需定期异地备份。
