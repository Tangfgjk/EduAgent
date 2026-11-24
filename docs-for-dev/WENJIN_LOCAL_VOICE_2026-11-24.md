# 服务器本地语音部署与验收

本次按用户选择，采用服务器离线中文语音，不要求学生填写语音 API Key。已有个人语音提供方选择仍优先，明确选择的提供方失败时不会静默切换。

## 安装与配置

- 安装 requirements/local-voice.txt。MeloTTS 通过 sherpa-onnx CPU 推理；识别使用 Vosk small Chinese 0.22。
- 按 deploy/wenjin/local-voice-models.json 下载官方模型、核验 SHA256，将两个模型目录放入 data/system/voice/models，保留模型许可证。模型是运行数据，不提交 Git。
- 设置 WENJIN_LOCAL_VOICE_ENABLED=true；默认关闭，避免其他部署被自动改动。
- 更新镜像时先构建 Dockerfile.voice（Linux wheel 离线安装，基于原依赖镜像），再构建 Dockerfile.overlay。overlay 另需 Linux frontend 阶段导出的 standalone 目录打包为 deploy/wenjin/runtime-assets/frontend.tar.gz。完整新部署可直接使用 Dockerfile。
- 语音进程按请求启动、单线程、每种任务串行、180 秒处理超时；录音最多 120 秒，限制上传大小。
- 课堂可选择“服务器中文语音”，配音与逐页视频沿用持久化任务、字幕、取消与播放接续。

## 麦克风与 HTTPS

正式试用地址 https://47.111.38.174:8088 。HTTP 原地址重定向；使用已有免费 IP 证书，systemd 定时检查续期，Nginx 保留其他 80 端口网站。浏览器需允许麦克风权限。识别结果只填入输入框，学生检查修改后发送。

小模型可能出现同音字及数学术语识别错误，不使用大模型自动改写学生答案。模型升级或个人语音 API 可在后续提高识别准确率。

## 陪学延迟

原 DeepSeek V4 省略 thinking 参数时隐式启用 high。现在只对未明确选择思考强度的 DeepSeek V4 陪学回合采用 none，保留原模型、工具和证据判断。对话执行器在历史整理之前建立本回合专属配置，让评分、画像和历史辅助调用继承同一强度；不修改共享账号设置。明确的用户思考设置优先。WENJIN_MASTERY_REASONING_EFFORT=native 可恢复提供方默认行为。

## 来源

- MeloTTS/sherpa 官方模型：https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html ，下载包许可证 MIT。
- Vosk 模型：https://alphacephei.com/vosk/models ，small Chinese 0.22 为 Apache 2.0。
- DeepSeek 思考模式：https://api-docs.deepseek.com/guides/thinking_mode/ 。

最终公网端到端结果补充在 WENJIN_DEPLOYMENT_ACCEPTANCE_2026-11-24.md。
