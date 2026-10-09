# ai-chat

单用户、自托管的 AI 聊天网站，支持流式回复、消息分支和可选的长期记忆。前端使用 React + Vite，后端使用 FastAPI 和 MySQL。

## 功能概览

- `admin` 单账户登录、会话的新建与删除，以及消息持久化。
- OpenAI、Anthropic 以及自定义服务商配置、连通性测试、模型切换与默认模型保存。自定义服务商支持 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages 协议，可填写 API Base URL 并手动录入模型 ID。
- 流式回复；消息可复制、编辑，或从编辑处创建新的对话分支。
- 回答可重新生成并在 sibling 版本间切换；可从 AI 消息创建、打开、重命名、归档或删除分支。
- 分支树形展示和可视化消息树；分支面板支持即时发送、流式状态与宽度拖动。
- 长对话游标分页：首次加载最新 40 条，向上滚动按需加载更早消息，并保持阅读位置。
- 设置中心提供账户、外观、服务商和默认模型配置；支持日间/夜间模式和主题色。
- 可选 MCP 长期记忆：OpenAI 与 Anthropic 模型可按需检索或写入记忆，聊天中会展示可展开的工具调用轨迹。

## 技术栈

- FastAPI、SQLAlchemy、Alembic、MySQL
- React、Vite、Tailwind CSS
- OpenAI Chat Completions / Responses、Anthropic native provider
- 可选：兼容 Streamable HTTP 的 MCP 记忆服务（开发记录中使用 Ombre Brain）

## 项目结构

- `backend/`：FastAPI 应用、数据库模型、Alembic 迁移、API 路由和 provider 实现
- `frontend/`：React + Vite 前端
- `docs/local/devdiary.md`：功能演进和开发记录

## 环境要求

- MySQL 8+
- Node.js 与 npm
- Python 3.11+（或项目根目录已有的 `.venv`）
- 可选：长期记忆需要另行运行 MCP 服务

仓库中的虚拟环境已安装后端依赖，可直接使用：

- `D:\websites\ai-chat\.venv\Scripts\python.exe`
- `D:\websites\ai-chat\.venv\Scripts\uvicorn.exe`
- `D:\websites\ai-chat\.venv\Scripts\alembic.exe`

## 配置后端

复制 [`backend/.env.example`](backend/.env.example) 为 `backend/.env` 后按实际环境修改：

```env
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=10000

DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=ai_chat
DB_USER=aichat
DB_PASSWORD=change_me

JWT_SECRET=replace_with_a_long_random_string
JWT_EXPIRE_DAYS=7
KEY_ENCRYPTION_SECRET=replace_with_a_second_long_random_string
LOGIN_PASSWORD_HASH=replace_with_a_real_bcrypt_hash

DEFAULT_PROVIDER=openai
DEFAULT_MODEL=gpt-4.1-mini
DEFAULT_TEMPERATURE=0.7
DEFAULT_MAX_TOKENS=2000
```

`LOGIN_PASSWORD_HASH`、`JWT_SECRET` 和 `KEY_ENCRYPTION_SECRET` 不应使用示例值，也不应提交到仓库。

### 可选：启用长期记忆

先部署可用的 Streamable HTTP MCP 记忆服务，再在 `backend/.env` 中配置：

```env
MEMORY_ENABLED=true
MEMORY_MCP_URL=http://127.0.0.1:8001/mcp
MEMORY_TIMEOUT_SECONDS=20
MEMORY_WRITE_TIMEOUT_SECONDS=15
MEMORY_MAX_CONTEXT_CHARS=3000
MEMORY_WRITE_MAX_CHARS=6000
```

未部署记忆服务时保持 `MEMORY_ENABLED=false`，不会影响普通聊天。

## 初始化

### 1. 生成管理员密码哈希

```powershell
cd D:\websites\ai-chat\backend
$env:PYTHONPATH=(Get-Location).Path
D:\websites\ai-chat\.venv\Scripts\python.exe scripts\generate_password_hash.py
```

将输出填入 `backend/.env` 的 `LOGIN_PASSWORD_HASH`。仅当用户表为空时，应用启动会初始化 `admin`；已有用户不会被启动流程覆盖。

### 2. 初始化数据库

创建数据库与账号可参考 [`backend/sql/init_database.sql`](backend/sql/init_database.sql)。随后执行迁移：

```powershell
cd D:\websites\ai-chat\backend
D:\websites\ai-chat\.venv\Scripts\alembic.exe upgrade head
```

## 本地开发

### 1. 启动后端

```powershell
cd D:\websites\ai-chat\backend
D:\websites\ai-chat\.venv\Scripts\uvicorn.exe app.main:app --reload --host 127.0.0.1 --port 10000
```

### 2. 安装并启动前端

```powershell
cd D:\websites\ai-chat\frontend
npm ci
npm run dev
```

访问 <http://127.0.0.1:5173>，使用 `admin` 和生成密码哈希时输入的明文密码登录。

## 配置模型服务商

登录后打开“设置” → “服务商”，新增并测试服务商配置。

| 类型 | `provider` | `base_url` |
| --- | --- | --- |
| OpenAI | `openai` | 官方 OpenAI 可留空；使用 Chat Completions 协议的第三方服务填写 API 根地址，通常以 `/v1` 结尾 |
| Anthropic 原生 API | `anthropic` | 官方 Anthropic 可留空；自定义网关填写 Anthropic API 根地址 |

保存成功后，可在“默认模型”中指定新建对话默认使用的服务商和模型。

自定义服务商请选择“自定义服务商”，填写完整的 `http(s)` API Base URL（通常以 `/v1` 结尾）、API Key 和协议。展开服务商卡片后可刷新远端模型，也可以直接输入模型 ID；手动模型适用于不提供 `/models` 列表接口的兼容网关。

## 构建与验证

```powershell
cd D:\websites\ai-chat\frontend
npm run build
```

前端产物输出到 `backend/static`，FastAPI 会在该目录存在时托管 SPA 静态文件。

常用验证命令：

```powershell
cd D:\websites\ai-chat\backend
D:\websites\ai-chat\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
D:\websites\ai-chat\.venv\Scripts\python.exe -m unittest discover -s tests
```

```powershell
cd D:\websites\ai-chat\frontend
npm run lint
npm run build
```

## 图片上传与视觉聊天部署

上传支持 JPG/PNG，单图 1 MiB 和 2500 万像素，每条消息合计 8 MiB 和 5000 万像素。图片存入 `backend/data/uploads`（`LOCAL_UPLOAD_DIR` 相对 `backend` 解析），该目录必须持久化，并放在静态文件根目录之外。图片通过登录鉴权的预览接口访问，不应在 Nginx 或其他 Web 服务器配置公开目录映射。备份数据库时同步备份图片目录；永久保存不会代替运维备份。

首次启用前执行 `alembic upgrade head` 创建附件表。此版本运行保护、取消及中断恢复按**单 backend worker**部署：生产启动使用 `uvicorn app.main:app --workers 1`；多 worker 不在本次验证范围内。迁移回滚使用 `alembic downgrade 0012_projects`，会删除附件关系及元数据表，但不会自动删除磁盘文件；应先备份数据库和上传目录，并停用图片功能，再回滚。

默认保留 256 MiB 磁盘安全余量；低于余量时拒绝上传。每分钟最多上传 30 次，解码并发 1、超时 10 秒、解码峰值预算 256 MiB。模型图片缓冲另有 256 MiB 进程驻留预算，编码并发 1，三个 adapter 的请求体默认上限均为 16 MiB，图片请求超时 90 秒。全部参数见 `backend/.env.example`。这些预算按单 worker 计算，选择视觉模型后仍需在实际 provider 上验证其图片及上下文限制。

完整历史上下文也受适配器的保守图片预算约束：OpenAI Chat Completions 和 Responses 默认最多 500 张；Anthropic Messages 默认最多 100 张且图片任一边不超过 8000 像素。各适配器可分别通过 `*_MAX_IMAGES` 和 `*_MAX_IMAGE_DIMENSION` 调整；尺寸配置为 0 表示仅使用上传阶段的像素保护。超限时在读取图片文件前返回 `413 VISION_CONTEXT_LIMIT`，不会删减图片。具体模型的视觉支持、图片数量、尺寸和 token 上下文限制由使用者手动验证；未能本地判断的限制会返回上游模型错误，原消息和图片引用仍保留以供调整后重试。

应用在 multipart 解析前执行完整请求体 1.5 MiB 上限，读取体的总超时为 30 秒；反向代理也应设置 `client_max_body_size 1536k;`（上传路由），避免超大请求到达应用。代理的 413 页面可以不同于应用 JSON 错误，但应保持 HTTP 413 状态；应用独立检查单文件 1 MiB 上限。部署方应监控上传目录所在磁盘的余量和 `STORAGE_FULL`/`STORAGE_ERROR` 错误。

清理由应用 lifespan 每 5 分钟调度，先恢复中断运行再开始扫描。未发送附件 24 小时后清理；已关联图片不按年龄删除。显式删除失败保留墓碑，需用户手动重试；消息删除、替换和孤儿清理失败会自动退避重试。清理者使用同一连接持有 MySQL advisory lock，临时文件与落盘未入库的孤儿文件只有超过 TTL 才删除。上传目录及其临时目录必须处于同一文件系统，以支持原子落盘。

## 当前范围

项目目前以单用户自托管为目标：没有注册、多用户权限管理或完整的改密流程。分支管理、记忆服务与第三方模型服务均应在上线前结合实际 provider 和浏览器环境完成联调。
