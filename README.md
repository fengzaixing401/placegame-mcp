# PlaceGame MCP

面向 PlaceGame 网页游戏的自托管账号托管服务：在服务端加密保存多个游戏账号的凭据，
通过网页控制台做日常运维，并以 [MCP](https://modelcontextprotocol.io/)（Model Context
Protocol）工具的形式把账号状态与挂机收益预览暴露给 AI 客户端。

## 当前能力

- **多账号凭据托管**：支持「用户名 + 密码」和「仅会话令牌」两种认证方式。凭据用
  AES-GCM 加密后落库，创建后不再回显。
- **网页控制台**（中文）：管理员密码登录，账号的增删改查、启用/停用、暂停/恢复，
  以及查看账号状态与挂机预览。
- **MCP 只读工具**：`accounts_list`、`account_status`、`idle_preview`。
- **审计与策略**：操作写入审计事件，账号级并发锁避免同一账号被并行操作。

### 尚未开放的能力

游戏侧接口契约当前状态是 `live_contract_unverified`（见
[docs/contracts/placegame-idle-contract-status.md](docs/contracts/placegame-idle-contract-status.md)）：
`tests/fixtures/game/v1` 下的样本是人工构造的最小 schema，不是真实抓包。

因此**挂机收益领取（`idle_execute`）只在 `TEST_MODE=true` 下注册**，正式模式的 MCP
只暴露上面三个只读工具。任何会改变游戏状态的操作都要先完成一次经脱敏与审阅的真实
契约抓包。

## 快速开始（Docker）

需要 Docker 与 Docker Compose v2。

```bash
git clone https://github.com/fengzaixing401/placegame-mcp.git
cd placegame-mcp
cp .env.docker.example .env.docker
```

按 `.env.docker` 里的注释生成并填入三个值：

```bash
# PostgreSQL 密码
openssl rand -hex 24

# 主密钥 PLACEGAME_MASTER_KEY_B64 与 MCP 令牌 PLACEGAME_MCP_TOKEN 各生成一个
python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"
```

然后启动：

```bash
docker compose --env-file .env.docker up -d --build
```

`migrate` 服务会先跑完 Alembic 迁移，`app` 才会启动。就绪后访问
<http://127.0.0.1:18080>，首次打开会要求设置管理员密码。

管理员密码只在首次运行时设置一次，之后可以在控制台右上角的「修改密码」里更改
（需要输入当前密码）。程序不强制最小长度，只要求非空——它保护的是加密存储的游戏账号
凭据，请自行设置足够强的密码。修改密码会使所有已登录会话立即失效，包括当前这个。

```bash
docker compose --env-file .env.docker logs -f app   # 查看日志
docker compose --env-file .env.docker down          # 停止（保留数据卷）
docker compose --env-file .env.docker down -v       # 停止并删除数据库数据
```

> `--env-file` 必须显式指定。根目录的 `.env` 是应用自身的配置文件，只接受
> `Settings` 中已定义的字段；把 `POSTGRES_PASSWORD` 这类仅供 compose 变量替换的值写进
> `.env`，之后在本地直接运行应用或跑测试会因为存在未知字段而启动失败。

生产部署另有一套配置：[deploy/compose.yaml](deploy/compose.yaml) 使用预构建的 GHCR
镜像和文件型 secrets，配合 [deploy/README.md](deploy/README.md) 使用。

## 配置项

配置通过环境变量或根目录 `.env` 读入（见 [.env.example](.env.example)）。常用项：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://placegame:placegame@postgres:5432/placegame` | 数据库地址 |
| `PLACEGAME_DATABASE_URL_FILE` | 空 | 从文件读取数据库地址，优先级高于上一项 |
| `PLACEGAME_MASTER_KEY_B64` | 空 | 凭据加密主密钥，URL-safe base64，解码后须为 32 字节 |
| `PLACEGAME_MASTER_KEY_FILE` | `/run/secrets/placegame_master_key` | 主密钥文件路径，`_B64` 未设置时使用 |
| `PLACEGAME_MCP_TOKEN` | 空 | MCP 静态 Bearer 令牌，43 位 URL-safe 字符 |
| `PLACEGAME_MCP_TOKEN_FILE` | `/run/secrets/placegame_mcp_token` | 令牌文件路径，上一项未设置时使用 |
| `PLACEGAME_MCP_ALLOWED_HOSTS` | `["127.0.0.1:*","localhost:*","[::1]:*"]` | MCP 端点允许的 Host，防 DNS rebinding |
| `PLACEGAME_ADMIN_COOKIE_SECURE` | `true` | 管理会话 cookie 是否仅限 HTTPS；通过 HTTP 访问时须设为 `false` |
| `GAME_BASE_URL` | `https://game.placegame.cn` | 正式模式下不可改为其它地址 |
| `TEST_MODE` | `false` | 置 `true` 时 `GAME_BASE_URL` 必须指向回环地址 |
| `SCHEDULER_LEASE_SECONDS` | `30` | 调度租约时长 |
| `MAX_ACCOUNT_CONCURRENCY` | `4` | 跨账号的最大并发数 |
| `AUDIT_RETENTION_DAYS` | `90` | 审计事件保留天数 |

## 对外端点

| 路径 | 说明 |
| --- | --- |
| `/` | 网页控制台，Cookie 会话认证 |
| `/api/admin/v1/*` | 控制台后端 API，同源 Cookie 认证 |
| `/mcp` | MCP 端点，仅接受 `Authorization: Bearer <PLACEGAME_MCP_TOKEN>` |
| `/health/live`、`/health/ready` | 存活与就绪探针 |

MCP 与控制台的认证边界是隔离的：控制台的会话 Cookie 不能用于访问 `/mcp`，反之亦然。

## 本地开发

需要 Python 3.12 与 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
uv run pytest -q          # 单元测试
uv run pyright            # 类型检查
```

集成测试需要一个 PostgreSQL 16。有 Docker 时由 testcontainers 自动拉起；没有 Docker
则要显式指定，否则相关用例会全部跳过：

```bash
PLACEGAME_TEST_DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:5432/placegame_test uv run pytest -q
```

> 只跑单元测试很容易得到虚假的「全绿」——集成用例覆盖了事务、租约与迁移路径，
> 提交前请确认它们确实执行了（输出中 skipped 为 0）。

数据库迁移：

```bash
uv run alembic upgrade head
```

## 项目结构

```
src/placegame/
  accounts/     账号仓储与凭据加解密
  admin/        控制台后端 API 与管理员认证
  application/  用例层：账号状态查询、挂机计划
  game/         游戏 HTTP 客户端与契约映射
  mcp/           MCP 适配器与 Bearer 认证中间件
  policy/       策略存储
  security/     加密与脱敏
  web/          控制台静态资源（index.html / app.js / style.css）
migrations/     Alembic 迁移
deploy/         生产部署（compose、部署脚本）
docs/           设计文档、实施计划与契约状态
tests/          unit / integration / contract / deployment
```

## 安全边界

- 凭据、会话令牌、主密钥、MCP 令牌不会出现在日志、审计 JSON、API 响应或测试输出中。
- 应用默认只监听回环地址，对外暴露必须经 HTTPS 反向代理，并把
  `PLACEGAME_ADMIN_COOKIE_SECURE` 设回 `true`。
- `.env`、`.env.docker` 等含密钥的文件已在 `.gitignore` 中忽略，请勿提交。
- 正式模式下游戏地址被固定为 `https://game.placegame.cn`，不能指向任意主机。
