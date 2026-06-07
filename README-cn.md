# codex-modelx 中文说明

`codex-modelx` 是一个 Windows 优先的 Codex Skill，用来把 MiMo、Qwen、Kimi、DeepSeek、GLM 等第三方 OpenAI-compatible 模型接入 Codex，并尽量保留 Codex 的工具 / 插件能力。

> 如果你已经在使用 CC Switch，日常推荐优先走 CC Switch；`codex-modelx` 更适合作为备用代理、安装引导和插件链路诊断工具。

## 它解决什么问题

Codex Desktop 原生更偏向 OpenAI Responses API。很多第三方模型虽然兼容 OpenAI API，但常见接口是 Chat Completions。`codex-modelx` 在本地启动一个代理：

```text
Codex Desktop
→ custom provider
→ http://127.0.0.1:17891/v1
→ codex-modelx 本地代理
→ 第三方模型 /v1/chat/completions
```

主要能力：

- 保持 Codex 的 provider 名为 `custom`，避免切换 provider 导致会话 / 沙箱桶变化。
- 将 Codex 的 `/v1/responses` 请求转换为第三方模型常用的 `/v1/chat/completions`。
- 在 `/v1/models` 中合并上游模型和手动配置的第三方模型。
- 默认尽量转发工具 schema。
- 工具失败时可降级到 Chrome / Zotero / Presentations 等常用插件策略。
- 提供启动、停止、修复、测试脚本。

## 安装方式

### 方法一：从 GitHub 克隆

```powershell
cd $env:USERPROFILE\.codex\skills
git clone https://github.com/GantAlan/codex-modelx.git codex-modelx-repo
Copy-Item -Recurse .\codex-modelx-repo\codex-modelx .\codex-modelx
```

最终目录应该是：

```text
C:\Users\<你的用户名>\.codex\skills\codex-modelx\SKILL.md
```

不要变成：

```text
C:\Users\<你的用户名>\.codex\skills\codex-modelx\codex-modelx\SKILL.md
```

### 方法二：下载 ZIP

1. 打开仓库：<https://github.com/GantAlan/codex-modelx>
2. 点击 `Code -> Download ZIP`。
3. 解压后，把里面的 `codex-modelx` 文件夹复制到：

```text
C:\Users\<你的用户名>\.codex\skills\
```

## 首次配置

进入 Skill 目录：

```powershell
cd $env:USERPROFILE\.codex\skills\codex-modelx
python .\scripts\configure.py
```

按提示填写：

```text
Base URL：你的第三方 API 地址，例如 https://your-api.example.com/v1
API Key：你的 API Key
协议类型：默认选择 openai_chat_completions
模型名：例如 mimo-v2.5、qwen-max、kimi-k2.5、deepseek-chat、glm-5.1
```

配置文件会生成在：

```text
assets\config\modelx.config.json
```

注意：这个文件会明文保存 API Key，不要外传。

## 启动代理

```powershell
cd $env:USERPROFILE\.codex\skills\codex-modelx
.\scripts\start_proxy.ps1
```

安全默认：`start_proxy.ps1` 只启动或检查本地代理，不会自动修改 `C:\Users\<你的用户名>\.codex\config.toml`。如果你明确要让脚本修复 Codex 路由，才使用：

```powershell
.\scripts\start_proxy.ps1 -RepairCodexConfig
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:17891/__health
```

正常应返回：

```json
{"ok": true}
```

## 配置 Codex

把 Codex 的 `custom` provider 指向本地代理：

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

配置文件通常在：

```text
C:\Users\<你的用户名>\.codex\config.toml
```

建议修改前先备份。

## 测试

```powershell
python .\scripts\test_modelx.py
```

如果需要测试插件链路：

```powershell
python .\scripts\test_modelx.py --run-codex-plugin-tests
```

重点测试：

- 文本返回
- function tool 调用
- Chrome
- Zotero
- Presentations

## 常见问题

### 只看到 GPT 模型

先确认代理自己是否能看到第三方模型：

```powershell
Invoke-RestMethod http://127.0.0.1:17891/v1/models
```

如果代理里有 MiMo/Qwen/Kimi 等模型，但 Codex Desktop 仍然只显示 GPT，再运行：

```powershell
.\scripts\repair_custom_provider.ps1
.\scripts\start_proxy.ps1 -RepairCodexConfig
```

然后完整重启 Codex Desktop。

### 模型菜单高级目录

默认不要安装 `model_catalog_json`，因为格式不对会让 Codex Desktop 的模型菜单解析失败。需要时先预览：

```powershell
python .\scripts\generate_catalog.py --check-current
python .\scripts\generate_catalog.py --include common
```

确认无误后再安装：

```powershell
python .\scripts\generate_catalog.py --include common --install
```

如果 Codex Desktop 出现模型菜单异常，撤销：

```powershell
python .\scripts\generate_catalog.py --uninstall
```

### 不想继续用 codex-modelx

停止代理：

```powershell
.\scripts\stop_proxy.ps1
```

然后把 Codex `config.toml` 的 `base_url` 改回你想使用的代理或 CC Switch 地址。

### 和 CC Switch 会冲突吗？

不会直接冲突，因为端口不同：

```text
CC Switch:     http://127.0.0.1:15721/v1
codex-modelx:  http://127.0.0.1:17891/v1
```

Codex 实际走哪个，只看 `config.toml` 中 `[model_providers.custom].base_url`。

## 不要上传这些文件

```text
codex-modelx/assets/config/modelx.config.json
codex-modelx/logs/
codex-modelx/state/
```

这些可能包含本地配置、日志或 API Key。
