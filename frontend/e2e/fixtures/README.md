# 自编浏览器媒体样本

`self-authored-color-vp8.webm` 是本机 FFmpeg 生成的单色、1秒、160×90、10fps、无声 VP8 样本（808 bytes），用于验证浏览器确实可以解码视频；不是供应商生成结果，不产生外部费用。

生成方式：`ffmpeg -f lavfi -i color=c=teal:s=160x90:r=10:d=1 -c:v libvpx -b:v 30k -an self-authored-color-vp8.webm`。

课堂测试还在内存中生成自编 PNG/WAV，模拟同源课堂资源与保存API；禁止向外部发出媒体生成请求，断言 duration/readyState、公式、互动与刷新状态。不能据此宣称收费媒体API已实测。
