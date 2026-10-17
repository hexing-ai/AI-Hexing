# 真实服务配置与费用

先用默认 mock 模式完成安装和操作熟悉。切换 live 前，准备自己的 GitHub/百炼账户与本人录音；本项目不附带 API 密钥、账号、音色或免费额度。

## 文本与语音

在根目录 `.env` 配置（留空项需自行填写）：

```dotenv
PROVIDER_MODE=live
LIVE_CALLS_ENABLED=true
PROVIDER_CONTRACT_VERSION=S1-2026-09-08
GITHUB_TOKEN=
QWEN_API_KEY=
QWEN_MODEL=qwen3.7-flash
QWEN_REGION=beijing
QWEN_ANALYSIS_THINKING=false
QWEN_WRITING_THINKING=false
VOICE_API_KEY=
VOICE_REGION=beijing
VOICE_TARGET_MODEL=cosyvoice-v3.5-flash
VOICE_SAMPLE_ALLOWED_HOST=
```

重启 API 和 worker 后生效。令牌仅写入本地环境文件，不能提交 Git、写入前端代码或附在 Issue 中。GitHub token 用于提升真实读取配额；访问和模型可用性取决于你的账户及服务商当前支持情况。

当前模型/地域属于已接入契约。模型不可用时会报错，不会自动替换为其他型号；只修改模型名并不意味着新型号已完成适配或计价。写作关闭思考是当前实现配置，不代表已经完成不同模型、不同思考模式的效果横评。

官方入口：

- [百炼控制台](https://bailian.console.aliyun.com/)
- [Qwen 兼容接口](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)
- [声音复刻指南](https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide)
- [模型计价](https://help.aliyun.com/zh/model-studio/model-pricing)

## 本人录音与私有 OSS

当前上传入口支持 16-bit PCM WAV，5–60 秒、采样率至少 16kHz、单/双声道、文件不超过 10MB。建议 10–20 秒清晰单人录音；不要只修改 MP3 文件扩展名。

1. 在阿里云 OSS 创建或使用自己的北京地域私有桶，保持私有读写。
2. 把桶的外网域名填入 `VOICE_SAMPLE_ALLOWED_HOST`，格式为 `your-bucket.oss-cn-beijing.aliyuncs.com`；不含协议、文件路径和签名参数。
3. 将同一份录音上传工作台及 OSS。在 OSS 控制台对该对象生成短时有效的 HTTPS 签名链接。
4. 在工作台样本入口绑定链接。后端会读取并校验文件哈希与上传样本一致，不接受重定向或不匹配文件。
5. 核对发送录音的提示，创建音色，再确认费用生成试听；本人试听确认后用于正式配音。

签名链接属于临时访问凭证，不公开分享。链接到期不会删除 OSS 对象；本地删除也不会自动删除 OSS 或供应商音色。项目不自动创建云资源。

## 登录限流

十分钟内的失败登录分三层计数，任一层达到上限即返回 429，十分钟后自动解除：

| 范围 | 上限 | 说明 |
| --- | --- | --- |
| 同一账户名 | 10 次 | 不区分大小写和首尾空格；该账户成功登录后清零 |
| 同一客户端地址 | 20 次 | 防止一个来源轮换账户名猜测 |
| 全站 | 200 次 | 兜底，防止伪造来源地址绕过前两层 |

Web 经同源代理访问 API，API 看到的直连地址都是本机，因此客户端地址取自 `X-Forwarded-For` 的最后一段，且仅当直连地址在 `TRUSTED_PROXY_HOSTS` 中时才采用：

```dotenv
TRUSTED_PROXY_HOSTS=["127.0.0.1","::1","::ffff:127.0.0.1"]
```

Next.js 只在请求本身没有 `X-Forwarded-For` 时才补上来源地址，访客自带的值会原样转发。本机使用影响不大；对外部署时必须在 Next.js 前加一层反向代理并**覆盖**该请求头（如 nginx `proxy_set_header X-Forwarded-For $remote_addr;`），否则按地址计数可被伪造绕过，只剩账户和全站两层生效。

## 预算

默认单次上限 1 元、每月上限 30 元。配置使用整数微元，1 元 = 1,000,000 微元：

```dotenv
COST_SINGLE_LIMIT_MICROS=1000000
COST_MONTHLY_LIMIT_MICROS=30000000
```

真实调用先展示报价并获取确认，再预占预算；计价不可确定、价目过期或超过限额时不提交。结果未知保留占用，不自动重发。报价、预占与实际账单分别展示，不能将估算当成已扣费金额。

本地目录价具有来源与有效期，服务商可能更新价格；免费额度不作为预算计算前提。预算仅覆盖此工作台纳入计价的新增模型请求，不包含 OSS、其他应用调用或历史账单。模型报价实现见 `backend/app/services/costs.py`。

价目到期后所有付费操作都会提示“价目已过核验期限”。到[模型计价](https://help.aliyun.com/zh/model-studio/model-pricing)与[声音复刻说明](https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide)核实 `costs.py` 中的单价仍然有效后，在 `.env` 延后 `PRICE_VALID_UNTIL`（文本与配音）和 `ENROLLMENT_PRICE_VALID_UNTIL`（创建音色）并重启 API 与 worker；单价有变化时须先修改代码。
