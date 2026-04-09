# 程钰的百宝箱 — 概要设计文档

## 项目概述

**项目名称：** 程钰的百宝箱  
**类型：** 本地 Web 工具集  
**语言：** Python 3 + HTML/CSS/JS  
**路径：** `/Users/chengyu/PycharmProjects/investment_hub/`  
**启动：** `uvicorn main:app --reload --port 8080`  
**访问：** http://localhost:8080

---

## 技术栈

| 组件 | 说明 |
|---|---|
| FastAPI | Web 框架 |
| Jinja2 | 服务端模板渲染 |
| SQLite | 本地持久化（`~/.baibao/baibao.db`） |
| Pydantic v2 | 数据校验（nullable 字段必须用 `Optional[str] = None`） |
| Python venv | `/Users/chengyu/PycharmProjects/PythonProject/.venv/` |

---

## 目录结构

```
investment_hub/
├── main.py                  # FastAPI 主文件（路由 + API）
├── uploads/                 # 音频上传临时目录（处理后自动清理）
├── static/
│   └── css/style.css        # 全局样式
└── templates/
    ├── index.html           # 首页（功能入口卡片）
    ├── stock.html           # 股票分析页
    ├── audio.html           # 录音转会议纪要页
    ├── chart.html           # 一图一表（流程图编辑器）
    ├── tasks.html           # 待办管理页
    └── settings.html        # 设置页（API Key 配置）
```

---

## 功能模块

### 1. 股票分析 `/stock`
- 读取股神计划报告目录：`~/Desktop/quant_trading/reports/*.md`
- 日期选择器切换报告
- ↻ 刷新按钮：调用 `POST /api/stock/refresh` → 触发股神计划 `main.py` 重新生成报告
- 可视化展示：
  - 排行榜（按上涨概率降序，彩色进度条）
  - 个股详情卡片（五维评分 / 筹码区块 / 技术指标表）

**相关 API：**
```
GET  /api/stock/report?date=YYYYMMDD   # 读取已有报告
POST /api/stock/refresh                 # 触发重新生成
```

### 2. 录音转会议纪要 `/audio`
- 拖拽 / 点击上传音频（MP3 / M4A / WAV / AAC / OGG）
- 调用 Qwen Audio API（dashscope）进行语音识别
- 输出结构化会议纪要（日期 / 参会人 / 主题 / 讨论内容 / 决议 / 待跟进）
- API Key 和模型在 `/settings` 页面配置，存入 SQLite settings 表

**相关 API：**
```
POST /api/audio/transcribe   # 上传音频，返回 minutes 文本
```

**Qwen 配置：**
- Endpoint：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- 模型可选：`qwen-audio-turbo`（快速）/ `qwen-audio-chat`（精准）

### 3. 一图一表 `/chart`
- SVG 画布流程图编辑器（纯前端 JS）
- 拖拽节点 / 双击编辑文字 / 点击连箭头 / 右键删除节点 / 点击线段删除边
- 多图表管理（左侧列表，新建 / 保存 / 删除）
- 数据存储：JSON（nodes + edges）序列化存入 SQLite charts 表

**相关 API：**
```
GET    /api/charts           # 列表
GET    /api/charts/{id}      # 详情
POST   /api/charts           # 新建
PUT    /api/charts/{id}      # 更新
DELETE /api/charts/{id}      # 删除
```

### 4. 待办管理 `/tasks`
- 按日期分组任务（日期选择器）
- 支持新建 / 完成 / 删除
- 支持备注、重复任务标记

**相关 API：**
```
GET    /api/tasks?date=YYYY-MM-DD
POST   /api/tasks
PUT    /api/tasks/{id}/status
DELETE /api/tasks/{id}
GET    /api/tasks/dates
```

---

## 数据库结构（SQLite）

```sql
-- 任务
CREATE TABLE tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    note         TEXT,
    is_recurring INTEGER,
    task_date    TEXT,
    status       TEXT,         -- 'todo' | 'done'
    done_at      TEXT
);

-- 设置（KV 存储）
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- 当前 keys: qwen_api_key, qwen_audio_model

-- 流程图
CREATE TABLE charts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    data_json  TEXT NOT NULL,  -- {"nodes":[{id,x,y,label}], "edges":[{id,from,to}]}
    created_at TEXT,
    updated_at TEXT
);
```

---

## 开发规范

- **端口统一 8080**，启动命令：`uvicorn main:app --reload --port 8080`
- **Pydantic v2**：所有可为 null 的字段必须声明为 `Optional[str] = None`
- **前端 DOM 操作**：统一用 `createElement` / `textContent` / `replaceChildren`，禁止 `innerHTML` 拼接用户数据（防 XSS）
- **新增功能页**：在 `main.py` 的 `FEATURES` 列表加入入口，在 `templates/` 下新建模板，在 `static/css/style.css` 补充所需样式类
- **股神计划集成**：通过 `subprocess.run()` 调用，venv python 路径固定为 `/Users/chengyu/PycharmProjects/PythonProject/.venv/bin/python`
