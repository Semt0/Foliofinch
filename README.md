# Foliofinch

Foliofinch 是一个 macOS 科研博客阅读器。它每天检查你关注的网站，把新文章整理成中文摘要，方便你决定哪些值得打开细读。

**[下载 macOS 版（DMG）](https://github.com/Semt0/Foliofinch/releases/latest/download/Foliofinch-macOS-arm64.dmg)** · Apple Silicon（M 系列芯片）· macOS 14 或更高版本

## 使用

1. 打开 DMG，把 **Foliofinch** 拖到「应用程序」，再从那里启动。当前版本未经 Apple 公证。如果系统阻止首次打开，确认下载来源后，可到「系统设置 → 隐私与安全性」点「仍要打开」。[Apple 操作说明](https://support.apple.com/zh-cn/102445)
2. 首次打开时选择摘要方式并保存。选「免费本机模型」后，应用会在首次生成摘要时下载约 **1.12 GB** 的 Qwen 模型，在 Mac 上处理文章节选。选「自有 API」则填写兼容 Chat Completions 的 HTTPS 地址、模型名和密钥，文章节选会发送给该服务。首次更新也需要联网下载订阅组件。
3. 点「添加订阅」，粘贴博客主页、文章列表或 RSS / Atom 地址。没有订阅源的网站会先显示识别到的文章链接，确认后点「订阅此网页」。
4. 登录 Mac 期间，应用每天 **09:00** 自动检查，关闭窗口后也会运行。打开「今日更新」看摘要，点「用 Safari 阅读」打开原文。想提前查看时，点「立即检查」。

## 名称由来

*Folio* 指书页，*finch* 是雀鸟。Foliofinch 取一只小雀每天衔来新书页的意象。
