# Devlog

## May

### 5.11
完成了后端基础功能，配置数据库，配置venv

## June

### 6.2
规划并实现消息树功能的第一版完整基础。

后端新增消息树接口：
- 新增 `GET /api/conversations/{conversation_id}/message-tree`，返回轻量图数据：`nodes`、`edges`、`active_path`、`current_leaf_message_id` 和分支标记。
- 消息树节点只返回摘要和元数据，不返回完整 `content`，避免大对话响应过重。
- 新增 `GET /api/conversations/{conversation_id}/messages/{message_id}`，用于按节点 id 加载完整消息详情。
- `active_path` 使用 `current_leaf_message_id` 沿 `parent_id` 回溯到根，再反转得到，整体复杂度为 O(n)。
- 分支表只作为 marker 使用，不决定消息树结构。

前端新增消息树页面：
- 安装并接入 `@xyflow/react`。
- 新增 `MessageTreePanel.jsx`，实现覆盖式大面板。
- 使用 React Flow 渲染消息节点和 edge，支持缩放、平移、拖拽节点。
- 布局采用纵向消息树：深度决定 y，同父节点 siblings 按时间顺序横向排列。
- 高亮 active path 和 current leaf。
- 支持摘要搜索、fit view、定位 active path、定位选中节点。
- 节点详情栏按 message id 懒加载完整消息内容。
- 节点详情支持“切换到此 path”和“打开分支”。

聊天页集成：
- 顶部标题栏新增 `Network` 图标入口，显示为“消息树”。
- 前端 API 新增 `getMessageTree` 和 `getMessage`。
- `activateMessageBranch` 支持 `exact=true`，消息树里允许切换到任意节点作为 path 终点，同时保持旧的 sibling 切换默认行为不变。
- 主消息刷新后会触发已打开消息树面板刷新。

验证：
- `npm run lint` 通过。
- `npm run build` 通过，仅有 React Flow 引入后 bundle 超过 500KB 的 Vite 提示。
- `..\.venv\Scripts\python.exe -m unittest discover tests` 通过，19 个后端测试全部 OK。
- 本地 dev server 启动后 `http://127.0.0.1:5173` 返回 200。
