# 桂子问津 Ubuntu 部署与数据迁移

目标分支：V23-20261118-Deploy。代码及运行数据根目录 /opt/guiziwenjin，独立备份 /var/backups/guiziwenjin。

## 网络与申请表

拟申请 wenjin.ccnu.edu.cn，备选 guiziwenjin.ccnu.edu.cn，仅启用学校最终批准的域名。学校 HTTPS 443 → 10.222.10.245:8088 → 本机前端3782 → 本机后端8001。数据库只监听127.0.0.1:55434。域名审批、DNS、公网入口与证书由学校协调，本分支不会自动开通域名。

部署前用 ss -lntp 检查端口。独立增加站点，保留其他网站配置；防火墙只允许学校认可的入口来源连接8088。不要向公网发布后端或数据库端口。若学校未提供TLS入口，需要先补齐服务器证书和HTTPS站点再开放；本模板的8088是内网HTTP上游。

## 1. 准备环境与代码

先检查Ubuntu源、Docker镜像源、PyPI、npm、Node下载及实际模型接口可达性。安装Docker Engine与Compose插件、Python3.11～3.14、Node22、Nginx、fonts-noto-cjk；按官方仓库安装，不替换现有应用的系统Python。建议使用Python3.11或3.12独立虚拟环境。不要把Windows的.venv、node_modules上传到Linux。

```bash
# 以下在服务器执行；若目录或账户已存在，核对后跳过对应创建动作。
useradd --system --user-group --create-home --home-dir /home/wenjin --shell /usr/sbin/nologin wenjin
mkdir -p /opt/guiziwenjin /var/backups/guiziwenjin /etc/guiziwenjin
git clone --branch V23-20261118-Deploy --single-branch https://github.com/Tangfgjk/EduAgent.git /opt/guiziwenjin
cd /opt/guiziwenjin
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements/wenjin-production.txt
.venv/bin/python -m pip install -e . --no-deps
cd web
npm ci --legacy-peer-deps
npm run build
cd ..
```

前端构建脚本会准备standalone所需的静态文件。保留源码安装的web/node_modules和web/scripts/render-slide-formula.mjs，后端公式渲染依赖它们。不要直接用上游DeepTutor镜像代替此分支，现有通用Dockerfile没有完整纳入本次新增服务及公式脚本。

## 2. 一致性迁移本地数据

正式切换时停止本地前后端和后台写入，数据库保持运行。运行 scripts/backup_wenjin_postgres.py 生成自定义格式dump，再经SSH/SFTP传输完整data目录与该dump。业务切换后的权威数据是PostgreSQL，不要运行stage_wenjin_postgres.py将旧SQLite重新覆盖回去。

必须一起迁移：data/system/postgres/business.json（稳定scope映射）、connection.json、必要账号/加密密钥配置、用户设置和密钥、上传资料、知识索引、模型文件、课堂媒体与仍使用SQLite的辅助模块文件。禁止提交Git或放入公开下载目录。文件快照与数据库备份必须属于同一次暂停写入窗口。

备份脚本不导出数据库角色。服务器需通过受保护的data/system/postgres/compose.env和init.sql初始化角色（或由运维创建同名应用角色），并调整connection.json：本方案应用直接运行在宿主机，host为127.0.0.1、port为55434，账号为非超级用户wenjin_app。不要把Docker内部的postgres主机名写给宿主机应用。

```bash
cd /opt/guiziwenjin
docker compose -f compose.wenjin-postgres.yaml up -d --wait
# 以下仅针对首次初始化的空目标库；确认后替换文件名执行。
docker compose -f compose.wenjin-postgres.yaml cp /var/backups/guiziwenjin/REPLACE_WITH_SNAPSHOT.dump postgres:/tmp/wenjin-import.dump
docker compose -f compose.wenjin-postgres.yaml exec -T --user postgres postgres pg_restore --exit-on-error --no-owner --no-acl --role=wenjin_app --dbname=wenjin /tmp/wenjin-import.dump
```

不要在已有正式数据的库上反复执行恢复。恢复前先确认目标库为空；保留原始快照与本地环境。核对账户、项目、对话、证据数量及关联；测试新用户隔离。资源映射使用runtime-home相对路径，但旧上传记录或索引可能包含Windows绝对路径，需要逐类核查并修正，不能承诺只复制文件即全部可用。

## 3. 配置并运行服务

```bash
cd /opt/guiziwenjin
cp deploy/wenjin/app.env.example /etc/guiziwenjin/app.env
chmod 600 /etc/guiziwenjin/app.env
# 编辑该文件的端口、域名与HTTPS开关；同步修改nginx端口及域名。
chown -R wenjin:wenjin /opt/guiziwenjin
cp deploy/wenjin/wenjin-backend.service /etc/systemd/system/
cp deploy/wenjin/wenjin-frontend.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wenjin-backend wenjin-frontend
```

HTTP校内预验收时AUTH_COOKIE_SECURE=false；公网HTTPS启用前必须改为true并重启两个应用服务，保持AUTH_ENABLED、NEXT_PUBLIC_AUTH_ENABLED、DEEPTUTOR_AUTH_ENABLED为true。CORS_ORIGINS只填写实际批准的HTTPS来源；备用域名未批准时不加入。前端DEEPTUTOR_API_BASE_URL继续使用127.0.0.1:8001，不改成公网域名。根目录.env不会自动代替这些配置。

systemd服务以wenjin用户运行，数据库与应用日志独立。生产launcher固定绑定回环地址，默认单后端worker；先验证并发与资源占用再扩展。

## 4. Nginx和公网接入

```bash
cp /opt/guiziwenjin/deploy/wenjin/nginx.conf /etc/nginx/sites-available/guiziwenjin
ln -s /etc/nginx/sites-available/guiziwenjin /etc/nginx/sites-enabled/guiziwenjin
nginx -t
# 仅在检查成功且确认不影响现有站点后执行：
systemctl reload nginx
```

不要替换nginx主配置，不删除其他站点。模板包含WebSocket转发、长请求和上传限制。学校入口必须保留浏览器Host，并允许WebSocket及视频Range请求。80/443由学校现有入口承载；8088仅是内网源站端口。

## 5. Linux音视频与验收

Windows System.Speech不能搬到Ubuntu。管理员及学生在自己的语音设置中配置有效TTS，生成课堂时选择“我的语音模型”；没有TTS配置则不宣称配音可用。中文字体和FFmpeg均需可用。预检查不会实际调用付费语音接口。

```bash
cd /opt/guiziwenjin
sudo -u wenjin .venv/bin/python scripts/check_wenjin_deployment.py
curl -f http://127.0.0.1:8001/health/ready
curl -I http://127.0.0.1:3782/login
journalctl -u wenjin-backend -u wenjin-frontend -n 100 --no-pager
```

验收：登录注册、旧账户/对话/项目/资料、跨账号拒绝访问、诊断与个人训练、自动计划、课堂生成、公式、真实Linux配音、视频播放与字幕、Range拖动、导出及重启恢复。专门测试域名HTTPS的登录Cookie与WebSocket。申请表使用专用审核账号，不使用服务器root或本地初始弱密码。

## 6. 备份和升级

备份PostgreSQL与data文件，复制到/var/backups/guiziwenjin及独立存储；敏感文件设置最小权限。更新前记录git提交号并备份。停止应用服务后拉取指定分支、安装依赖、构建、预检查再启动；失败时依据备份和提交号回退，不切回旧SQLite。不要删除数据库卷或用git clean清理运行数据。音视频持续增长，应监测磁盘容量并制定留存策略。

当前仅验证本地代码及模板，不代表已在10.222.10.245安装、恢复或完成域名开通。负责人和机房信息待确认，未写入应用代码。


## 本地发布检查记录

- 部署launcher、BKT、课堂/音视频、PostgreSQL仓库与训练、个人模型：71项测试通过。
- 课堂/播放器、诊断、个人模型与导航等前端：43项测试通过。
- TypeScript检查与新增脚本Ruff F/E9检查通过。
- 只读预检查：Python、Node、稳定资源映射、非超级用户PostgreSQL连接、公式渲染、FFmpeg和中文字体全部通过。
- 实际Ubuntu systemd、Nginx及Linux TTS仍需在目标服务器按本说明验收；本地通过不代表服务器已经部署。

- npm run build生产构建成功；临时启动standalone产物，登录页HTTP 200，测试进程随后关闭。
