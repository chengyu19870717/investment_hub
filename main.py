from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from pathlib import Path
import sqlite3, json, re, os, subprocess
from pypinyin import lazy_pinyin, Style

app = FastAPI()

BASE_DIR     = Path(__file__).parent
DB_PATH      = Path.home() / ".baibao" / "baibao.db"
REPORT_DIR   = Path.home() / "Desktop" / "quant_trading" / "reports"
UPLOAD_DIR   = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app.mount("/static",  StaticFiles(directory=BASE_DIR / "static"),  name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR),           name="uploads")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

@app.get("/data-standard", response_class=HTMLResponse)
def data_standard_page(request: Request):
    return templates.TemplateResponse("data_standard.html", {"request": request})

@app.get("/data-graph", response_class=HTMLResponse)
def data_graph_page(request: Request):
    return templates.TemplateResponse("data_graph.html", {"request": request})

@app.get("/api/pinyin")
def to_pinyin(text: str = ""):
    """将中文转为拼音首字母大写驼峰（用于自动生成字段英文名）"""
    if not text.strip():
        return {"result": ""}
    parts = lazy_pinyin(text.strip(), style=Style.NORMAL)
    result = "_".join(p for p in parts if p.isalpha())
    return {"result": result}

FEATURES = [
    {"title": "股票分析",       "url": "/stock",         "icon": "📈", "description": "每日复盘报告可视化",   "status": "active"},
    {"title": "录音转会议纪要", "url": "/audio",         "icon": "🎙️", "description": "上传录音自动生成纪要", "status": "active"},
    {"title": "一图一表",       "url": "/chart",         "icon": "🗂️", "description": "可编辑业务流程图",     "status": "active"},
    {"title": "待办管理",       "url": "/tasks",         "icon": "📝", "description": "快速登记和管理待办",   "status": "active"},
    {"title": "数据标准",       "url": "/data-standard",  "icon": "📐", "description": "数据标准化配置与管理",   "status": "active"},
    {"title": "量化参数",       "url": "/quant-params",   "icon": "⚖️", "description": "因子权重配置与管理",     "status": "active"},
    {"title": "代理网关",       "url": "/proxy",          "icon": "🌐", "description": "一键开关系统代理服务",   "status": "active"},
]

# ── 代理配置 ──────────────────────────────────────────────
PROXY_IFACE  = "Wi-Fi"          # 网络接口名称
PROXY_HOST   = "127.0.0.1"
PROXY_PORT   = 9981


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
        CREATE TABLE IF NOT EXISTS products (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id      TEXT UNIQUE NOT NULL,
            product_name    TEXT NOT NULL,
            product_desc    TEXT,
            product_manager TEXT,
            biz_contact     TEXT,
            biz_dept        TEXT,
            chart_data      TEXT,  -- JSON: {steps:[{id,name,desc}]}
            created_at      TEXT,
            updated_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS charts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL,
            data_json  TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
    """)

    # 数据标准模块 — 兼容旧表迁移
    cols_rules = [r[1] for r in conn.execute("PRAGMA table_info(rules)").fetchall()]
    if not cols_rules:
        conn.execute("""
            CREATE TABLE rules (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                description     TEXT,
                input_json      TEXT,
                output_json     TEXT,
                created_at      TEXT,
                updated_at      TEXT
            )
        """)
    elif "input_json" not in cols_rules:
        conn.execute("ALTER TABLE rules ADD COLUMN input_json TEXT")
        conn.execute("ALTER TABLE rules ADD COLUMN output_json TEXT")
        conn.execute("ALTER TABLE rules ADD COLUMN created_at TEXT")
        conn.execute("ALTER TABLE rules ADD COLUMN updated_at TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_roots (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            meaning         TEXT,
            root_type       TEXT,
            length          INTEGER,
            code_values     TEXT,
            remark          TEXT,
            created_at      TEXT,
            updated_at      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_fields (
            id              TEXT PRIMARY KEY,
            name_en         TEXT NOT NULL,
            name_cn         TEXT,
            meaning         TEXT,
            root_id         TEXT,
            root_name       TEXT,
            field_type      TEXT,
            length          INTEGER,
            code_values     TEXT,
            remark          TEXT,
            created_at      TEXT,
            updated_at      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS interfaces (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            description     TEXT,
            input_json      TEXT,
            output_json     TEXT,
            created_at      TEXT,
            updated_at      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS field_rules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id        TEXT,
            rule_id         TEXT,
            created_at      TEXT
        )
    """)

    # 量化参数 — 因子权重配置
    conn.execute("""
        CREATE TABLE IF NOT EXISTS factor_weights (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            factor_key      TEXT UNIQUE NOT NULL,
            factor_name     TEXT NOT NULL,
            weight          REAL NOT NULL DEFAULT 0,
            description     TEXT,
            is_active       INTEGER DEFAULT 1,
            created_at      TEXT,
            updated_at      TEXT
        )
    """)

    # 初始化默认因子权重（来自股神计划 ai_scorer.py）
    existing_factors = conn.execute("SELECT COUNT(*) FROM factor_weights").fetchone()[0]
    if existing_factors == 0:
        now = datetime.now().isoformat()
        defaults = [
            ("technical",   "技术面", 0.30, "技术指标评分（MA/MACD/KDJ/布林带等）"),
            ("fundamental", "基本面", 0.20, "基本面指标评分（毛利率/ROE/营收增长/PE等）"),
            ("money_flow",  "资金面", 0.20, "资金流向评分（主力净流入/流通市值）"),
            ("sentiment",   "情绪面", 0.15, "市场情绪评分（换手率/量比/涨跌幅）"),
            ("chip",        "筹码面", 0.15, "筹码分布评分（筹码密集/获利比/筹码宽度）"),
        ]
        for key, name, w, desc in defaults:
            conn.execute(
                "INSERT INTO factor_weights(factor_key,factor_name,weight,description,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (key, name, w, desc, now, now),
            )

    # 因子细项参数表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS factor_sub_params (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            factor_key  TEXT NOT NULL,
            param_key   TEXT NOT NULL,
            param_name  TEXT NOT NULL,
            param_value REAL NOT NULL,
            description TEXT,
            UNIQUE(factor_key, param_key)
        )
    """)

    # 初始化默认细项参数（对应 ai_scorer.py 硬编码值）
    existing_sub = conn.execute("SELECT COUNT(*) FROM factor_sub_params").fetchone()[0]
    if existing_sub == 0:
        sub_defaults = [
            # 技术面
            ("technical", "signal_score",       "信号单项得分",       12, "每个看多/看空信号的得分绝对值"),
            ("technical", "multi_signal_bonus",  "多信号共振加分",     10, "≥2个同向信号时额外加/减分"),
            ("technical", "ma20_far_penalty",    "MA20远距惩罚(>10%)", 15, "股价偏离MA20超过10%时扣分"),
            ("technical", "ma20_mid_penalty",    "MA20中距惩罚(>5%)",  8,  "股价偏离MA20超过5%时扣分"),
            # 基本面
            ("fundamental", "gm_high_score",      "高毛利率加分(>30%)", 15, "毛利率高于30%加分"),
            ("fundamental", "gm_mid_score",       "中毛利率加分(>20%)", 10, "毛利率高于20%加分"),
            ("fundamental", "gm_low_penalty",     "低毛利率惩罚(<5%)",  10, "毛利率低于5%扣分"),
            ("fundamental", "roe_high_score",     "高ROE加分(>15%)",    15, "ROE高于15%加分"),
            ("fundamental", "roe_mid_score",      "中ROE加分(>10%)",    10, "ROE高于10%加分"),
            ("fundamental", "roe_neg_penalty",    "负ROE惩罚",          15, "ROE为负时扣分"),
            ("fundamental", "rev_growth_score",   "营收增长加分(>20%)", 10, "营收增长超过20%加分"),
            ("fundamental", "profit_growth_score","利润增长加分(>20%)", 10, "净利润增长超过20%加分"),
            ("fundamental", "pe_good_score",      "合理PE加分(0~20)",   10, "PE处于0~20合理区间加分"),
            ("fundamental", "pe_missing_penalty", "PE缺失惩罚",         15, "PE数据缺失或为0时扣分"),
            ("fundamental", "pe_bad_penalty",     "PE异常惩罚(>100)",   10, "PE超过100或为负时扣分"),
            ("fundamental", "pb_good_score",      "合理PB加分(0~2)",    5,  "PB处于0~2合理区间加分"),
            ("fundamental", "pb_missing_penalty", "PB缺失惩罚",         10, "PB数据缺失或为0时扣分"),
            # 资金面
            ("money_flow", "flow_very_high_score","强流入加分(>5%)",    30, "主力净流入占流通市值>5%"),
            ("money_flow", "flow_high_score",     "中流入加分(>3%)",    20, "主力净流入占流通市值>3%"),
            ("money_flow", "flow_mid_score",      "低流入加分(>1%)",    10, "主力净流入占流通市值>1%"),
            ("money_flow", "flow_high_penalty",   "强流出惩罚(<-3%)",   25, "主力净流出占流通市值>3%"),
            ("money_flow", "flow_mid_penalty",    "低流出惩罚(<-1%)",   15, "主力净流出占流通市值>1%"),
            # 情绪面
            ("sentiment", "turnover_high_score",  "高换手加分(>10%)",   15, "换手率高于10%加分"),
            ("sentiment", "turnover_mid_score",   "中换手加分(>5%)",    8,  "换手率高于5%加分"),
            ("sentiment", "turnover_low_penalty", "低换手惩罚(<1%)",    10, "换手率低于1%扣分"),
            ("sentiment", "vol_high_score",       "高量比加分(>2)",     15, "量比高于2加分"),
            ("sentiment", "vol_mid_score",        "中量比加分(>1.5)",   8,  "量比高于1.5加分"),
            ("sentiment", "vol_low_penalty",      "低量比惩罚(<0.5)",   10, "量比低于0.5扣分"),
            ("sentiment", "change_high_score",    "大涨跌幅加分(>5%)",  10, "涨跌幅绝对值超过5%加分"),
            ("sentiment", "change_mid_score",     "中涨跌幅加分(>3%)",  5,  "涨跌幅绝对值超过3%加分"),
            # 筹码面
            ("chip", "converging_score",          "筹码收敛信号加分",   20, "近15天筹码持续收敛"),
            ("chip", "tight_low_profit_score",    "极紧集中低获利加分", 20, "70%筹码集中+低获利比例"),
            ("chip", "wide_low_profit_score",     "大范围低获利加分",   15, "大范围套牢盘有解套动力"),
            ("chip", "low_profit_bonus",          "超低获利奖励(<10%)", 5,  "获利比例低于10%额外加分"),
            ("chip", "narrow_width_bonus",        "极窄宽度奖励(<5%)",  5,  "筹码宽度低于5%额外加分"),
        ]
        for fk, pk, pn, pv, pd in sub_defaults:
            conn.execute(
                "INSERT INTO factor_sub_params(factor_key,param_key,param_name,param_value,description) VALUES(?,?,?,?,?)",
                (fk, pk, pn, pv, pd),
            )

    conn.commit()
    conn.close()

init_db()

# ── 后台任务状态追踪 ──────────────────────────────────────
refresh_task_status = {"running": False, "progress": "", "done": False, "error": None, "dates": [], "latest": None}

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

@app.get("/quant-params", response_class=HTMLResponse)
def quant_params_page(request: Request):
    return templates.TemplateResponse("quant_params.html", {"request": request})


# ── 股票分析 API ──────────────────────────────────────────

QUANT_DIR  = Path.home() / "Desktop" / "quant_trading"
QUANT_VENV = QUANT_DIR / "venv" / "bin" / "python"

def _do_refresh():
    """后台执行股神计划，更新全局状态"""
    global refresh_task_status
    refresh_task_status = {"running": True, "progress": "启动分析引擎…", "done": False, "error": None, "dates": [], "latest": None}
    try:
        result = subprocess.run(
            [str(QUANT_VENV), "main.py"],
            cwd=str(QUANT_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            refresh_task_status = {
                "running": False, "progress": "", "done": True,
                "error": result.stderr[-500:] or "运行失败", "dates": [], "latest": None
            }
            return
        reports = sorted(REPORT_DIR.glob("*_report.md"), reverse=True) if REPORT_DIR.exists() else []
        dates   = [r.stem.replace("_report", "") for r in reports]
        refresh_task_status = {
            "running": False, "progress": "", "done": True,
            "error": None, "dates": dates, "latest": dates[0] if dates else None
        }
    except subprocess.TimeoutExpired:
        refresh_task_status = {
            "running": False, "progress": "", "done": True,
            "error": "分析超时（>180s）", "dates": [], "latest": None
        }
    except Exception as e:
        refresh_task_status = {
            "running": False, "progress": "", "done": True,
            "error": str(e), "dates": [], "latest": None
        }

@app.post("/api/stock/refresh")
async def start_refresh():
    """启动后台刷新（非阻塞）"""
    global refresh_task_status
    if refresh_task_status["running"]:
        return JSONResponse({"error": "已有刷新任务在运行，请稍后"}, status_code=409)
    import threading
    t = threading.Thread(target=_do_refresh, daemon=True)
    t.start()
    return {"ok": True, "message": "后台刷新已启动"}

@app.get("/api/stock/refresh/status")
def get_refresh_status():
    """查询刷新进度"""
    return refresh_task_status


analyze_task_status: dict = {"running": False, "code": None, "name": None, "done": False, "error": None, "result": None}

def _do_analyze_single(code: str, name: str):
    global analyze_task_status
    analyze_task_status = {"running": True, "code": code, "name": name, "done": False, "error": None, "result": None}
    try:
        result = subprocess.run(
            [str(QUANT_VENV), "main.py", "--stock", code],
            cwd=str(QUANT_DIR),
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            analyze_task_status.update({"running": False, "done": True, "error": result.stderr[-500:] or "分析失败"})
            return
        # 从最新 JSON 报告里取该股票的结果
        json_files = sorted(REPORT_DIR.glob("*_report.json"), reverse=True) if REPORT_DIR.exists() else []
        stock_data = None
        if json_files:
            with open(json_files[0], "r", encoding="utf-8") as f:
                stocks = json.load(f)
            stock_data = next((s for s in stocks if s.get("code") == code), None)
        analyze_task_status.update({"running": False, "done": True, "result": stock_data})
    except subprocess.TimeoutExpired:
        analyze_task_status.update({"running": False, "done": True, "error": "分析超时（>120s）"})
    except Exception as e:
        analyze_task_status.update({"running": False, "done": True, "error": str(e)})

@app.post("/api/stock/analyze")
async def start_analyze_single(request: Request):
    global analyze_task_status
    if analyze_task_status["running"]:
        return JSONResponse({"error": "已有分析任务在运行，请稍后"}, status_code=409)
    body = await request.json()
    code = body.get("code", "").strip()
    name = body.get("name", code)
    if not code:
        return JSONResponse({"error": "code 不能为空"}, status_code=400)
    import threading
    threading.Thread(target=_do_analyze_single, args=(code, name), daemon=True).start()
    return {"ok": True, "message": f"开始分析 {name}({code})"}

@app.get("/api/stock/analyze/status")
def get_analyze_status():
    return analyze_task_status


# ── 历史数据下载 ──────────────────────────────────────────────────────────────
download_task_status: dict = {
    "running": False, "done": False, "error": None,
    "current_code": None, "current_name": None,
    "progress": [],          # [{code, name, msg}] 滚动日志
    "results": [],           # 最终每只股票的结果
    "summary": [],           # hist_daily 数据库统计（各股行数范围）
}

def _do_download_history(stocks: list):
    global download_task_status
    download_task_status = {
        "running": True, "done": False, "error": None,
        "current_code": None, "current_name": None,
        "progress": [], "results": [], "summary": [],
    }

    import sys
    sys.path.insert(0, str(QUANT_DIR / "src"))
    try:
        from history_downloader import HistoryDownloader

        def on_progress(code, msg):
            download_task_status["current_code"] = code
            log_entry = {"code": code, "msg": msg}
            download_task_status["progress"].append(log_entry)
            # 只保留最近 200 条日志，防止内存无限增长
            if len(download_task_status["progress"]) > 200:
                download_task_status["progress"] = download_task_status["progress"][-200:]

        dl = HistoryDownloader(progress_cb=on_progress)
        results = dl.download_all(stocks, years=3)
        summary = HistoryDownloader.get_stock_summary()

        download_task_status.update({
            "running": False, "done": True,
            "results": results, "summary": summary,
        })
    except Exception as e:
        import traceback
        download_task_status.update({
            "running": False, "done": True,
            "error": str(e) + "\n" + traceback.format_exc()[-300:],
        })

@app.post("/api/stock/download-history")
async def start_download_history(request: Request):
    """触发历史数据增量下载（整个 watchlist）"""
    global download_task_status
    if download_task_status["running"]:
        return JSONResponse({"error": "下载任务正在进行，请稍后"}, status_code=409)
    stocks = _read_watchlist()
    if not stocks:
        return JSONResponse({"error": "监控列表为空"}, status_code=400)
    import threading
    threading.Thread(target=_do_download_history, args=(stocks,), daemon=True).start()
    return {"ok": True, "message": f"开始下载 {len(stocks)} 只股票近3年历史数据"}

@app.post("/api/stock/download-history/single")
async def start_download_history_single(request: Request):
    """触发单只股票历史数据增量下载"""
    global download_task_status
    if download_task_status["running"]:
        return JSONResponse({"error": "下载任务正在进行，请稍后"}, status_code=409)
    body = await request.json()
    code = body.get("code", "").strip()
    name = body.get("name", code)
    if not code:
        return JSONResponse({"error": "code 不能为空"}, status_code=400)
    import threading
    threading.Thread(target=_do_download_history, args=([(code, name)],), daemon=True).start()
    return {"ok": True, "message": f"开始下载 {name}({code}) 近3年历史数据"}

@app.get("/api/stock/download-history/status")
def get_download_status():
    return download_task_status

@app.get("/api/stock/download-history/summary")
def get_download_summary():
    """返回各股票在本地数据库中的数据量统计"""
    import sys
    sys.path.insert(0, str(QUANT_DIR / "src"))
    try:
        from history_downloader import HistoryDownloader
        return {"summary": HistoryDownloader.get_stock_summary()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/stock/results")
def get_stock_results():
    """读取最新的股神计划分析结果 JSON（供前端展示用）"""
    json_files = sorted(REPORT_DIR.glob("*_report.json"), reverse=True) if REPORT_DIR.exists() else []
    if not json_files:
        return JSONResponse({"error": "暂无分析结果，请先点击「刷新分析」"}, status_code=404)
    latest = json_files[0]
    date_str = latest.stem.replace("_report", "")
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    with open(latest, "r", encoding="utf-8") as f:
        stocks = json.load(f)
    import os
    updated_ts = int(os.path.getmtime(latest))
    return {"date": date_fmt, "stocks": stocks, "updated_at": updated_ts}


@app.get("/api/stock/report")
def get_stock_report(date: str):
    path = REPORT_DIR / f"{date}_report.md"
    if not path.exists():
        return JSONResponse({"error": "报告不存在"}, status_code=404)
    md   = path.read_text(encoding="utf-8")
    return JSONResponse({"date": date, "stocks": _parse_report(md)})

def _parse_report(md: str) -> list:
    stocks = []
    # 先从排行榜表格提取 reason（名称 → reason 映射）
    reason_map = {}
    for rm in re.finditer(
        r"\|\s*\d+\s*\|\s*\*\*(.+?)\*\*\([^)]+\)\s*\|\s*[🟢🟡🔴][^|]+\|[^|]+\|\s*(.+?)\s*\|", md
    ):
        reason_map[rm.group(1).strip()] = rm.group(2).strip()

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
            "reason": reason_map.get(name, ""),
        })
    return stocks


# ── Watchlist 管理 API ───────────────────────────────────

WATCHLIST_PATH = Path.home() / "Desktop" / "quant_trading" / "config" / "watchlist.json"

def _read_watchlist() -> list:
    if WATCHLIST_PATH.exists():
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("stocks", [])
    return []

def _write_watchlist(stocks: list):
    data = {"stocks": stocks}
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.get("/api/watchlist")
def get_watchlist():
    return _read_watchlist()

@app.post("/api/watchlist")
async def add_watchlist(request: Request):
    body = await request.json()
    code = body.get("code", "").strip()
    name = body.get("name", "").strip()
    if not code or not name:
        return JSONResponse({"error": "code 和 name 不能为空"}, status_code=400)
    stocks = _read_watchlist()
    if any(s[0] == code for s in stocks):
        return JSONResponse({"error": f"{code} 已在监控列表中"}, status_code=409)
    stocks.append([code, name])
    _write_watchlist(stocks)
    return {"ok": True, "stocks": stocks}

@app.delete("/api/watchlist/{code}")
def remove_watchlist(code: str):
    stocks = _read_watchlist()
    new_stocks = [s for s in stocks if s[0] != code]
    if len(new_stocks) == len(stocks):
        return JSONResponse({"error": f"{code} 不在监控列表中"}, status_code=404)
    _write_watchlist(new_stocks)
    return {"ok": True, "stocks": new_stocks}

@app.get("/api/stock/search")
def search_stock(q: str = ""):
    """股票搜索：精准代码匹配 或 名称模糊搜索"""
    if not q.strip():
        return []
    import sys
    quant_src = Path.home() / "Desktop" / "quant_trading" / "src"
    if str(quant_src) not in sys.path:
        sys.path.insert(0, str(quant_src))
    try:
        from data_collector import StockDataCollector
        return StockDataCollector.search_stock(q.strip())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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


# ── 代理网关 ─────────────────────────────────────────────

@app.get("/proxy", response_class=HTMLResponse)
def proxy_page(request: Request):
    return templates.TemplateResponse("proxy.html", {"request": request})

def _get_proxy_status() -> dict:
    """读取当前系统代理状态，返回 {enabled, host, port}"""
    try:
        out = subprocess.check_output(
            ["networksetup", "-getwebproxy", PROXY_IFACE],
            text=True, timeout=5
        )
        enabled = "Enabled: Yes" in out
        return {"enabled": enabled, "host": PROXY_HOST, "port": PROXY_PORT, "iface": PROXY_IFACE}
    except Exception as e:
        return {"enabled": False, "host": PROXY_HOST, "port": PROXY_PORT, "iface": PROXY_IFACE, "error": str(e)}

@app.get("/api/proxy/status")
def proxy_status():
    return JSONResponse(_get_proxy_status())

@app.post("/api/proxy/toggle")
def proxy_toggle():
    status = _get_proxy_status()
    target = "off" if status["enabled"] else "on"
    try:
        for cmd in [
            ["networksetup", "-setwebproxystate",          PROXY_IFACE, target],
            ["networksetup", "-setsecurewebproxystate",    PROXY_IFACE, target],
            ["networksetup", "-setsocksfirewallproxystate",PROXY_IFACE, target],
        ]:
            subprocess.run(cmd, check=True, timeout=5)
        return JSONResponse({"enabled": target == "on", "host": PROXY_HOST, "port": PROXY_PORT})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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


# ── 数据标准 导入/导出 API ─────────────────────────────

def _csv_response(content, filename):
    from fastapi.responses import Response
    # Use ASCII filename to avoid latin-1 encoding error in headers
    safe_name = filename.encode("ascii", "ignore").decode("ascii")
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'}
    )

# --- 字根 导出模板 / 导出 / 导入 ---
@app.get("/api/data-roots/template")
def download_root_template():
    header = "字根ID,字根名,字根含义,字根类型,字根长度,字根码值,字根备注\n"
    example = 'ROOT_EXAMPLE,示例字根,这是一个示例,字符型,10,"[""A"",""B"",""C""]",示例备注\n'
    return _csv_response(header + example, "字根导入模板.csv")

@app.get("/api/data-roots/export")
def export_roots():
    conn = get_db()
    rows = conn.execute("SELECT id,name,meaning,root_type,length,code_values,remark FROM data_roots ORDER BY id").fetchall()
    conn.close()
    header = "字根ID,字根名,字根含义,字根类型,字根长度,字根码值,字根备注\n"
    lines = ""
    for r in rows:
        cv = (r[5] or "").replace('"', '""')
        lines += f'{r[0]},{r[1]},{r[2] or ""},{r[3] or ""},{r[4] or ""},"{cv}",{r[6] or ""}\n'
    return _csv_response(header + lines, f"字根导出_{datetime.now().strftime('%Y%m%d')}.csv")

@app.post("/api/data-roots/import")
async def import_roots(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode("utf-8-sig")
    import csv, io
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 4:
        return JSONResponse({"error": "文件格式不正确，请下载使用模板文件"}, status_code=400)
    success, errors = 0, 0
    conn = get_db()
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i, row in enumerate(reader, 2):
        try:
            rid = (row[0] or "").strip()
            rname = (row[1] or "").strip()
            if not rid or not rname:
                errors += 1; continue
            rmean = (row[2] or "").strip()
            rtype = (row[3] or "字符型").strip()
            rlen = int(row[4]) if len(row) > 4 and (row[4] or "").strip() else None
            rcode = (row[5] or "").strip() if len(row) > 5 else None
            rremark = (row[6] or "").strip() if len(row) > 6 else None
            existing = conn.execute("SELECT id FROM data_roots WHERE id=?", (rid,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE data_roots SET name=?,meaning=?,root_type=?,length=?,code_values=?,remark=?,updated_at=? WHERE id=?",
                    (rname, rmean, rtype, rlen, rcode, rremark, now_ts, rid)
                )
            else:
                conn.execute(
                    "INSERT INTO data_roots(id,name,meaning,root_type,length,code_values,remark,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (rid, rname, rmean, rtype, rlen, rcode, rremark, now_ts, now_ts)
                )
            success += 1
        except Exception:
            errors += 1
    conn.commit()
    conn.close()
    return {"ok": True, "success": success, "errors": errors}

# --- 字段 导出模板 / 导出 / 导入 ---
@app.get("/api/data-fields/template")
def download_field_template():
    header = "字段ID,字段英文名,字段中文名,字段含义,引用字根ID,引用字根名,字段类型,字段长度,字段码值,字段备注\n"
    example = 'FIELD_EXAMPLE,exampleField,示例字段,用于示例,ROOT_EXAMPLE,示例字根,字符型,10,"[""A"",""B""]",示例备注\n'
    return _csv_response(header + example, "字段导入模板.csv")

@app.get("/api/data-fields/export")
def export_fields():
    conn = get_db()
    rows = conn.execute("SELECT id,name_en,name_cn,meaning,root_id,root_name,field_type,length,code_values,remark FROM data_fields ORDER BY id").fetchall()
    conn.close()
    header = "字段ID,字段英文名,字段中文名,字段含义,引用字根ID,引用字根名,字段类型,字段长度,字段码值,字段备注\n"
    lines = ""
    for r in rows:
        cv = (r[8] or "").replace('"', '""')
        lines += f'{r[0]},{r[1]},{r[2] or ""},{r[3] or ""},{r[4] or ""},{r[5] or ""},{r[6] or ""},{r[7] or ""},"{cv}",{r[9] or ""}\n'
    return _csv_response(header + lines, f"字段导出_{datetime.now().strftime('%Y%m%d')}.csv")

@app.post("/api/data-fields/import")
async def import_fields(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode("utf-8-sig")
    import csv, io
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        return JSONResponse({"error": "文件格式不正确，请下载使用模板文件"}, status_code=400)
    success, errors = 0, 0
    conn = get_db()
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i, row in enumerate(reader, 2):
        try:
            fid = (row[0] or "").strip()
            fname_en = (row[1] or "").strip()
            if not fid or not fname_en:
                errors += 1; continue
            fname_cn = (row[2] or "").strip() if len(row) > 2 else ""
            fmean = (row[3] or "").strip() if len(row) > 3 else ""
            froot_id = (row[4] or "").strip() if len(row) > 4 else None
            froot_name = (row[5] or "").strip() if len(row) > 5 else None
            ftype = (row[6] or "").strip() if len(row) > 6 else None
            flen = int(row[7]) if len(row) > 7 and (row[7] or "").strip() else None
            fcode = (row[8] or "").strip() if len(row) > 8 else None
            fremark = (row[9] or "").strip() if len(row) > 9 else ""
            existing = conn.execute("SELECT id FROM data_fields WHERE id=?", (fid,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE data_fields SET name_en=?,name_cn=?,meaning=?,root_id=?,root_name=?,field_type=?,length=?,code_values=?,remark=?,updated_at=? WHERE id=?",
                    (fname_en, fname_cn, fmean, froot_id, froot_name, ftype, flen, fcode, fremark, now_ts, fid)
                )
            else:
                conn.execute(
                    "INSERT INTO data_fields(id,name_en,name_cn,meaning,root_id,root_name,field_type,length,code_values,remark,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, fname_en, fname_cn, fmean, froot_id, froot_name, ftype, flen, fcode, fremark, now_ts, now_ts)
                )
            success += 1
        except Exception:
            errors += 1
    conn.commit()
    conn.close()
    return {"ok": True, "success": success, "errors": errors}


# ── 数据标准 API ──────────────────────────────────────────

# 字根 CRUD
@app.get("/api/data-roots")
def list_roots():
    conn = get_db()
    rows = conn.execute("SELECT * FROM data_roots ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/data-roots")
def create_root(data: dict):
    conn = get_db()
    conn.execute(
        "INSERT INTO data_roots(id,name,meaning,root_type,length,code_values,remark,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (data["id"], data["name"], data.get("meaning"), data.get("root_type"),
         data.get("length"), data.get("code_values"), data.get("remark"),
         data.get("created_at"), data.get("updated_at"))
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": data["id"]}

@app.put("/api/data-roots/{root_id}")
def update_root(root_id: str, data: dict):
    conn = get_db()
    conn.execute(
        "UPDATE data_roots SET name=?,meaning=?,root_type=?,length=?,code_values=?,remark=?,updated_at=? WHERE id=?",
        (data["name"], data.get("meaning"), data.get("root_type"),
         data.get("length"), data.get("code_values"), data.get("remark"),
         data.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"), root_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/api/data-roots/{root_id}")
def delete_root(root_id: str):
    conn = get_db()
    conn.execute("DELETE FROM data_roots WHERE id=?", (root_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# 字段 CRUD
@app.get("/api/data-fields")
def list_fields():
    conn = get_db()
    rows = conn.execute("SELECT * FROM data_fields ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/data-fields")
def create_field(data: dict):
    conn = get_db()
    conn.execute(
        "INSERT INTO data_fields(id,name_en,name_cn,meaning,root_id,root_name,field_type,length,code_values,remark,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (data["id"], data["name_en"], data.get("name_cn"), data.get("meaning"),
         data.get("root_id"), data.get("root_name"), data.get("field_type"),
         data.get("length"), data.get("code_values"), data.get("remark"),
         data.get("created_at"), data.get("updated_at"))
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": data["id"]}

@app.put("/api/data-fields/{field_id}")
def update_field(field_id: str, data: dict):
    conn = get_db()
    conn.execute(
        "UPDATE data_fields SET name_en=?,name_cn=?,meaning=?,root_id=?,root_name=?,field_type=?,length=?,code_values=?,remark=?,updated_at=? WHERE id=?",
        (data["name_en"], data.get("name_cn"), data.get("meaning"),
         data.get("root_id"), data.get("root_name"), data.get("field_type"),
         data.get("length"), data.get("code_values"), data.get("remark"),
         data.get("updated_at") or datetime.now().strftime('%Y-%m-%d %H:%M:%S'), field_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/api/data-fields/{field_id}")
def delete_field(field_id: str):
    conn = get_db()
    conn.execute("DELETE FROM data_fields WHERE id=?", (field_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# 接口 CRUD
@app.get("/api/interfaces")
def list_interfaces():
    conn = get_db()
    rows = conn.execute("SELECT * FROM interfaces ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/interfaces")
def create_interface(data: dict):
    conn = get_db()
    conn.execute(
        "INSERT INTO interfaces(id,name,description,input_json,output_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (data["id"], data["name"], data.get("description"),
         data.get("input_json", "[]"), data.get("output_json", "[]"),
         data.get("created_at"), data.get("updated_at"))
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": data["id"]}

@app.put("/api/interfaces/{iface_id}")
def update_interface(iface_id: str, data: dict):
    conn = get_db()
    conn.execute(
        "UPDATE interfaces SET name=?,description=?,input_json=?,output_json=?,updated_at=? WHERE id=?",
        (data["name"], data.get("description"),
         data.get("input_json", "[]"), data.get("output_json", "[]"),
         data.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"), iface_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/api/interfaces/{iface_id}")
def delete_interface(iface_id: str):
    conn = get_db()
    conn.execute("DELETE FROM interfaces WHERE id=?", (iface_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# 规则 CRUD
@app.get("/api/rules")
def list_rules():
    conn = get_db()
    rows = conn.execute("SELECT * FROM rules ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/rules")
def create_rule(data: dict):
    conn = get_db()
    conn.execute(
        "INSERT INTO rules(id,name,description,input_json,output_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (data["id"], data["name"], data.get("description"),
         data.get("input_json", "[]"), data.get("output_json", "[]"),
         data.get("created_at"), data.get("updated_at"))
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": data["id"]}

@app.put("/api/rules/{rule_id}")
def update_rule(rule_id: str, data: dict):
    conn = get_db()
    conn.execute(
        "UPDATE rules SET name=?,description=?,input_json=?,output_json=?,updated_at=? WHERE id=?",
        (data["name"], data.get("description"),
         data.get("input_json", "[]"), data.get("output_json", "[]"),
         data.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"), rule_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: str):
    conn = get_db()
    conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── 产品管理 API（一图一表入口）────────────────────────────

class ProductBase(BaseModel):
    product_id:      str
    product_name:    str
    product_desc:    Optional[str] = None
    product_manager: Optional[str] = None
    biz_contact:     Optional[str] = None
    biz_dept:        Optional[str] = None

class ProductCreate(ProductBase):
    pass

class ProductUpdate(BaseModel):
    product_name:    Optional[str] = None
    product_desc:    Optional[str] = None
    product_manager: Optional[str] = None
    biz_contact:     Optional[str] = None
    biz_dept:        Optional[str] = None

@app.get("/api/products", response_model=list[dict])
def list_products():
    conn = get_db()
    rows = conn.execute("SELECT * FROM products ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/products")
def create_product(p: ProductCreate):
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO products(product_id,product_name,product_desc,product_manager,biz_contact,biz_dept,chart_data,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (p.product_id, p.product_name, p.product_desc, p.product_manager, p.biz_contact, p.biz_dept, None, now_ts, now_ts)
        )
        conn.commit()
        return {"ok": True, "id": cur.lastrowid}
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        conn.close()

@app.put("/api/products/{pid}")
def update_product(pid: int, p: ProductUpdate):
    conn = get_db()
    conn.execute(
        "UPDATE products SET product_name=?,product_desc=?,product_manager=?,biz_contact=?,biz_dept=?,updated_at=? WHERE id=?",
        (p.product_name, p.product_desc, p.product_manager, p.biz_contact, p.biz_dept,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pid)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/api/products/{pid}")
def delete_product(pid: int):
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# 获取产品的图表数据
@app.get("/api/products/{pid}/chart")
def get_product_chart(pid: int):
    conn = get_db()
    row = conn.execute("SELECT id,product_name,chart_data FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not row:
        return JSONResponse({"error": "产品不存在"}, status_code=404)
    return {"id": row[0], "product_name": row[1], "chart_data": row[2]}

# 保存产品的图表数据（步骤信息）
@app.put("/api/products/{pid}/chart")
def save_product_chart(pid: int, body: dict):
    chart_data = body.get("chart_data")
    title = body.get("title")
    conn = get_db()
    if title:
        conn.execute("UPDATE products SET product_name=?,chart_data=?,updated_at=? WHERE id=?",
                     (title, chart_data, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pid))
    else:
        conn.execute("UPDATE products SET chart_data=?,updated_at=? WHERE id=?",
                     (chart_data, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── 量化参数 API（因子权重配置）────────────────────────────

class FactorWeightCreate(BaseModel):
    factor_key:   str
    factor_name:  str
    weight:       float
    description:  Optional[str] = None
    is_active:    Optional[int] = 1

class FactorWeightUpdate(BaseModel):
    factor_name:  Optional[str] = None
    weight:       Optional[float] = None
    description:  Optional[str] = None
    is_active:    Optional[int] = None

@app.get("/api/factor-weights")
def list_factor_weights():
    """获取所有因子权重列表"""
    conn = get_db()
    rows = conn.execute("SELECT * FROM factor_weights ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/factor-weights")
def create_factor_weight(f: FactorWeightCreate):
    """新建因子权重"""
    now_ts = datetime.now().isoformat()
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO factor_weights(factor_key,factor_name,weight,description,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (f.factor_key, f.factor_name, f.weight, f.description, f.is_active, now_ts, now_ts)
        )
        conn.commit()
        return {"ok": True, "id": cur.lastrowid}
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        conn.close()

@app.put("/api/factor-weights/{fid}")
def update_factor_weight(fid: int, f: FactorWeightUpdate):
    """更新因子权重"""
    conn = get_db()
    try:
        fields = []
        values = []
        if f.factor_name is not None:
            fields.append("factor_name=?"); values.append(f.factor_name)
        if f.weight is not None:
            fields.append("weight=?"); values.append(f.weight)
        if f.description is not None:
            fields.append("description=?"); values.append(f.description)
        if f.is_active is not None:
            fields.append("is_active=?"); values.append(f.is_active)
        fields.append("updated_at=?"); values.append(datetime.now().isoformat())
        values.append(fid)
        conn.execute(f"UPDATE factor_weights SET {','.join(fields)} WHERE id=?", values)
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        conn.close()

@app.delete("/api/factor-weights/{fid}")
def delete_factor_weight(fid: int):
    """删除因子权重"""
    conn = get_db()
    try:
        conn.execute("DELETE FROM factor_weights WHERE id=?", (fid,))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        conn.close()

# ── 因子细项参数 API ──────────────────────────────────────

class SubParamUpdate(BaseModel):
    param_value: float

@app.get("/api/factor-sub-params")
def list_sub_params():
    """获取所有细项参数（按 factor_key 分组）"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM factor_sub_params ORDER BY factor_key, id"
    ).fetchall()
    conn.close()
    result: dict = {}
    for r in rows:
        d = dict(r)
        fk = d["factor_key"]
        result.setdefault(fk, []).append(d)
    return result

@app.put("/api/factor-sub-params/{pid}")
def update_sub_param(pid: int, body: SubParamUpdate):
    """更新单个细项参数值"""
    conn = get_db()
    try:
        conn.execute("UPDATE factor_sub_params SET param_value=? WHERE id=?",
                     (body.param_value, pid))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        conn.close()

@app.post("/api/factor-sub-params/sync")
def sync_sub_params_to_quant():
    """将细项参数导出为 JSON 供股神计划使用"""
    import json
    conn = get_db()
    rows = conn.execute("SELECT * FROM factor_sub_params ORDER BY factor_key, id").fetchall()
    conn.close()

    config: dict = {}
    for r in rows:
        d = dict(r)
        config.setdefault(d["factor_key"], {})[d["param_key"]] = d["param_value"]

    # 同时写入因子主权重
    conn2 = get_db()
    fw_rows = conn2.execute("SELECT factor_key, weight, is_active FROM factor_weights").fetchall()
    conn2.close()
    weights = {r["factor_key"]: {"weight": r["weight"], "is_active": bool(r["is_active"])} for r in fw_rows}
    config["_weights"] = weights

    out_path = Path.home() / "Desktop" / "quant_trading" / "config" / "scorer_params.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(out_path)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
