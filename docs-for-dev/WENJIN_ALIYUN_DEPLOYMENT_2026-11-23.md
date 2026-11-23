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
