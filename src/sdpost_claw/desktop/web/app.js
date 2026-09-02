/* ===================== sdpost-claw Web Client ===================== */
'use strict';

const state = {
    sessionId: null,
    isProcessing: false,
    stream: null,
    streamSession: null,
    workspace: '.',
    availableModels: [],
    config: { provider: 'deepseek', model: 'deepseek-chat', mode: 'build' },
};

const CATEGORIES = ['日常办公', '代码开发', '设计创意', '文档处理', '金融服务', '数据分析及可视化', '个人工作台', '幻灯片', '深度'];

const QUICK_ENTRIES = [
    { icon: '📊', title: '数据分析', desc: '分析表格 / 生成图表', prompt: '帮我分析数据并生成可视化图表' },
    { icon: '📝', title: '写文档', desc: '报告 / 总结 / 邮件', prompt: '帮我写一份工作文档' },
    { icon: '📑', title: '生成 PPT', desc: '幻灯片大纲与内容', prompt: '帮我生成一份幻灯片' },
    { icon: '💻', title: '代码审查', desc: '检查 Bug 与风格', prompt: '帮我审查这段代码' },
    { icon: '🔍', title: '资料整理', desc: '归纳与摘要', prompt: '帮我整理并总结这些资料' },
    { icon: '🤖', title: '自动化任务', desc: '规划并执行多步任务', prompt: '帮我规划并完成一个多步骤任务' },
];

const TICKER_MESSAGES = [
    '系统就绪 · 等待您的指令',
    'WorkBuddy-style mission control',
    '酸柠绿色 accent · 深色 mission-control',
    'Geist + JetBrains Mono aesthetic',
    'Agent-core · 工具调用 · 多轮对话',
    '本地优先 · 隐私安全 · 可控可审计',
];

let tickerIndex = 0;

/* ---------- API helpers ---------- */
async function api(path, opts) {
    const resp = await fetch(path, opts);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
}

/* ---------- Boot ---------- */
document.addEventListener('DOMContentLoaded', () => {
    renderCategories();
    renderQuickEntries();
    bindEvents();
    loadConfig();
    loadSessions();
    loadSpaces();
    checkHealth();
    startTickerRotation();
    updateStatusIndicator();
});

function startTickerRotation() {
    setInterval(() => {
        if (!state.isProcessing) {
            tickerIndex = (tickerIndex + 1) % TICKER_MESSAGES.length;
            const inner = document.getElementById('tickerInner');
            inner.innerHTML = `<span class="ticker-msg">${TICKER_MESSAGES[tickerIndex]}</span>`;
        }
    }, 8000);
}

function updateStatusIndicator() {
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    if (state.isProcessing) {
        dot.className = 'status-dot busy';
        text.textContent = '处理中';
    } else {
        dot.className = 'status-dot idle';
        text.textContent = '就绪';
    }
}

/* ---------- Rendering: categories / quick entries ---------- */
function renderCategories() {
    const wrap = document.getElementById('categoryChips');
    wrap.innerHTML = CATEGORIES.map(c => `<button class="chip" data-cat="${c}">${c}</button>`).join('');
    wrap.querySelectorAll('.chip').forEach(b => {
        b.onclick = () => {
            const box = document.getElementById('inputBox');
            box.value = `【${b.dataset.cat}】` + box.value;
            box.focus();
        };
    });
}

function renderQuickEntries() {
    const wrap = document.getElementById('quickEntries');
    wrap.innerHTML = QUICK_ENTRIES.map((q, i) => `
        <div class="quick-card" data-i="${i}">
            <div class="qc-icon">${q.icon}</div>
            <div class="qc-title">${q.title}</div>
            <div class="qc-desc">${q.desc}</div>
        </div>`).join('');
    wrap.querySelectorAll('.quick-card').forEach(card => {
        card.onclick = () => {
            document.getElementById('inputBox').value = QUICK_ENTRIES[card.dataset.i].prompt;
            document.getElementById('inputBox').focus();
        };
    });
}

/* ---------- Events ---------- */
function bindEvents() {
    document.getElementById('sendBtn').onclick = sendMessage;
    document.getElementById('inputBox').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    document.getElementById('inputBox').addEventListener('input', autoResize);
    document.getElementById('clearBtn').onclick = clearChat;
    document.getElementById('scrollTopBtn').onclick = () =>
        document.getElementById('chatScroll').scrollTo({ top: 0, behavior: 'smooth' });

    document.getElementById('modelSelect').onchange = e => {
        const mid = e.target.value;
        if (!mid) return;
        // Send model_id so the backend applies the full entry
        // (provider + model + api_key + base_url) of that model.
        updateConfig({ model_id: mid });
    };
    document.getElementById('workspaceSelect').onchange = e => {
        if (e.target.value === BROWSE_OPT) {
            // revert until a folder is actually confirmed
            e.target.value = state.workspace || '.';
            openFolderPicker();
            return;
        }
        state.workspace = e.target.value;
    };
    document.getElementById('modeSelect').onchange = e =>
        updateConfig({ mode: e.target.value });

    // Folder picker events
    document.getElementById('folderModalClose').onclick = closeFolderPicker;
    document.getElementById('folderCancelBtn').onclick = closeFolderPicker;
    document.getElementById('folderConfirmBtn').onclick = confirmFolderPicker;
    document.getElementById('folderUpBtn').onclick = () =>
        loadFolderListing(folderPicker.parent === null ? null : folderPicker.parent);
    document.getElementById('folderPathInput').addEventListener('keydown', e => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const v = e.target.value.trim();
            if (v) loadFolderListing(v);
        }
    });
    document.getElementById('folderModal').addEventListener('click', e => {
        if (e.target === document.getElementById('folderModal')) {
            closeFolderPicker();
        }
    });

    document.getElementById('sidebarNav').querySelectorAll('.nav-item').forEach(btn => {
        btn.onclick = () => switchNav(btn);
    });

    document.getElementById('taskTitle').onclick = () => toggleSection('taskList');

    // Model modal events
    document.getElementById('modelModalClose').onclick = closeModelModal;
    document.getElementById('modelModal').addEventListener('click', (e) => {
        if (e.target === document.getElementById('modelModal')) {
            closeModelModal();
        }
    });
    document.getElementById('toggleApiKeyBtn').onclick = toggleApiKeyVisibility;
    document.getElementById('testModelBtn').onclick = testModel;
    document.getElementById('deleteModelBtn').onclick = deleteModel;
    document.getElementById('saveModelBtn').onclick = saveModel;
}

function autoResize(e) {
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

/* ---------- Nav switching ---------- */
function switchNav(btn) {
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const view = btn.dataset.view;
    const key = btn.dataset.key;

    document.getElementById('viewChat').classList.toggle('hidden', view !== 'chat');
    document.getElementById('viewList').classList.toggle('hidden', view !== 'list');

    if (view === 'chat' && key === 'newtask') {
        clearChat();
    }
    if (view === 'list') {
        renderList(key);
    }
}

async function renderList(key) {
    const header = document.getElementById('listHeader');
    const body = document.getElementById('listBody');
    body.innerHTML = '<div class="empty-state">加载中…</div>';

    try {
        if (key === 'settings') {
            renderSettings(key);
        } else if (key === 'projects') {
            header.textContent = '项目 / 空间';
            const { spaces } = await api('/api/spaces');
            document.getElementById('taskCount').textContent = '';
            if (!spaces.length) {
                body.innerHTML = emptyState('还没有空间，去「新任务」创建第一个吧');
                return;
            }
            body.innerHTML = spaces.map(sp => `
                <div class="list-card">
                    <h3>📁 ${escapeHtml(sp.name)}</h3>
                    <p>${sp.tasks.length} 个任务</p>
                    ${sp.tasks.map(t => `<div class="space-child" data-sid="${t.id}">• ${escapeHtml(t.title)}</div>`).join('')}
                </div>`).join('');
            body.querySelectorAll('.space-child').forEach(el =>
                el.onclick = () => {
                    openSession(el.dataset.sid);
                    switchNav(chatNavBtn());
                }
            );
        } else if (key === 'experts') {
            header.textContent = '专家 · 技能 · 连接器';
            const [ex, sk, cn] = await Promise.all([
                api('/api/experts'),
                api('/api/skills'),
                api('/api/connectors')
            ]);
            let html = '';
            html += groupHtml('专家', ex.experts.map(e =>
                `<div class="list-card"><h3>${escapeHtml(e.name)}</h3><p>${escapeHtml(e.description || '')}</p><span class="tag">${escapeHtml(String(e.mode))}</span></div>`
            ));
            html += groupHtml('技能', sk.skills.map(s =>
                `<div class="list-card"><h3>${escapeHtml(s.name)}</h3><p>${escapeHtml(s.description || '')}</p></div>`
            ));
            html += groupHtml('连接器', (cn.connectors && cn.connectors.length)
                ? cn.connectors.map(c => `<div class="list-card"><h3>${escapeHtml(c.name || 'MCP')}</h3></div>`)
                : [emptyState('未配置连接器')]
            );
            body.innerHTML = html;
        } else if (key === 'automation') {
            header.textContent = '自动化';
            const { automations } = await api('/api/automations');
            body.innerHTML = automations.length
                ? automations.map(a => `<div class="list-card"><h3>${escapeHtml(a.name)}</h3></div>`).join('')
                : emptyState('暂无自动化任务');
        } else if (key === 'library') {
            header.textContent = '资料库';
            const { items } = await api('/api/library');
            body.innerHTML = items.length
                ? items.map(i => `<div class="list-card"><h3>${escapeHtml(i.name)}</h3></div>`).join('')
                : emptyState('资料库为空');
        } else if (key === 'more') {
            header.textContent = '更多';
            body.innerHTML = `<div class="list-card"><h3>关于 sdpost-claw</h3><p>开源全场景 AI 办公智能体桌面工作台。复刻 WorkBuddy 核心能力，本地优先、安全可控。</p></div>`;
        } else if (key === 'apps') {
            header.textContent = '应用 · 灵感';
            body.innerHTML = emptyState('灵感中心即将上线 ✨');
        }
    } catch (e) {
        body.innerHTML = emptyState('加载失败：' + e.message);
    }
}

function groupHtml(title, cards) {
    return `<h3 class="group-heading">${title}</h3><div class="list-grid">${cards.join('')}</div>`;
}
function emptyState(t) { return `<div class="empty-state">${escapeHtml(t)}</div>`; }

/* ---------- Sessions / sidebar ---------- */
async function loadSessions() {
    try {
        const { sessions } = await api('/api/sessions');
        document.getElementById('taskCount').textContent = sessions.length;
        const list = document.getElementById('taskList');
        if (!sessions.length) {
            list.innerHTML = '';
            return;
        }
        list.innerHTML = sessions.map(s => `
            <div class="task-item ${s.id === state.sessionId ? 'active' : ''}" data-sid="${s.id}">
                <div class="task-info">
                    <span class="task-title">${escapeHtml(s.title || '未命名')}</span>
                    <span class="task-sub">${escapeHtml((s.agent_mode || 'build'))} · ${fmtTime(s.updated_at)}</span>
                </div>
                <button class="task-delete-btn" data-sid="${s.id}" title="删除此任务">
                    <svg width="12" height="12" viewBox="0 0 14 14" fill="none">
                        <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                    </svg>
                </button>
            </div>`).join('');
        list.querySelectorAll('.task-item').forEach(el =>
            el.onclick = () => openSession(el.dataset.sid)
        );
        list.querySelectorAll('.task-delete-btn').forEach(btn =>
            btn.onclick = e => {
                e.stopPropagation();
                deleteSession(btn.dataset.sid);
            }
        );
    } catch (e) {
        console.error(e);
    }
}

async function deleteSession(id) {
    const sessions = await api('/api/sessions').catch(() => null);
    const s = sessions && sessions.sessions.find(x => x.id === id);
    const name = (s && s.title) || '未命名任务';
    if (!window.confirm(`确定删除「${name}」吗？本地消息记录将一并删除，且不可恢复。`)) return;

    try {
        await api('/api/sessions/' + id, { method: 'DELETE' });
        addTickerMessage('✓ 已删除任务: ' + name);
        if (state.sessionId === id) {
            clearChat();  // resets sessionId, closes stream, shows hero, reloads list
        } else {
            await loadSessions();
        }
    } catch (e) {
        addTickerMessage('✗ 删除失败: ' + e.message);
    }
}

const BROWSE_OPT = '__browse__';
const folderPicker = { path: '', parent: null };

async function loadSpaces() {
    try {
        const { spaces } = await api('/api/spaces');
        const sel = document.getElementById('workspaceSelect');
        const opts = ['<option value=".">.</option>'];
        spaces.forEach(sp =>
            opts.push(`<option value="${escapeHtml(sp.cwd)}">${escapeHtml(sp.name)}</option>`)
        );
        // Preserve a manually-picked local folder across reloads
        if (state.workspace && state.workspace !== '.' &&
                !spaces.some(sp => sp.cwd === state.workspace)) {
            opts.push(`<option value="${escapeHtml(state.workspace)}">${escapeHtml(state.workspace)}</option>`);
        }
        opts.push(`<option value="${BROWSE_OPT}">＋ 选择本地文件夹…</option>`);
        sel.innerHTML = opts.join('');
        sel.value = state.workspace || '.';
    } catch (e) {}
}

/* ---------- Folder picker (workspace) ---------- */
function openFolderPicker() {
    document.getElementById('folderModal').classList.remove('hidden');
    loadFolderListing(null);
}

function closeFolderPicker() {
    document.getElementById('folderModal').classList.add('hidden');
}

async function loadFolderListing(path) {
    const list = document.getElementById('folderList');
    list.innerHTML = '<div class="empty-state">加载中…</div>';
    try {
        const q = path ? `?path=${encodeURIComponent(path)}` : '';
        const data = await api('/api/fs/browse' + q);
        folderPicker.path = data.path || '';
        folderPicker.parent = data.parent;
        document.getElementById('folderPathInput').value = folderPicker.path;
        if (!data.dirs.length) {
            list.innerHTML = '<div class="empty-state">（没有子文件夹）</div>';
            return;
        }
        list.innerHTML = data.dirs.map(d =>
            `<div class="folder-item" data-path="${escapeHtml(d.path)}">📁 ${escapeHtml(d.name)}</div>`
        ).join('');
        list.querySelectorAll('.folder-item').forEach(el =>
            el.onclick = () => loadFolderListing(el.dataset.path)
        );
    } catch (e) {
        list.innerHTML = emptyState('加载失败：' + e.message);
    }
}

function confirmFolderPicker() {
    if (!folderPicker.path) return;
    state.workspace = folderPicker.path;
    const sel = document.getElementById('workspaceSelect');
    let opt = sel.querySelector(`option[value="${CSS.escape(folderPicker.path)}"]`);
    if (!opt) {
        opt = document.createElement('option');
        opt.value = folderPicker.path;
        opt.textContent = folderPicker.path;
        sel.insertBefore(opt, sel.querySelector(`option[value="${BROWSE_OPT}"]`));
    }
    sel.value = folderPicker.path;
    closeFolderPicker();
}

async function loadConfig() {
    try {
        const cfg = await api('/api/config');
        state.config = cfg;
        document.getElementById('modeSelect').value = cfg.mode || 'build';
    } catch (e) {}

    // Load real model list from API
    try {
        const { models } = await api('/api/models');
        state.availableModels = models;
        const sel = document.getElementById('modelSelect');
        sel.innerHTML = models.map(m => {
            const label = m.name + ' (' + m.provider + ')';
            return `<option value="${escapeHtml(m.id)}" ${m.enabled ? '' : 'disabled'}>${escapeHtml(label)}</option>`;
        }).join('');
        // Select the current model from config
        if (state.config.model) {
            const idx = models.findIndex(m => m.model === state.config.model || m.id === state.config.model);
            if (idx >= 0) sel.value = models[idx].id;
        }
    } catch (e) {}
}

async function updateConfig(patch) {
    try {
        const resp = await api('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch)
        });
        if (patch.model_id) {
            const m = state.availableModels.find(mm => mm.id === patch.model_id);
            if (m) {
                state.config.provider = m.provider;
                state.config.model = m.model;
            }
            if (resp.provider_error) {
                addTickerMessage('✗ 模型切换失败: ' + resp.provider_error);
            } else {
                addTickerMessage('✓ 已切换模型: ' + (resp.model || patch.model_id));
            }
        }
        if (patch.mode) state.config.mode = patch.mode;
    } catch (e) {
        addTickerMessage('✗ 配置更新失败: ' + e.message);
    }
}

async function refreshModelDropdown() {
    try {
        const { models } = await api('/api/models');
        state.availableModels = models;
        const sel = document.getElementById('modelSelect');
        sel.innerHTML = models.map(m => {
            const label = m.name + ' (' + m.provider + ')';
            return `<option value="${escapeHtml(m.id)}" ${m.enabled ? '' : 'disabled'}>${escapeHtml(label)}</option>`;
        }).join('');
        if (state.config.model) {
            const idx = models.findIndex(m => m.model === state.config.model || m.id === state.config.model);
            if (idx >= 0) sel.value = models[idx].id;
        }
    } catch (e) {}
}

async function newSession(title = '新任务') {
    const session = await api('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            cwd: state.workspace,
            title,
            agent_mode: document.getElementById('modeSelect').value
        }),
    });
    state.sessionId = session.id;
    document.getElementById('chatMessages').innerHTML = '';
    hideHero();
    await loadSessions();
    return session;
}

async function openSession(id) {
    state.sessionId = id;
    hideHero();
    document.getElementById('chatMessages').innerHTML = '';
    try {
        const s = await api('/api/sessions/' + id);
        (s.history || []).forEach(m => {
            if (m.role === 'user') addUserMessage(m.content);
            else if (m.role === 'assistant') addAssistantMessage(m.content);
            else if (m.role === 'tool') addToolCard(m.name, m.content, m.is_error);
        });
        if ((s.history || []).length) hideHero(); else showHero();
    } catch (e) {}
    await loadSessions();
    ensureStream(id);
}

/* ---------- Markdown rendering (offline, dependency-free) ---------- */
function renderMarkdown(src) {
    if (!src) return '';
    const esc = escapeHtml;
    const codeBlocks = [];

    // Extract fenced code blocks first so their content is not markdown-processed
    src = src.replace(/```(\w*)\n?([\s\S]*?)(?:```|$)/g, (_, lang, code) => {
        const idx = codeBlocks.length;
        codeBlocks.push({ lang: lang || 'text', code });
        return `\u0000CODE${idx}\u0000`;
    });

    const inline = s => esc(s)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|\W)\*([^*\n]+)\*(?=\W|$)/g, '$1<em>$2</em>')
        .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    const lines = src.split('\n');
    const out = [];
    let list = null;      // 'ul' | 'ol'
    let para = [];
    let quote = null;
    let tableRows = [];   // consecutive markdown table rows

    const flushPara = () => {
        if (para.length) { out.push(`<p>${para.map(inline).join('<br>')}</p>`); para = []; }
    };
    const flushTable = () => {
        if (!tableRows.length) return;
        const rows = tableRows.map(splitTableRow).filter(r => r.length);
        tableRows = [];
        if (!rows.length) return;
        const tr = cells => `<tr>${cells.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`;
        const head = rows.length > 1
            ? `<tr>${rows[0].map(c => `<th>${inline(c)}</th>`).join('')}</tr>`
            : '';
        const body = rows.slice(rows.length > 1 ? 1 : 0).map(tr).join('');
        out.push(`<table>${head}${body}</table>`);
    };
    const flushAll = () => {
        flushPara();
        flushTable();
        if (list) { out.push(`</${list}>`); list = null; }
        if (quote) { out.push('</blockquote>'); quote = null; }
    };

    const isTableSep = l => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
    const isTableRow = l => l.includes('|') && !isTableSep(l) && l.trim().startsWith('|');

    const splitTableRow = l =>
        l.split('|').map(c => c.trim())
            .filter((c, i, a) => !(i === 0 && c === '') && !(i === a.length - 1 && c === ''));

    for (const raw of lines) {
        const line = raw.replace(/\s+$/, '');

        const cb = line.match(/^\u0000CODE(\d+)\u0000$/);
        if (cb) {
            flushAll();
            const b = codeBlocks[Number(cb[1])];
            out.push(renderCodeBlock(b.lang, b.code));
            continue;
        }
        if (/^(---+|\*\*\*+|___+)\s*$/.test(line)) { flushAll(); out.push('<hr>'); continue; }
        if (isTableSep(line) && tableRows.length) { continue; }  // table separator row

        const h = line.match(/^(#{1,6})\s+(.*)/);
        if (h) { flushAll(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }

        if (!line.trim()) { flushAll(); continue; }

        const q = line.match(/^>\s?(.*)/);
        if (q) {
            flushPara(); if (list) { out.push(`</${list}>`); list = null; }
            if (!quote) { out.push('<blockquote>'); quote = true; }
            out.push(`<p>${inline(q[1])}</p>`);
            continue;
        }
        if (quote) { out.push('</blockquote>'); quote = null; }

        const ul = line.match(/^\s*[-*+]\s+(.*)/);
        const ol = line.match(/^\s*(\d+)[.)]\s+(.*)/);
        if (ul || ol) {
            flushPara();
            const want = ul ? 'ul' : 'ol';
            if (list !== want) {
                if (list) out.push(`</${list}>`);
                out.push(`<${want}>`);
                list = want;
            }
            out.push(`<li>${inline((ul ? ul[1] : ol[2]))}</li>`);
            continue;
        }
        if (list) { out.push(`</${list}>`); list = null; }

        // Table row (consecutive rows group into one <table>)
        if (isTableRow(line)) {
            flushPara();
            tableRows.push(line);
            continue;
        }
        flushTable();

        para.push(line);
    }
    flushAll();

    return out.join('\n');
}

function renderCodeBlock(lang, code) {
    const escd = escapeHtml(code.replace(/\n$/, ''));
    return `<div class="md-code"><div class="md-code-head"><span>${escapeHtml(lang)}</span>` +
        `<button class="md-copy-btn" data-code="${encodeURIComponent(code)}">复制</button></div>` +
        `<pre><code>${escd}</code></pre></div>`;
}

/* ---------- Chat ---------- */
async function sendMessage() {
    const box = document.getElementById('inputBox');
    const text = box.value.trim();
    if (!text || state.isProcessing) return;

    state.isProcessing = true;
    box.value = '';
    box.style.height = 'auto';
    document.getElementById('sendBtn').disabled = true;
    updateStatusIndicator();

    addTickerMessage('正在处理: ' + text.substring(0, 60));

    if (!state.sessionId) await newSession();
    addUserMessage(text);
    hideHero();
    ensureStream(state.sessionId);

    try {
        await api('/api/sessions/' + state.sessionId + '/prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
    } catch (e) {
        addAssistantMessage('发送失败：' + e.message);
        finishTurn();
    }
}

function addTickerMessage(msg) {
    const inner = document.getElementById('tickerInner');
    inner.innerHTML = `<span class="ticker-msg">${escapeHtml(msg)}</span>`;
}

function ensureStream(id) {
    if (state.stream && state.streamSession === id) return;
    if (state.stream) { state.stream.close(); }
    state.streamSession = id;
    state.stream = new EventSource('/api/sessions/' + id + '/stream');
    state.stream.onmessage = ev => {
        let data;
        try {
            data = JSON.parse(ev.data);
        } catch {
            return;
        }
        if (data.type === 'user') {
            addTickerMessage('用户: ' + (data.content || '').substring(0, 60));
            return;
        }
        if (data.type === 'delta') {
            appendDelta(data.kind, data.content);
            return;
        }
        if (data.type === 'message') {
            finalizeAssistantMessage(data.content);
            addTickerMessage('正在生成响应...');
        } else if (data.type === 'tool') {
            settleLiveTurn();
            addToolCard(data.name, data.content, data.is_error);
            addTickerMessage(`执行工具: ${data.name}`);
        } else if (data.type === 'turn_stats') {
            addTurnStats(data);
        } else if (data.type === 'title') {
            // Auto-generated task title after the first turn — refresh sidebar
            addTickerMessage('✓ 已生成任务标题: ' + (data.title || ''));
            loadSessions();
        } else if (data.type === 'done') {
            settleLiveTurn();
            settleLastAssistantMessage();
            finishTurn();
            addTickerMessage('任务完成 ✓');
            setTimeout(() => {
                tickerIndex = (tickerIndex + 1) % TICKER_MESSAGES.length;
                addTickerMessage(TICKER_MESSAGES[tickerIndex]);
            }, 3000);
        }
    };
    state.stream.onerror = () => { /* will auto-reconnect or stop */ };
}

function finishTurn() {
    state.isProcessing = false;
    document.getElementById('sendBtn').disabled = false;
    updateStatusIndicator();
    loadSessions();
    loadSpaces();
}

function addUserMessage(text) {
    const c = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = 'msg user';
    div.innerHTML = `<div class="msg-content"><div class="msg-role">你</div><div class="bubble">${escapeHtml(text)}</div></div><div class="msg-avatar">🙂</div>`;
    c.appendChild(div);
    scrollBottom();
}

function addAssistantMessage(text) {
    const c = document.getElementById('chatMessages');
    const last = c.lastChild;
    if (last && last.classList.contains('msg') && last.classList.contains('assistant')) {
        // Append to last message for streaming
        const bubble = last.querySelector('.bubble');
        bubble.innerHTML = renderMarkdown(text) + '<span class="cursor"></span>';
    } else {
        const div = document.createElement('div');
        div.className = 'msg assistant';
        div.innerHTML = `<div class="msg-avatar">🤖</div><div class="msg-content"><div class="msg-role">sdpost</div><div class="bubble md">${renderMarkdown(text)}<span class="cursor"></span></div></div>`;
        c.appendChild(div);
    }
    scrollBottom();
}

/* ---------- Streaming: SSE delta consumption (deepseek-harness style) ---------- */

/* Get the current live (still streaming) assistant message element, creating
 * it right after the last non-live message if needed. */
function ensureLiveAssistantMsg() {
    const c = document.getElementById('chatMessages');
    let msg = c.querySelector('.msg.assistant.live');
    if (!msg) {
        msg = document.createElement('div');
        msg.className = 'msg assistant live';
        msg.innerHTML = `<div class="msg-avatar">🤖</div><div class="msg-content"><div class="msg-role">sdpost</div></div>`;
        c.appendChild(msg);
    }
    return msg;
}

function appendDelta(kind, chunk) {
    if (!chunk) return;
    const msg = ensureLiveAssistantMsg();
    const content = msg.querySelector('.msg-content');

    if (kind === 'reasoning') {
        let tb = content.querySelector('.thinking-block');
        if (!tb) {
            tb = document.createElement('div');
            tb.className = 'thinking-block live';
            tb.innerHTML = `
                <button class="thinking-head" type="button">
                    <span class="tw">▾</span>
                    <span class="thinking-status">思考中…</span>
                    <span class="thinking-shimmer"></span>
                </button>
                <div class="thinking-body"></div>`;
            tb.querySelector('.thinking-head').onclick = () =>
                tb.classList.toggle('collapsed');
            content.insertBefore(tb, content.querySelector('.bubble'));
        }
        const body = tb.querySelector('.thinking-body');
        body.textContent += chunk;
        scrollBottom();
        return;
    }

    // text delta — live bubble with plain text (markdown comes on finalize)
    let bubble = content.querySelector('.bubble.streaming');
    if (!bubble) {
        bubble = document.createElement('div');
        bubble.className = 'bubble md streaming';
        bubble.innerHTML = `<span class="stream-text"></span><span class="cursor"></span>`;
        content.appendChild(bubble);
    }
    const st = bubble.querySelector('.stream-text');
    st.textContent += chunk;
    scrollBottom();
}

/* Settle everything still streaming: collapse thinking, freeze text bubble. */
function settleLiveTurn() {
    const c = document.getElementById('chatMessages');
    c.querySelectorAll('.msg.assistant.live').forEach(msg => {
        const tb = msg.querySelector('.thinking-block.live');
        if (tb) settleThinkingBlock(tb);
        const bubble = msg.querySelector('.bubble.streaming');
        if (bubble) {
            const st = bubble.querySelector('.stream-text');
            bubble.classList.remove('streaming');
            bubble.innerHTML = renderMarkdown(st.textContent);
        }
        msg.classList.remove('live');
    });
}

function settleThinkingBlock(tb) {
    tb.classList.remove('live');
    tb.classList.add('collapsed');
    const n = (tb.querySelector('.thinking-body').textContent || '').length;
    tb.querySelector('.thinking-status').textContent =
        n ? `已深度思考 · ${n} 字` : '深度思考';
    const shim = tb.querySelector('.thinking-shimmer');
    if (shim) shim.remove();
}

/* Authoritative final text replaces the streamed plain text with markdown. */
function finalizeAssistantMessage(text) {
    const c = document.getElementById('chatMessages');
    const live = c.querySelector('.msg.assistant.live');
    if (live) {
        const content = live.querySelector('.msg-content');
        const tb = content.querySelector('.thinking-block.live');
        if (tb) settleThinkingBlock(tb);
        let bubble = content.querySelector('.bubble');
        if (!bubble) {
            bubble = document.createElement('div');
            bubble.className = 'bubble md';
            content.appendChild(bubble);
        }
        bubble.classList.remove('streaming');
        bubble.innerHTML = renderMarkdown(text);
        live.classList.remove('live');
    } else {
        addAssistantMessage(text);
    }
    scrollBottom();
}

/* Remove the streaming cursor from the last assistant bubble (turn finished). */
function settleLastAssistantMessage() {
    const all = document.getElementById('chatMessages').querySelectorAll('.msg.assistant');
    const last = all[all.length - 1];
    if (last) {
        const cur = last.querySelector('.cursor');
        if (cur) cur.remove();
    }
}

const TOOL_ICONS = {
    read: '📄', write: '✏️', edit: '🩹', glob: '🔎', grep: '🔍',
    bash: '💻', webfetch: '🌐', question: '❓',
};

function addToolCard(name, content, isError) {
    const c = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = 'msg';
    const icon = TOOL_ICONS[name] || '⚡';
    const preview = (content || '').split('\n')[0].slice(0, 60) || '(无输出)';
    div.innerHTML = `<div class="msg-avatar">🔧</div><div class="tool-card collapsed ${isError ? 'error' : ''}"><div class="tool-head"><span class="tw">▸</span><span class="tool-icon">${icon}</span><span class="tool-name">${escapeHtml(name || 'tool')}</span><span class="tool-preview">${escapeHtml(preview)}</span></div><div class="tool-body"><pre><code>${escapeHtml(content || '(无输出)')}</code></pre></div></div>`;
    div.querySelector('.tool-head').onclick = () => {
        const card = div.querySelector('.tool-card');
        card.classList.toggle('collapsed');
        div.querySelector('.tw').textContent = card.classList.contains('collapsed') ? '▸' : '▾';
    };
    c.appendChild(div);
    scrollBottom();
}

/* ---------- Turn statistics (per conversation turn) ---------- */
function addTurnStats(stats) {
    const c = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = 'turn-stats';

    const dur = stats.duration_ms >= 1000
        ? (stats.duration_ms / 1000).toFixed(1) + ' s'
        : stats.duration_ms + ' ms';

    const toolChips = (stats.tool_calls || []).map(t =>
        `<span class="ts-chip ${t.is_error ? 'err' : ''}">${TOOL_ICONS[t.name] || '⚡'} ${escapeHtml(t.name)}</span>`
    ).join('');

    const rows = [
        ['耗时', dur],
        ['模型', escapeHtml(stats.model || '—')],
        ['迭代', `${stats.iterations} 步`],
        ['提示词', `${stats.prompt_chars} 字符`],
        ['思考', stats.reasoning_chars ? `${stats.reasoning_chars} 字` : '—'],
        ['工具调用', `${stats.tool_count} 次${stats.tool_errors ? ` · ${stats.tool_errors} 个失败` : ''}`],
    ];

    div.innerHTML = `
        <div class="ts-head">
            <span class="ts-label">📊 本轮统计</span>
            <span class="ts-model">${escapeHtml(stats.model || '')}</span>
        </div>
        <div class="ts-grid">${rows.map(([k, v]) =>
            `<div class="ts-row"><span class="ts-k">${k}</span><span class="ts-v">${v}</span></div>`).join('')}
        </div>
        ${toolChips ? `<div class="ts-tools">${toolChips}</div>` : ''}
    `;
    c.appendChild(div);
    scrollBottom();
}

/* Copy buttons for markdown code blocks (event delegation). */
document.addEventListener('click', e => {
    const btn = e.target.closest('.md-copy-btn');
    if (!btn) return;
    const code = decodeURIComponent(btn.dataset.code || '');
    navigator.clipboard.writeText(code).then(() => {
        btn.textContent = '✓ 已复制';
        setTimeout(() => { btn.textContent = '复制'; }, 1500);
    }).catch(() => {
        btn.textContent = '✗ 失败';
        setTimeout(() => { btn.textContent = '复制'; }, 1500);
    });
});

/* ---------- Hero / clear ---------- */
function showHero() { document.getElementById('chatHero').classList.remove('hidden'); }
function hideHero() { document.getElementById('chatHero').classList.add('hidden'); }
function clearChat() {
    state.sessionId = null;
    if (state.stream) {
        state.stream.close();
        state.stream = null;
        state.streamSession = null;
    }
    document.getElementById('chatMessages').innerHTML = '';
    showHero();
    loadSessions();
}

function scrollBottom() {
    const s = document.getElementById('chatScroll');
    s.scrollTop = s.scrollHeight;
}

/* ---------- Utils ---------- */
function toggleSection(id) {
    document.getElementById(id).classList.toggle('hidden');
}
function chatNavBtn() {
    return document.querySelector('.nav-item[data-key="newtask"]');
}
function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/* ---------- Settings (tabbed: general / models / context / skills / advanced) ---------- */
let editingModelId = null;
let settingsTab = 'general';

const SETTINGS_TABS = [
    { key: 'general',    label: '通用' },
    { key: 'models',     label: '模型配置' },
    { key: 'context',    label: '上下文与压缩' },
    { key: 'skills',     label: '技能与扩展' },
    { key: 'advanced',   label: '高级' },
];

async function renderSettings(key) {
    const header = document.getElementById('listHeader');
    const body = document.getElementById('listBody');

    header.textContent = '设置';

    // Tab bar
    let html = '<div class="settings-tabs" id="settingsTabs">';
    SETTINGS_TABS.forEach(t => {
        html += `<button class="settings-tab ${settingsTab === t.key ? 'active' : ''}" data-tab="${t.key}">${t.label}</button>`;
    });
    html += '</div><div id="settingsBody"><div class="empty-state">加载中…</div></div>';
    body.innerHTML = html;

    body.querySelectorAll('.settings-tab').forEach(btn => {
        btn.onclick = () => {
            settingsTab = btn.dataset.tab;
            renderSettings('settings');
        };
    });

    try {
        const cfg = await api('/api/config');
        state.config = cfg;
        const container = body.querySelector('#settingsBody');
        if (settingsTab === 'models') {
            await renderSettingsModels(container);
        } else {
            renderSettingsForm(container, cfg);
        }
    } catch (e) {
        body.querySelector('#settingsBody').innerHTML = emptyState('加载失败: ' + e.message);
    }
}

/* --- 通用 / 上下文 / 技能 / 高级: form-based settings --- */
function renderSettingsForm(container, cfg) {
    const comp = cfg.compaction || {};
    let html = '<div class="settings-form">';

    if (settingsTab === 'general') {
        html += `
            <div class="settings-row">
                <div class="settings-label">界面语言</div>
                <select class="form-input" id="setLanguage">
                    <option value="zh-CN" ${cfg.language === 'zh-CN' ? 'selected' : ''}>简体中文</option>
                    <option value="en" ${cfg.language === 'en' ? 'selected' : ''}>English</option>
                </select>
            </div>
            <div class="settings-row">
                <div class="settings-label">主题</div>
                <select class="form-input" id="setTheme">
                    <option value="default" ${(cfg.theme || 'default') === 'default' ? 'selected' : ''}>默认</option>
                    <option value="dark" ${cfg.theme === 'dark' ? 'selected' : ''}>深色</option>
                </select>
            </div>
            <div class="settings-row">
                <div class="settings-label">默认权限模式</div>
                <select class="form-input" id="setDefaultMode">
                    <option value="build" ${(cfg.default_mode || 'build') === 'build' ? 'selected' : ''}>build（完全访问）</option>
                    <option value="plan" ${cfg.default_mode === 'plan' ? 'selected' : ''}>plan（只读）</option>
                    <option value="general" ${cfg.default_mode === 'general' ? 'selected' : ''}>general（子任务）</option>
                </select>
            </div>
            <div class="settings-hint">权限模式决定智能体的默认工具访问范围，新会话创建时生效。</div>`;
    } else if (settingsTab === 'context') {
        html += `
            <div class="settings-row">
                <div class="settings-label">启用上下文压缩</div>
                <label class="checkbox-wrap"><input type="checkbox" id="setCompEnabled" ${comp.enabled !== false ? 'checked' : ''}></label>
            </div>
            <div class="settings-row">
                <div class="settings-label">压缩触发阈值（tokens）</div>
                <input type="number" class="form-input" id="setCompMax" min="1000" value="${comp.max_tokens || 100000}">
            </div>
            <div class="settings-row">
                <div class="settings-label">预留缓冲（tokens）</div>
                <input type="number" class="form-input" id="setCompBuffer" min="0" value="${comp.buffer_tokens || 20000}">
            </div>
            <div class="settings-row">
                <div class="settings-label">保留近史（tokens）</div>
                <input type="number" class="form-input" id="setCompKeep" min="0" value="${comp.keep_tokens || 8000}">
            </div>
            <div class="settings-hint">对话上下文超过阈值时，历史会被压缩为结构化摘要以继续工作。</div>`;
    } else if (settingsTab === 'skills') {
        html += `
            <div class="settings-row settings-row-col">
                <div class="settings-label">技能目录（每行一个路径）</div>
                <textarea class="form-input" id="setSkillDirs" rows="5" placeholder="D:\\skills&#10;C:\\Users\\me\\.sdpost\\skills">${escapeHtml((cfg.skill_dirs || []).join('\n'))}</textarea>
            </div>
            <div class="settings-hint">额外目录中的 SKILL.md / frontmatter Markdown 会被发现为可用技能。MCP 连接器: ${(cfg.mcp_servers || []).length} 个（在 config.yaml 中配置）。</div>`;
    } else if (settingsTab === 'advanced') {
        html += `
            <div class="settings-row">
                <div class="settings-label">日志级别</div>
                <select class="form-input" id="setLogLevel">
                    ${['DEBUG', 'INFO', 'WARNING', 'ERROR'].map(l =>
                        `<option value="${l}" ${(cfg.log_level || 'INFO') === l ? 'selected' : ''}>${l}</option>`).join('')}
                </select>
            </div>
            <div class="settings-row">
                <div class="settings-label">启用审计日志</div>
                <label class="checkbox-wrap"><input type="checkbox" id="setAudit" ${cfg.audit_enabled !== false ? 'checked' : ''}></label>
            </div>
            <div class="settings-row">
                <div class="settings-label">数据目录</div>
                <input type="text" class="form-input" value="${escapeHtml(cfg.sdpost_home || '')}" readonly>
            </div>`;
    }

    html += `
        </div>
        <div class="settings-actions">
            <button class="btn btn-primary" id="saveSettingsBtn">保存设置</button>
        </div>`;
    container.innerHTML = html;
    document.getElementById('saveSettingsBtn').onclick = saveSettingsForm;
}

async function saveSettingsForm() {
    const patch = {};
    try {
        if (settingsTab === 'general') {
            patch.language = document.getElementById('setLanguage').value;
            patch.theme = document.getElementById('setTheme').value;
            patch.default_mode = document.getElementById('setDefaultMode').value;
        } else if (settingsTab === 'context') {
            patch.compaction = {
                enabled: document.getElementById('setCompEnabled').checked,
                max_tokens: parseInt(document.getElementById('setCompMax').value, 10) || 100000,
                buffer_tokens: parseInt(document.getElementById('setCompBuffer').value, 10) || 20000,
                keep_tokens: parseInt(document.getElementById('setCompKeep').value, 10) || 8000,
            };
        } else if (settingsTab === 'skills') {
            patch.skill_dirs = document.getElementById('setSkillDirs').value
                .split('\n').map(s => s.trim()).filter(Boolean);
        } else if (settingsTab === 'advanced') {
            patch.log_level = document.getElementById('setLogLevel').value;
            patch.audit_enabled = document.getElementById('setAudit').checked;
        }
        await api('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
        });
        addTickerMessage('✓ 设置已保存');
        if (patch.default_mode) {
            document.getElementById('modeSelect').value = patch.default_mode;
        }
    } catch (e) {
        addTickerMessage('✗ 保存失败: ' + e.message);
    }
}

/* --- 模型配置: original model list --- */
async function renderSettingsModels(container) {
    container.innerHTML = '<div class="empty-state">加载模型列表…</div>';
    const { models } = await api('/api/models');
    let html = '';

    // Batch toolbar
    html += '<div class="batch-toolbar" id="batchToolbar">';
    html += '<label class="checkbox-wrap"><input type="checkbox" id="selectAllModels"> 全选</label>';
    html += '<span class="batch-info" id="batchInfo">已选 0 个</span>';
    html += '<button class="btn btn-ghost" id="batchDeleteBtn" style="display:none">删除选中</button>';
    html += '</div>';

    // Model list
    html += '<div class="list-grid">';
    models.forEach(m => {
        const statusClass = m.enabled ? 'enabled' : 'disabled';
        const statusLabel = m.enabled ? '已启用' : '已禁用';
        html += `
            <div class="provider-card" data-id="${escapeHtml(m.id)}">
                <div class="provider-header">
                    <label class="checkbox-wrap" onclick="event.stopPropagation()">
                        <input type="checkbox" class="model-checkbox" value="${escapeHtml(m.id)}">
                    </label>
                    <span class="provider-name">${escapeHtml(m.name)}</span>
                    <span class="provider-status ${statusClass}">${statusLabel}</span>
                </div>
                <div class="provider-id">${escapeHtml(m.provider)} · ${escapeHtml(m.model)}</div>
                <div class="provider-meta">
                    <span>${m.api_key_set ? '✅ 已配置密钥' : '❌ 未配置密钥'}</span>
                </div>
                <div class="provider-meta">${escapeHtml(m.base_url) || '—'}</div>
                <div class="provider-actions">
                    <button class="btn btn-ghost edit-model-btn" data-id="${escapeHtml(m.id)}">编辑</button>
                </div>
            </div>`;
    });
    html += '</div>';
    html += '<div style="margin-top: 16px;"><button class="btn btn-ghost" id="addModelBtn">+ 添加模型</button></div>';
    container.innerHTML = html;

    // Bind edit buttons
    container.querySelectorAll('.edit-model-btn').forEach(btn => {
        btn.onclick = () => openModelModal(btn.dataset.id);
    });
    document.getElementById('addModelBtn').onclick = () => openModelModal(null);

    // Multi-select events
    document.getElementById('selectAllModels').onchange = (e) => {
        container.querySelectorAll('.model-checkbox').forEach(cb => cb.checked = e.target.checked);
        updateBatchInfo();
    };
    container.querySelectorAll('.model-checkbox').forEach(cb => {
        cb.onchange = updateBatchInfo;
    });
    document.getElementById('batchDeleteBtn').onclick = batchDeleteModels;
}

function updateBatchInfo() {
    const checked = document.querySelectorAll('.model-checkbox:checked');
    const info = document.getElementById('batchInfo');
    const btn = document.getElementById('batchDeleteBtn');
    if (info) info.textContent = '已选 ' + checked.length + ' 个';
    if (btn) btn.style.display = checked.length > 0 ? '' : 'none';
}

async function batchDeleteModels() {
    const checked = document.querySelectorAll('.model-checkbox:checked');
    const ids = Array.from(checked).map(cb => cb.value);
    if (!ids.length) return;
    if (!confirm('确定要删除选中的 ' + ids.length + ' 个模型吗？')) return;

    try {
        await api('/api/models/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        });
        addTickerMessage('✓ 已删除 ' + ids.length + ' 个模型');
        renderSettings('settings');
        refreshModelDropdown();
    } catch (e) {
        addTickerMessage('✗ 删除失败: ' + e.message);
    }
}

function openModelModal(modelId) {
    editingModelId = modelId;
    const modal = document.getElementById('modelModal');
    const title = document.getElementById('modelModalTitle');
    const deleteBtn = document.getElementById('deleteModelBtn');

    // Reset form
    document.getElementById('editModelId').value = '';
    document.getElementById('editModelName').value = '';
    document.getElementById('editModelProvider').value = '';
    document.getElementById('editModelModel').value = '';
    document.getElementById('editModelApiKey').value = '';
    document.getElementById('editModelBaseUrl').value = '';
    document.getElementById('testResult').classList.add('hidden');
    document.getElementById('testResult').textContent = '';

    if (modelId) {
        title.textContent = '编辑模型';
        deleteBtn.classList.remove('hidden');
        document.getElementById('editModelId').readOnly = true;

        api('/api/models/' + encodeURIComponent(modelId)).then(m => {
            document.getElementById('editModelId').value = m.id;
            document.getElementById('editModelName').value = m.name;
            document.getElementById('editModelProvider').value = m.provider || '';
            document.getElementById('editModelModel').value = m.model || '';
            document.getElementById('editModelApiKey').value = '';
            document.getElementById('editModelBaseUrl').value = m.base_url || '';
        }).catch(() => {});
    } else {
        title.textContent = '添加模型';
        deleteBtn.classList.add('hidden');
        document.getElementById('editModelId').readOnly = false;
    }

    modal.classList.remove('hidden');
}

function closeModelModal() {
    document.getElementById('modelModal').classList.add('hidden');
    editingModelId = null;
}

async function saveModel() {
    const id = document.getElementById('editModelId').value.trim() || document.getElementById('editModelModel').value.trim();
    const name = document.getElementById('editModelName').value.trim() || id;
    const provider = document.getElementById('editModelProvider').value.trim();
    const model = document.getElementById('editModelModel').value.trim();
    const apiKey = document.getElementById('editModelApiKey').value.trim();
    const baseUrl = document.getElementById('editModelBaseUrl').value.trim();

    if (!model) {
        addTickerMessage('✗ 请输入模型名');
        return;
    }
    if (!baseUrl) {
        addTickerMessage('✗ 请输入 Base URL');
        return;
    }

    const isEdit = !!editingModelId;
    const url = isEdit ? '/api/models/' + encodeURIComponent(editingModelId) : '/api/models';
    const method = isEdit ? 'PUT' : 'POST';

    try {
        await api(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, name, provider, model, api_key: apiKey, base_url: baseUrl, enabled: true }),
        });
        addTickerMessage('✓ 模型已保存: ' + name);
        closeModelModal();
        renderSettings('settings');
        refreshModelDropdown();
    } catch (e) {
        addTickerMessage('✗ 保存失败: ' + e.message);
    }
}

async function testModel() {
    const id = document.getElementById('editModelId').value.trim() || document.getElementById('editModelModel').value.trim();
    const model = document.getElementById('editModelModel').value.trim();
    const apiKey = document.getElementById('editModelApiKey').value.trim();
    const baseUrl = document.getElementById('editModelBaseUrl').value.trim();

    const result = document.getElementById('testResult');
    result.className = 'test-result';
    result.textContent = '测试中…';
    result.classList.remove('hidden');

    try {
        const resp = await api('/api/models/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, model, api_key: apiKey, base_url: baseUrl }),
        });
        if (resp.status === 'ok') {
            result.className = 'test-result ok';
            result.textContent = `✓ 连接成功 (${resp.elapsed}s)`;
        } else {
            result.className = 'test-result error';
            result.textContent = `✗ 连接失败: ${resp.message || '未知错误'}`;
        }
    } catch (e) {
        result.className = 'test-result error';
        result.textContent = '✗ 测试失败: ' + e.message;
    }
}

async function deleteModel() {
    if (!editingModelId) return;
    if (!confirm('确定要删除模型 "' + editingModelId + '" 吗？')) return;

    try {
        await api('/api/models/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: [editingModelId] }),
        });
        addTickerMessage('✓ 模型已删除');
        closeModelModal();
        renderSettings('settings');
        refreshModelDropdown();
    } catch (e) {
        addTickerMessage('✗ 删除失败: ' + e.message);
    }
}

function toggleApiKeyVisibility() {
    const input = document.getElementById('editModelApiKey');
    const btn = document.getElementById('toggleApiKeyBtn');
    if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = '隐藏';
    } else {
        input.type = 'password';
        btn.textContent = '显示';
    }
}

async function checkHealth() {
    try {
        await api('/api/health');
        document.querySelector('.brand-version').textContent = 'v0.1.0 · 已连接';
        addTickerMessage('✓ 服务器连接正常');
    } catch (e) {
        document.querySelector('.brand-version').textContent = 'v0.1.0 · 未连接';
        addTickerMessage('✗ 无法连接服务器');
    }
}
