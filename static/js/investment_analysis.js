const state = {
    stockResult: null,
    stocks: [],
    watchlist: [],
    events: [],
    factors: [],
    history: [],
    industryData: {},
    exposuresByCode: {},
    dimensionsByCode: {},
    dimensionHistoryByCode: {},
    factorWeightsByCode: {},
    factorBacktestByCode: {},
    factorBacktestGeneratedAt: null,
    notes: [],
    alerts: [],
    klineByCode: {},
    overviewSort: { key: 'probability', asc: false },
    selectedCode: '',
    watchSearchSelected: null,
};

let watchSearchTimer = null;
let refreshTimer = null;

const layerName = {
    raw_material: '原材料',
    upstream: '上游',
    midstream: '中游',
    downstream: '下游',
};

const layerOrder = {
    raw_material: 0,
    upstream: 1,
    midstream: 2,
    downstream: 3,
};

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

function escapeJsArg(s) {
    return escapeHtml(JSON.stringify(String(s ?? '')));
}

function fmtPct(v, digits = 1) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return '-';
    return `${Number(v).toFixed(digits)}%`;
}

function fmtNum(v, digits = 1) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return '-';
    return Number(v).toFixed(digits);
}

function clamp(v, min = 0, max = 100) {
    return Math.max(min, Math.min(max, Number(v) || 0));
}

function average(values, fallback = 50) {
    const nums = values.filter(v => v !== null && v !== undefined && !Number.isNaN(Number(v))).map(Number);
    return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : fallback;
}

function unique(items) {
    return Array.from(new Set(items.filter(Boolean)));
}

function textList(value) {
    if (Array.isArray(value)) return value.map(item => String(item).trim()).filter(Boolean);
    if (value === null || value === undefined) return [];
    return String(value).split(/[、,，;；\s]+/).map(item => item.trim()).filter(Boolean);
}

function codeKeys(code) {
    const raw = String(code || '').trim();
    if (!raw) return [];
    const keys = new Set([raw, raw.toUpperCase()]);
    keys.add(raw.replace(/^HK:/i, ''));
    keys.add(raw.split('.')[0]);
    const digits = raw.replace(/\D/g, '');
    if (digits) keys.add(digits);
    return Array.from(keys).filter(Boolean);
}

function normalizeWatchItem(item) {
    if (Array.isArray(item)) return { code: String(item[0] || '').trim(), name: String(item[1] || '').trim() };
    return { code: String(item?.code || '').trim(), name: String(item?.name || '').trim() };
}

function normalizedWatchlist() {
    return (state.watchlist || []).map(normalizeWatchItem).filter(item => item.code && item.name);
}

function sameCode(a, b) {
    const bKeys = codeKeys(b);
    return codeKeys(a).some(key => bKeys.includes(key));
}

function findStockByCode(code) {
    return state.stocks.find(stock => sameCode(stock.code, code)) || null;
}

function selectedWatchItem() {
    return normalizedWatchlist().find(item => sameCode(item.code, state.selectedCode))
        || normalizedWatchlist()[0]
        || null;
}

function selectedContext() {
    const watch = selectedWatchItem();
    const stock = watch ? findStockByCode(watch.code) : state.stocks[0] || null;
    return {
        watch: watch || (stock ? { code: stock.code, name: stock.name } : null),
        stock,
    };
}

function dataDate(stock = null) {
    return stock?.date || state.stockResult?.date || state.history?.[0]?.date || '-';
}

function scoreClass(score) {
    if (score >= 62) return 'good';
    if (score >= 42) return 'warn';
    return 'bad';
}

function decisionForStock(stock) {
    if (!stock) return { label: '待分析', cls: 'warn' };
    const probability = Number(stock.probability || 0);
    if (probability >= 55 && !String(stock.risk_label || '').includes('危险')) return { label: '偏进攻', cls: 'good' };
    if (probability < 40 || String(stock.risk_label || '').includes('危险')) return { label: '偏防守', cls: 'bad' };
    return { label: '观察', cls: 'warn' };
}

async function safeJson(url) {
    try {
        const res = await fetch(url);
        if (!res.ok) return null;
        return await res.json();
    } catch (_) {
        return null;
    }
}

async function loadIndustryData() {
    return await safeJson('/api/industry-chain') || {};
}

function isWatchlisted(code) {
    return normalizedWatchlist().some(item => sameCode(item.code, code));
}

function buildExposures() {
    const matched = {};
    const stockUniverse = [...normalizedWatchlist(), ...state.stocks];
    Object.values(state.industryData || {}).forEach(industry => {
        (industry.nodes || []).forEach(node => {
            (node.companies || []).forEach(company => {
                const hit = stockUniverse.find(item => codeKeys(company.code).some(key => codeKeys(item.code).includes(key)));
                if (!hit) return;
                const exposure = {
                    industryId: industry.id,
                    industry: industry.name,
                    industryObj: industry,
                    node,
                    nodeId: node.id,
                    nodeName: node.name,
                    layer: node.layer,
                    layerName: layerName[node.layer] || node.layer,
                    supplyLevel: node.supply_level,
                    domesticRate: node.domestic_rate,
                    priceNote: node.price_note || '',
                    company,
                };
                codeKeys(company.code).forEach(key => {
                    matched[key] = matched[key] || [];
                    if (!matched[key].some(x => x.industryId === industry.id && x.nodeId === node.id)) {
                        matched[key].push(exposure);
                    }
                });
            });
        });
    });
    state.exposuresByCode = matched;
}

function getExposures(code) {
    const out = [];
    codeKeys(code).forEach(key => {
        (state.exposuresByCode[key] || []).forEach(exp => {
            if (!out.some(x => x.industryId === exp.industryId && x.nodeId === exp.nodeId)) out.push(exp);
        });
    });
    return out;
}

function primaryExposure(stock, watch = null) {
    const code = stock?.code || watch?.code;
    const exposures = getExposures(code);
    return exposures[0] || null;
}

function stockEvents(code) {
    const keys = codeKeys(code);
    return state.events
        .filter(ev => keys.includes(String(ev.stock_code || '').trim()) || keys.includes(String(ev.stock_code || '').replace(/\D/g, '')))
        .sort((a, b) => String(a.event_date).localeCompare(String(b.event_date)));
}

function daysUntil(dateStr) {
    const target = new Date(`${dateStr}T00:00:00`);
    if (Number.isNaN(target.getTime())) return null;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return Math.round((target - today) / 86400000);
}

// 供给/需求/盈利三维评分的计算公式以后端 /api/investment-analysis/dimensions 为唯一权威来源
// （investment_snapshot.stock_dimensions），前端只做展示层的文案拼接，不再重复实现打分公式，
// 避免前端和每日快照脚本各算一份导致数字对不上。
function stockDimensions(stock) {
    if (!stock) {
        return {
            supply: 50,
            demand: 50,
            profit: 50,
            spread: 0,
            divergent: false,
            reasons: { supply: ['暂无股票分析结果'], demand: ['暂无股票分析结果'], profit: ['暂无股票分析结果'] },
        };
    }
    const dims = codeKeys(stock.code).map(key => state.dimensionsByCode[key]).find(Boolean) || {
        supply: 50, demand: 50, profit: 50, spread: 0, divergent: false,
    };
    const exposures = getExposures(stock.code);
    const supplyReasons = [];
    if (exposures.length) {
        const risky = exposures.filter(exp => exp.supplyLevel === 'risky').length;
        const tight = exposures.filter(exp => exp.supplyLevel === 'tight').length;
        const domesticRates = exposures.filter(exp => typeof exp.domesticRate === 'number').map(exp => exp.domesticRate);
        const avgDomestic = average(domesticRates, null);
        if (risky) supplyReasons.push(`${risky} 个断供风险环节拉低供给侧评分`);
        if (tight) supplyReasons.push(`${tight} 个供给偏紧环节带来议价弹性`);
        if (avgDomestic !== null) supplyReasons.push(`命中环节平均国产率 ${fmtPct(avgDomestic, 0)}`);
    } else {
        supplyReasons.push('未命中产业链库，供给侧按中性处理');
    }
    return {
        supply: dims.supply,
        demand: dims.demand,
        profit: dims.profit,
        spread: dims.spread,
        divergent: dims.divergent,
        reasons: {
            supply: supplyReasons,
            demand: [`上涨概率 ${fmtPct(stock.probability)}`, `营收增速 ${fmtPct(stock.revenue_growth)}`, `量比 ${fmtNum(stock.vol_ratio, 2)}`],
            profit: [`毛利率 ${fmtPct(stock.gross_margin)}`, `利润增速 ${fmtPct(stock.profit_growth)}`, `ROE ${fmtPct(stock.roe)}`],
        },
    };
}

function stockConceptTags(stock) {
    if (!stock) return [];
    return unique([
        ...textList(stock.concepts),
        ...textList(stock.concept),
        ...textList(stock.concept_tags),
        ...textList(stock.themes),
        ...textList(stock.tags),
    ]).slice(0, 8);
}

function compactCompanyList(nodes, limit = 6) {
    const companies = [];
    nodes.forEach(node => {
        (node.companies || []).forEach(company => {
            if (!company.name || companies.some(item => item.name === company.name)) return;
            companies.push({ ...company, nodeName: node.name, layerName: layerName[node.layer] || node.layer });
        });
    });
    return companies
        .sort((a, b) => Number(Boolean(b.is_leader)) - Number(Boolean(a.is_leader)) || Number(b.gross_margin || 0) - Number(a.gross_margin || 0))
        .slice(0, limit);
}

function relatedChainNodes(exp) {
    if (!exp?.industryObj) return { upstream: [], current: [], downstream: [] };
    const nodes = exp.industryObj.nodes || [];
    const current = nodes.filter(node => node.id === exp.nodeId);
    const currentLayer = layerOrder[exp.layer] ?? 1;
    const childIds = new Set((exp.node.children || []).filter(Boolean));
    const upstream = nodes
        .filter(node => node.id !== exp.nodeId)
        .filter(node => (node.children || []).includes(exp.nodeId) || (layerOrder[node.layer] ?? 0) < currentLayer)
        .sort((a, b) => Math.abs((layerOrder[a.layer] ?? 0) - currentLayer) - Math.abs((layerOrder[b.layer] ?? 0) - currentLayer))
        .slice(0, 4);
    const downstream = nodes
        .filter(node => node.id !== exp.nodeId)
        .filter(node => childIds.has(node.id) || (layerOrder[node.layer] ?? 0) > currentLayer)
        .sort((a, b) => (layerOrder[a.layer] ?? 0) - (layerOrder[b.layer] ?? 0))
        .slice(0, 4);
    return { upstream, current, downstream };
}

function nodePowerScore(node) {
    const leaderCount = (node.companies || []).filter(company => company.is_leader).length;
    const avgGross = average((node.companies || []).map(company => company.gross_margin), 20);
    const supplyPremium = node.supply_level === 'risky' ? 36 : node.supply_level === 'tight' ? 26 : 10;
    const domesticPenalty = typeof node.domestic_rate === 'number' ? (100 - node.domestic_rate) * .12 : 8;
    return Math.max(8, 24 + supplyPremium + leaderCount * 8 + avgGross * .16 + domesticPenalty);
}

function layerDistribution(industry, mode = 'power') {
    if (!industry) return [];
    const buckets = new Map();
    (industry.nodes || []).forEach(node => {
        const key = node.layer || 'unknown';
        const companies = node.companies || [];
        const score = mode === 'profit'
            ? Math.max(8, average(companies.map(company => company.gross_margin), 18) + average(companies.map(company => company.revenue_ratio), 35) * .18)
            : nodePowerScore(node);
        buckets.set(key, (buckets.get(key) || 0) + score);
    });
    const total = Array.from(buckets.values()).reduce((a, b) => a + b, 0) || 1;
    return Array.from(buckets.entries())
        .sort((a, b) => (layerOrder[a[0]] ?? 9) - (layerOrder[b[0]] ?? 9))
        .map(([layer, value]) => ({ name: layerName[layer] || layer, value: value / total * 100 }));
}

function latestByCode(report) {
    const map = new Map();
    (report?.stocks || []).forEach(stock => {
        codeKeys(stock.code).forEach(key => map.set(key, stock));
    });
    return map;
}

function stockTrendItems(stock) {
    if (!stock || state.history.length < 2) return [];
    const previous = state.history[1];
    const prevMap = latestByCode(previous);
    const prev = codeKeys(stock.code).map(key => prevMap.get(key)).find(Boolean);
    if (!prev) return [];
    const items = [];
    const probDelta = Number(stock.probability || 0) - Number(prev.probability || 0);
    items.push({
        name: '概率变化',
        value: `${fmtPct(prev.probability)} → ${fmtPct(stock.probability)}`,
        delta: `${probDelta > 0 ? '+' : ''}${fmtPct(probDelta)}`,
        cls: probDelta >= 0 ? 'good' : 'bad',
    });
    const priceDelta = Number(stock.price || 0) - Number(prev.price || 0);
    if (prev.price) {
        items.push({
            name: '价格变化',
            value: `${fmtNum(prev.price, 2)} → ${fmtNum(stock.price, 2)}`,
            delta: `${priceDelta > 0 ? '+' : ''}${fmtNum(priceDelta, 2)}`,
            cls: priceDelta >= 0 ? 'good' : 'bad',
        });
    }
    if (String(prev.risk_label || '') !== String(stock.risk_label || '')) {
        items.push({ name: '风险标签', value: `${prev.risk_label || '无'} → ${stock.risk_label || '无'}`, delta: '变化', cls: 'warn' });
    }
    return items;
}

function buildRiskList(stock, watch) {
    const risks = [];
    const exposures = getExposures(stock?.code || watch?.code);
    const dims = stockDimensions(stock);
    const add = (title, desc, severity = 'medium', action = '') => risks.push({ title, desc, severity, action });
    if (!stock) {
        add('缺少最新股票分析结果', '当前监控股票尚未生成最新分析，价格、资金和盈利侧判断不完整。', 'medium', '点击右上角刷新标识重新抓取分析数据。');
    } else {
        if (Number(stock.probability || 0) < 40 || String(stock.risk_label || '').includes('危险')) {
            add('价格与风险评分偏弱', `上涨概率 ${fmtPct(stock.probability)}，风险标签为 ${stock.risk_label || '未标注'}。`, Number(stock.probability || 0) < 30 ? 'high' : 'medium', stock.risk_advice || '控制仓位，等待反转证据。');
        }
        if (Number(stock.profit_growth || 0) < -50 || Number(stock.revenue_growth || 0) < -35) {
            add('盈利侧验证不足', `营收增速 ${fmtPct(stock.revenue_growth)}，利润增速 ${fmtPct(stock.profit_growth)}。`, Number(stock.profit_growth || 0) < -100 ? 'high' : 'medium', '复核财报、订单和费用端变化。');
        }
        if (dims.divergent) {
            add('三维交叉验证出现背离', `供给 ${fmtNum(dims.supply, 0)}、需求 ${fmtNum(dims.demand, 0)}、盈利 ${fmtNum(dims.profit, 0)}，分差 ${fmtNum(dims.spread, 0)}。`, dims.spread >= 35 ? 'high' : 'medium', '确认背离来自数据滞后还是基本面变化。');
        }
        if (Number(stock.max_position || 0) <= 0) {
            add('仓位建议为零', '系统风控给出的最大仓位为 0%，不适合直接进攻。', 'high', '只保留观察，等待概率和盈利侧改善。');
        }
    }
    exposures.filter(exp => exp.supplyLevel === 'risky').forEach(exp => {
        add('供应链断供风险暴露', `${exp.industry} / ${exp.nodeName} 国产率 ${fmtPct(exp.domesticRate, 0)}，${exp.priceNote || '关键上游变量需要持续跟踪'}。`, 'high', '跟踪国产替代进度、上游产能和客户验证。');
    });
    if (!exposures.length) {
        add('产业链归属缺失', '当前股票未命中产业链库，无法完整判断其业务链位置与上下游约束。', 'low', '补充公司所处环节、核心原材料和客户结构。');
    }
    stockEvents(stock?.code || watch?.code).filter(ev => daysUntil(ev.event_date) < 0 && ev.status === 'pending').forEach(ev => {
        add('事件节点已逾期', `${ev.event_date} 的「${ev.event_title}」仍为待跟踪状态。`, 'medium', '补齐事件结论，避免信息闭环缺失。');
    });
    return risks.slice(0, 8);
}

function addSignal(bucket, type, title, desc, options = {}) {
    bucket[type].push({
        title,
        desc,
        severity: options.severity || 'medium',
        dimension: options.dimension || '',
        identifiedDate: options.identifiedDate || dataDate(options.stock),
        basis: options.basis || `股票分析结果 ${dataDate(options.stock)}`,
        targetDate: options.targetDate || '',
    });
}

function buildStockSignals(stock, watch) {
    const signals = { good: [], bad: [], watch: [] };
    const exposures = getExposures(stock?.code || watch?.code);
    const dims = stockDimensions(stock);
    const analysisDate = dataDate(stock);
    if (stock) {
        if (Number(stock.probability || 0) >= 55) {
            addSignal(signals, 'good', '概率进入进攻区', `上涨概率 ${fmtPct(stock.probability)}，价格/情绪侧给出正向提示。`, {
                severity: Number(stock.probability || 0) >= 62 ? 'high' : 'medium',
                dimension: '价格/情绪',
                identifiedDate: analysisDate,
                basis: `股票分析结果 ${analysisDate}`,
                stock,
            });
        }
        if (Number(stock.revenue_growth || 0) > 20 || Number(stock.profit_growth || 0) > 20) {
            addSignal(signals, 'good', '成长侧数据改善', `营收增速 ${fmtPct(stock.revenue_growth)}，利润增速 ${fmtPct(stock.profit_growth)}。`, {
                severity: 'medium',
                dimension: '盈利侧',
                identifiedDate: analysisDate,
                basis: `基本面数据 ${analysisDate}`,
                stock,
            });
        }
        if (Number(stock.probability || 0) < 40 || String(stock.risk_label || '').includes('危险')) {
            addSignal(signals, 'bad', '风险评分偏弱', `概率 ${fmtPct(stock.probability)}，${stock.risk_advice || stock.reason || '需要控制仓位。'}`, {
                severity: Number(stock.probability || 0) < 30 ? 'high' : 'medium',
                dimension: '价格/风控',
                identifiedDate: analysisDate,
                basis: `股票分析结果 ${analysisDate}`,
                stock,
            });
        }
        if (Number(stock.revenue_growth || 0) < -35 || Number(stock.profit_growth || 0) < -50) {
            addSignal(signals, 'bad', '基本面承压', `营收增速 ${fmtPct(stock.revenue_growth)}，利润增速 ${fmtPct(stock.profit_growth)}，盈利验证没有跟上。`, {
                severity: Number(stock.profit_growth || 0) < -100 ? 'high' : 'medium',
                dimension: '盈利侧',
                identifiedDate: analysisDate,
                basis: `基本面数据 ${analysisDate}`,
                stock,
            });
        }
        if ((stock.signals || []).includes('KDJ_OVERSOLD')) {
            addSignal(signals, 'watch', '出现超卖反弹线索', '技术面提示 KDJ 超卖，但需要成交量、需求和盈利同步确认。', {
                severity: 'low',
                dimension: '技术面',
                identifiedDate: analysisDate,
                basis: `技术指标 ${analysisDate}`,
                stock,
            });
        }
        if (dims.divergent) {
            addSignal(signals, 'watch', '三维指标背离', `供给 ${fmtNum(dims.supply, 0)}、需求 ${fmtNum(dims.demand, 0)}、盈利 ${fmtNum(dims.profit, 0)} 不同步。`, {
                severity: dims.spread >= 35 ? 'high' : 'medium',
                dimension: '交叉验证',
                identifiedDate: analysisDate,
                basis: `三维交叉验证 ${analysisDate}`,
                stock,
            });
        }
    } else {
        addSignal(signals, 'watch', '等待首次分析', '该监控股票尚未生成最新结果，暂不能判断价格、需求和盈利侧信号。', {
            severity: 'medium',
            dimension: '数据覆盖',
            identifiedDate: analysisDate,
            basis: '监控列表',
        });
    }
    exposures.forEach(exp => {
        if (exp.supplyLevel === 'risky') {
            addSignal(signals, 'bad', '暴露在断供风险环节', `${exp.industry} / ${exp.nodeName} 国产率 ${fmtPct(exp.domesticRate, 0)}，供应链约束会影响估值弹性。`, {
                severity: 'high',
                dimension: '供给侧',
                identifiedDate: analysisDate,
                basis: `产业链库 + 股票分析结果 ${analysisDate}`,
            });
        } else if (exp.supplyLevel === 'tight') {
            addSignal(signals, 'good', '所在环节供给偏紧', `${exp.industry} / ${exp.nodeName} 供给偏紧，若需求持续，环节定价权更容易提升。`, {
                severity: 'medium',
                dimension: '供给侧',
                identifiedDate: analysisDate,
                basis: `产业链库 + 股票分析结果 ${analysisDate}`,
            });
        }
    });
    stockEvents(stock?.code || watch?.code).forEach(ev => {
        const d = daysUntil(ev.event_date);
        if (ev.status !== 'pending' || d === null) return;
        if (d < 0) {
            addSignal(signals, 'bad', `事件逾期：${ev.event_title}`, `事件已过期 ${Math.abs(d)} 天仍未完成跟踪，信息闭环缺失。`, {
                severity: 'medium',
                dimension: '事件',
                identifiedDate: analysisDate,
                basis: `事件台账 ${ev.event_date}`,
                targetDate: ev.event_date,
            });
        } else if (d <= 60) {
            addSignal(signals, 'watch', `临近事件：${ev.event_title}`, `${d === 0 ? '今天' : `${d} 天后`}触发，适合作为交易前验证点。`, {
                severity: d <= 7 || ev.importance === 'high' ? 'high' : 'medium',
                dimension: '事件',
                identifiedDate: analysisDate,
                basis: `事件台账 ${ev.event_date}`,
                targetDate: ev.event_date,
            });
        }
    });
    ['good', 'bad', 'watch'].forEach(type => {
        signals[type] = signals[type].slice(0, 8);
    });
    return signals;
}

function parseBaseDate() {
    const raw = state.stockResult?.date || new Date().toISOString().slice(0, 10);
    const date = new Date(`${raw}T00:00:00`);
    return Number.isNaN(date.getTime()) ? new Date() : date;
}

function dateStr(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
}

function nextMonthDate(day = 10) {
    const base = parseBaseDate();
    const out = new Date(base.getFullYear(), base.getMonth() + 1, day);
    return dateStr(out);
}

function disclosureDeadlines() {
    const base = parseBaseDate();
    const y = base.getFullYear();
    const candidates = [
        { date: `${y}-04-30`, title: '年报/一季报披露截止窗口' },
        { date: `${y}-08-31`, title: '半年报披露截止窗口' },
        { date: `${y}-10-31`, title: '三季报披露截止窗口' },
        { date: `${y + 1}-04-30`, title: '年报/一季报披露截止窗口' },
    ];
    return candidates.filter(item => new Date(`${item.date}T00:00:00`) >= base).slice(0, 3);
}

function industryEvents(exp) {
    if (!exp) return [];
    const leaders = compactCompanyList((exp.industryObj?.nodes || []).filter(node => node.layer === exp.layer), 3)
        .map(company => company.name)
        .join('、');
    const out = disclosureDeadlines().map(item => ({
        event_date: item.date,
        event_title: `${exp.industry}：${item.title}`,
        event_type: '行业披露',
        event_desc: leaders ? `重点观察同环节龙头 ${leaders} 的订单、毛利率和产能表述。` : '重点观察同环节龙头的订单、毛利率和产能表述。',
        importance: 'high',
        source: '行业强关联',
    }));
    out.push({
        event_date: nextMonthDate(10),
        event_title: `${exp.nodeName} 价格/供需月度观察`,
        event_type: '行业数据',
        event_desc: exp.priceNote || '跟踪原材料价格、供给状态和国产替代进度。',
        importance: exp.supplyLevel === 'risky' ? 'high' : 'normal',
        source: '行业强关联',
    });
    return out.slice(0, 4);
}

function barRows(items, cls = 'good') {
    if (!items.length) return '<div class="empty">暂无可计算分布</div>';
    return items.map(item => `
        <div class="bar-row">
            <div class="bar-name">${escapeHtml(item.name)}</div>
            <div class="bar"><div class="bar-fill ${cls}" style="width:${clamp(item.value)}%"></div></div>
            <div class="bar-value">${fmtPct(item.value, 0)}</div>
        </div>
    `).join('');
}

function renderMonitorTabs() {
    const box = document.getElementById('monitorTabs');
    const items = normalizedWatchlist();
    if (!items.length) {
        box.innerHTML = '<div class="ia-card"><div class="empty">暂无监控股票，点击右上角“新增”开始维护。</div></div>';
        return;
    }
    box.innerHTML = items.map(item => {
        const stock = findStockByCode(item.code);
        const decision = decisionForStock(stock);
        const eventCount = stockEvents(item.code).filter(ev => ev.status === 'pending').length;
        const active = sameCode(item.code, state.selectedCode) ? 'active' : '';
        const prob = stock ? fmtPct(stock.probability) : '待分析';
        return `
            <div class="monitor-card ${active}" role="button" tabindex="0" aria-pressed="${active ? 'true' : 'false'}" onclick="selectStock(${escapeJsArg(item.code)})" onkeydown="handleMonitorKey(event, ${escapeJsArg(item.code)})">
                <button class="monitor-close" type="button" title="删除监控股票" onclick="deleteWatchStock(${escapeJsArg(item.code)}, ${escapeJsArg(item.name)}, event)">×</button>
                <div class="monitor-name">${escapeHtml(item.name)}</div>
                <div class="monitor-code">${escapeHtml(item.code)}</div>
                <div class="monitor-meta">
                    <span class="tag ${decision.cls}">${escapeHtml(decision.label)}</span>
                    <span class="tag info">${escapeHtml(prob)}</span>
                    ${eventCount ? `<span class="tag warn">${eventCount} 个事件</span>` : ''}
                </div>
            </div>
        `;
    }).join('');
}

function renderMetric(label, value, note = '', cls = '') {
    return `
        <div class="metric ${cls}">
            <div class="metric-label">${escapeHtml(label)}</div>
            <div class="metric-value">${escapeHtml(value)}</div>
            <div class="metric-note">${escapeHtml(note)}</div>
        </div>
    `;
}

function renderHero(ctx) {
    const { watch, stock } = ctx;
    const exp = primaryExposure(stock, watch);
    const company = exp?.company;
    const decision = decisionForStock(stock);
    const concepts = stockConceptTags(stock);
    const title = watch?.name || stock?.name || '未选择股票';
    const code = watch?.code || stock?.code || '-';
    const business = company?.analysis || company?.core || stock?.reason || '暂无企业概要数据，建议补充产业链归属、主营业务和核心客户信息。';
    const subParts = [
        code,
        stock?.sector || exp?.industry || '行业待补充',
        exp ? `${exp.industry} / ${exp.nodeName}` : '',
    ].filter(Boolean);
    return `
        <section class="detail-hero">
            <div>
                <div class="tagline">
                    <span class="tag ${decision.cls}">${escapeHtml(decision.label)}</span>
                    ${concepts.slice(0, 4).map(tag => `<span class="tag info">${escapeHtml(tag)}</span>`).join('')}
                    ${exp ? `<span class="tag warn">${escapeHtml(exp.layerName)}</span>` : '<span class="tag">产业链待匹配</span>'}
                </div>
                <div class="hero-name">${escapeHtml(title)}</div>
                <div class="hero-sub">${escapeHtml(subParts.join(' · '))}</div>
                <div class="hero-text">${escapeHtml(business)}</div>
            </div>
            <div class="hero-metrics">
                ${renderMetric('上涨概率', stock ? fmtPct(stock.probability) : '-', stock ? `数据 ${dataDate(stock)}` : '待刷新', stock ? decision.cls : 'warn')}
                ${renderMetric('当前价格', stock ? fmtNum(stock.price, 2) : '-', stock ? `涨跌 ${fmtPct(stock.change_pct)}` : '待刷新')}
                ${renderMetric('仓位上限', stock ? fmtPct(Number(stock.max_position || 0) * 100, 0) : '-', stock?.risk_label || '待分析', Number(stock?.max_position || 0) > 0 ? 'good' : 'bad')}
                ${renderMetric('行业地位', company?.market_share || company?.core || '-', exp?.industry || '待补充')}
            </div>
        </section>
    `;
}

function renderCompanySummary(ctx) {
    const { watch, stock } = ctx;
    const exp = primaryExposure(stock, watch);
    const company = exp?.company;
    const products = unique([...(exp?.node?.core_products || []), company?.core].filter(Boolean)).slice(0, 5);
    const items = [
        {
            title: '行业地位',
            desc: company?.market_share || company?.core || (exp ? `${exp.industry} ${exp.nodeName} 环节参与者。` : '暂未匹配到产业链库中的行业地位描述。'),
        },
        {
            title: '主营业务方向',
            desc: products.length ? products.join('、') : stock?.reason || '暂无主营业务方向描述。',
        },
        {
            title: '当前投资定位',
            desc: stock ? `系统给出的上涨概率为 ${fmtPct(stock.probability)}，风险标签为 ${stock.risk_label || '未标注'}，建议仓位上限 ${fmtPct(Number(stock.max_position || 0) * 100, 0)}。` : '该股票尚未生成最新投资分析结果。',
        },
    ];
    return `
        <section class="ia-card">
            <div class="ia-card-hd">
                <div class="ia-card-title">一、企业概要信息</div>
                <div class="ia-card-meta">${escapeHtml(dataDate(stock))}</div>
            </div>
            <div class="ia-card-pad summary-list">
                ${items.map(item => `
                    <div class="summary-item">
                        <div class="summary-title">${escapeHtml(item.title)}</div>
                        <div class="summary-desc">${escapeHtml(item.desc)}</div>
                    </div>
                `).join('')}
            </div>
        </section>
    `;
}

function renderBusinessModel(ctx) {
    const { watch, stock } = ctx;
    const exp = primaryExposure(stock, watch);
    if (!exp) {
        return `
            <section class="ia-card">
                <div class="ia-card-hd"><div class="ia-card-title">二、企业商业模式拆解</div><div class="ia-card-meta">产业链待补充</div></div>
                <div class="ia-card-pad"><div class="empty">当前股票未命中产业链库，暂不能生成上下游、定价权和盈利分布分析。</div></div>
            </section>
        `;
    }
    const rel = relatedChainNodes(exp);
    const upstreamCompanies = compactCompanyList(rel.upstream);
    const currentCompanies = compactCompanyList(rel.current);
    const downstreamCompanies = compactCompanyList(rel.downstream);
    const power = layerDistribution(exp.industryObj, 'power');
    const profit = layerDistribution(exp.industryObj, 'profit');
    const company = exp.company;
    const modelDesc = [
        company?.core ? `公司核心业务：${company.core}` : '',
        company?.revenue_ratio ? `相关业务收入占比约 ${fmtPct(company.revenue_ratio, 0)}` : '',
        company?.gross_margin ? `毛利率约 ${fmtPct(company.gross_margin, 1)}` : '',
        company?.client_concentration ? `客户集中度约 ${fmtPct(company.client_concentration, 0)}` : '',
    ].filter(Boolean).join('；') || exp.node.description || '暂无盈利商业模式细节。';
    const companyLines = items => items.length
        ? items.map(company => `<div class="company-line">${escapeHtml(company.name)} · ${escapeHtml(company.nodeName)}${company.core ? ` · ${escapeHtml(company.core)}` : ''}</div>`).join('')
        : '<div class="company-line">暂无明确企业样本</div>';
    return `
        <section class="ia-card">
            <div class="ia-card-hd">
                <div class="ia-card-title">二、企业商业模式拆解</div>
                <div class="ia-card-meta">${escapeHtml(exp.industry)} · ${escapeHtml(exp.nodeName)}</div>
            </div>
            <div class="ia-card-pad model-list">
                <div class="model-item">
                    <div class="model-title">业务链位置</div>
                    <div class="model-desc">当前处于「${escapeHtml(exp.layerName)} / ${escapeHtml(exp.nodeName)}」环节。${escapeHtml(exp.node.description || '')}</div>
                </div>
                <div class="chain-grid">
                    <div class="chain-box"><div class="chain-title">上游企业</div>${companyLines(upstreamCompanies)}</div>
                    <div class="chain-box"><div class="chain-title">同环节企业</div>${companyLines(currentCompanies)}</div>
                    <div class="chain-box"><div class="chain-title">下游企业</div>${companyLines(downstreamCompanies)}</div>
                </div>
                <div class="ia-grid two">
                    <div class="model-item">
                        <div class="model-title">行业定价话语权权重分布</div>
                        <div class="model-desc">基于供给状态、龙头数量、国产率与毛利率估算。</div>
                        ${barRows(power, 'warn')}
                    </div>
                    <div class="model-item">
                        <div class="model-title">盈利分布情况</div>
                        <div class="model-desc">基于各环节样本公司毛利率和相关业务占比估算。</div>
                        ${barRows(profit, 'good')}
                    </div>
                </div>
                <div class="model-item">
                    <div class="model-title">盈利商业模式分析</div>
                    <div class="model-desc">${escapeHtml(modelDesc)}</div>
                </div>
            </div>
        </section>
    `;
}

function renderRiskList(ctx) {
    const risks = buildRiskList(ctx.stock, ctx.watch);
    return `
        <section class="ia-card">
            <div class="ia-card-hd"><div class="ia-card-title">三、风险清单</div><div class="ia-card-meta">${risks.length} 项</div></div>
            <div class="ia-card-pad risk-list">
                ${risks.length ? risks.map(risk => `
                    <div class="risk-item ${escapeHtml(risk.severity)}">
                        <div class="risk-title">${escapeHtml(risk.title)}</div>
                        <div class="risk-desc">${escapeHtml(risk.desc)}</div>
                        ${risk.action ? `<div class="risk-desc"><strong>动作：</strong>${escapeHtml(risk.action)}</div>` : ''}
                    </div>
                `).join('') : '<div class="empty">暂无显著风险项</div>'}
            </div>
        </section>
    `;
}

function renderSignalColumn(title, type, items) {
    const cls = type === 'good' ? 'good' : type === 'bad' ? 'bad' : 'watch';
    return `
        <div class="signal-column">
            <div class="signal-column-hd"><div class="signal-kind">${escapeHtml(title)}</div><div class="ia-card-meta">${items.length} 条</div></div>
            <div class="signal-list">
                ${items.length ? items.map(sig => `
                    <div class="signal ${cls}">
                        <div class="signal-title">${escapeHtml(sig.title)}</div>
                        <div class="signal-desc">${escapeHtml(sig.desc)}</div>
                        <div class="signal-meta">
                            <span>识别日期：${escapeHtml(sig.identifiedDate)}</span>
                            <span>依据：${escapeHtml(sig.basis)}</span>
                            ${sig.targetDate ? `<span>目标日期：${escapeHtml(sig.targetDate)}</span>` : ''}
                            ${sig.dimension ? `<span>${escapeHtml(sig.dimension)}</span>` : ''}
                        </div>
                    </div>
                `).join('') : '<div class="empty">暂无信号</div>'}
            </div>
        </div>
    `;
}

function renderSignalsSection(ctx) {
    const signals = buildStockSignals(ctx.stock, ctx.watch);
    const total = signals.good.length + signals.bad.length + signals.watch.length;
    return `
        <section class="ia-card">
            <div class="ia-card-hd">
                <div class="ia-card-title">四、投资信号追踪</div>
                <div class="ia-card-meta">${total} 条信号</div>
            </div>
            <div class="ia-card-pad signal-board">
                ${renderSignalColumn('利好信号', 'good', signals.good)}
                ${renderSignalColumn('利空信号', 'bad', signals.bad)}
                ${renderSignalColumn('待观察信号', 'watch', signals.watch)}
            </div>
        </section>
    `;
}

function renderDim(name, value, reasons = []) {
    const cls = scoreClass(value);
    return `
        <div>
            <div class="bar-row">
                <div class="bar-name">${escapeHtml(name)}</div>
                <div class="bar"><div class="bar-fill ${cls}" style="width:${clamp(value)}%"></div></div>
                <div class="bar-value">${fmtNum(value, 0)}</div>
            </div>
            <div class="model-desc">${escapeHtml((reasons || []).slice(0, 3).join('；'))}</div>
        </div>
    `;
}

function dimensionHistory(code) {
    return codeKeys(code).map(key => state.dimensionHistoryByCode[key]).find(Boolean) || [];
}

function sparklinePath(values, w = 180, h = 40) {
    if (values.length < 2) return '';
    const step = w / (values.length - 1);
    return values.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(h - clamp(v) / 100 * h).toFixed(1)}`).join(' ');
}

function renderDimTrendRow(label, values, color) {
    const latest = values[values.length - 1];
    return `
        <div class="bar-row" style="grid-template-columns: minmax(72px, 92px) minmax(0, 1fr) minmax(42px, 48px); align-items: center;">
            <div class="bar-name">${escapeHtml(label)}</div>
            <svg width="100%" height="32" viewBox="0 0 180 40" preserveAspectRatio="none" style="display:block;">
                <path d="${sparklinePath(values)}" fill="none" stroke="${color}" stroke-width="2.5" vector-effect="non-scaling-stroke"></path>
            </svg>
            <div class="bar-value">${fmtNum(latest, 0)}</div>
        </div>
    `;
}

function renderDimTrendCard(stock, watch) {
    const code = stock?.code || watch?.code;
    const hist = dimensionHistory(code);
    if (hist.length < 2) {
        return `
            <div class="model-item">
                <div class="model-title">三维评分历史走势</div>
                <div class="model-desc">${hist.length === 1 ? `已积累 1 天快照（${escapeHtml(hist[0].date)}），需要至少 2 天数据才能画出趋势线。` : '暂无历史快照，每日 16:00 自动积累（investment_snapshot.py + launchd）。'}</div>
            </div>
        `;
    }
    const supply = hist.map(h => h.supply);
    const demand = hist.map(h => h.demand);
    const profit = hist.map(h => h.profit);
    return `
        <div class="model-item">
            <div class="model-title">三维评分历史走势</div>
            <div class="model-desc">${escapeHtml(hist[0].date)} 至 ${escapeHtml(hist[hist.length - 1].date)}，共 ${hist.length} 个快照点。</div>
            ${renderDimTrendRow('供给侧', supply, '#64748b')}
            ${renderDimTrendRow('需求侧', demand, '#3b82f6')}
            ${renderDimTrendRow('盈利侧', profit, '#22c55e')}
        </div>
    `;
}

function renderTracking(ctx) {
    const stock = ctx.stock;
    const dims = stockDimensions(stock);
    const trends = stockTrendItems(stock);
    return `
        <section class="ia-card">
            <div class="ia-card-hd">
                <div class="ia-card-title">五、股票跟踪与三维交叉验证</div>
                <div class="ia-card-meta">${stock ? `数据 ${dataDate(stock)}` : '待分析'}</div>
            </div>
            <div class="ia-card-pad track-grid">
                <div>
                    <div class="price-grid">
                        ${renderMetric('现价', stock ? fmtNum(stock.price, 2) : '-', stock ? `涨跌 ${fmtPct(stock.change_pct)}` : '')}
                        ${renderMetric('止损', stock ? fmtNum(stock.stop_loss, 2) : '-', stock ? `止盈 ${fmtNum(stock.take_profit, 2)}` : '')}
                        ${renderMetric('仓位', stock ? fmtPct(Number(stock.max_position || 0) * 100, 0) : '-', stock?.risk_label || '')}
                    </div>
                    <div class="summary-list" style="margin-top:10px;">
                        ${trends.length ? trends.map(item => `
                            <div class="summary-item">
                                <div class="summary-title">${escapeHtml(item.name)} <span class="tag ${item.cls}">${escapeHtml(item.delta)}</span></div>
                                <div class="summary-desc">${escapeHtml(item.value)}</div>
                            </div>
                        `).join('') : '<div class="summary-item"><div class="summary-title">边际变化</div><div class="summary-desc">历史样本不足或未命中上一期数据。</div></div>'}
                    </div>
                </div>
                <div class="model-list">
                    ${renderDim('供给侧', dims.supply, dims.reasons.supply)}
                    ${renderDim('需求侧', dims.demand, dims.reasons.demand)}
                    ${renderDim('盈利侧', dims.profit, dims.reasons.profit)}
                    <div class="summary-item">
                        <div class="summary-title">${dims.divergent ? '三维背离' : '三维同步'}</div>
                        <div class="summary-desc">供需盈最大分差 ${fmtNum(dims.spread, 0)}。任一维度明显背离都应进入观察或复核队列。</div>
                    </div>
                    ${renderDimTrendCard(ctx.stock, ctx.watch)}
                </div>
            </div>
        </section>
    `;
}

function renderEventsSection(ctx) {
    const code = ctx.stock?.code || ctx.watch?.code;
    const exp = primaryExposure(ctx.stock, ctx.watch);
    const own = stockEvents(code).map(ev => ({ ...ev, source: '企业自身' }));
    const industry = industryEvents(exp);
    const all = [...own, ...industry]
        .sort((a, b) => String(a.event_date).localeCompare(String(b.event_date)))
        .slice(0, 12);
    return `
        <section class="ia-card">
            <div class="ia-card-hd"><div class="ia-card-title">六、重要事件节点</div><div class="ia-card-meta">${own.length} 个企业节点，${industry.length} 个行业节点</div></div>
            <div class="ia-card-pad event-list">
                ${all.length ? all.map(ev => {
                    const d = daysUntil(ev.event_date);
                    const tagCls = d !== null && d < 0 ? 'bad' : d !== null && d <= 30 ? 'warn' : 'info';
                    const dayText = d === null ? ev.importance || '' : d < 0 ? `逾期 ${Math.abs(d)} 天` : d === 0 ? '今天' : `${d} 天后`;
                    return `
                        <div class="event-card">
                            <div class="tagline">
                                <span class="tag ${tagCls}">${escapeHtml(dayText)}</span>
                                <span class="tag info">${escapeHtml(ev.source || '企业自身')}</span>
                                <span class="tag">${escapeHtml(ev.event_type || '事件')}</span>
                            </div>
                            <div class="event-title">${escapeHtml(ev.event_date)} · ${escapeHtml(ev.event_title)}</div>
                            <div class="event-desc">${escapeHtml(ev.event_desc || '等待补充跟踪结论')}</div>
                        </div>
                    `;
                }).join('') : '<div class="empty">暂无重要事件节点</div>'}
            </div>
        </section>
    `;
}

const FACTOR_SCORE_FIELD = {
    technical: 'tech_score',
    fundamental: 'fund_score',
    money_flow: 'money_score',
    sentiment: 'sentiment_score',
    chip: 'chip_score',
};

function factorWeights(code) {
    return codeKeys(code).map(key => state.factorWeightsByCode[key]).find(Boolean) || null;
}

function factorBacktest(code) {
    return codeKeys(code).map(key => state.factorBacktestByCode[key]).find(Boolean) || null;
}

function renderFactorBacktestBlock(code) {
    const bt = factorBacktest(code);
    if (!bt) return '';
    const weights = factorWeights(code) || Object.keys(FACTOR_SCORE_FIELD).map(fk => ({ factor_key: fk, factor_name: fk }));
    const updated = state.factorBacktestGeneratedAt ? new Date(state.factorBacktestGeneratedAt * 1000).toLocaleDateString('zh-CN') : '';
    const worthChanging = bt.improvement >= 3;
    const recLine = weights.map(w => `${w.factor_name}${fmtPct((bt.rec_weights[w.factor_key] || 0) * 100, 0)}`).join(' / ');
    return `
        <div class="summary-item">
            <div class="summary-title">历史权重回测（${escapeHtml(updated)} 更新，近 ${bt.days} 个交易日真实K线）</div>
            <div class="summary-desc">
                当前权重配置历史准确率 ${fmtPct(bt.current_accuracy)}，回测最优方案「${escapeHtml(bt.best_scheme)}」准确率 ${fmtPct(bt.best_accuracy)}，
                差距 ${fmtNum(bt.improvement, 1)} 个百分点。
                ${worthChanging
                    ? `建议权重可调整为：${recLine}。`
                    : '差距小于 3 个百分点，当前权重基本合理，不建议调整。'}
                这是整套权重方案级别的回测（比较不同权重组合的历史表现），不是单个因子独立剥离验证。
            </div>
        </div>
    `;
}

function renderFactorAttribution(ctx) {
    const { watch, stock } = ctx;
    const code = stock?.code || watch?.code;
    if (!stock) {
        return `
            <section class="ia-card">
                <div class="ia-card-hd"><div class="ia-card-title">七、因子归因分析</div><div class="ia-card-meta">待分析</div></div>
                <div class="ia-card-pad"><div class="empty">该监控股票尚未生成最新分析结果，暂不能拆解因子贡献。</div></div>
            </section>
        `;
    }
    const weights = factorWeights(code) || Object.keys(FACTOR_SCORE_FIELD).map(fk => ({
        factor_key: fk, factor_name: fk, weight: 0, is_override: false,
    }));
    const rows = weights.map(w => {
        const field = FACTOR_SCORE_FIELD[w.factor_key];
        const raw = Number(stock[field]);
        const contribution = (_isNum(raw) ? raw : 0) * Number(w.weight || 0);
        return { ...w, raw: _isNum(raw) ? raw : null, contribution };
    });
    const totalContribution = rows.reduce((sum, r) => sum + r.contribution, 0);
    const probability = Number(stock.probability || 0);
    const delta = probability - totalContribution;
    return `
        <section class="ia-card">
            <div class="ia-card-hd">
                <div class="ia-card-title">七、因子归因分析</div>
                <div class="ia-card-meta">${weights.some(w => w.is_override) ? '含个股权重覆盖' : '使用全局默认权重'}</div>
            </div>
            <div class="ia-card-pad model-list">
                ${rows.map(r => `
                    <div>
                        <div class="bar-row">
                            <div class="bar-name">${escapeHtml(r.factor_name)}</div>
                            <div class="bar"><div class="bar-fill ${scoreClass(r.raw ?? 0)}" style="width:${clamp(r.raw ?? 0)}%"></div></div>
                            <div class="bar-value">${r.raw === null ? '-' : fmtNum(r.raw, 0)}</div>
                        </div>
                        <div class="model-desc">权重 ${fmtPct(r.weight * 100, 0)}${r.is_override ? '（个股覆盖）' : '（全局默认）'} · 贡献 ${fmtNum(r.contribution, 1)} 分</div>
                    </div>
                `).join('')}
                <div class="summary-item">
                    <div class="summary-title">五因子加权预估 ${fmtNum(totalContribution, 1)} 分</div>
                    <div class="summary-desc">系统最终给出的上涨概率为 ${fmtPct(probability)}，与五因子加权预估相差 ${fmtNum(delta, 1)} 分，差值来自权重表未覆盖的模型细节（如风控修正、极端值处理），仅供参考，不代表归因不完整。</div>
                </div>
                ${renderFactorBacktestBlock(code)}
            </div>
        </section>
    `;
}

function _isNum(v) {
    return v !== null && v !== undefined && !Number.isNaN(Number(v));
}

function stockNotes(code) {
    return state.notes.filter(n => sameCode(n.code, code));
}

function verdictCls(verdict) {
    if (verdict === '兑现') return 'good';
    if (verdict === '未兑现') return 'bad';
    return 'warn';
}

function renderDecisionReview(ctx) {
    const code = ctx.stock?.code || ctx.watch?.code;
    const name = ctx.stock?.name || ctx.watch?.name || '';
    if (!code) return '';
    const notes = stockNotes(code);
    return `
        <section class="ia-card">
            <div class="ia-card-hd">
                <div class="ia-card-title">八、决策复盘</div>
                <div class="ia-card-meta">${notes.length} 条记录</div>
            </div>
            <div class="ia-card-pad summary-list">
                ${notes.length ? notes.map(n => `
                    <div class="summary-item">
                        <div class="summary-title">
                            ${escapeHtml(n.created_at ? n.created_at.slice(0, 10) : '')} 关注理由
                            ${n.resolved ? `<span class="tag ${verdictCls(n.verdict)}">${escapeHtml(n.verdict)}</span>` : '<span class="tag warn">待复盘</span>'}
                        </div>
                        <div class="summary-desc">${escapeHtml(n.note)}${n.target_date ? `（预期验证点：${escapeHtml(n.target_date)}）` : ''}</div>
                        ${n.resolved
                            ? `<div class="summary-desc">${n.resolved_note ? `复盘：${escapeHtml(n.resolved_note)}` : ''}</div>`
                            : `<div class="modal-actions" style="justify-content:flex-start; padding-top:8px;">
                                <button class="ia-btn ghost" type="button" onclick="resolveDecisionNote(${n.id}, '兑现')">标记兑现</button>
                                <button class="ia-btn ghost" type="button" onclick="resolveDecisionNote(${n.id}, '部分兑现')">部分兑现</button>
                                <button class="ia-btn ghost" type="button" onclick="resolveDecisionNote(${n.id}, '未兑现')">未兑现</button>
                               </div>`
                        }
                    </div>
                `).join('') : '<div class="empty">还没有记录关注理由，用下面的表单记一条。</div>'}
                <form class="add-form" onsubmit="submitDecisionNote(event, ${escapeJsArg(code)}, ${escapeJsArg(name)})">
                    <textarea class="input full" id="decisionNoteInput" rows="2" placeholder="当初为什么关注这只股票？预期看到什么信号会验证这个判断？" required></textarea>
                    <input class="input" id="decisionTargetDate" type="date" placeholder="预期验证日期（可选）">
                    <div class="modal-actions full" style="justify-content:flex-start;">
                        <button class="ia-btn primary" type="submit">记录关注理由</button>
                    </div>
                </form>
            </div>
        </section>
    `;
}

async function submitDecisionNote(event, code, name) {
    event.preventDefault();
    const noteEl = document.getElementById('decisionNoteInput');
    const dateEl = document.getElementById('decisionTargetDate');
    const note = String(noteEl?.value || '').trim();
    if (!note) return;
    setProgress('正在记录关注理由...');
    try {
        const res = await fetch('/api/investment-analysis/notes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, name, note, target_date: dateEl?.value || '' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || '记录失败');
        setProgress('');
        await loadAll();
    } catch (e) {
        setProgress(`记录失败：${e.message}`);
    }
}

async function resolveDecisionNote(id, verdict) {
    const reflection = prompt(`记一句复盘小结（可留空）——判定为「${verdict}」`, '');
    if (reflection === null) return;
    setProgress('正在保存复盘结果...');
    try {
        const res = await fetch(`/api/investment-analysis/notes/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ verdict, resolved_note: reflection }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || '保存失败');
        setProgress('');
        await loadAll();
    } catch (e) {
        setProgress(`保存失败：${e.message}`);
    }
}

function renderDetail() {
    const ctx = selectedContext();
    const box = document.getElementById('stockDetail');
    if (!ctx.watch && !ctx.stock) {
        box.innerHTML = '<section class="ia-card"><div class="empty">暂无监控股票，点击右上角“新增”添加后开始分析。</div></section>';
        return;
    }
    const code = ctx.stock?.code || ctx.watch?.code;
    if (code) ensureKline(code);
    box.innerHTML = [
        renderHero(ctx),
        renderPriceChart(ctx),
        renderCompanySummary(ctx),
        renderBusinessModel(ctx),
        renderRiskList(ctx),
        renderSignalsSection(ctx),
        renderTracking(ctx),
        renderEventsSection(ctx),
        renderFactorAttribution(ctx),
        renderDecisionReview(ctx),
    ].join('');
}

function movingAverage(values, period) {
    return values.map((_, i) => {
        if (i < period - 1) return null;
        let sum = 0;
        for (let j = i - period + 1; j <= i; j++) sum += values[j];
        return sum / period;
    });
}

function renderPriceChart(ctx) {
    const code = ctx.stock?.code || ctx.watch?.code;
    const kline = code ? state.klineByCode[code] : null;
    const stock = ctx.stock;
    if (kline === undefined || kline === null) {
        return `<section class="ia-card"><div class="ia-card-hd"><div class="ia-card-title">价格走势与技术指标</div><div class="ia-card-meta">加载中…</div></div><div class="ia-card-pad"><div class="empty">正在读取历史K线…</div></div></section>`;
    }
    if (!kline.length) {
        return `<section class="ia-card"><div class="ia-card-hd"><div class="ia-card-title">价格走势与技术指标</div><div class="ia-card-meta">无数据</div></div><div class="ia-card-pad"><div class="empty">本地暂无该股历史K线，可在股票分析页「下载历史数据」后再看。</div></div>${renderTechnicals(stock)}</section>`;
    }
    const bars = kline.slice(-90);
    const W = 800, H = 260, padL = 4, padR = 4, padT = 8, padB = 8;
    const chartW = W - padL - padR, chartH = H - padT - padB;
    const highs = bars.map(b => b.high), lows = bars.map(b => b.low), closes = bars.map(b => b.close);
    const maxP = Math.max(...highs), minP = Math.min(...lows);
    const range = maxP - minP || 1;
    const step = chartW / bars.length;
    const bw = Math.max(1, step * 0.62);
    const yOf = p => padT + (maxP - p) / range * chartH;
    // 蜡烛：A股约定 红涨绿跌
    const candles = bars.map((b, i) => {
        const x = padL + i * step + step / 2;
        const up = b.close >= b.open;
        const color = up ? '#ef4444' : '#22c55e';
        const bodyTop = yOf(Math.max(b.open, b.close));
        const bodyH = Math.max(1, Math.abs(yOf(b.open) - yOf(b.close)));
        return `<line x1="${x.toFixed(1)}" y1="${yOf(b.high).toFixed(1)}" x2="${x.toFixed(1)}" y2="${yOf(b.low).toFixed(1)}" stroke="${color}" stroke-width="1"></line>`
            + `<rect x="${(x - bw / 2).toFixed(1)}" y="${bodyTop.toFixed(1)}" width="${bw.toFixed(1)}" height="${bodyH.toFixed(1)}" fill="${color}"></rect>`;
    }).join('');
    const maLine = (period, color) => {
        const ma = movingAverage(closes, period);
        const pts = ma.map((v, i) => v === null ? null : `${(padL + i * step + step / 2).toFixed(1)},${yOf(v).toFixed(1)}`).filter(Boolean).join(' ');
        return pts ? `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" opacity="0.9"></polyline>` : '';
    };
    const first = bars[0], last = bars[bars.length - 1];
    const periodReturn = ((last.close - first.close) / first.close) * 100;
    return `
        <section class="ia-card">
            <div class="ia-card-hd">
                <div class="ia-card-title">价格走势与技术指标</div>
                <div class="ia-card-meta">${escapeHtml(first.date)} ~ ${escapeHtml(last.date)} · 区间涨跌 ${fmtPct(periodReturn)}</div>
            </div>
            <div class="ia-card-pad">
                <svg class="kline-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
                    ${candles}
                    ${maLine(5, '#f59e0b')}
                    ${maLine(20, '#3b82f6')}
                </svg>
                <div class="kline-legend">
                    <span style="color:#f59e0b;">MA5</span>
                    <span style="color:#3b82f6;">MA20</span>
                    <span style="color:#ef4444;">阳线(收≥开)</span>
                    <span style="color:#22c55e;">阴线</span>
                    <span>最高 ${fmtNum(maxP, 2)} · 最低 ${fmtNum(minP, 2)}</span>
                </div>
                ${renderTechnicals(stock)}
            </div>
        </section>
    `;
}

function techClass(name, v, stock) {
    const n = Number(v);
    if (Number.isNaN(n)) return '';
    if (name === 'MACD') return n >= 0 ? 'good' : 'bad';
    if (name === 'KDJ-J') return n >= 80 ? 'bad' : n <= 20 ? 'good' : '';
    return '';
}

function renderTechnicals(stock) {
    if (!stock) return '';
    const price = Number(stock.price);
    const bbPos = (_isNum(stock.bb_upper) && _isNum(stock.bb_lower) && stock.bb_upper !== stock.bb_lower)
        ? (price - stock.bb_lower) / (stock.bb_upper - stock.bb_lower) * 100 : null;
    const items = [
        { label: 'MA20 乖离', value: _isNum(stock.distance_from_ma20) ? fmtPct(stock.distance_from_ma20) : '-' },
        { label: 'MACD 柱', value: _isNum(stock.macd_hist) ? fmtNum(stock.macd_hist, 3) : '-', cls: techClass('MACD', stock.macd_hist, stock) },
        { label: 'KDJ (K/D/J)', value: [stock.kdj_k, stock.kdj_d, stock.kdj_j].map(v => _isNum(v) ? fmtNum(v, 0) : '-').join('/'), cls: techClass('KDJ-J', stock.kdj_j, stock) },
        { label: '布林带位置', value: bbPos === null ? '-' : fmtPct(bbPos, 0), cls: bbPos === null ? '' : bbPos >= 90 ? 'bad' : bbPos <= 10 ? 'good' : '' },
        { label: '量比', value: _isNum(stock.vol_ratio) ? fmtNum(stock.vol_ratio, 2) : '-' },
        { label: '换手率', value: _isNum(stock.turnover_rate) ? fmtPct(stock.turnover_rate) : '-' },
        { label: 'OBV 斜率', value: _isNum(stock.obv_slope) ? fmtNum(stock.obv_slope, 3) : '-', cls: _isNum(stock.obv_slope) ? (stock.obv_slope >= 0 ? 'good' : 'bad') : '' },
        { label: '振幅', value: _isNum(stock.amplitude) ? fmtPct(stock.amplitude) : '-' },
    ];
    return `
        <div class="tech-grid">
            ${items.map(it => `<div class="tech-item"><div class="tech-label">${escapeHtml(it.label)}</div><div class="tech-value ${it.cls || ''}">${escapeHtml(it.value)}</div></div>`).join('')}
        </div>
    `;
}

async function ensureKline(code) {
    if (state.klineByCode[code] !== undefined) return;
    state.klineByCode[code] = null;  // 标记加载中，避免重复请求
    const res = await safeJson(`/api/investment-analysis/kline?code=${encodeURIComponent(code)}&days=120`);
    state.klineByCode[code] = Array.isArray(res?.kline) ? res.kline : [];
    // 仅当当前仍选中该股票时刷新详情，避免快速切换时错位
    if (sameCode(code, state.selectedCode)) renderDetail();
}

function selectStock(code) {
    state.selectedCode = code;
    renderAll();
}

function handleMonitorKey(event, code) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    selectStock(code);
}

function setProgress(text) {
    const box = document.getElementById('progressBox');
    box.textContent = text || '';
    box.classList.toggle('show', !!text);
}

function setWatchMessage(text, status = '') {
    const el = document.getElementById('watchManageMsg');
    if (!el) return;
    el.textContent = text || '';
    el.className = `add-message full ${status}`.trim();
}

function openAddModal() {
    document.getElementById('addModal').classList.add('show');
    setWatchMessage('');
    setTimeout(() => document.getElementById('addSearchInput')?.focus(), 30);
}

function closeAddModal() {
    document.getElementById('addModal').classList.remove('show');
    state.watchSearchSelected = null;
    renderWatchSearchResults([]);
    setWatchMessage('');
}

function renderWatchSearchResults(items, message = '') {
    const box = document.getElementById('watchSearchResults');
    if (!box) return;
    if (message) {
        box.innerHTML = `<div class="search-item"><span>${escapeHtml(message)}</span></div>`;
        box.classList.add('show');
        return;
    }
    if (!items.length) {
        box.innerHTML = '';
        box.classList.remove('show');
        return;
    }
    box.innerHTML = items.slice(0, 8).map(item => {
        const code = String(item.code || item[0] || '');
        const name = String(item.name || item[1] || '');
        return `
            <button class="search-item" type="button" onmousedown="selectWatchCandidate(${escapeJsArg(code)}, ${escapeJsArg(name)})">
                <span>${escapeHtml(name)}</span>
                <span class="monitor-code">${escapeHtml(code)}</span>
            </button>
        `;
    }).join('');
    box.classList.add('show');
}

function scheduleWatchSearch(value) {
    clearTimeout(watchSearchTimer);
    state.watchSearchSelected = null;
    setWatchMessage('');
    const q = String(value || '').trim();
    if (!q) {
        renderWatchSearchResults([]);
        return;
    }
    watchSearchTimer = setTimeout(() => searchWatchCandidates(q), 260);
}

async function searchWatchCandidates(q) {
    renderWatchSearchResults([], '正在搜索...');
    try {
        const res = await fetch(`/api/stock/search?q=${encodeURIComponent(q)}`);
        const list = await res.json();
        if (!res.ok) throw new Error(list.error || '搜索失败');
        const items = Array.isArray(list)
            ? list.map(item => ({ code: item.code || item[0], name: item.name || item[1] })).filter(item => item.code && item.name)
            : [];
        renderWatchSearchResults(items, items.length ? '' : '未找到匹配股票');
    } catch (e) {
        renderWatchSearchResults([], `搜索失败：${e.message}`);
    }
}

function selectWatchCandidate(code, name) {
    state.watchSearchSelected = { code, name };
    document.getElementById('addSearchInput').value = `${name} (${code})`;
    document.getElementById('watchCodeInput').value = code;
    document.getElementById('watchNameInput').value = name;
    renderWatchSearchResults([]);
    setWatchMessage(`已选择 ${name}，确认后加入监控。`);
}

async function addWatchlistFromForm(event) {
    if (event) event.preventDefault();
    const codeEl = document.getElementById('watchCodeInput');
    const nameEl = document.getElementById('watchNameInput');
    const btn = document.getElementById('btnAddWatch');
    const selected = state.watchSearchSelected || {};
    const code = String(codeEl?.value || selected.code || '').trim();
    const name = String(nameEl?.value || selected.name || '').trim();
    if (!code || !name) {
        setWatchMessage('请填写股票代码和名称，或先搜索选择一只股票。', 'bad');
        return;
    }
    if (isWatchlisted(code)) {
        setWatchMessage(`${name} ${code} 已在监控列表中。`, 'bad');
        return;
    }
    btn.disabled = true;
    setWatchMessage(`正在添加 ${name} ${code}...`);
    try {
        const res = await fetch('/api/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, name }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || '添加失败');
        state.watchlist = Array.isArray(data.stocks) ? data.stocks : [...state.watchlist, [code, name]];
        state.selectedCode = code;
        state.watchSearchSelected = null;
        document.getElementById('addSearchInput').value = '';
        codeEl.value = '';
        nameEl.value = '';
        setWatchMessage(`${name} ${code} 已加入监控。`, 'good');
        closeAddModal();
        renderAll();
    } catch (e) {
        setWatchMessage(`添加失败：${e.message}`, 'bad');
    } finally {
        btn.disabled = false;
    }
}

async function deleteWatchStock(code, name, event = null) {
    if (event) event.stopPropagation();
    if (!confirm(`确认从监控列表删除 ${name} ${code}？`)) return;
    setProgress(`正在删除 ${name} ${code}...`);
    try {
        const res = await fetch(`/api/watchlist/${encodeURIComponent(code)}`, { method: 'DELETE' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || '删除失败');
        state.watchlist = Array.isArray(data.stocks)
            ? data.stocks
            : state.watchlist.filter(item => !sameCode(normalizeWatchItem(item).code, code));
        const remaining = normalizedWatchlist();
        if (!remaining.some(item => sameCode(item.code, state.selectedCode))) {
            state.selectedCode = remaining[0]?.code || '';
        }
        renderAll();
        setProgress('');
    } catch (e) {
        setProgress(`删除失败：${e.message}`);
    }
}

function prevProbByCode() {
    const prev = state.history.length >= 2 ? state.history[1] : null;
    return prev ? latestByCode(prev) : new Map();
}

function overviewRows() {
    const prevMap = prevProbByCode();
    return normalizedWatchlist().map(item => {
        const stock = findStockByCode(item.code);
        const dims = stockDimensions(stock);
        const decision = decisionForStock(stock);
        const prev = codeKeys(item.code).map(key => prevMap.get(key)).find(Boolean);
        const prob = stock ? Number(stock.probability || 0) : null;
        const probDelta = (stock && prev) ? prob - Number(prev.probability || 0) : null;
        const primaryExp = primaryExposure(stock, item);
        return {
            code: item.code,
            name: item.name,
            hasStock: !!stock,
            probability: prob,
            probDelta,
            supply: dims.supply,
            demand: dims.demand,
            profit: dims.profit,
            divergent: dims.divergent,
            decision,
            riskLabel: stock?.risk_label || '',
            maxPosition: stock ? Number(stock.max_position || 0) : 0,
            eventCount: stockEvents(item.code).filter(ev => ev.status === 'pending').length,
            industry: stock?.sector || primaryExp?.industry || '',
        };
    });
}

function sortOverview(key) {
    if (state.overviewSort.key === key) state.overviewSort.asc = !state.overviewSort.asc;
    else state.overviewSort = { key, asc: false };
    renderOverview();
}

const OVERVIEW_COLS = [
    { key: 'name', label: '股票' },
    { key: 'probability', label: '上涨概率' },
    { key: 'probDelta', label: '较上期' },
    { key: 'supply', label: '供给' },
    { key: 'demand', label: '需求' },
    { key: 'profit', label: '盈利' },
    { key: 'maxPosition', label: '仓位上限' },
    { key: 'riskLabel', label: '风险' },
    { key: 'eventCount', label: '事件' },
];

function renderRankingTable(rows) {
    const { key, asc } = state.overviewSort;
    const sorted = [...rows].sort((a, b) => {
        const av = a[key], bv = b[key];
        if (typeof av === 'string' || typeof bv === 'string') {
            return String(av || '').localeCompare(String(bv || '')) * (asc ? 1 : -1);
        }
        const an = av === null || av === undefined ? -Infinity : Number(av);
        const bn = bv === null || bv === undefined ? -Infinity : Number(bv);
        return (an - bn) * (asc ? 1 : -1);
    });
    const head = OVERVIEW_COLS.map(col => {
        const cls = col.key === key ? `sorted ${asc ? 'asc' : ''}` : '';
        const align = ['probability', 'probDelta', 'supply', 'demand', 'profit', 'maxPosition', 'eventCount'].includes(col.key) ? 'ov-num' : '';
        return `<th class="${cls} ${align}" onclick="sortOverview(${escapeJsArg(col.key)})">${escapeHtml(col.label)}</th>`;
    }).join('');
    const body = sorted.map(r => {
        const active = sameCode(r.code, state.selectedCode) ? 'active' : '';
        const deltaCls = r.probDelta === null ? '' : r.probDelta >= 0 ? 'up' : 'down';
        const deltaTxt = r.probDelta === null ? '-' : `${r.probDelta > 0 ? '+' : ''}${fmtPct(r.probDelta)}`;
        return `
            <tr class="${active}" onclick="selectStock(${escapeJsArg(r.code)})">
                <td><span class="ov-name">${escapeHtml(r.name)}</span> <span class="ov-code">${escapeHtml(r.code)}</span>
                    <span class="tag ${r.decision.cls}">${escapeHtml(r.decision.label)}</span>${r.divergent ? ' <span class="tag warn">背离</span>' : ''}</td>
                <td class="ov-num">${r.probability === null ? '待分析' : fmtPct(r.probability)}</td>
                <td class="ov-num ov-delta ${deltaCls}">${deltaTxt}</td>
                <td class="ov-num">${fmtNum(r.supply, 0)}</td>
                <td class="ov-num">${fmtNum(r.demand, 0)}</td>
                <td class="ov-num">${fmtNum(r.profit, 0)}</td>
                <td class="ov-num">${r.hasStock ? fmtPct(r.maxPosition * 100, 0) : '-'}</td>
                <td>${r.riskLabel ? `<span class="tag ${String(r.riskLabel).includes('危险') || String(r.riskLabel).includes('高') ? 'bad' : 'warn'}">${escapeHtml(r.riskLabel)}</span>` : '-'}</td>
                <td class="ov-num">${r.eventCount || '-'}</td>
            </tr>
        `;
    }).join('');
    return `
        <section class="ia-card">
            <div class="ia-card-hd"><div class="ia-card-title">监控总览</div><div class="ia-card-meta">${rows.length} 只 · 点击表头排序</div></div>
            <div class="ia-card-pad ov-table-scroll">
                <table class="ov-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
            </div>
        </section>
    `;
}

function renderPortfolioCard(rows) {
    const analyzed = rows.filter(r => r.hasStock);
    const totalPosition = analyzed.reduce((s, r) => s + r.maxPosition, 0);
    const attackCount = rows.filter(r => r.decision.label === '偏进攻').length;
    const defenseCount = rows.filter(r => r.decision.label === '偏防守').length;
    const riskyCount = rows.filter(r => String(r.riskLabel).includes('危险') || String(r.riskLabel).includes('高')).length;
    const industryCount = {};
    analyzed.forEach(r => { if (r.industry) industryCount[r.industry] = (industryCount[r.industry] || 0) + 1; });
    const topIndustry = Object.entries(industryCount).sort((a, b) => b[1] - a[1])[0];
    const concentration = analyzed.length ? (topIndustry ? topIndustry[1] / analyzed.length * 100 : 0) : 0;
    return `
        <section class="ia-card">
            <div class="ia-card-hd"><div class="ia-card-title">组合概览</div><div class="ia-card-meta">${analyzed.length}/${rows.length} 只已分析</div></div>
            <div class="ia-card-pad">
                <div class="portfolio-grid">
                    <div class="pf-stat"><div class="pf-label">建议总仓位</div><div class="pf-value">${fmtPct(totalPosition * 100, 0)}</div><div class="pf-note">各股仓位上限加总${totalPosition > 1 ? '，已超100%需取舍' : ''}</div></div>
                    <div class="pf-stat"><div class="pf-label">进攻 / 防守</div><div class="pf-value">${attackCount} / ${defenseCount}</div><div class="pf-note">其余为观察</div></div>
                    <div class="pf-stat"><div class="pf-label">高风险标的</div><div class="pf-value">${riskyCount}</div><div class="pf-note">风险标签含高/危险</div></div>
                    <div class="pf-stat"><div class="pf-label">行业集中度</div><div class="pf-value">${fmtPct(concentration, 0)}</div><div class="pf-note">${topIndustry ? `最集中：${escapeHtml(topIndustry[0])}（${topIndustry[1]}只）` : '暂无行业数据'}</div></div>
                </div>
            </div>
        </section>
    `;
}

function renderAlertInbox() {
    if (!state.alerts.length) return '';
    return `
        <section class="ia-card">
            <div class="ia-card-hd"><div class="ia-card-title">🔔 预警收件箱</div><div class="ia-card-meta">近 ${state.alerts.length} 条</div></div>
            <div class="ia-card-pad alert-list">
                ${state.alerts.slice(0, 12).map(a => `
                    <div class="alert-item ${escapeHtml(a.alert_type || '')}" onclick="selectStock(${escapeJsArg(a.code)})" style="cursor:pointer;">
                        <span class="alert-date">${escapeHtml(a.alert_date)}</span>
                        <span class="alert-msg">${escapeHtml(a.message)}</span>
                    </div>
                `).join('')}
            </div>
        </section>
    `;
}

function renderOverview() {
    const box = document.getElementById('overviewArea');
    if (!box) return;
    const rows = overviewRows();
    if (!rows.length) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="overview-wrap">${renderAlertInbox()}${renderPortfolioCard(rows)}${renderRankingTable(rows)}</div>`;
}

function renderAll() {
    buildExposures();
    const items = normalizedWatchlist();
    if (!state.selectedCode || !items.some(item => sameCode(item.code, state.selectedCode))) {
        state.selectedCode = items[0]?.code || state.stocks[0]?.code || '';
    }
    renderOverview();
    renderMonitorTabs();
    renderDetail();
    const updated = state.stockResult?.updated_at ? new Date(state.stockResult.updated_at * 1000).toLocaleString('zh-CN', { hour12: false }) : '';
    document.getElementById('dataSub').textContent = updated ? `最新分析更新于 ${updated}` : '未读取到股票分析更新时间';
}

async function loadAll() {
    setProgress('正在读取投资分析数据...');
    try {
        const [stockResult, historyResult, watchlist, events, factors, industryData, dimensionsResult, dimHistoryResult, factorWeightsResult, notesResult, factorBacktestResult, alertsResult] = await Promise.all([
            safeJson('/api/stock/results'),
            safeJson('/api/stock/results/history?limit=5'),
            safeJson('/api/watchlist'),
            safeJson('/api/stock-events'),
            safeJson('/api/factor-weights'),
            loadIndustryData(),
            safeJson('/api/investment-analysis/dimensions'),
            safeJson('/api/investment-analysis/history?limit=30'),
            safeJson('/api/investment-analysis/factor-weights'),
            safeJson('/api/investment-analysis/notes'),
            safeJson('/api/investment-analysis/factor-backtest'),
            safeJson('/api/investment-analysis/alerts?limit=50'),
        ]);
        state.stockResult = stockResult || null;
        state.stocks = stockResult?.stocks || [];
        state.history = Array.isArray(historyResult?.reports) ? historyResult.reports : [];
        state.watchlist = Array.isArray(watchlist) ? watchlist : [];
        state.events = Array.isArray(events) ? events : [];
        state.factors = Array.isArray(factors) ? factors : [];
        state.industryData = industryData || {};
        state.dimensionsByCode = dimensionsResult?.dimensions || {};
        state.dimensionHistoryByCode = dimHistoryResult?.history || {};
        state.factorWeightsByCode = factorWeightsResult?.weights || {};
        state.notes = Array.isArray(notesResult) ? notesResult : [];
        state.factorBacktestByCode = factorBacktestResult?.backtest || {};
        state.factorBacktestGeneratedAt = factorBacktestResult?.generated_at || null;
        state.alerts = Array.isArray(alertsResult) ? alertsResult : [];
        renderAll();
        setProgress('');
    } catch (e) {
        setProgress(`读取失败：${e.message}`);
    }
}

async function refreshAnalysis() {
    const btn = document.getElementById('btnRefresh');
    const icon = document.getElementById('refreshIcon');
    btn.disabled = true;
    icon.classList.add('spin');
    setProgress('正在重新抓取分析数据...');
    try {
        const res = await fetch('/api/stock/refresh', { method: 'POST' });
        if (!res.ok && res.status !== 409) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || '刷新失败');
        }
        if (res.status === 409) {
            setProgress('已有刷新任务在运行，正在接入刷新进度...');
        }
        clearInterval(refreshTimer);
        refreshTimer = setInterval(async () => {
            const status = await safeJson('/api/stock/refresh/status');
            if (!status) return;
            if (status.progress) setProgress(status.progress);
            if (status.done) {
                clearInterval(refreshTimer);
                refreshTimer = null;
                if (status.error) {
                    setProgress(`刷新失败：${status.error}`);
                } else {
                    setProgress('刷新完成，正在更新页面...');
                    await loadAll();
                }
                btn.disabled = false;
                icon.classList.remove('spin');
            }
        }, 1600);
    } catch (e) {
        setProgress(`刷新失败：${e.message}`);
        btn.disabled = false;
        icon.classList.remove('spin');
    }
}

document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeAddModal();
});

loadAll();
