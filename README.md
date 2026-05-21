# ai-chat

单用户、自托管的 AI 聊天网站。

## Tech

- FastAPI
- React + Vite
- MySQL
- OpenAI-compatible providers

## Current Status

当前已实现一版可用的单用户聊天流程：

- `admin` 登录
- 会话列表、新建、删除
- 消息持久化
- OpenAI-compatible API Key 管理
- 流式聊天回复

还未完成的主要工作：

- 构建后的单服务托管验证
- 更完整的测试
- 正式多用户流程

## Project Structure

- `backend/`: FastAPI、数据库模型、Alembic、API 路由
- `frontend/`: React + Vite 前端
- `docs/`: 备忘和开发记录

## Requirements

- MySQL 8+
- Node.js / npm
- 项目根目录下已有 `.venv`

当前仓库里的 `.venv` 已安装后端依赖，可以直接使用：

- `D:\websites\ai-chat\.venv\Scripts\python.exe`
- `D:\websites\ai-chat\.venv\Scripts\uvicorn.exe`
- `D:\websites\ai-chat\.venv\Scripts\alembic.exe`

## Backend Config

后端读取 [backend/.env](/D:/websites/ai-chat/backend/.env:1)。

至少需要这些配置：

```env
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=8000

DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=ai_chat
DB_USER=aichat
DB_PASSWORD=change_me

JWT_SECRET=change-this-jwt-secret
JWT_EXPIRE_DAYS=7
KEY_ENCRYPTION_SECRET=change-this-key-secret

LOGIN_PASSWORD_HASH=replace-me

DEFAULT_PROVIDER=openai
DEFAULT_MODEL=gpt-4.1-mini
DEFAULT_TEMPERATURE=0.7
DEFAULT_MAX_TOKENS=2000
```

## Reset Admin Password

生成新的 `LOGIN_PASSWORD_HASH`：

```powershell
cd D:\websites\ai-chat\backend
$env:PYTHONPATH=(Get-Location).Path
D:\websites\ai-chat\.venv\Scripts\python.exe scripts\generate_password_hash.py
```

把输出的 hash 填回 [backend/.env](/D:/websites/ai-chat/backend/.env:1) 的 `LOGIN_PASSWORD_HASH`，然后重启后端。

注意：

- 当前逻辑只会在用户表为空时初始化 `admin`
- 如果数据库里已经有用户，重启后端不会再覆盖现有 `admin` 密码
- 想重置密码时，需要你手动把数据库里的 `admin.password_hash` 改成新的 hash，或者后续补一个改密码流程

## Database

初始化库和用户可参考 [init_database.sql](/D:/websites/ai-chat/backend/sql/init_database.sql:1)。

如果库已存在但表还没同步，执行：

```powershell
cd D:\websites\ai-chat\backend
D:\websites\ai-chat\.venv\Scripts\alembic.exe upgrade head
```

## Development Run

### 1. Start Backend

```powershell
cd D:\websites\ai-chat\backend
D:\websites\ai-chat\.venv\Scripts\uvicorn.exe app.main:app --reload --host 127.0.0.1 --port 8000
```

### 2. Start Frontend

```powershell
cd D:\websites\ai-chat\frontend
npm run dev
```

### 3. Open the App

浏览器打开：

```text
http://127.0.0.1:5173
```

## First Login

- 用户名固定是 `admin`
- 首次初始化时，密码是你为 `LOGIN_PASSWORD_HASH` 生成 hash 时输入的明文密码

## Configure API Key

登录后，在侧边栏打开 `管理 API Keys`，新增一条：

- `provider`: `openai`
- `display_name`: 任意，例如 `OpenAI`
- `base_url`: 官方 OpenAI 留空；第三方兼容服务填它的 API 根地址，通常以 `/v1` 结尾
- `api_key`: 你的实际 key

保存后可以先点“测试”，成功后再发消息。

## Build Frontend

```powershell
cd D:\websites\ai-chat\frontend
npm run build
```

当前构建产物输出到 `backend/static`。开发模式已验证可用；构建后单独由 FastAPI 直接托管前端产物的流程，后续还需要再做一次完整确认。

## Notes

- `messages` 表没有 `user_id`，但通过 `conversation_id -> conversations.user_id` 可以关联到用户，当前结构可继续扩展。
- 项目现在是单用户优先，多用户注册、改密、管理员初始化收敛都还没做完。
