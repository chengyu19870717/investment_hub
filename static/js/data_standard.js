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
            <tr>
                <td>${esc(r.id)}</td>
                <td><strong>${esc(r.name)}</strong></td>
                <td>${esc(r.meaning || '—')}</td>
                <td>${esc(r.root_type)}</td>
                <td>${r.length ?? '—'}</td>
                <td class="ds-table-actions">
                    <button class="ds-btn-sm" onclick="editRoot('${r.id}')">编辑</button>
                    <button class="ds-btn-sm ds-btn-sm--danger" onclick="deleteRoot('${r.id}')">删除</button>
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
        $('#rootCodeValues').value = '';
        $('#rootRemark').value = '';
        $('#rootModalTitle').textContent = '新增字根';
        toggleRootCodeGroup();
        openModal('rootModal');
    });

    $('#rootType')?.addEventListener('change', toggleRootCodeGroup);
    function toggleRootCodeGroup() {
        const g = $('#rootCodeGroup');
        if (g) g.style.display = $('#rootType')?.value === '字符型' ? '' : 'none';
    }

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
            code_values: type === '字符型' ? ($('#rootCodeValues').value || '').trim() : null,
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
        $('#rootCodeValues').value = r.code_values || '';
        $('#rootRemark').value = r.remark || '';
        $('#rootModalTitle').textContent = '编辑字根';
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

    // 字根关联图谱
    $('#btnRootGraph')?.addEventListener('click', async () => {
        const id = $('#rootEditId').value;
        if (!id) { alert('请先选择或新增一个字根'); return; }
        const r = roots.find(x => x.id === id);
        if (!r) return;
        const usedFields = fields.filter(f => f.root_id === id);
        const fieldIds = usedFields.map(f => f.id);
        const usedByIfaces = ifaces.filter(ifc => {
            const inF = parseJSON(ifc.input_json, []);
            const outF = parseJSON(ifc.output_json, []);
            return [...inF, ...outF].some(x => fieldIds.includes(x.field_id));
        });
        const usedByRules = rules.filter(ru => {
            const inF = parseJSON(ru.input_json, []);
            const outF = parseJSON(ru.output_json, []);
            return [...inF, ...outF].some(x => fieldIds.includes(x.field_id));
        });
        showGraph(r.name, '字根', usedFields, usedByIfaces, usedByRules);
    });

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
            <tr>
                <td>${esc(f.id)}</td>
                <td><strong>${esc(f.name_en)}</strong></td>
                <td>${esc(f.name_cn || '—')}</td>
                <td>${esc(f.field_type || '—')}</td>
                <td>${f.length ?? '—'}</td>
                <td>${esc(f.root_name || f.root_id || '—')}</td>
                <td class="ds-table-actions">
                    <button class="ds-btn-sm" onclick="editField('${f.id}')">编辑</button>
                    <button class="ds-btn-sm ds-btn-sm--danger" onclick="deleteField('${f.id}')">删除</button>
                </td>
            </tr>
        `).join('');
    }

    $('#fieldSearch')?.addEventListener('input', renderFieldTable);

    function populateFieldRootSelect() {
        const sel = $('#fieldRootId');
        if (!sel) return;
        sel.innerHTML = '<option value="">-- 不引用 --</option>' +
            roots.map(r => `<option value="${r.id}">${esc(r.id)} - ${esc(r.name)}</option>`).join('');
    }

    function setupFieldRootChange() {
        const sel = $('#fieldRootId');
        if (!sel) return;
        sel.addEventListener('change', () => {
            const root = roots.find(r => r.id === sel.value);
            if (root) {
                $('#fieldType').value = root.root_type || '';
                $('#fieldLength').value = root.length || '';
                if (root.root_type === '字符型' && root.code_values) {
                    $('#fieldCodeGroup').style.display = '';
                    $('#fieldCodeValues').placeholder = '从字根码值中选择子集，字根码值: ' + root.code_values;
                } else {
                    $('#fieldCodeGroup').style.display = 'none';
                }
            } else {
                $('#fieldType').value = '';
                $('#fieldLength').value = '';
                $('#fieldCodeGroup').style.display = '';
                $('#fieldCodeValues').placeholder = '';
            }
        });
    }

    $('#btnAddField')?.addEventListener('click', () => {
        $('#fieldEditId').value = '';
        $('#fieldId').value = genId('FIELD');
        $('#fieldNameEn').value = '';
        $('#fieldNameCn').value = '';
        $('#fieldMeaning').value = '';
        $('#fieldRemark').value = '';
        $('#fieldRootId').value = '';
        $('#fieldType').value = '';
        $('#fieldLength').value = '';
        $('#fieldCodeValues').value = '';
        $('#fieldCodeGroup').style.display = '';
        $('#fieldModalTitle').textContent = '新增字段';
        populateFieldRootSelect();
        openModal('fieldModal');
    });

    $('#btnSaveField')?.addEventListener('click', async () => {
        const id = ($('#fieldId').value || '').trim();
        const nameEn = ($('#fieldNameEn').value || '').trim();
        if (!id || !nameEn) { alert('请填写字段ID和字段英文名'); return; }
        const rootId = ($('#fieldRootId').value || '').trim();
        const root = roots.find(r => r.id === rootId);
        const data = {
            id, name_en: nameEn,
            name_cn: ($('#fieldNameCn').value || '').trim(),
            meaning: ($('#fieldMeaning').value || '').trim(),
            root_id: rootId || null,
            root_name: root ? root.name : null,
            field_type: ($('#fieldType').value || '').trim() || null,
            length: parseInt($('#fieldLength').value) || null,
            code_values: ($('#fieldCodeValues').value || '').trim() || null,
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
        $('#fieldMeaning').value = f.meaning || '';
        $('#fieldRemark').value = f.remark || '';
        populateFieldRootSelect();
        $('#fieldRootId').value = f.root_id || '';
        $('#fieldType').value = f.field_type || '';
        $('#fieldLength').value = f.length || '';
        $('#fieldCodeValues').value = f.code_values || '';
        $('#fieldModalTitle').textContent = '编辑字段';
        if (f.field_type !== '字符型') $('#fieldCodeGroup').style.display = 'none';
        openModal('fieldModal');
    };

    window.deleteField = async function (id) {
        if (!confirm('确认删除字段 ' + id + '?')) return;
        try {
            await api('/api/data-fields/' + id, { method: 'DELETE' });
            await loadFields();
        } catch (e) { alert('删除失败: ' + e.message); }
    };

    // 字段关联图谱
    $('#btnFieldGraph')?.addEventListener('click', async () => {
        const id = $('#fieldEditId').value;
        if (!id) { alert('请先选择或新增一个字段'); return; }
        const f = fields.find(x => x.id === id);
        if (!f) return;
        const usedByIfaces = ifaces.filter(ifc => {
            const inF = parseJSON(ifc.input_json, []);
            const outF = parseJSON(ifc.output_json, []);
            return [...inF, ...outF].some(x => x.field_id === id);
        });
        const usedByRules = rules.filter(ru => {
            const inF = parseJSON(ru.input_json, []);
            const outF = parseJSON(ru.output_json, []);
            return [...inF, ...outF].some(x => x.field_id === id);
        });
        showGraph(f.name_en, '字段', [], usedByIfaces, usedByRules);
    });

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
    //  通用: 拖拽列表
    // ═══════════════════════════════════════════════════════
    function renderDragList(containerId, items, prefix) {
        const container = document.getElementById(containerId);
        if (!container) return;
        if (!items.length) {
            container.innerHTML = '<div class="ds-empty-hint">拖入或点击按钮添加字段</div>';
            return;
        }
        container.innerHTML = items.map((item, i) => `
            <div class="ds-drag-item" draggable="true" data-index="${i}" data-prefix="${prefix}">
                <span class="ds-drag-handle">☰</span>
                <span class="ds-drag-item-text">${esc(item.field_name || item.field_id)}${item.rule_ids?.length ? ' ⚙️×' + item.rule_ids.length : ''}</span>
                <button class="ds-drag-item-rule-btn" data-idx="${i}" data-prefix="${prefix}">关联规则</button>
                <button class="ds-drag-item-remove" data-idx="${i}" data-prefix="${prefix}">×</button>
            </div>
        `).join('');

        // Remove handlers
        container.querySelectorAll('.ds-drag-item-remove').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.idx);
                const list = btn.dataset.prefix === 'ifaceInput' ? ifaceInputFields :
                             btn.dataset.prefix === 'ifaceOutput' ? ifaceOutputFields :
                             btn.dataset.prefix === 'ruleInput' ? ruleInputFields : ruleOutputFields;
                list.splice(idx, 1);
                renderDragList(containerId, list, btn.dataset.prefix);
            });
        });

        // Rule button handlers
        container.querySelectorAll('.ds-drag-item-rule-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.idx);
                const list = btn.dataset.prefix === 'ifaceInput' ? ifaceInputFields :
                             btn.dataset.prefix === 'ifaceOutput' ? ifaceOutputFields :
                             btn.dataset.prefix === 'ruleInput' ? ruleInputFields : ruleOutputFields;
                openFieldRulePicker(list[idx]);
            });
        });

        // Drag and drop
        setupDrag(container, containerId, prefix);
    }

    function setupDrag(container, containerId, prefix) {
        let dragIdx = null;

        container.addEventListener('dragstart', (e) => {
            const item = e.target.closest('.ds-drag-item');
            if (!item) return;
            dragIdx = parseInt(item.dataset.index);
            item.classList.add('ds-drag-item--dragging');
            e.dataTransfer.effectAllowed = 'move';
        });

        container.addEventListener('dragend', (e) => {
            const item = e.target.closest('.ds-drag-item');
            if (item) item.classList.remove('ds-drag-item--dragging');
        });

        container.addEventListener('dragover', (e) => {
            e.preventDefault();
            const after = getDragAfterElement(container, e.clientY);
            const dragging = container.querySelector('.ds-drag-item--dragging');
            if (!dragging) return;
            if (after) {
                container.insertBefore(dragging, after);
            } else {
                container.appendChild(dragging);
            }
        });

        container.addEventListener('drop', (e) => {
            e.preventDefault();
            const items = container.querySelectorAll('.ds-drag-item');
            const list = prefix === 'ifaceInput' ? ifaceInputFields :
                         prefix === 'ifaceOutput' ? ifaceOutputFields :
                         prefix === 'ruleInput' ? ruleInputFields : ruleOutputFields;
            const newOrder = Array.from(items).map(el => {
                const idx = parseInt(el.dataset.index);
                return list[idx];
            });
            // Replace the list
            if (prefix === 'ifaceInput') ifaceInputFields = newOrder;
            else if (prefix === 'ifaceOutput') ifaceOutputFields = newOrder;
            else if (prefix === 'ruleInput') ruleInputFields = newOrder;
            else ruleOutputFields = newOrder;
            renderDragList(containerId, list, prefix);
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

    // Field add buttons for interface/rule
    function setupFieldAddBtn(btnId, listType, containerId, prefix) {
        const btn = document.getElementById(btnId);
        if (!btn) return;
        btn.addEventListener('click', () => {
            dragContext = { target: listType, containerId, prefix };
            populateFieldPicker();
            openModal('fieldPickerModal');
        });
    }

    setupFieldAddBtn('btnAddIfaceInput', 'ifaceInput', 'ifaceInputList', 'ifaceInput');
    setupFieldAddBtn('btnAddIfaceOutput', 'ifaceOutput', 'ifaceOutputList', 'ifaceOutput');
    setupFieldAddBtn('btnAddRuleInput', 'ruleInput', 'ruleInputList', 'ruleInput');
    setupFieldAddBtn('btnAddRuleOutput', 'ruleOutput', 'ruleOutputList', 'ruleOutput');

    // Field search inside interface/rule
    ['ifaceInput', 'ifaceOutput', 'ruleInput', 'ruleOutput'].forEach(prefix => {
        const searchId = prefix === 'ifaceInput' ? 'ifaceInputSearch' :
                         prefix === 'ifaceOutput' ? 'ifaceOutputSearch' :
                         prefix === 'ruleInput' ? 'ruleInputSearch' : 'ruleOutputSearch';
        const searchEl = document.getElementById(searchId);
        if (searchEl) {
            searchEl.addEventListener('input', () => {
                // Filter displayed fields in the current drag list view
                const containerId = prefix === 'ifaceInput' ? 'ifaceInputList' :
                                    prefix === 'ifaceOutput' ? 'ifaceOutputList' :
                                    prefix === 'ruleInput' ? 'ruleInputList' : 'ruleOutputList';
                // Simple: just filter the render
                // For now, the search filters the field picker
            });
        }
    });

    // ═══════════════════════════════════════════════════════
    //  字段选择器
    // ═══════════════════════════════════════════════════════
    function populateFieldPicker() {
        const list = document.getElementById('fieldPickerList');
        if (!list) return;
        list.innerHTML = fields.map(f => `
            <div class="ds-field-picker-item">
                <input type="checkbox" value="${f.id}" data-name="${f.name_en}">
                <div class="ds-field-picker-item-label">
                    <span class="en">${esc(f.name_en)}</span>
                    <span class="cn">${esc(f.name_cn || '')}</span>
                </div>
            </div>
        `).join('');
    }

    $('#fieldPickerSearch')?.addEventListener('input', () => {
        const q = ($('#fieldPickerSearch').value || '').toLowerCase();
        document.querySelectorAll('.ds-field-picker-item').forEach(item => {
            const text = item.textContent.toLowerCase();
            item.style.display = text.includes(q) ? '' : 'none';
        });
    });

    $('#btnConfirmFieldPick')?.addEventListener('click', () => {
        const checked = document.querySelectorAll('#fieldPickerList input:checked');
        const list = dragContext.prefix === 'ifaceInput' ? ifaceInputFields :
                     dragContext.prefix === 'ifaceOutput' ? ifaceOutputFields :
                     dragContext.prefix === 'ruleInput' ? ruleInputFields : ruleOutputFields;
        checked.forEach(cb => {
            if (!list.some(x => x.field_id === cb.value)) {
                list.push({ field_id: cb.value, field_name: cb.dataset.name, rule_ids: [] });
            }
        });
        renderDragList(dragContext.containerId, list, dragContext.prefix);
        closeModal('fieldPickerModal');
    });

    // ═══════════════════════════════════════════════════════
    //  字段关联规则
    // ═══════════════════════════════════════════════════════
    let currentFieldRuleItem = null;

    function openFieldRulePicker(item) {
        currentFieldRuleItem = item;
        const list = document.getElementById('fieldRuleList');
        const select = document.getElementById('fieldRuleSelect');
        if (!list || !select) return;

        // Show current rules
        const currentRuleIds = item.rule_ids || [];
        const currentRules = currentRuleIds.map(id => rules.find(r => r.id === id)).filter(Boolean);
        list.innerHTML = currentRules.length ? currentRules.map(r => `
            <div class="ds-field-rule-item">
                <span>⚙️ ${esc(r.name)}</span>
                <button class="ds-field-rule-remove" data-id="${r.id}">移除</button>
            </div>
        `).join('') : '<div class="ds-empty-hint">暂无关联规则</div>';

        list.querySelectorAll('.ds-field-rule-remove').forEach(btn => {
            btn.addEventListener('click', () => {
                item.rule_ids = item.rule_ids.filter(id => id !== btn.dataset.id);
                openFieldRulePicker(item); // refresh
            });
        });

        // Populate select
        select.innerHTML = '<option value="">-- 选择规则关联 --</option>' +
            rules.filter(r => !currentRuleIds.includes(r.id)).map(r =>
                `<option value="${r.id}">${esc(r.id)} - ${esc(r.name)}</option>`
            ).join('');

        openModal('fieldRuleModal');
    }

    $('#btnSaveFieldRule')?.addEventListener('click', () => {
        const select = document.getElementById('fieldRuleSelect');
        const val = select?.value;
        if (val && currentFieldRuleItem) {
            if (!currentFieldRuleItem.rule_ids) currentFieldRuleItem.rule_ids = [];
            if (!currentFieldRuleItem.rule_ids.includes(val)) {
                currentFieldRuleItem.rule_ids.push(val);
            }
        }
        closeModal('fieldRuleModal');
        // Re-render the active drag lists
        renderDragList('ifaceInputList', ifaceInputFields, 'ifaceInput');
        renderDragList('ifaceOutputList', ifaceOutputFields, 'ifaceOutput');
        renderDragList('ruleInputList', ruleInputFields, 'ruleInput');
        renderDragList('ruleOutputList', ruleOutputFields, 'ruleOutput');
    });

    // ═══════════════════════════════════════════════════════
    //  关联图谱展示 & 导出
    // ═══════════════════════════════════════════════════════
    let graphData = null;

    function showGraph(name, type, usedFields, usedByIfaces, usedByRules) {
        $('#graphModalTitle').textContent = type + '「' + name + '」关联图谱';
        const content = document.getElementById('graphContent');
        graphData = { name, type, usedFields, usedByIfaces, usedByRules };

        let html = '';

        if (usedFields.length) {
            html += '<div class="ds-graph-section"><h4>📋 关联字段（' + usedFields.length + '）</h4>';
            html += '<table class="ds-graph-table"><thead><tr><th>字段ID</th><th>字段名</th><th>类型</th></tr></thead><tbody>';
            html += usedFields.map(f => `<tr><td>${esc(f.id)}</td><td>${esc(f.name_en)}</td><td>${esc(f.field_type || '—')}</td></tr>`).join('');
            html += '</tbody></table></div>';
        }

        html += '<div class="ds-graph-section"><h4>🔗 被接口引用（' + usedByIfaces.length + '）</h4>';
        if (usedByIfaces.length) {
            html += '<table class="ds-graph-table"><thead><tr><th>接口ID</th><th>接口名称</th><th>描述</th></tr></thead><tbody>';
            html += usedByIfaces.map(i => `<tr><td>${esc(i.id)}</td><td>${esc(i.name)}</td><td>${esc(i.description || '—')}</td></tr>`).join('');
            html += '</tbody></table></div>';
        } else {
            html += '<div class="ds-empty-hint">无接口引用</div></div>';
        }

        html += '<div class="ds-graph-section"><h4>⚙️ 被规则引用（' + usedByRules.length + '）</h4>';
        if (usedByRules.length) {
            html += '<table class="ds-graph-table"><thead><tr><th>规则ID</th><th>规则名称</th><th>描述</th></tr></thead><tbody>';
            html += usedByRules.map(r => `<tr><td>${esc(r.id)}</td><td>${esc(r.name)}</td><td>${esc(r.description || '—')}</td></tr>`).join('');
            html += '</tbody></table></div>';
        } else {
            html += '<div class="ds-empty-hint">无规则引用</div></div>';
        }

        content.innerHTML = html;
        openModal('graphModal');
    }

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
