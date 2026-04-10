/*
 * 数据标准模块 - 前端交互逻辑
 */
(function () {
    'use strict';

    // ── State ──────────────────────────────────────────────
    let roots = [];
    let fields = [];
    let ifaces = [];
    let rules = [];
    let allRules = [];  // for field rule picker

    // Current drag list context
    let dragContext = { target: '', fieldId: null, ruleId: null };

    // 选中记录
    let selectedRootId = null;
    let selectedFieldId = null;

    // ── Helpers ────────────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    async function api(url, opts = {}) {
        const res = await fetch(url, {
            headers: { 'Content-Type': 'application/json', ...opts.headers },
            ...opts,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ error: res.statusText }));
            throw new Error(err.error || res.statusText);
        }
        return res.json();
    }

    function openModal(id) {
        const el = document.getElementById(id);
        if (el) el.classList.add('ds-modal--open');
    }
    function closeModal(id) {
        const el = document.getElementById(id);
        if (el) el.classList.remove('ds-modal--open');
    }

    function genId(prefix) {
        return prefix + '_' + Date.now().toString(36).toUpperCase() + Math.random().toString(36).substr(2, 3).toUpperCase();
    }

    function now() { return new Date().toISOString().slice(0, 19).replace('T', ' '); }

    // ── Tab Switching ─────────────────────────────────────
    $$('#dsTabs .ds-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            $$('#dsTabs .ds-tab').forEach(b => b.classList.remove('ds-tab--active'));
            btn.classList.add('ds-tab--active');
            $$('.ds-panel').forEach(p => p.classList.remove('ds-panel--active'));
            document.getElementById('panel-' + tab).classList.add('ds-panel--active');
        });
    });

    // ── Modal close buttons ───────────────────────────────
    $$('[data-close]').forEach(btn => {
        btn.addEventListener('click', () => closeModal(btn.dataset.close));
    });
    $$('.ds-modal-backdrop').forEach(bp => {
        bp.addEventListener('click', () => {
            bp.parentElement.classList.remove('ds-modal--open');
        });
    });

    // ═══════════════════════════════════════════════════════
    //  导入/导出功能
    // ═══════════════════════════════════════════════════════

    // 下载模板
    function downloadTemplate(url) {
        window.open(url, '_blank');
    }

    // 导出 CSV
    function exportData(url) {
        window.open(url, '_blank');
    }

    // 导入文件
    function importFile(uploadUrl, fileInput, typeName) {
        const file = fileInput.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);

        const origText = fileInput.parentElement?.querySelector('.btn-secondary')?.textContent || '导入中...';
        fileInput.disabled = true;

        fetch(uploadUrl, { method: 'POST', body: formData })
            .then(res => {
                if (!res.ok) return res.json().then(e => { throw new Error(e.error || '导入失败'); });
                return res.json();
            })
            .then(data => {
                showImportResult(typeName, data.success, data.errors);
                fileInput.value = '';
                fileInput.disabled = false;
                // 刷新列表
                if (typeName === '字根') loadRoots();
                else if (typeName === '字段') loadFields();
            })
            .catch(e => {
                alert('导入失败: ' + e.message);
                fileInput.disabled = false;
            });
    }

    function showImportResult(typeName, success, errors) {
        const body = $('#importResultBody');
        $('#importResultTitle').textContent = typeName + ' 导入结果';
        let html = '';
        if (success > 0) {
            html += '<div style="color:#34c759;font-size:15px;margin-bottom:8px;">✅ 成功导入 <strong>' + success + '</strong> 条</div>';
        }
        if (errors > 0) {
            html += '<div style="color:#ff3b30;font-size:15px;margin-bottom:8px;">❌ <strong>' + errors + '</strong> 条解析失败（请检查格式）</div>';
        }
        if (success === 0 && errors === 0) {
            html += '<div style="color:var(--text-muted);">未找到有效数据</div>';
        }
        body.innerHTML = html;
        openModal('importResultModal');
    }

    // 字根 导入/导出/模板 按钮
    $('#btnDownloadRootTemplate')?.addEventListener('click', () => downloadTemplate('/api/data-roots/template'));
    $('#btnExportRoots')?.addEventListener('click', () => exportData('/api/data-roots/export'));
    $('#btnImportRoots')?.addEventListener('click', () => $('#rootFileInput').click());
    $('#rootFileInput')?.addEventListener('change', () => importFile('/api/data-roots/import', $('#rootFileInput'), '字根'));

    // 字段 导入/导出/模板 按钮
    $('#btnDownloadFieldTemplate')?.addEventListener('click', () => downloadTemplate('/api/data-fields/template'));
    $('#btnExportFields')?.addEventListener('click', () => exportData('/api/data-fields/export'));
    $('#btnImportFields')?.addEventListener('click', () => $('#fieldFileInput').click());
    $('#fieldFileInput')?.addEventListener('change', () => importFile('/api/data-fields/import', $('#fieldFileInput'), '字段'));

    // ═══════════════════════════════════════════════════════
    //  1. 字根维护
    // ═══════════════════════════════════════════════════════
    async function loadRoots() {
        roots = await api('/api/data-roots');
        renderRootTable();
    }

    function renderRootTable() {
        const q = ($('#rootSearch')?.value || '').toLowerCase();
        const list = roots.filter(r =>
            r.id.toLowerCase().includes(q) || r.name.toLowerCase().includes(q) || (r.meaning || '').includes(q)
        );
        const tbody = $('#rootTable tbody');
        if (!list.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="ds-table-empty">暂无数据</td></tr>';
            return;
        }
        tbody.innerHTML = list.map(r => `
            <tr class="ds-table-row${selectedRootId === r.id ? ' ds-row--selected' : ''}" onclick="selectRoot('${r.id}')">
                <td>${esc(r.id)}</td>
                <td><strong>${esc(r.name)}</strong></td>
                <td>${esc(r.meaning || '—')}</td>
                <td>${esc(r.root_type)}</td>
                <td>${r.length ?? '—'}</td>
                <td class="ds-table-actions">
                    <button class="ds-btn-sm" onclick="event.stopPropagation();editRoot('${r.id}')">编辑</button>
                    <button class="ds-btn-sm ds-btn-sm--danger" onclick="event.stopPropagation();deleteRoot('${r.id}')">删除</button>
                </td>
            </tr>
        `).join('');
    }

    $('#rootSearch')?.addEventListener('input', renderRootTable);

    $('#btnAddRoot')?.addEventListener('click', () => {
        $('#rootEditId').value = '';
        $('#rootId').value = genId('ROOT');
        $('#rootName').value = '';
        $('#rootMeaning').value = '';
        $('#rootType').value = '字符型';
        $('#rootLength').value = '';
        $('#rootRemark').value = '';
        $('#rootModalTitle').textContent = '新增字根';
        renderCodeRows([]);
        toggleRootCodeGroup();
        openModal('rootModal');
    });

    $('#rootType')?.addEventListener('change', toggleRootCodeGroup);
    function toggleRootCodeGroup() {
        const g = $('#rootCodeGroup');
        if (g) g.style.display = $('#rootType')?.value === '字符型' ? '' : 'none';
    }

    // ── 码值序列编辑器（WPS 风格）──────────────────────────
    function addCodeRow(code, label) {
        const container = $('#rootCodeRows');
        if (!container) return;
        const row = document.createElement('div');
        row.className = 'cv-row';

        const codeInput = document.createElement('input');
        codeInput.type = 'text';
        codeInput.className = 'cv-code';
        codeInput.placeholder = '码值';
        codeInput.value = code || '';

        const labelInput = document.createElement('input');
        labelInput.type = 'text';
        labelInput.className = 'cv-label';
        labelInput.placeholder = '含义';
        labelInput.value = label || '';

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'cv-del-btn';
        delBtn.title = '删除';
        delBtn.textContent = '\u00d7';
        delBtn.addEventListener('click', () => row.remove());

        row.appendChild(codeInput);
        row.appendChild(labelInput);
        row.appendChild(delBtn);
        container.appendChild(row);
    }

    function renderCodeRows(values) {
        const container = $('#rootCodeRows');
        if (!container) return;
        container.textContent = '';
        (values || []).forEach(function(v) {
            const sep = v.indexOf('=');
            const code  = sep >= 0 ? v.slice(0, sep) : v;
            const label = sep >= 0 ? v.slice(sep + 1) : '';
            addCodeRow(code, label);
        });
    }

    function getCodeValues() {
        const rows = document.querySelectorAll('#rootCodeRows .cv-row');
        const result = [];
        rows.forEach(function(row) {
            const code  = (row.querySelector('.cv-code')?.value  || '').trim();
            const label = (row.querySelector('.cv-label')?.value || '').trim();
            if (code) result.push(label ? code + '=' + label : code);
        });
        return result.length ? JSON.stringify(result) : null;
    }

    $('#btnAddCodeRow')?.addEventListener('click', () => addCodeRow('', ''));

    $('#btnSaveRoot')?.addEventListener('click', async () => {
        const id = ($('#rootId').value || '').trim();
        const name = ($('#rootName').value || '').trim();
        if (!id || !name) { alert('请填写字根ID和字根名'); return; }
        const type = $('#rootType')?.value || '字符型';
        const data = {
            id, name,
            meaning: ($('#rootMeaning').value || '').trim(),
            root_type: type,
            length: parseInt($('#rootLength')?.value) || null,
            code_values: type === '字符型' ? getCodeValues() : null,
            remark: ($('#rootRemark').value || '').trim(),
        };
        try {
            const editId = $('#rootEditId').value;
            if (editId) {
                await api('/api/data-roots/' + editId, { method: 'PUT', body: JSON.stringify(data) });
            } else {
                data.created_at = now();
                data.updated_at = now();
                await api('/api/data-roots', { method: 'POST', body: JSON.stringify(data) });
            }
            closeModal('rootModal');
            await loadRoots();
        } catch (e) { alert('保存失败: ' + e.message); }
    });

    window.editRoot = async function (id) {
        const r = roots.find(x => x.id === id);
        if (!r) return;
        $('#rootEditId').value = r.id;
        $('#rootId').value = r.id;
        $('#rootName').value = r.name;
        $('#rootMeaning').value = r.meaning || '';
        $('#rootType').value = r.root_type || '字符型';
        $('#rootLength').value = r.length || '';
        $('#rootRemark').value = r.remark || '';
        $('#rootModalTitle').textContent = '编辑字根';
        var vals = [];
        try { vals = JSON.parse(r.code_values || '[]'); } catch (_) { vals = []; }
        renderCodeRows(vals);
        toggleRootCodeGroup();
        openModal('rootModal');
    };

    window.deleteRoot = async function (id) {
        if (!confirm('确认删除字根 ' + id + '?')) return;
        try {
            await api('/api/data-roots/' + id, { method: 'DELETE' });
            await loadRoots();
        } catch (e) { alert('删除失败: ' + e.message); }
    };

    // ── 行选中 ────────────────────────────────────────────────
    window.selectRoot = function (id) {
        selectedRootId = id;
        renderRootTable();
    };
    window.selectField = function (id) {
        selectedFieldId = id;
        renderFieldTable();
    };

    // ── 面板图谱按钮入口（HTML onclick 直接调用）─────────────
    window.openSelectedRootGraph = function () {
        if (!selectedRootId) { alert('请先选中一条字根记录，再查看关联图谱'); return; }
        window.open('/data-graph?type=root&id=' + encodeURIComponent(selectedRootId), '_blank');
    };
    window.openSelectedFieldGraph = function () {
        if (!selectedFieldId) { alert('请先选中一条字段记录，再查看关联图谱'); return; }
        window.open('/data-graph?type=field&id=' + encodeURIComponent(selectedFieldId), '_blank');
    };

    // ── 关联图谱（全局辅助）──────────────────────────────────
    function openGraphModal(title, html) {
        document.getElementById('graphModalTitle').textContent = title;
        const content = document.getElementById('graphContent');
        const exportBtn = document.getElementById('btnExportGraph');
        if (exportBtn) exportBtn.style.display = 'none';
        content.innerHTML = html;
        openModal('graphModal');
    }

    function buildGraphHtml(usedFields, usedByIfaces, usedByRules) {
        const hasAny = usedFields.length || usedByIfaces.length || usedByRules.length;
        if (!hasAny) return '<div class="ds-empty-hint" style="padding:32px 0;text-align:center;font-size:15px;">暂无关联</div>';
        let html = '';
        if (usedFields.length) {
            html += '<div class="ds-graph-section"><h4>📋 引用字段（' + usedFields.length + '）</h4>' +
                '<table class="ds-graph-table"><thead><tr><th>字段ID</th><th>字段英文名</th><th>字段中文名</th></tr></thead><tbody>' +
                usedFields.map(f => '<tr><td>' + esc(f.id) + '</td><td>' + esc(f.name_en) + '</td><td>' + esc(f.name_cn || '—') + '</td></tr>').join('') +
                '</tbody></table></div>';
        }
        html += '<div class="ds-graph-section"><h4>🔗 被接口引用（' + usedByIfaces.length + '）</h4>';
        if (usedByIfaces.length) {
            html += '<table class="ds-graph-table"><thead><tr><th>接口ID</th><th>接口名称</th><th>描述</th></tr></thead><tbody>' +
                usedByIfaces.map(i => '<tr><td>' + esc(i.id) + '</td><td>' + esc(i.name) + '</td><td>' + esc(i.description || '—') + '</td></tr>').join('') +
                '</tbody></table>';
        } else {
            html += '<div class="ds-empty-hint">无接口引用</div>';
        }
        html += '</div>';
        html += '<div class="ds-graph-section"><h4>⚙️ 被规则引用（' + usedByRules.length + '）</h4>';
        if (usedByRules.length) {
            html += '<table class="ds-graph-table"><thead><tr><th>规则ID</th><th>规则名称</th><th>描述</th></tr></thead><tbody>' +
                usedByRules.map(ru => '<tr><td>' + esc(ru.id) + '</td><td>' + esc(ru.name) + '</td><td>' + esc(ru.description || '—') + '</td></tr>').join('') +
                '</tbody></table>';
        } else {
            html += '<div class="ds-empty-hint">无规则引用</div>';
        }
        html += '</div>';
        return html;
    }

    // 字根图谱（面板按钮调用）
    window.showRootGraph = function (id) {
        const r = roots.find(x => x.id === id);
        if (!r) return;
        const usedFields = fields.filter(f => f.root_id === id);
        const fieldIds = usedFields.map(f => f.id);
        const usedByIfaces = ifaces.filter(ifc => {
            const arr = [...parseJSON(ifc.input_json, []), ...parseJSON(ifc.output_json, [])];
            return arr.some(x => fieldIds.includes(x.field_id));
        });
        const usedByRules = rules.filter(ru => {
            const arr = [...parseJSON(ru.input_json, []), ...parseJSON(ru.output_json, [])];
            return arr.some(x => fieldIds.includes(x.field_id));
        });

        openGraphModal('字根「' + r.name + '」关联图谱', buildGraphHtml(usedFields, usedByIfaces, usedByRules));
    };

    // ═══════════════════════════════════════════════════════
    //  2. 标准数据字段维护
    // ═══════════════════════════════════════════════════════
    async function loadFields() {
        fields = await api('/api/data-fields');
        renderFieldTable();
    }

    function renderFieldTable() {
        const q = ($('#fieldSearch')?.value || '').toLowerCase();
        const list = fields.filter(f =>
            f.id.toLowerCase().includes(q) || (f.name_en || '').toLowerCase().includes(q) || (f.name_cn || '').includes(q)
        );
        const tbody = $('#fieldTable tbody');
        if (!list.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="ds-table-empty">暂无数据</td></tr>';
            return;
        }
        tbody.innerHTML = list.map(f => `
            <tr class="ds-table-row${selectedFieldId === f.id ? ' ds-row--selected' : ''}" onclick="selectField('${f.id}')">
                <td>${esc(f.id)}</td>
                <td><strong>${esc(f.name_en)}</strong></td>
                <td>${esc(f.name_cn || '—')}</td>
                <td>${esc(f.field_type || '—')}</td>
                <td>${f.length ?? '—'}</td>
                <td>${esc(f.root_name || f.root_id || '—')}</td>
                <td class="ds-table-actions">
                    <button class="ds-btn-sm" onclick="event.stopPropagation();editField('${f.id}')">编辑</button>
                    <button class="ds-btn-sm ds-btn-sm--danger" onclick="event.stopPropagation();deleteField('${f.id}')">删除</button>
                </td>
            </tr>
        `).join('');
    }

    $('#fieldSearch')?.addEventListener('input', renderFieldTable);

    // ── 字段码值子集编辑器 ────────────────────────────────────
    function addFieldCodeRow(code, label) {
        const container = document.getElementById('fieldCodeRows');
        if (!container) return;
        const row = document.createElement('div');
        row.className = 'cv-row';

        const codeInput = document.createElement('input');
        codeInput.type = 'text';
        codeInput.className = 'cv-code';
        codeInput.readOnly = true;
        codeInput.value = code || '';

        const labelInput = document.createElement('input');
        labelInput.type = 'text';
        labelInput.className = 'cv-label';
        labelInput.readOnly = true;
        labelInput.value = label || '';

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'cv-del-btn';
        delBtn.title = '删除';
        delBtn.textContent = '\u00d7';
        delBtn.addEventListener('click', () => row.remove());

        row.appendChild(codeInput);
        row.appendChild(labelInput);
        row.appendChild(delBtn);
        container.appendChild(row);
    }

    // rootVals: 字根全量码值数组 ["01=个人","02=对公"]
    // fieldVals: 字段已有码值数组（子集），null 表示新建时显示全量
    function renderFieldCodeRows(rootVals, fieldVals) {
        const container = document.getElementById('fieldCodeRows');
        if (!container) return;
        container.textContent = '';
        const subset = fieldVals && fieldVals.length ? new Set(fieldVals) : null;
        (rootVals || []).forEach(function (v) {
            if (subset && !subset.has(v)) return;  // 编辑时只显示已选子集
            const sep = v.indexOf('=');
            const code  = sep >= 0 ? v.slice(0, sep) : v;
            const label = sep >= 0 ? v.slice(sep + 1) : '';
            addFieldCodeRow(code, label);
        });
    }

    function getFieldCodeValues() {
        const rows = document.querySelectorAll('#fieldCodeRows .cv-row');
        const result = [];
        rows.forEach(function (row) {
            const code  = (row.querySelector('.cv-code')?.value  || '').trim();
            const label = (row.querySelector('.cv-label')?.value || '').trim();
            if (code) result.push(label ? code + '=' + label : code);
        });
        return result.length ? JSON.stringify(result) : null;
    }

    // ── 字根选择器（带搜索）────────────────────────────────────
    function populateFieldRootSelect(filterText) {
        const sel = document.getElementById('fieldRootId');
        if (!sel) return;
        const q = (filterText || '').toLowerCase();
        const filtered = roots.filter(r =>
            !q || r.id.toLowerCase().includes(q) || r.name.toLowerCase().includes(q)
        );
        sel.textContent = '';
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '-- 不引用 --';
        sel.appendChild(blank);
        filtered.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r.id;
            opt.textContent = r.id + ' - ' + r.name;
            sel.appendChild(opt);
        });
    }

    document.getElementById('fieldRootSearch')?.addEventListener('input', function () {
        const current = document.getElementById('fieldRootId')?.value;
        populateFieldRootSelect(this.value);
        // restore selection if still visible
        const sel = document.getElementById('fieldRootId');
        if (sel && current) sel.value = current;
    });

    // 选中字根后回显类型/长度/码值
    window.onFieldRootChange = function () {
        const sel = document.getElementById('fieldRootId');
        if (!sel) return;
        const root = roots.find(r => r.id === sel.value);
        const codeGroup = document.getElementById('fieldCodeGroup');
        if (root) {
            document.getElementById('fieldType').value   = root.root_type || '';
            document.getElementById('fieldLength').value = root.length   || '';
            if (root.root_type === '字符型' && root.code_values) {
                let rootVals = [];
                try { rootVals = JSON.parse(root.code_values); } catch (_) {}
                renderFieldCodeRows(rootVals, null);  // 新选字根时显示全量供删减
                codeGroup.style.display = '';
            } else {
                codeGroup.style.display = 'none';
            }
        } else {
            document.getElementById('fieldType').value   = '';
            document.getElementById('fieldLength').value = '';
            codeGroup.style.display = 'none';
        }
    };

    $('#btnAddField')?.addEventListener('click', () => {
        $('#fieldEditId').value = '';
        $('#fieldId').value = genId('FIELD');
        $('#fieldNameEn').value = '';
        $('#fieldNameCn').value = '';
        $('#fieldRemark').value = '';
        $('#fieldType').value = '';
        $('#fieldLength').value = '';
        document.getElementById('fieldCodeGroup').style.display = 'none';
        document.getElementById('fieldCodeRows').textContent = '';
        document.getElementById('fieldRootSearch').value = '';
        $('#fieldModalTitle').textContent = '新增字段';
        populateFieldRootSelect('');
        openModal('fieldModal');
    });

    $('#btnSaveField')?.addEventListener('click', async () => {
        const id = ($('#fieldId').value || '').trim();
        const nameEn = ($('#fieldNameEn').value || '').trim();
        if (!id || !nameEn) { alert('请填写字段ID和字段英文名'); return; }
        const rootId = (document.getElementById('fieldRootId')?.value || '').trim();
        const root = roots.find(r => r.id === rootId);
        const data = {
            id, name_en: nameEn,
            name_cn: ($('#fieldNameCn').value || '').trim(),
            root_id: rootId || null,
            root_name: root ? root.name : null,
            field_type: ($('#fieldType').value || '').trim() || null,
            length: parseInt($('#fieldLength').value) || null,
            code_values: getFieldCodeValues(),
            remark: ($('#fieldRemark').value || '').trim(),
        };
        try {
            const editId = $('#fieldEditId').value;
            if (editId) {
                await api('/api/data-fields/' + editId, { method: 'PUT', body: JSON.stringify(data) });
            } else {
                data.created_at = now();
                data.updated_at = now();
                await api('/api/data-fields', { method: 'POST', body: JSON.stringify(data) });
            }
            closeModal('fieldModal');
            await loadFields();
        } catch (e) { alert('保存失败: ' + e.message); }
    });

    window.editField = async function (id) {
        const f = fields.find(x => x.id === id);
        if (!f) return;
        $('#fieldEditId').value = f.id;
        $('#fieldId').value = f.id;
        $('#fieldNameEn').value = f.name_en;
        $('#fieldNameCn').value = f.name_cn || '';
        $('#fieldRemark').value = f.remark || '';
        document.getElementById('fieldRootSearch').value = '';
        populateFieldRootSelect('');
        document.getElementById('fieldRootId').value = f.root_id || '';
        $('#fieldType').value = f.field_type || '';
        $('#fieldLength').value = f.length || '';
        // 码值：回显字根全量 + 仅保留已有子集
        const codeGroup = document.getElementById('fieldCodeGroup');
        const root = roots.find(r => r.id === f.root_id);
        if (root && root.root_type === '字符型' && root.code_values) {
            let rootVals = [], fieldVals = [];
            try { rootVals  = JSON.parse(root.code_values); } catch (_) {}
            try { fieldVals = JSON.parse(f.code_values || '[]'); } catch (_) {}
            renderFieldCodeRows(rootVals, fieldVals.length ? fieldVals : null);
            codeGroup.style.display = '';
        } else {
            codeGroup.style.display = 'none';
            document.getElementById('fieldCodeRows').textContent = '';
        }
        $('#fieldModalTitle').textContent = '编辑字段';
        openModal('fieldModal');
    };

    window.deleteField = async function (id) {
        if (!confirm('确认删除字段 ' + id + '?')) return;
        try {
            await api('/api/data-fields/' + id, { method: 'DELETE' });
            await loadFields();
        } catch (e) { alert('删除失败: ' + e.message); }
    };

    // 字段图谱（面板按钮调用）
    window.showFieldGraph = function (id) {
        const f = fields.find(x => x.id === id);
        if (!f) { alert('找不到字段数据'); return; }
        const usedByIfaces = ifaces.filter(ifc => {
            const arr = [...parseJSON(ifc.input_json, []), ...parseJSON(ifc.output_json, [])];
            return arr.some(x => x.field_id === id);
        });
        const usedByRules = rules.filter(ru => {
            const arr = [...parseJSON(ru.input_json, []), ...parseJSON(ru.output_json, [])];
            return arr.some(x => x.field_id === id);
        });
        openGraphModal('字段「' + f.name_en + '」关联图谱', buildGraphHtml([], usedByIfaces, usedByRules));
    };

    // ═══════════════════════════════════════════════════════
    //  3. 接口维护
    // ═══════════════════════════════════════════════════════
    let ifaceInputFields = [];
    let ifaceOutputFields = [];

    async function loadIfaces() {
        ifaces = await api('/api/interfaces');
        renderIfaceTable();
    }

    function renderIfaceTable() {
        const q = ($('#ifaceSearch')?.value || '').toLowerCase();
        const list = ifaces.filter(ifc =>
            ifc.id.toLowerCase().includes(q) || (ifc.name || '').toLowerCase().includes(q)
        );
        const tbody = $('#ifaceTable tbody');
        if (!list.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="ds-table-empty">暂无数据</td></tr>';
            return;
        }
        tbody.innerHTML = list.map(ifc => {
            const inCount = parseJSON(ifc.input_json, []).length;
            const outCount = parseJSON(ifc.output_json, []).length;
            return `
            <tr>
                <td>${esc(ifc.id)}</td>
                <td><strong>${esc(ifc.name)}</strong></td>
                <td>${esc(ifc.description || '—')}</td>
                <td>${inCount}</td>
                <td>${outCount}</td>
                <td class="ds-table-actions">
                    <button class="ds-btn-sm" onclick="editIface('${ifc.id}')">编辑</button>
                    <button class="ds-btn-sm ds-btn-sm--danger" onclick="deleteIface('${ifc.id}')">删除</button>
                </td>
            </tr>`;
        }).join('');
    }

    $('#ifaceSearch')?.addEventListener('input', renderIfaceTable);

    $('#btnAddIface')?.addEventListener('click', () => {
        $('#ifaceEditId').value = '';
        $('#ifaceId').value = genId('IFACE');
        $('#ifaceName').value = '';
        $('#ifaceDesc').value = '';
        ifaceInputFields = [];
        ifaceOutputFields = [];
        $('#ifaceModalTitle').textContent = '新增接口';
        renderPool('ifacePoolList', null, 'ifacePoolSearch');
        renderDragList('ifaceInputList', ifaceInputFields, 'ifaceInput');
        renderDragList('ifaceOutputList', ifaceOutputFields, 'ifaceOutput');
        openModal('ifaceModal');
    });

    $('#btnSaveIface')?.addEventListener('click', async () => {
        const id = ($('#ifaceId').value || '').trim();
        const name = ($('#ifaceName').value || '').trim();
        if (!id || !name) { alert('请填写接口ID和接口名称'); return; }
        const data = {
            id, name,
            description: ($('#ifaceDesc').value || '').trim(),
            input_json: JSON.stringify(ifaceInputFields),
            output_json: JSON.stringify(ifaceOutputFields),
        };
        try {
            const editId = $('#ifaceEditId').value;
            if (editId) {
                await api('/api/interfaces/' + editId, { method: 'PUT', body: JSON.stringify(data) });
            } else {
                data.created_at = now();
                data.updated_at = now();
                await api('/api/interfaces', { method: 'POST', body: JSON.stringify(data) });
            }
            closeModal('ifaceModal');
            await loadIfaces();
        } catch (e) { alert('保存失败: ' + e.message); }
    });

    window.editIface = async function (id) {
        const ifc = ifaces.find(x => x.id === id);
        if (!ifc) return;
        $('#ifaceEditId').value = ifc.id;
        $('#ifaceId').value = ifc.id;
        $('#ifaceName').value = ifc.name;
        $('#ifaceDesc').value = ifc.description || '';
        $('#ifaceModalTitle').textContent = '编辑接口';
        ifaceInputFields = parseJSON(ifc.input_json, []);
        ifaceOutputFields = parseJSON(ifc.output_json, []);
        renderPool('ifacePoolList', null, 'ifacePoolSearch');
        renderDragList('ifaceInputList', ifaceInputFields, 'ifaceInput');
        renderDragList('ifaceOutputList', ifaceOutputFields, 'ifaceOutput');
        openModal('ifaceModal');
    };

    window.deleteIface = async function (id) {
        if (!confirm('确认删除接口 ' + id + '?')) return;
        try {
            await api('/api/interfaces/' + id, { method: 'DELETE' });
            await loadIfaces();
        } catch (e) { alert('删除失败: ' + e.message); }
    };

    // ═══════════════════════════════════════════════════════
    //  4. 规则维护
    // ═══════════════════════════════════════════════════════
    let ruleInputFields = [];
    let ruleOutputFields = [];

    async function loadRules() {
        rules = await api('/api/rules');
        renderRuleTable();
    }

    function renderRuleTable() {
        const q = ($('#ruleSearch')?.value || '').toLowerCase();
        const list = rules.filter(ru =>
            ru.id.toLowerCase().includes(q) || (ru.name || '').toLowerCase().includes(q)
        );
        const tbody = $('#ruleTable tbody');
        if (!list.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="ds-table-empty">暂无数据</td></tr>';
            return;
        }
        tbody.innerHTML = list.map(ru => {
            const inCount = parseJSON(ru.input_json, []).length;
            const outCount = parseJSON(ru.output_json, []).length;
            return `
            <tr>
                <td>${esc(ru.id)}</td>
                <td><strong>${esc(ru.name)}</strong></td>
                <td>${esc(ru.description || '—')}</td>
                <td>${inCount}</td>
                <td>${outCount}</td>
                <td class="ds-table-actions">
                    <button class="ds-btn-sm" onclick="editRule('${ru.id}')">编辑</button>
                    <button class="ds-btn-sm ds-btn-sm--danger" onclick="deleteRule('${ru.id}')">删除</button>
                </td>
            </tr>`;
        }).join('');
    }

    $('#ruleSearch')?.addEventListener('input', renderRuleTable);

    $('#btnAddRule')?.addEventListener('click', () => {
        $('#ruleEditId').value = '';
        $('#ruleId').value = genId('RULE');
        $('#ruleName').value = '';
        $('#ruleDesc').value = '';
        ruleInputFields = [];
        ruleOutputFields = [];
        $('#ruleModalTitle').textContent = '新增规则';
        renderPool('rulePoolList', null, 'rulePoolSearch');
        renderDragList('ruleInputList', ruleInputFields, 'ruleInput');
        renderDragList('ruleOutputList', ruleOutputFields, 'ruleOutput');
        openModal('ruleModal');
    });

    $('#btnSaveRule')?.addEventListener('click', async () => {
        const id = ($('#ruleId').value || '').trim();
        const name = ($('#ruleName').value || '').trim();
        if (!id || !name) { alert('请填写规则ID和规则名称'); return; }
        const data = {
            id, name,
            description: ($('#ruleDesc').value || '').trim(),
            input_json: JSON.stringify(ruleInputFields),
            output_json: JSON.stringify(ruleOutputFields),
        };
        try {
            const editId = $('#ruleEditId').value;
            if (editId) {
                await api('/api/rules/' + editId, { method: 'PUT', body: JSON.stringify(data) });
            } else {
                data.created_at = now();
                data.updated_at = now();
                await api('/api/rules', { method: 'POST', body: JSON.stringify(data) });
            }
            closeModal('ruleModal');
            await loadRules();
        } catch (e) { alert('保存失败: ' + e.message); }
    });

    window.editRule = async function (id) {
        const ru = rules.find(x => x.id === id);
        if (!ru) return;
        $('#ruleEditId').value = ru.id;
        $('#ruleId').value = ru.id;
        $('#ruleName').value = ru.name;
        $('#ruleDesc').value = ru.description || '';
        $('#ruleModalTitle').textContent = '编辑规则';
        ruleInputFields = parseJSON(ru.input_json, []);
        ruleOutputFields = parseJSON(ru.output_json, []);
        renderPool('rulePoolList', null, 'rulePoolSearch');
        renderDragList('ruleInputList', ruleInputFields, 'ruleInput');
        renderDragList('ruleOutputList', ruleOutputFields, 'ruleOutput');
        openModal('ruleModal');
    };

    window.deleteRule = async function (id) {
        if (!confirm('确认删除规则 ' + id + '?')) return;
        try {
            await api('/api/rules/' + id, { method: 'DELETE' });
            await loadRules();
        } catch (e) { alert('删除失败: ' + e.message); }
    };

    // ═══════════════════════════════════════════════════════
    //  通用: 拖拽列表 + 字段池
    // ═══════════════════════════════════════════════════════

    // 渲染字段池
    function renderPool(poolListId, targetLists, searchId) {
        const container = document.getElementById(poolListId);
        if (!container) return;
        const q = (document.getElementById(searchId)?.value || '').toLowerCase();
        const filtered = fields.filter(f =>
            (f.name_en || '').toLowerCase().includes(q) || (f.name_cn || '').includes(q) || f.id.toLowerCase().includes(q)
        );
        if (!filtered.length) {
            container.innerHTML = '<div class="ds-empty-hint">无可用字段</div>';
            return;
        }
        container.innerHTML = filtered.map(f => `
            <div class="ds-pool-item" draggable="true" data-field-id="${f.id}" data-field-name="${esc(f.name_en)}">
                <span class="ds-pool-item-text">${esc(f.name_en)}${f.name_cn ? ' (' + esc(f.name_cn) + ')' : ''}</span>
                <span class="ds-pool-item-add">+</span>
            </div>
        `).join('');

        // Drag start from pool
        container.querySelectorAll('.ds-pool-item').forEach(item => {
            item.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('text/plain', JSON.stringify({
                    source: 'pool',
                    field_id: item.dataset.fieldId,
                    field_name: item.dataset.fieldName
                }));
                e.dataTransfer.effectAllowed = 'copy';
                item.style.opacity = '0.4';
            });
            item.addEventListener('dragend', (e) => {
                item.style.opacity = '';
            });
        });
    }

    // 渲染拖拽列表
    function renderDragList(containerId, items, prefix) {
        const container = document.getElementById(containerId);
        if (!container) return;

        // Update count badge
        const countId = containerId.replace('List', 'Count');
        const countEl = document.getElementById(countId);
        if (countEl) countEl.textContent = items.length;

        if (!items.length) {
            container.innerHTML = '<div class="ds-drop-hint">从左侧字段池拖入字段</div>';
            setupDropZone(container, containerId, prefix);
            return;
        }
        container.innerHTML = items.map((item, i) => `
            <div class="ds-drag-item" draggable="true" data-index="${i}" data-prefix="${prefix}">
                <span class="ds-drag-handle">☰</span>
                <span class="ds-drag-item-text">${esc(item.field_name || item.field_id)}${item.rule_ids?.length ? ' ⚙️×' + item.rule_ids.length : ''}</span>
                <button class="ds-drag-item-rule-btn" data-idx="${i}" data-prefix="${prefix}">规则</button>
                <button class="ds-drag-item-remove" data-idx="${i}" data-prefix="${prefix}">×</button>
            </div>
        `).join('');

        // Remove handlers
        container.querySelectorAll('.ds-drag-item-remove').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.idx);
                const list = getListByPrefix(btn.dataset.prefix);
                list.splice(idx, 1);
                renderDragList(containerId, list, btn.dataset.prefix);
            });
        });

        // Rule button handlers
        container.querySelectorAll('.ds-drag-item-rule-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.idx);
                const list = getListByPrefix(btn.dataset.prefix);
                openFieldRulePicker(list[idx]);
            });
        });

        // Reorder drag within list
        setupDrag(container, containerId, prefix);
        // Drop zone
        setupDropZone(container, containerId, prefix);
    }

    function getListByPrefix(prefix) {
        return prefix === 'ifaceInput' ? ifaceInputFields :
               prefix === 'ifaceOutput' ? ifaceOutputFields :
               prefix === 'ruleInput' ? ruleInputFields : ruleOutputFields;
    }

    // 设置放置区域
    function setupDropZone(container, containerId, prefix) {
        container.addEventListener('dragover', (e) => {
            e.preventDefault();
            const data = e.dataTransfer.getData('text/plain');
            if (!data) return;
            try {
                const parsed = JSON.parse(data);
                if (parsed.source === 'pool') {
                    container.classList.add('ds-drag-over');
                    e.dataTransfer.dropEffect = 'copy';
                }
            } catch {}

            // Reorder within list
            const after = getDragAfterElement(container, e.clientY);
            const dragging = container.querySelector('.ds-drag-item--dragging');
            if (dragging) {
                if (after) container.insertBefore(dragging, after);
                else container.appendChild(dragging);
            }
        });

        container.addEventListener('dragleave', (e) => {
            if (!container.contains(e.relatedTarget)) {
                container.classList.remove('ds-drag-over');
            }
        });

        container.addEventListener('drop', (e) => {
            e.preventDefault();
            container.classList.remove('ds-drag-over');
            const data = e.dataTransfer.getData('text/plain');
            if (!data) return;

            try {
                const parsed = JSON.parse(data);
                if (parsed.source === 'pool') {
                    const list = getListByPrefix(prefix);
                    // Check if already exists
                    if (!list.some(x => x.field_id === parsed.field_id)) {
                        list.push({ field_id: parsed.field_id, field_name: parsed.field_name, rule_ids: [] });
                        renderDragList(containerId, list, prefix);
                    }
                    return;
                }
            } catch {}

            // Reorder within list
            const items = container.querySelectorAll('.ds-drag-item');
            const list = getListByPrefix(prefix);
            const newOrder = Array.from(items).map(el => list[parseInt(el.dataset.index)]);
            if (prefix === 'ifaceInput') ifaceInputFields = newOrder;
            else if (prefix === 'ifaceOutput') ifaceOutputFields = newOrder;
            else if (prefix === 'ruleInput') ruleInputFields = newOrder;
            else ruleOutputFields = newOrder;
            renderDragList(containerId, list, prefix);
        });
    }

    function setupDrag(container, containerId, prefix) {
        let dragIdx = null;

        container.addEventListener('dragstart', (e) => {
            const item = e.target.closest('.ds-drag-item');
            if (!item) return;
            dragIdx = parseInt(item.dataset.index);
            item.classList.add('ds-drag-item--dragging');
            e.dataTransfer.setData('text/plain', JSON.stringify({
                source: 'list',
                index: dragIdx,
                prefix: prefix
            }));
            e.dataTransfer.effectAllowed = 'move';
        });

        container.addEventListener('dragend', (e) => {
            const item = e.target.closest('.ds-drag-item');
            if (item) item.classList.remove('ds-drag-item--dragging');
        });
    }

    function getDragAfterElement(container, y) {
        const items = [...container.querySelectorAll('.ds-drag-item:not(.ds-drag-item--dragging)')];
        return items.reduce((closest, child) => {
            const box = child.getBoundingClientRect();
            const offset = y - box.top - box.height / 2;
            if (offset < 0 && offset > closest.offset) {
                return { offset, element: child };
            }
            return closest;
        }, { offset: Number.NEGATIVE_INFINITY }).element;
    }

    // Pool search
    $('#ifacePoolSearch')?.addEventListener('input', () => renderPool('ifacePoolList', null, 'ifacePoolSearch'));
    $('#rulePoolSearch')?.addEventListener('input', () => renderPool('rulePoolList', null, 'rulePoolSearch'));

    // ═══════════════════════════════════════════════════════
    //  工具
    let graphData = null;

    $('#btnExportGraph')?.addEventListener('click', () => {
        if (!graphData) return;
        let csv = '类型,ID,名称,描述/含义\n';
        csv += graphData.type + ',"' + graphData.name + '",\n';
        graphData.usedFields.forEach(f => {
            csv += '字段,"' + f.id + '","' + f.name_en + '","' + (f.field_type || '') + '"\n';
        });
        graphData.usedByIfaces.forEach(i => {
            csv += '接口,"' + i.id + '","' + i.name + '","' + (i.description || '') + '"\n';
        });
        graphData.usedByRules.forEach(r => {
            csv += '规则,"' + r.id + '","' + r.name + '","' + (r.description || '') + '"\n';
        });
        downloadFile(csv, 'graph_' + graphData.name + '.csv', 'text/csv;charset=utf-8;');
    });

    function downloadFile(content, filename, mime) {
        const blob = new Blob(['\uFEFF' + content], { type: mime });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    // ═══════════════════════════════════════════════════════
    //  工具
    // ═══════════════════════════════════════════════════════
    function parseJSON(str, fallback) {
        try { return JSON.parse(str); } catch { return fallback; }
    }

    function esc(s) {
        if (!s) return '';
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    // ═══════════════════════════════════════════════════════
    //  初始化
    // ═══════════════════════════════════════════════════════
    async function init() {
        await Promise.all([loadRoots(), loadFields(), loadIfaces(), loadRules()]);
    }

    init();

})();
