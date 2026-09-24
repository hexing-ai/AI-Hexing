# AI-Hexing 在线演示

静态、无密钥、无模型调用的独立演示，使用预置公开资料快照与规则组合文本。不是完整后端的云部署。

本机预览：在仓库根目录运行 `python3 -m http.server 8080 --directory demo`，访问 `http://127.0.0.1:8080`。

`data.js` 的三项对比资料来自项目已审校的公开资料快照，24 个工具简介来自 `frontend/lib/curated-tools.json`；不代表当前产品状态或实测结论。Bot SVG 为项目静态资源，Demo 同时附带 [bloub MIT 许可](third-party/bloub-LICENSE.txt)，完整工作台另有引擎来源声明。

演示不保存输入、不读取 Cookie、不收集录音。编辑仅存在当前页面内存中。文本下载由浏览器本地生成；点击 GitHub 链接会访问外站。
