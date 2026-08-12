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
D:\websites\ai-chat\.venv\Scripts\python.exe -m unittest discover -s tests
```

```powershell
cd D:\websites\ai-chat\frontend
npm run lint
npm run build
```

## 当前范围

项目目前以单用户自托管为目标：没有注册、多用户权限管理或完整的改密流程。分支管理、记忆服务与第三方模型服务均应在上线前结合实际 provider 和浏览器环境完成联调。
