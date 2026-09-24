# 0.1.0 发布记录

本版本为 AI-Hexing 首个公开本地 MVP，MIT 许可。发布入口：[源码](https://github.com/hexing-ai/AI-Hexing) · [交互 Demo](https://hexing-ai.github.io/AI-Hexing/) · [Release](https://github.com/hexing-ai/AI-Hexing/releases/tag/v0.1.0)。

## 交付内容

- 创作任务、按主题发现与三工具对比、四段口播和配套文案、本人声音、完整素材导出、预算和处理记录。
- 24 个工具 Bot、工具介绍、GitHub 链接以及独立发布/下架的公开榜单。
- 模拟供应商可在无模型密钥时使用；真实 Qwen 与 CosyVoice 通过用户自备配置接入。
- 安装与统一启动脚本，README、使用说明、架构、限制和第三方许可声明。
- GitHub Pages 独立静态 Demo：公开资料、规则组合示例、编辑/下载、轻量 Bot 动画，无后端和付费调用。

## 验证边界

整理前已通过 271 项后端非 live 测试、Ruff、前端 ESLint、生产构建、TypeScript，以及 ego lite 隔离模拟流程。完整素材包此前完成过真实 33.94 秒配音案例；本次公开发布不重新调用真实模型，也不公开该录音或私人文稿。

本次隔离副本已完成依赖安装、271 项后端回归、3 项启动脚本检查、Ruff、前端构建、类型和 ESLint 检查。静态 Demo 已验收三工具比较、四段文本编辑、文案同步、文本下载、24 个 Bot 详情与 Escape 关闭，以及 390px 无横向溢出。

本地浏览器验收使用 ego lite 的专用空间与模拟数据。页面预览来自独立模拟环境；本轮截图接口超时，在线 Demo 与更新后的榜单图片由同一浏览器打印渲染生成。最新依赖安装、回归及构建结果以 [GitHub Actions](https://github.com/hexing-ai/AI-Hexing/actions) 中对应提交为准；静态 Demo 的部署记录在 Pages workflow。

## 未包含的材料

`.env`、API 密钥、账号及会话、数据库、个人录音、生成音频与导出包、日志、内部 PRD、通用技术手册、开发参考图和内部验收记录均不属于公开交付物。环境变量示例只包含空密钥与安全默认值。

MIT 适用于本项目原创代码；bloub、Matter.js 等保留原许可证。当前范围仍是单创作者本机工作台，不代表多用户生产 SaaS。真实质量、延迟与设备覆盖限制见 [版本状态](STATUS.md)。
