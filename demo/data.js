window.DEMO_DATA = {
  tools: [
    {
      name: "AI-FITNESS",
      repository: "ZHENGYANG-QIAN/AI-FITNESS",
      url: "https://github.com/ZHENGYANG-QIAN/AI-FITNESS",
      fields: {
        purpose: {
          text: "识别健身动作，辅助进行动作计数与分类。",
          evidence: [
            {
              ref: "r653684949-f1",
              quote: "基于人体关键点检测的健身动作计数应用",
            },
            { ref: "r653684949-f1", quote: "动作姿势分类" },
          ],
        },
        feature: {
          text: "通过人体关键点判断动作，包含深蹲、俯卧撑相关示例。",
          evidence: [
            {
              ref: "r653684949-f1",
              quote:
                "对于类似深蹲和俯卧撑这样的常见运动，一个up和一个down是一个完整的动作",
            },
          ],
        },
        usage: {
          text: "提供安卓端和电脑端实现，需要自行运行或构建。",
          evidence: [
            {
              ref: "r653684949-f1",
              quote: "移动端部署目前完成了安卓应用的构建。",
            },
            { ref: "r653684949-f1", quote: "PC端部署使用了PyQt5" },
          ],
        },
        requirements: {
          text: "安卓端需 Android Studio；电脑端使用 Python、PyQt5 等环境。",
          evidence: [
            {
              ref: "r653684949-f1",
              quote:
                "Android 开发工具 - Android Studio 中进行修改、调试和生成 APK 文件",
            },
            {
              ref: "r653684949-f1",
              quote:
                "PC端部署使用了PyQt5，它是 Qt 应用程序框架在 Python 中的封装。",
            },
          ],
        },
      },
    },
    {
      name: "claude-fitness-cn",
      repository: "BorisBW/claude-fitness-cn",
      url: "https://github.com/BorisBW/claude-fitness-cn",
      fields: {
        purpose: {
          text: "结合手表与力量训练记录，生成日常训练建议和复盘。",
          evidence: [
            {
              ref: "r1170601643-f1",
              quote:
                "Coach Paddy reads your watch + strength-log data, turns it into actionable coaching (morning readiness, evening recaps, weekly reviews, adaptive plans)",
            },
          ],
        },
        feature: {
          text: "结合睡眠、心率与训练负荷评估恢复状态，训练记录保存在本地。",
          evidence: [
            {
              ref: "r1170601643-f1",
              quote:
                "adjusted by sleep, HRV, energy, RHR trend, and active injury",
            },
            {
              ref: "r1170601643-f1",
              quote: "records everything in **plain-text local files you own**",
            },
          ],
        },
        usage: {
          text: "在 Claude Code 中使用教练技能，可另部署手机看板。",
          evidence: [
            {
              ref: "r1170601643-f1",
              quote:
                "A data-driven, multi-sport AI fitness coach for [Claude Code]",
            },
            {
              ref: "r1170601643-f1",
              quote: "mobile dashboard you can deploy to Vercel",
            },
          ],
        },
        requirements: {
          text: "需 Claude Code 并接入手表数据；Apple Watch 需另配快捷指令。",
          evidence: [
            {
              ref: "r1170601643-f1",
              quote:
                "A data-driven, multi-sport AI fitness coach for [Claude Code]",
            },
            {
              ref: "r1170601643-f2",
              quote:
                "| Apple Watch | `fitness-coach.md` **and** `apple-watch-fitness-coach.md` | no MCP — set up the Shortcut (step 1) |",
            },
          ],
        },
      },
    },
    {
      name: "fitness-coach-rpg",
      repository: "chenklein26-maker/fitness-coach-rpg",
      url: "https://github.com/chenklein26-maker/fitness-coach-rpg",
      fields: {
        purpose: {
          text: "用 AI 记录训练、安排计划，持续跟进个人健身过程。",
          evidence: [
            {
              ref: "r1228695623-f1",
              quote: "一个带训练日志、属性成长、恢复判断的教练系统",
            },
            {
              ref: "r1228695623-f2",
              quote: "本周训练计划，由 AI 或用户维护。",
            },
          ],
        },
        feature: {
          text: "把训练记录与 RPG 等级、属性成长和剧情推进结合。",
          evidence: [
            {
              ref: "r1228695623-f1",
              quote: "训练记录、RPG 属性成长、剧情推进三者联动",
            },
          ],
        },
        usage: {
          text: "下载仓库，在能读写本地文件的 AI 环境中对话使用。",
          evidence: [
            { ref: "r1228695623-f1", quote: "克隆或下载本仓库到任意目录。" },
            {
              ref: "r1228695623-f1",
              quote: "在 AI 对话中说“开始训练”或“初始化档案”。",
            },
            {
              ref: "r1228695623-f1",
              quote: "只要你的 AI 环境能读写本地文件，就能玩",
            },
          ],
        },
        requirements: {
          text: "需 AI 环境持续读写本地文件；Python 仅为可选脚本依赖。",
          evidence: [
            {
              ref: "r1228695623-f1",
              quote: "可持续访问同一工作区中的多个 Markdown 文件",
            },
            {
              ref: "r1228695623-f2",
              quote: "可选：Python 3.8+（用于脚本，没有也能跑）",
            },
          ],
        },
      },
    },
  ],
  curated: [
    {
      name: "DeepSeek Harness",
      description:
        "DeepSeek 推出的通用 AI Agent 工作台，通过插件扩展能力，提供本地网页操作界面。",
      highlights: [
        "通过按需安装插件，让同一个 AI 工作台获得不同任务所需的工具，减少重复搭建工作环境的成本。",
      ],
      github_url: "https://github.com/deepseek-ai/deepseek-harness",
      avatar: "bots/cloud-pink.svg",
    },
    {
      name: "Watermarks Remover",
      description:
        "面向自有内容的清理工具，可处理文本中的特殊字符以及图片、文档等文件的元数据。",
      highlights: [
        "清理自有文件中不需要的特殊字符和元数据，减少内容流转时附带的无关信息。",
      ],
      github_url: "https://github.com/guillaumemeyer/watermarks-remover",
      avatar: "bots/drop-blue.svg",
    },
    {
      name: "Cumora",
      description: "让人类与 AI Agent 在同一个聊天空间协作的团队工具。",
      highlights: [
        "把人与 AI 的沟通、分工和任务进度放在同一个群聊空间，减少来回转述需求和协调工作的时间。",
      ],
      github_url: "https://github.com/yetone/cumora",
      avatar: "bots/capsule-mint.svg",
    },
    {
      name: "Amagine3D",
      description:
        "根据文字需求、参考图片和尺寸，生成可编辑硬件结构的 AI 3D 设计工具。",
      highlights: [
        "把产品描述和尺寸转成可修改的三维结构，缩短从硬件想法到建模与打样准备的过程。",
      ],
      github_url: "https://github.com/amagine-ai/Amagine3D",
      avatar: "bots/orbit-amber.svg",
    },
    {
      name: "AutoResearch",
      description:
        "面向 AI 与机器学习研究的自动化助手，将研究想法推进为可审查的实验材料。",
      highlights: [
        "让 AI 把研究想法推进为有代码、实验记录和结果的证据材料，减少研究者反复执行实验流程的时间。",
      ],
      github_url: "https://github.com/EvoMap/AutoResearch",
      avatar: "bots/pebble-cream.svg",
    },
    {
      name: "Editable Design",
      description:
        "把文字需求变成可编辑海报、信息图和演示图表的 AI 设计工具，以 Codex Skill 的形式使用。",
      highlights: [
        "把 AI 生成的设计保留为可单独修改的文字和图层，让改一个细节不必重新生成整张图。",
      ],
      github_url: "https://github.com/yejy53/Editable-Design",
      avatar: "bots/spark-violet.svg",
    },
    {
      name: "FrontierAgent",
      description:
        "面向长流程研究和文件工作的 AI Agent 工具，提供原生终端界面。",
      highlights: [
        "让多个 AI 分工查资料和处理文件，把需要连续执行多步的任务推进到可检查的交付成果。",
      ],
      github_url: "https://github.com/ApodexAI/FrontierAgent",
      avatar: "bots/triangle-pink.svg",
    },
    {
      name: "爪爪 PawWork",
      description:
        "在 Chrome 侧边栏中运行的 AI 助手，可操作网页并制作办公内容。",
      highlights: [
        "让 AI 直接操作当前网页并整理成办公文档，减少人在网页和文档之间反复复制粘贴的工作。",
      ],
      github_url: "https://github.com/Player-YN/PawWork_ZhuaZhua",
      avatar: "bots/pixel-blue.svg",
    },
    {
      name: "NiubiGEO",
      description:
        "检查品牌在 AI 回答中如何被描述、推荐和引用的 GEO 监测工具。",
      highlights: [
        "用模型的原始回答和引用来源，查清品牌是否被 AI 推荐、与哪些竞品一起出现。",
      ],
      github_url: "https://github.com/Albert-Weasker/niubigeo",
      avatar: "bots/cloud-mint.svg",
    },
    {
      name: "Rakazo",
      description:
        "可自行部署的 AI 同事平台，让 Agent 持续保存记忆、执行任务并连接工作工具。",
      highlights: [
        "让 AI 保留工作记忆并使用浏览器和文件等工具，使重复任务能够持续执行而无需每次重新交代背景。",
      ],
      github_url: "https://github.com/elie222/rakazo",
      avatar: "bots/drop-amber.svg",
    },
    {
      name: "Goldie",
      description: "帮助开发者与 AI 编程助手制作应用商店截图和预览视频的工具。",
      highlights: [
        "自动把应用中的真实操作画面制作成商店宣传素材，省去反复截图、套框和剪辑的手工步骤。",
      ],
      github_url: "https://github.com/kacperkapusciak/goldie",
      avatar: "bots/capsule-cream.svg",
    },
    {
      name: "WeChat-AI",
      description: "可自托管的微信 AI 角色聊天服务，支持自定义人设和对话流程。",
      highlights: [
        "把自定义 AI 角色接入微信，让用户在熟悉的聊天入口里获得持续的人设对话体验。",
      ],
      github_url: "https://github.com/SMNETSTUDIO/WeChat-AI",
      avatar: "bots/orbit-violet.svg",
    },
    {
      name: "Easel",
      description:
        "面向自媒体创作者的 AI 工作台，将热点发现、选题策划、内容制作和运营复盘放进同一套流程。",
      highlights: [
        "围绕同一个账号定位把选题转成多个平台的内容版本，减少创作者重复找热点、改稿和整理素材的工作。",
      ],
      github_url: "https://github.com/ZJU-REAL/Easel",
      avatar: "bots/pebble-pink.svg",
    },
    {
      name: "Bot Crossing",
      description:
        "把 AI 工作任务变成“太空殖民地”的趣味可视化工具：每段 Agent 会话都是一名正在工作的宇航员。",
      highlights: [
        "把分散的 AI 任务状态变成一张宇航员地图，让你一眼看出哪些任务在运行、哪些需要自己处理。",
      ],
      github_url: "https://github.com/Station-Sciences/bot-crossing",
      avatar: "bots/spark-blue.svg",
    },
    {
      name: "∞ TAPEDECK",
      description:
        "根据你收藏的歌曲分析音乐偏好，再持续生成相近风格的新音乐，搭建属于自己的 AI 电台。",
      highlights: [
        "用收藏的歌曲和收听反馈引导新音乐生成，让电台持续补充符合自己口味的内容而无需逐首写提示词。",
      ],
      github_url: "https://github.com/CharlesMod/infinite-tapedeck",
      avatar: "bots/triangle-mint.svg",
    },
    {
      name: "Digest for Bilibili",
      description:
        "将 B 站视频变成可阅读、可提问、可整理笔记的学习资料的浏览器扩展。",
      highlights: [
        "把视频内容变成带时间戳的概览和笔记，让学习者快速找到重点并随时回到原片段核对。",
      ],
      github_url: "https://github.com/biuworks/bilibili-digest",
      avatar: "bots/pixel-amber.svg",
    },
    {
      name: "Ptero",
      description: "将多种 AI 模型整合到同一个界面的聊天平台。",
      highlights: [
        "把多个模型放进同一个聊天界面，让用户围绕同一问题切换和比较回答，减少跨平台操作。",
      ],
      github_url: "https://github.com/aminkheddache-dotcom/Ptero",
      avatar: "bots/cloud-cream.svg",
    },
    {
      name: "LiveStream Agent Studio",
      description:
        "面向直播电商团队的 Windows AI 工作台，整合主播研究、直播分析和内容策划。",
      highlights: [
        "把直播录屏、话术和流量数据放在同一个复盘流程中，帮助运营人员提炼可用于下一场直播的改进依据。",
      ],
      github_url: "https://github.com/HanyuanWang/LiveStream-Agent-Studio",
      avatar: "bots/drop-violet.svg",
    },
    {
      name: "barehands",
      description:
        "利用摄像头识别手势，让用户用双手操作笔记、图片和 3D 模型的交互工具。",
      highlights: [
        "用普通摄像头把手势变成屏幕操作，让用户不添置专用设备也能直接抓取、移动和缩放数字内容。",
      ],
      github_url: "https://github.com/jaredrhod/barehands",
      avatar: "bots/capsule-pink.svg",
    },
    {
      name: "MonoCode",
      description: "为多种 AI 编程 Agent 提供统一图形界面的桌面应用。",
      highlights: [
        "把已经在用的多个 AI 编程工具集中到同一桌面界面，减少切换工具和管理会话的操作成本。",
      ],
      github_url: "https://github.com/hardbeat920/monocode",
      avatar: "bots/orbit-blue.svg",
    },
    {
      name: "Wake",
      description: "将多个 AI 编程工具的本地会话历史集中管理的桌面应用。",
      highlights: [
        "把散落在不同 AI 工具里的历史对话变成可统一搜索和恢复的记录，减少找回工作上下文的时间。",
      ],
      github_url: "https://github.com/iAmCorey/Wake",
      avatar: "bots/pebble-mint.svg",
    },
    {
      name: "DeepSeek Harness Studio",
      description:
        "基于 DeepSeek Harness 的社区桌面增强工具，降低插件和 AI 工作环境的使用门槛。",
      highlights: [
        "把插件安装、模型配置和会话管理变成可点击的桌面操作，降低使用 DeepSeek Harness 的命令行门槛。",
      ],
      github_url: "https://github.com/fufankeji/deepseek-harness-studio",
      avatar: "bots/spark-amber.svg",
    },
    {
      name: "AI Data Extractor",
      description: "导出并统一整理多个 AI 编程助手本地聊天记录的工具。",
      highlights: [
        "把不同 AI 工具的本地聊天记录转换为统一格式，让个人工作积累更容易备份和分析。",
      ],
      github_url: "https://github.com/bawadou/ai-data-extractor",
      avatar: "bots/triangle-cream.svg",
    },
    {
      name: "易创 easy-writing",
      description:
        "面向小说作者的本地写作桌面软件，整合章节管理、创作资料和 AI 辅助写作。",
      highlights: [
        "把稿件、人物设定和 AI 辅助创作集中在本地写作空间，让作者在保留作品控制权的同时减少资料切换和重复整理。",
      ],
      github_url: "https://github.com/yilujian/easy-writing",
      avatar: "bots/pixel-violet.svg",
    },
  ],
};
