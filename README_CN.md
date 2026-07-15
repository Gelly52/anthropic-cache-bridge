# Anthropic Cache Bridge

一个零第三方依赖的本地反向代理，用于 Anthropic 兼容接口的 prompt cache
断点补充和 thinking signature 修复。代理通过字节级修改请求，避免 JSON
重新序列化导致已有签名失效。

## 解决的问题

部分 Anthropic 客户端或协议适配器没有在有效位置添加 `cache_control`，可能
出现缓存只写不读或命中率低的问题。

部分客户端在多轮会话中重新发送 assistant thinking block 时，会携带空的或
缺失的 `signature`，上游随后返回：

```text
Invalid `signature` in `thinking` block
```

本项目在不修改客户端和上游源码的情况下，通过本地代理统一解决这两类问题。

## 工作方式

```text
Anthropic 客户端
      |
      v
http://127.0.0.1:18787
      |
      v
Anthropic Cache Bridge
      |
      v
Anthropic 兼容上游
```

对于 `/v1/messages` 请求，代理包含两个独立模块：

**Prompt Cache 模块**

1. 给最后一个 tool definition 添加 `cache_control`；
2. 给最后一个 system block 添加 `cache_control`；
3. 给最新 user message 的最后一个 content block 添加 `cache_control`；
4. 可选注入稳定的 `metadata.user_id`，提高上游缓存路由一致性。

详细说明见 [docs/prompt-cache.md](docs/prompt-cache.md)。这是面向各种 Anthropic
兼容客户端和适配器的通用缓存兼容层，不绑定某一个具体客户端。

**Thinking Signature 模块**

1. 从 JSON 和 SSE 响应捕获真实 thinking signature；
2. 按 `(model, SHA-256(thinking))` 恢复空或缺失的 signature；
3. 默认用本地 SQLite 保存 signature 7 天，最多 2048 条。

详细说明见 [docs/thinking-signature.md](docs/thinking-signature.md)。该模块主要
面向 Cursor++ 及其他会在多轮会话中重放 thinking block、但可能丢失或错误携带
signature 的客户端。

两个模块相互独立，Thinking Signature 不依赖 Prompt Cache。由于它们可能修改
同一个请求，代理固定按以下顺序处理：

```text
原始请求
    -> Thinking Signature：先恢复缺失 signature
    -> Prompt Cache：再用字节级方式注入 cache_control
    -> 上游
```

响应返回前，Thinking Signature 模块在上游响应完整读取后捕获新的有效签名。

请求采用字节级插入，不进行 `json.loads` -> `json.dumps` 往返处理，因为重新
序列化可能改变 thinking block 的原始表示并使签名失效。

## 环境要求

- Python 3.9+
- macOS launchd 或 Linux systemd user service
- 提供 `/v1/messages` 的 Anthropic 兼容上游

无需安装第三方 Python 包。

## 一键安装

```bash
git clone <你的仓库地址> anthropic-cache-bridge
cd anthropic-cache-bridge
sh ./install.sh --upstream https://api.example.com
```

如果配置已经存在，安装器默认保留现有配置。只有明确要替换上游和缓存设置时
才使用 `--force-config`：

```bash
sh ./install.sh --upstream https://api.example.com --force-config
```

已经安装过的机器可以直接执行 `sh ./install.sh` 重新安装程序，同时保留现有配置。

如果上游需要稳定路由，可设置一个不敏感的固定 ID：

```bash
sh ./install.sh \
  --upstream https://api.example.com \
  --affinity-id my-local-cache-route
```

然后把客户端 Anthropic base URL 改为：

```text
http://127.0.0.1:18787
```

如果客户端要求包含版本路径，则使用：

```text
http://127.0.0.1:18787/v1
```

API Key 继续放在客户端配置中。安装器不会询问或保存 API Key，代理只转发
请求头。

完成后检查：

```bash
~/.local/bin/acbctl doctor
```

OpenCode 和 Cursor++ 示例见 [docs/clients.md](docs/clients.md)。

## 日常管理

```bash
acbctl status
acbctl start
acbctl stop
acbctl restart
acbctl logs
acbctl logs -f
acbctl db
acbctl doctor
acbctl backup
acbctl paths
```

用当前仓库版本更新已安装代理：

```bash
acbctl update .
```

更新命令会校验四个 Python 模块，整体备份并替换，再重启服务。

## 配置

配置文件：

```text
~/.config/anthropic-cache-bridge/config.json
```

示例：

```json
{
  "upstream_url": "https://api.example.com",
  "proxy_port": 18787,
  "enable_prompt_cache": true,
  "enable_thinking_signature": true,
  "cache_ttl": "5m",
  "cache_affinity_user_id": "",
  "signature_ttl": 604800,
  "signature_limit": 2048,
  "signature_db_path": "~/.local/share/anthropic-cache-bridge/signatures.sqlite3",
  "dump_requests": false
}
```

`cache_ttl` 支持 `5m` 和 `1h`。上游是否支持 `1h` 以及对应计费方式，需要
按具体 Anthropic 兼容接口确认。

## 验证方法

Prompt Cache 模块验证：

1. 发送一轮足够长的请求；
2. 在缓存 TTL 内继续同一会话；
3. 查看客户端 usage 中的 cache read 是否大于 0。

Thinking Signature 模块验证：

1. 新建启用 thinking 的会话并发送第一轮；
2. 在日志中确认 `signatures_captured=1`；
3. 执行 `acbctl restart`；
4. 在同一会话发送下一轮；
5. 确认 `signatures_restored=1` 后出现 `RESP 200`。

## 安全和限制

- 代理只监听 `127.0.0.1`，不要直接暴露到公网。
- API Key 不会落盘，但会通过本地代理请求头转发。
- SQLite 只保存 signature、模型名和 thinking 文本哈希，不保存 thinking 原文；
  文件权限为 `600`。
- 请求体 dump 默认关闭。开启后可能把 prompt、工具参数、源代码等敏感内容
  写入磁盘。
- 当前实现会先缓冲完整响应再返回，包括 SSE；这有利于可靠捕获 signature，
  但超长响应的流式体验可能下降。
- 本地无法生成合法 signature。只有代理曾观察到相同模型和相同 thinking 文本
  对应的真实签名时，才能恢复。
- prompt cache TTL 与 signature 保存时间相互独立。缓存过期通常只降低命中；
  signature 缺失可能直接触发 400。
- 不同上游的 Anthropic 兼容程度不同，正式使用前应验证缓存统计和 thinking
  行为。

更多内容见 [docs/architecture.md](docs/architecture.md)、
[docs/troubleshooting.md](docs/troubleshooting.md) 和 [SECURITY.md](SECURITY.md)。

## 测试

```bash
sh ./tests/run.sh
```

测试只使用临时文件，不会请求外部 API。

## 卸载

```bash
sh ./uninstall.sh
```

卸载会移除服务和程序，但保留配置、日志、备份和 signature 数据库。确认不再
需要这些数据后再手动删除。

## License

MIT
