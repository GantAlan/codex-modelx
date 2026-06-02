# codex-modelx 安装提示词 / Installation Prompt

把下面这段提示词复制给另一台电脑上的 Codex，让它帮你安装 `codex-modelx` Skill。

---

请帮我安装这个 Codex Skill：`codex-modelx`。

我已经把安装包放在这里：

```text
【把这里改成当前电脑上的 zip 路径，例如 C:\Users\xxx\Downloads\codex-modelx-share.zip】
```

安装要求：

1. 这是一个 Codex Skill，不是普通项目。
2. 请把压缩包解压后，确保最终目录是：

```text
C:\Users\<当前用户>\.codex\skills\codex-modelx\SKILL.md
```

3. 不要解压成这种错误结构：

```text
C:\Users\<当前用户>\.codex\skills\codex-modelx\codex-modelx\SKILL.md
```

4. 如果目标目录已经存在，请先备份旧目录，不要直接删除。
5. 安装后检查目录结构，至少应该有：

```text
SKILL.md
agents\openai.yaml
scripts\
references\
assets\config\modelx.config.example.json
```

6. 如果本机有 Skill Creator 校验脚本，请运行 `quick_validate.py` 校验这个 Skill。
7. 不要使用别人机器里的 API Key；安装后让我自己填写 Base URL 和 API Key。
8. 安装完成后，请运行：

```powershell
cd C:\Users\<当前用户>\.codex\skills\codex-modelx
python .\scripts\configure.py
```

9. 运行配置时，让我填写：

```text
Base URL，例如 https://your-api.example.com/v1
API Key
协议类型，默认选 openai_chat_completions
模型名，例如 mimo-v2.5、qwen3.6-plus、kimi-k2.5、deepseek-chat、glm-5.1
```

10. 配置完成后启动本地代理：

```powershell
cd C:\Users\<当前用户>\.codex\skills\codex-modelx
.\scripts\start_proxy.ps1
```

11. 最后请告诉我需要把 Codex 的 `config.toml` 配成智能路由模式：

```toml
model_provider = "custom"
model = "gpt-5.5"

[model_providers.custom]
name = "custom"
base_url = "http://127.0.0.1:17891/v1"
wire_api = "responses"
requires_openai_auth = true
experimental_bearer_token = "dummy-key"
```

12. 目标效果是：

```text
Codex Desktop
-> model_provider = custom
-> http://127.0.0.1:17891/v1
-> codex-modelx 智能路由
   -> GPT 模型：原样 Responses 转发
   -> MiMo / Qwen / Kimi / DeepSeek / GLM：转换成 Chat Completions
```

13. 完成后请让我完整重启 Codex Desktop，然后检查模型下拉框是否同时出现 GPT 和第三方模型。
14. 如果模型列表里只有 GPT，请检查：

```text
C:\Users\<当前用户>\.codex\skills\codex-modelx\assets\config\modelx.config.json
```

里面是否有：

```json
"expose_upstream_models": true
```

并确认代理已经启动：

```text
http://127.0.0.1:17891/__health
```

---

安装完成后，不要把生成的 `assets/config/modelx.config.json` 发给别人，因为里面会明文保存 API Key。
