from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from pathlib import Path
import sqlite3, json, re, os, subprocess

app = FastAPI()

BASE_DIR     = Path(__file__).parent
DB_PATH      = Path.home() / ".baibao" / "baibao.db"
REPORT_DIR   = Path.home() / "Desktop" / "quant_trading" / "reports"
UPLOAD_DIR   = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app.mount("/static",  StaticFiles(directory=BASE_DIR / "static"),  name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR),           name="uploads")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

FEATURES = [
    {"title": "股票分析",       "url": "/stock",   "icon": "📈", "description": "每日复盘报告可视化",   "status": "active"},
    {"title": "录音转会议纪要", "url": "/audio",   "icon": "🎙️", "description": "上传录音自动生成纪要", "status": "active"},
    {"title": "一图一表",       "url": "/chart",   "icon": "🗂️", "description": "可编辑业务流程图",     "status": "active"},
    {"title": "待办管理",       "url": "/tasks",   "icon": "📝", "description": "快速登记和管理待办",   "status": "active"},
    {"title": "数据标准",       "url": "#",        "icon": "📐", "description": "数据标准化配置与管理",   "status": "coming_soon"},
]


# ── 数据库 ────────────────────────────────────────────────

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT    NOT NULL,
            note         TEXT,
            is_recurring INTEGER,
            task_date    TEXT,
            status       TEXT,
            done_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS charts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL,
            data_json  TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()
    conn.close()

init_db()

def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    row  = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    conn.commit()
    conn.close()


# ── 页面路由 ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "features": FEATURES})

@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request):
    return templates.TemplateResponse("tasks.html", {"request": request})

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "qwen_key": get_setting("qwen_api_key"),
        "qwen_model": get_setting("qwen_audio_model", "qwen-audio-turbo"),
    })

@app.post("/settings")
async def save_settings(
    qwen_api_key:    str = Form(""),
    qwen_audio_model: str = Form("qwen-audio-turbo"),
):
    set_setting("qwen_api_key", qwen_api_key)
    set_setting("qwen_audio_model", qwen_audio_model)
    return JSONResponse({"ok": True})

@app.get("/stock", response_class=HTMLResponse)
def stock_page(request: Request):
    reports = sorted(REPORT_DIR.glob("*_report.md"), reverse=True) if REPORT_DIR.exists() else []
    dates   = [r.stem.replace("_report", "") for r in reports]
    return templates.TemplateResponse("stock.html", {"request": request, "dates": dates})

@app.get("/audio", response_class=HTMLResponse)
def audio_page(request: Request):
    return templates.TemplateResponse("audio.html", {"request": request})

@app.get("/chart", response_class=HTMLResponse)
def chart_page(request: Request):
    conn   = get_db()
    charts = [dict(r) for r in conn.execute("SELECT id,title,updated_at FROM charts ORDER BY updated_at DESC").fetchall()]
    conn.close()
    return templates.TemplateResponse("chart.html", {"request": request, "charts": charts})


# ── 股票分析 API ──────────────────────────────────────────

QUANT_DIR  = Path.home() / "Desktop" / "quant_trading"
QUANT_VENV = Path("/Users/chengyu/PycharmProjects/PythonProject/.venv/bin/python")

@app.post("/api/stock/refresh")
async def refresh_stock_report():
    try:
        result = subprocess.run(
            [str(QUANT_VENV), "main.py"],
            cwd=str(QUANT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return JSONResponse({"error": result.stderr[-500:] or "运行失败"}, status_code=500)
        # 返回最新生成的日期列表
        reports = sorted(REPORT_DIR.glob("*_report.md"), reverse=True) if REPORT_DIR.exists() else []
        dates   = [r.stem.replace("_report", "") for r in reports]
        return JSONResponse({"ok": True, "dates": dates, "latest": dates[0] if dates else None})
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "分析超时（>120s）"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/stock/report")
def get_stock_report(date: str):
    path = REPORT_DIR / f"{date}_report.md"
    if not path.exists():
        return JSONResponse({"error": "报告不存在"}, status_code=404)
    md   = path.read_text(encoding="utf-8")
    return JSONResponse({"date": date, "stocks": _parse_report(md)})

def _parse_report(md: str) -> list:
    stocks = []
    # 按股票分段（### N. 名称 (代码) emoji）
    blocks = re.split(r"\n(?=### \d+\.)", md)
    for block in blocks:
        m = re.match(r"### \d+\.\s+(.+?)\s+\((\d+)\)\s*([🟢🟡🔴]?)", block)
        if not m:
            continue
        name, code, emoji = m.group(1), m.group(2), m.group(3)

        prob_m   = re.search(r"明日上涨概率.*?\*\*(\d+\.?\d*)%\*\*", block)
        price_m  = re.search(r"收盘价\*\*:\s*([\d.]+)元", block)
        change_m = re.search(r"涨跌\*\*:\s*([▲▼])([\d.]+)%", block)
        prob     = float(prob_m.group(1))  if prob_m  else 0
        price    = float(price_m.group(1)) if price_m else 0
        change_dir = change_m.group(1)    if change_m else ""
        change_val = float(change_m.group(2)) if change_m else 0
        change_pct = change_val if change_dir == "▲" else -change_val

        # 指标表格
        indicators = {}
        for row in re.finditer(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", block):
            key, val, sig = row.group(1), row.group(2), row.group(3)
            if key not in ("指标", "---", "数值"):
                indicators[key] = {"value": val.strip(), "signal": sig.strip()}

        # 综合评分
        scores = {}
        for sm in re.finditer(r"- (.+?): [★☆]+\s*\((\d+)\)", block):
            scores[sm.group(1)] = int(sm.group(2))

        # 技术信号
        sig_m  = re.search(r"\*\*技术信号\*\*:\s*(.+)", block)
        signals = [s.strip() for s in sig_m.group(1).split(",")] if sig_m else []

        # 筹码
        chip = {}
        for cm in re.finditer(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|", block):
            if cm.group(1) in ("70%筹码区间","筹码宽度","获利比例","15日收敛趋势","筹码信号"):
                chip[cm.group(1)] = cm.group(2).strip()
        bar_m = re.search(r"> 15日宽度变化趋势：(.+)", block)
        chip["trend_bar"] = bar_m.group(1).strip() if bar_m else ""

        stocks.append({
            "name": name, "code": code, "emoji": emoji,
            "probability": prob, "price": price, "change_pct": change_pct,
            "indicators": indicators, "scores": scores,
            "signals": [s for s in signals if s and s != "无明显信号"],
            "chip": chip,
        })
    return stocks


# ── 录音转会议纪要 API ────────────────────────────────────

@app.post("/api/audio/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    api_key = get_setting("qwen_api_key")
    if not api_key:
        return JSONResponse({"error": "请先在设置页配置 Qwen API Key"}, status_code=400)

    # 保存上传文件
    suffix  = Path(file.filename).suffix
    save_path = UPLOAD_DIR / f"audio_{datetime.now().strftime('%Y%m%d%H%M%S')}{suffix}"
    content = await file.read()
    save_path.write_bytes(content)

    try:
        import base64
        audio_b64  = base64.b64encode(content).decode()
        audio_url  = f"data:audio/mpeg;base64,{audio_b64}"
        model      = get_setting("qwen_audio_model", "qwen-audio-turbo")

        import urllib.request, json as _json
        payload = _json.dumps({
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "audio_url", "audio_url": {"url": audio_url}},
                    {"type": "text", "text": (
                        "请将这段录音转写并整理成会议纪要，格式如下：\n"
                        "## 会议纪要\n"
                        "**日期**：（从内容推断，无法确认请留空）\n"
                        "**参会人**：（从内容推断）\n"
                        "**主题**：（一句话概括）\n\n"
                        "### 一、主要讨论内容\n"
                        "（分条列出关键议题和讨论结果）\n\n"
                        "### 二、决议事项\n"
                        "（列出明确的决定和行动项，注明负责人和时间节点）\n\n"
                        "### 三、待跟进事项\n"
                        "（列出尚未确定、需要后续跟进的内容）"
                    )}
                ]
            }]
        }).encode()

        req = urllib.request.Request(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = _json.loads(resp.read())

        minutes = result["choices"][0]["message"]["content"]
        return JSONResponse({"minutes": minutes, "filename": file.filename})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        save_path.unlink(missing_ok=True)


# ── 一图一表 API ──────────────────────────────────────────

class ChartData(BaseModel):
    title:     str
    data_json: str  # JSON string of nodes+edges

@app.get("/api/charts")
def list_charts():
    conn   = get_db()
    charts = [dict(r) for r in conn.execute("SELECT id,title,updated_at FROM charts ORDER BY updated_at DESC").fetchall()]
    conn.close()
    return charts

@app.get("/api/charts/{chart_id}")
def get_chart(chart_id: int):
    conn = get_db()
    row  = conn.execute("SELECT * FROM charts WHERE id=?", (chart_id,)).fetchone()
    conn.close()
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    return dict(row)

@app.post("/api/charts")
def create_chart(data: ChartData):
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    cur  = conn.execute(
        "INSERT INTO charts(title,data_json,created_at,updated_at) VALUES(?,?,?,?)",
        (data.title, data.data_json, now, now)
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"id": new_id, "title": data.title, "updated_at": now}

@app.put("/api/charts/{chart_id}")
def update_chart(chart_id: int, data: ChartData):
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute(
        "UPDATE charts SET title=?, data_json=?, updated_at=? WHERE id=?",
        (data.title, data.data_json, now, chart_id)
    )
    conn.commit()
    conn.close()
    return {"id": chart_id, "title": data.title, "updated_at": now}

@app.delete("/api/charts/{chart_id}")
def delete_chart(chart_id: int):
    conn = get_db()
    conn.execute("DELETE FROM charts WHERE id=?", (chart_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── 任务 API ──────────────────────────────────────────────

class TaskBase(BaseModel):
    title:        str
    note:         Optional[str] = None
    is_recurring: int
    task_date:    str

class Task(TaskBase):
    id:      int
    status:  str
    done_at: Optional[str] = None

@app.get("/api/tasks", response_model=list[Task])
def read_tasks(date: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT id,title,note,is_recurring,task_date,status,done_at FROM tasks WHERE task_date=?", (date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/tasks/dates")
def read_task_dates():
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT task_date FROM tasks ORDER BY task_date DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/tasks", response_model=Task)
def create_task(task: TaskBase):
    conn = get_db()
    cur  = conn.execute(
        "INSERT INTO tasks(title,note,is_recurring,task_date,status,done_at) VALUES(?,?,?,?,?,?)",
        (task.title, task.note, task.is_recurring, task.task_date, "todo", None)
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {**task.dict(), "id": new_id, "status": "todo", "done_at": None}

@app.put("/api/tasks/{task_id}/status")
def update_task_status(task_id: int, body: dict):
    status  = body["status"]
    done_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "done" else None
    conn    = get_db()
    conn.execute("UPDATE tasks SET status=?, done_at=? WHERE id=?", (status, done_at, task_id))
    conn.commit()
    conn.close()
    return {"id": task_id, "status": status, "done_at": done_at}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    return {"ok": True}
