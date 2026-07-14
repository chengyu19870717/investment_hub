"""
投资分析 - 每日维度快照

把 investment_analysis.js 里 stockDimensions()/getExposures() 这套评分逻辑的 Python 版本，
用来在没有浏览器打开页面的情况下，离线计算供给/需求/盈利三维评分并落库，
为后续的历史回测、因子归因积累时序数据。

与前端 JS 版本对照：
- code_keys()      <-> codeKeys()
- build_exposures()<-> buildExposures()
- stock_dimensions()<-> stockDimensions()
两边算法必须保持一致，任何一边改动评分公式都要同步另一边，否则前端展示和快照库对不上。
"""
import json
import re
import sqlite3
import subprocess
from datetime import datetime, date
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = Path.home() / ".baibao" / "baibao.db"
REPORT_DIR = Path.home() / "project" / "quant_trading" / "reports"
INDUSTRY_CHAIN_PATH = BASE_DIR / "static" / "data" / "industry_chain.json"
WATCHLIST_PATH = Path.home() / "project" / "quant_trading" / "config" / "watchlist.json"


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_snapshot_table():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_dimension_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            probability REAL,
            score REAL,
            price REAL,
            risk_label TEXT,
            supply REAL,
            demand REAL,
            profit REAL,
            spread REAL,
            divergent INTEGER,
            exposures_count INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(snapshot_date, code)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            alert_type TEXT,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def clamp(v, lo=0, hi=100):
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0
    return max(lo, min(hi, v))


def average(values, fallback=50):
    nums = [float(v) for v in values if v is not None and str(v) != '' and _is_number(v)]
    return sum(nums) / len(nums) if nums else fallback


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def code_keys(code):
    raw = str(code or '').strip()
    if not raw:
        return []
    keys = {raw, raw.upper()}
    keys.add(re.sub(r'(?i)^HK:', '', raw))
    keys.add(raw.split('.')[0])
    digits = re.sub(r'\D', '', raw)
    if digits:
        keys.add(digits)
    return [k for k in keys if k]


def normalize_growth(v):
    if v is None or not _is_number(v):
        return 50
    return clamp(50 + float(v) / 2)


def normalize_margin(v):
    if v is None or not _is_number(v):
        return 50
    return clamp((float(v) + 20) * 1.1)


def load_industry_data():
    if not INDUSTRY_CHAIN_PATH.exists():
        return {}
    with open(INDUSTRY_CHAIN_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_watchlist():
    if not WATCHLIST_PATH.exists():
        return []
    with open(WATCHLIST_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    items = data.get('stocks', []) if isinstance(data, dict) else data
    out = []
    for item in items or []:
        if isinstance(item, (list, tuple)):
            out.append({'code': str(item[0] or '').strip(), 'name': str(item[1] or '').strip()})
        elif isinstance(item, dict):
            out.append({'code': str(item.get('code') or '').strip(), 'name': str(item.get('name') or '').strip()})
    return [i for i in out if i['code'] and i['name']]


def load_latest_report():
    if not REPORT_DIR.exists():
        return None
    files = sorted(REPORT_DIR.glob('*_report.json'), reverse=True)
    if not files:
        return None
    date_str = files[0].stem.replace('_report', '')
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    with open(files[0], 'r', encoding='utf-8') as f:
        stocks = json.load(f)
    return {'date': date_fmt, 'stocks': stocks}


def build_exposures(industry_data, stock_universe):
    """镜像 buildExposures()：返回 { code_key: [exposure, ...] }"""
    matched = {}
    for industry in (industry_data or {}).values():
        for node in industry.get('nodes', []) or []:
            for company in node.get('companies', []) or []:
                company_keys = set(code_keys(company.get('code')))
                hit = any(any(k in company_keys for k in code_keys(item.get('code'))) for item in stock_universe)
                if not hit:
                    continue
                exposure = {
                    'industry_id': industry.get('id'),
                    'industry': industry.get('name'),
                    'node_id': node.get('id'),
                    'node_name': node.get('name'),
                    'layer': node.get('layer'),
                    'supply_level': node.get('supply_level'),
                    'domestic_rate': node.get('domestic_rate'),
                }
                for key in code_keys(company.get('code')):
                    bucket = matched.setdefault(key, [])
                    if not any(e['industry_id'] == exposure['industry_id'] and e['node_id'] == exposure['node_id'] for e in bucket):
                        bucket.append(exposure)
    return matched


def get_exposures(exposures_by_code, code):
    out = []
    for key in code_keys(code):
        for exp in exposures_by_code.get(key, []):
            if not any(o['industry_id'] == exp['industry_id'] and o['node_id'] == exp['node_id'] for o in out):
                out.append(exp)
    return out


def stock_dimensions(stock, exposures_by_code):
    """镜像 JS stockDimensions()：返回 supply/demand/profit/spread/divergent。"""
    exposures = get_exposures(exposures_by_code, stock.get('code'))
    supply = 50
    if exposures:
        scores = []
        for exp in exposures:
            if exp['supply_level'] == 'tight':
                scores.append(64)
            elif exp['supply_level'] == 'risky':
                scores.append(32)
            else:
                scores.append(52)
        supply = average(scores)
        domestic_rates = [e['domestic_rate'] for e in exposures if _is_number(e.get('domestic_rate'))]
        avg_domestic = average(domestic_rates, None) if domestic_rates else None
        if avg_domestic is not None:
            supply = (supply * .72) + (avg_domestic * .28)

    demand = average([
        stock.get('sentiment_score'),
        stock.get('probability'),
        normalize_growth(stock.get('revenue_growth')),
        clamp(45 + (float(stock['vol_ratio']) - 1) * 28) if _is_number(stock.get('vol_ratio')) else 50,
    ])
    profit = average([
        stock.get('fund_score'),
        normalize_margin(stock.get('gross_margin')),
        normalize_growth(stock.get('profit_growth')),
        normalize_growth(stock.get('roe')),
    ])
    scores = [supply, demand, profit]
    spread = max(scores) - min(scores)
    return {
        'supply': clamp(supply),
        'demand': clamp(demand),
        'profit': clamp(profit),
        'spread': spread,
        'divergent': spread >= 26,
        'exposures_count': len(exposures),
    }


def _applescript_escape(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"')


def send_mac_notification(title, message):
    """本地 macOS 通知（osascript），仅在触发脚本的这台 Mac 上弹出，不做跨设备补发。"""
    script = f'display notification "{_applescript_escape(message)}" with title "{_applescript_escape(title)}"'
    try:
        subprocess.run(['osascript', '-e', script], check=False, timeout=10)
    except Exception:
        pass


def detect_alerts(prev_row, code, name, dims, stock):
    """对比上一次快照，找出值得主动提醒的信号变化。返回结构化 [{type, message}]。不依赖前端页面打开与否。"""
    alerts = []
    probability = float(stock.get('probability') or 0)
    risk_label = str(stock.get('risk_label') or '')
    if prev_row is None:
        return alerts
    prev_probability = float(prev_row['probability'] or 0)
    prev_risk_label = str(prev_row['risk_label'] or '')
    prev_divergent = bool(prev_row['divergent'])

    if risk_label != prev_risk_label and ('危险' in risk_label or '高风险' in risk_label):
        alerts.append({'type': 'risk', 'message': f'{name}({code})风险标签变为「{risk_label}」（原「{prev_risk_label or "无"}」）'})
    if dims['divergent'] and not prev_divergent:
        alerts.append({'type': 'divergent', 'message': f'{name}({code})三维评分出现背离，分差 {dims["spread"]:.0f}'})
    if prev_probability < 55 <= probability:
        alerts.append({'type': 'prob_up', 'message': f'{name}({code})上涨概率突破55%进入进攻区（{prev_probability:.1f}%→{probability:.1f}%）'})
    if prev_probability >= 40 > probability:
        alerts.append({'type': 'prob_down', 'message': f'{name}({code})上涨概率跌破40%进入弱势区（{prev_probability:.1f}%→{probability:.1f}%）'})
    return alerts


def run_snapshot(snapshot_date=None):
    """计算一次快照并写入 stock_dimension_snapshots。同一天重复跑会覆盖当天数据（幂等）。"""
    init_snapshot_table()
    snapshot_date = snapshot_date or date.today().isoformat()

    report = load_latest_report()
    if not report:
        return {'ok': False, 'error': '无股票分析结果（REPORT_DIR 下没有 *_report.json）', 'written': 0}

    watchlist = load_watchlist()
    stocks = report.get('stocks') or []
    stock_universe = watchlist + [{'code': s.get('code'), 'name': s.get('name')} for s in stocks]
    industry_data = load_industry_data()
    exposures_by_code = build_exposures(industry_data, stock_universe)

    stock_by_code = {}
    for s in stocks:
        for k in code_keys(s.get('code')):
            stock_by_code[k] = s

    targets = watchlist if watchlist else [{'code': s.get('code'), 'name': s.get('name')} for s in stocks]

    conn = get_db()
    now = datetime.now().isoformat()
    written = 0
    all_alerts = []
    for item in targets:
        stock = None
        for k in code_keys(item['code']):
            if k in stock_by_code:
                stock = stock_by_code[k]
                break
        if not stock:
            continue
        dims = stock_dimensions(stock, exposures_by_code)
        name = item.get('name') or stock.get('name')
        prev_row = conn.execute(
            "SELECT * FROM stock_dimension_snapshots WHERE code=? AND snapshot_date<? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (item['code'], snapshot_date),
        ).fetchone()
        stock_alerts = detect_alerts(prev_row, item['code'], name, dims, stock)
        for a in stock_alerts:
            a['code'] = item['code']
            a['name'] = name
        all_alerts.extend(stock_alerts)
        conn.execute("""
            INSERT INTO stock_dimension_snapshots
                (snapshot_date, code, name, probability, score, price, risk_label,
                 supply, demand, profit, spread, divergent, exposures_count, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_date, code) DO UPDATE SET
                name=excluded.name, probability=excluded.probability, score=excluded.score,
                price=excluded.price, risk_label=excluded.risk_label, supply=excluded.supply,
                demand=excluded.demand, profit=excluded.profit, spread=excluded.spread,
                divergent=excluded.divergent, exposures_count=excluded.exposures_count,
                created_at=excluded.created_at
        """, (
            snapshot_date, item['code'], name,
            stock.get('probability'), stock.get('score'), stock.get('price'), stock.get('risk_label'),
            dims['supply'], dims['demand'], dims['profit'], dims['spread'],
            1 if dims['divergent'] else 0, dims['exposures_count'], now,
        ))
        written += 1

    # 预警落库：同一天重跑先清当天再写，保持幂等（detect_alerts 对比昨日快照，同天结果确定）
    conn.execute("DELETE FROM stock_alerts WHERE alert_date=?", (snapshot_date,))
    for a in all_alerts:
        conn.execute(
            "INSERT INTO stock_alerts(alert_date, code, name, alert_type, message, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (snapshot_date, a['code'], a['name'], a['type'], a['message'], now),
        )
    conn.commit()
    conn.close()

    if all_alerts:
        title = f'投资分析信号提醒（{len(all_alerts)}条）'
        body = '；'.join(a['message'] for a in all_alerts)
        send_mac_notification(title, body[:250])

    return {
        'ok': True, 'written': written, 'snapshot_date': snapshot_date,
        'report_date': report.get('date'), 'alerts': all_alerts,
    }


if __name__ == '__main__':
    result = run_snapshot()
    print(json.dumps(result, ensure_ascii=False))
