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

function renderDetail() {
    const ctx = selectedContext();
    const box = document.getElementById('stockDetail');
    if (!ctx.watch && !ctx.stock) {
        box.innerHTML = '<section class="ia-card"><div class="empty">暂无监控股票，点击右上角“新增”添加后开始分析。</div></section>';
        return;
    }
    box.innerHTML = [
        renderHero(ctx),
        renderCompanySummary(ctx),
        renderBusinessModel(ctx),
        renderRiskList(ctx),
        renderSignalsSection(ctx),
        renderTracking(ctx),
        renderEventsSection(ctx),
    ].join('');
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

function renderAll() {
    buildExposures();
    const items = normalizedWatchlist();
    if (!state.selectedCode || !items.some(item => sameCode(item.code, state.selectedCode))) {
        state.selectedCode = items[0]?.code || state.stocks[0]?.code || '';
    }
    renderMonitorTabs();
    renderDetail();
    const updated = state.stockResult?.updated_at ? new Date(state.stockResult.updated_at * 1000).toLocaleString('zh-CN', { hour12: false }) : '';
    document.getElementById('dataSub').textContent = updated ? `最新分析更新于 ${updated}` : '未读取到股票分析更新时间';
}

async function loadAll() {
    setProgress('正在读取投资分析数据...');
    try {
        const [stockResult, historyResult, watchlist, events, factors, industryData, dimensionsResult] = await Promise.all([
            safeJson('/api/stock/results'),
            safeJson('/api/stock/results/history?limit=5'),
            safeJson('/api/watchlist'),
            safeJson('/api/stock-events'),
            safeJson('/api/factor-weights'),
            loadIndustryData(),
            safeJson('/api/investment-analysis/dimensions'),
        ]);
        state.stockResult = stockResult || null;
        state.stocks = stockResult?.stocks || [];
        state.history = Array.isArray(historyResult?.reports) ? historyResult.reports : [];
        state.watchlist = Array.isArray(watchlist) ? watchlist : [];
        state.events = Array.isArray(events) ? events : [];
        state.factors = Array.isArray(factors) ? factors : [];
        state.industryData = industryData || {};
        state.dimensionsByCode = dimensionsResult?.dimensions || {};
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
