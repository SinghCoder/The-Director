(function () {
    'use strict';

    // ── State ──────────────────────────────────────────────

    let state = {
        sessionId: null,
        genre: null,
        nodes: [],            // timeline tree from API
        characters: [],
        currentNodeId: null,
        selectedNodeId: null,
        sceneData: {},        // nodeId → full scene payload
        seenScenes: new Set(),
    };

    const cam = { x: 0, y: 0, scale: 1, panning: false, sx: 0, sy: 0, scx: 0, scy: 0 };

    const NODE_W = 280;
    const NODE_H = 230;
    const H_GAP = 60;
    const V_GAP = 150;

    const GENRE_ICONS = { film_noir: '🕵️', high_fantasy: '⚔️', sci_fi: '🚀', horror: '👻' };

    // ── API helper ─────────────────────────────────────────

    async function api(method, path, body) {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (body) opts.body = JSON.stringify(body);
        const res = await fetch(path, opts);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            const msg = Array.isArray(err.detail)
                ? err.detail.map(e => e.msg || JSON.stringify(e)).join('; ')
                : (err.detail || 'API error');
            throw new Error(msg);
        }
        return res.json();
    }

    // ── Screen management ──────────────────────────────────

    function showScreen(id) {
        document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
        document.getElementById(id).classList.add('active');
    }

    // ── Genres ──────────────────────────────────────────────

    async function initGenres() {
        const data = await api('GET', '/api/genres');
        const grid = document.getElementById('genre-grid');
        grid.innerHTML = '';
        data.genres.forEach(g => {
            const card = document.createElement('div');
            card.className = 'genre-card';
            card.dataset.genre = g.id;
            card.innerHTML = `
                <div class="genre-icon">${GENRE_ICONS[g.id] || '📖'}</div>
                <div class="genre-name">${g.name}</div>`;
            card.addEventListener('click', () => {
                state.genre = g.id;
                document.querySelectorAll('.genre-card').forEach(c =>
                    c.classList.toggle('selected', c.dataset.genre === g.id));
                document.getElementById('start-btn').disabled = false;
            });
            grid.appendChild(card);
        });
    }

    // ── Start story ────────────────────────────────────────

    async function startStory() {
        if (!state.genre) return;
        showScreen('game-screen');
        showLoading('The Director is setting the stage...');

        try {
            const premise = document.getElementById('custom-premise').value.trim() || null;
            const data = await api('POST', '/api/story/create', { genre: state.genre, premise });

            document.body.className = `genre-${state.genre}`;
            state.sessionId = data.session_id;
            state.currentNodeId = data.scene.node_id;
            state.characters = data.characters;
            state.nodes = data.timeline;
            state.sceneData[data.scene.node_id] = data.scene;

            hideLoading();
            renderCanvas();
            centerOnNode(data.scene.node_id);
            selectNode(data.scene.node_id);
        } catch (err) {
            hideLoading();
            alert('Failed to start story: ' + err.message);
            showScreen('landing-screen');
        }
    }

    // ── Canvas: pan & zoom ─────────────────────────────────

    function initCanvas() {
        const ct = document.getElementById('canvas-container');

        ct.addEventListener('pointerdown', e => {
            if (e.target.closest('.node-card')) return;
            cam.panning = true;
            cam.sx = e.clientX; cam.sy = e.clientY;
            cam.scx = cam.x; cam.scy = cam.y;
            ct.classList.add('grabbing');
            ct.setPointerCapture(e.pointerId);
        });

        ct.addEventListener('pointermove', e => {
            if (!cam.panning) return;
            cam.x = cam.scx + (e.clientX - cam.sx);
            cam.y = cam.scy + (e.clientY - cam.sy);
            applyTransform();
        });

        ct.addEventListener('pointerup', () => {
            cam.panning = false;
            ct.classList.remove('grabbing');
        });

        ct.addEventListener('wheel', e => {
            e.preventDefault();
            const factor = e.deltaY > 0 ? 0.9 : 1.1;
            const next = Math.min(Math.max(cam.scale * factor, 0.15), 3);
            const rect = ct.getBoundingClientRect();
            const cx = e.clientX - rect.left;
            const cy = e.clientY - rect.top;
            cam.x = cx - (cx - cam.x) * (next / cam.scale);
            cam.y = cy - (cy - cam.y) * (next / cam.scale);
            cam.scale = next;
            applyTransform();
            updateZoomLabel();
        }, { passive: false });
    }

    function applyTransform() {
        document.getElementById('canvas-world').style.transform =
            `translate(${cam.x}px,${cam.y}px) scale(${cam.scale})`;
    }

    function updateZoomLabel() {
        document.getElementById('zoom-level').textContent = `${Math.round(cam.scale * 100)}%`;
    }

    // ── Tree layout ────────────────────────────────────────

    function layoutNodes() {
        if (!state.nodes.length) return {};
        const map = {};
        state.nodes.forEach(n => { map[n.id] = { ...n, _x: 0, _y: 0, _sw: NODE_W }; });

        const root = state.nodes.find(n => !n.parent_id);
        if (!root) return map;

        function subtreeW(id) {
            const nd = map[id]; if (!nd) return NODE_W;
            const ch = (nd.children || []).filter(c => map[c]);
            if (!ch.length) { nd._sw = NODE_W; return NODE_W; }
            let t = 0;
            ch.forEach((c, i) => { t += subtreeW(c); if (i < ch.length - 1) t += H_GAP; });
            nd._sw = Math.max(NODE_W, t);
            return nd._sw;
        }

        function position(id, x, y) {
            const nd = map[id]; if (!nd) return;
            nd._x = x + (nd._sw - NODE_W) / 2;
            nd._y = y;
            const ch = (nd.children || []).filter(c => map[c]);
            let cx = x;
            ch.forEach(c => { position(c, cx, y + NODE_H + V_GAP); cx += map[c]._sw + H_GAP; });
        }

        subtreeW(root.id);
        position(root.id, 0, 0);
        return map;
    }

    // ── Build the active-path set (root → currentNode) ─────

    function getActivePath() {
        const set = new Set();
        const map = {};
        state.nodes.forEach(n => { map[n.id] = n; });
        let cur = state.currentNodeId;
        while (cur) {
            set.add(cur);
            const nd = map[cur];
            cur = nd ? nd.parent_id : null;
        }
        return set;
    }

    // ── Render canvas ──────────────────────────────────────

    function renderCanvas() {
        const lay = layoutNodes();
        const nodesCt = document.getElementById('canvas-nodes');
        const svg = document.getElementById('canvas-edges');
        nodesCt.innerHTML = '';
        svg.innerHTML = '';

        const activePath = getActivePath();

        // Edges
        state.nodes.forEach(n => {
            if (!n.parent_id || !lay[n.parent_id]) return;
            const p = lay[n.parent_id], c = lay[n.id];
            const x1 = p._x + NODE_W / 2, y1 = p._y + NODE_H;
            const x2 = c._x + NODE_W / 2, y2 = c._y;
            const my = (y1 + y2) / 2;
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', `M${x1} ${y1} C${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`);
            const isActive = activePath.has(n.parent_id) && activePath.has(n.id);
            path.setAttribute('class', `edge-path${isActive ? ' active-path' : ''}`);
            svg.appendChild(path);
        });

        // Nodes
        Object.values(lay).forEach(n => {
            const card = document.createElement('div');
            card.className = 'node-card node-appear';
            card.dataset.nodeId = n.id;
            card.style.left = `${n._x}px`;
            card.style.top = `${n._y}px`;
            card.style.width = `${NODE_W}px`;

            if (n.id === state.currentNodeId) card.classList.add('active');
            if (n.id === state.selectedNodeId) card.classList.add('selected');

            const preview = n.narration_preview || '';
            const img = n.image_url;
            const fromChoice = n.from_choice_text;
            const branchCount = (n.children || []).length;

            let inner = '';
            if (fromChoice) {
                inner += `<div class="node-from-choice">→ ${truncate(fromChoice, 40)}</div>`;
            }
            inner += img
                ? `<div class="node-image"><img src="${img}" alt="Scene ${n.scene_number}"></div>`
                : `<div class="node-image node-image-placeholder"></div>`;
            inner += `<div class="node-info">
                <div class="node-scene-badge">Scene ${n.scene_number}</div>
                <div class="node-preview">${preview}</div>
            </div>`;
            if (branchCount > 1) {
                inner += `<div class="node-branch-badge">${branchCount} branches</div>`;
            }

            card.innerHTML = inner;
            card.addEventListener('click', e => { e.stopPropagation(); selectNode(n.id); });
            nodesCt.appendChild(card);
        });
    }

    // ── Detail panel ───────────────────────────────────────

    function selectNode(nodeId) {
        state.selectedNodeId = nodeId;
        document.querySelectorAll('.node-card').forEach(c =>
            c.classList.toggle('selected', c.dataset.nodeId === nodeId));
        showDetailPanel(nodeId);
    }

    function showDetailPanel(nodeId) {
        const panel = document.getElementById('detail-panel');
        const body = document.getElementById('detail-body');
        const titleEl = document.getElementById('detail-title');

        const tNode = state.nodes.find(n => n.id === nodeId);
        const scene = state.sceneData[nodeId];
        if (!tNode) return;

        const isActive = nodeId === state.currentNodeId && tNode.choice_made === null;

        titleEl.textContent = `Scene ${tNode.scene_number}`;
        let html = '';

        // Image
        const imgUrl = (scene && scene.image_url) || tNode.image_url;
        if (imgUrl) html += `<div class="detail-image"><img src="${imgUrl}" alt="Scene"></div>`;

        // Meta
        const ws = (scene && scene.world_state) || {};
        if (ws.location || ws.time) {
            html += `<div class="detail-meta">`;
            if (ws.location) html += `<span>📍 ${ws.location}</span>`;
            if (ws.time) html += `<span>🕐 ${ws.time}</span>`;
            if (ws.tension) html += `<span>⚡ Tension ${ws.tension}/10</span>`;
            html += `</div>`;
        }

        // Narration
        const narration = (scene && scene.narration) || tNode.narration_preview || '';
        const isNew = !state.seenScenes.has(nodeId);
        html += `<div class="detail-narration" id="detail-narration-text">${isNew ? '' : narration}</div>`;

        // Dialogue
        const dialogue = (scene && scene.dialogue) || [];
        if (dialogue.length) {
            html += `<div class="detail-dialogue" id="detail-dialogue-area">`;
            if (!isNew) {
                dialogue.forEach(d => {
                    html += buildDialogueHTML(d);
                });
            }
            html += `</div>`;
        } else {
            html += `<div class="detail-dialogue" id="detail-dialogue-area"></div>`;
        }

        // Choices (active node)
        const choices = tNode.choices_presented || [];
        if (isActive && choices.length) {
            html += `<div class="detail-section-title" id="choices-title" ${isNew ? 'style="display:none"' : ''}>What do you do?</div>`;
            html += `<div class="detail-choices" id="detail-choices" ${isNew ? 'style="display:none"' : ''}>`;
            choices.forEach(c => {
                html += `<button class="detail-choice-btn" data-choice-id="${c.id}">
                    <span class="choice-num">${c.id}</span>
                    <span class="choice-txt">${c.text}</span>
                    ${c.tone ? `<span class="choice-tn">${c.tone}</span>` : ''}
                </button>`;
            });
            html += `</div>`;
        }

        // Past choice made
        if (tNode.choice_made !== null) {
            const chosen = choices.find(c => c.id === tNode.choice_made);
            html += `<div class="detail-choice-made">
                <div class="detail-section-title">Choice made</div>
                <div class="chosen-text">${chosen ? chosen.text : 'Custom action'}</div>
            </div>`;

            const unchosen = choices.filter(c => c.id !== tNode.choice_made);
            if (unchosen.length) {
                html += `<div class="detail-section-title">Explore alternatives</div>`;
                html += `<div class="detail-alternatives">`;
                unchosen.forEach(c => {
                    html += `<button class="detail-fork-btn" data-node-id="${tNode.id}" data-choice-id="${c.id}">
                        <span class="fork-icon">↳</span>
                        <span>${c.text}</span>
                    </button>`;
                });
                html += `</div>`;
            }
        }

        // What-if (on any node)
        html += `<div class="detail-whatif">
            <div class="detail-section-title">What if…?</div>
            <textarea class="whatif-textarea" id="whatif-input" placeholder="Describe what happens instead…"></textarea>
            <button class="whatif-submit" id="whatif-btn" data-node-id="${tNode.id}" data-is-active="${isActive}">
                ${isActive ? 'Take Custom Action' : 'Fork Timeline'}
            </button>
        </div>`;

        body.innerHTML = html;
        panel.classList.add('visible');

        // Bind events
        body.querySelectorAll('.detail-choice-btn').forEach(btn =>
            btn.addEventListener('click', () => makeChoice(parseInt(btn.dataset.choiceId))));

        body.querySelectorAll('.detail-fork-btn').forEach(btn =>
            btn.addEventListener('click', () => forkTimeline(btn.dataset.nodeId, parseInt(btn.dataset.choiceId))));

        const wBtn = body.querySelector('#whatif-btn');
        if (wBtn) {
            wBtn.addEventListener('click', () => {
                const txt = body.querySelector('#whatif-input').value.trim();
                if (!txt) return;
                if (wBtn.dataset.isActive === 'true') {
                    makeChoice(null, txt);
                } else {
                    forkTimeline(wBtn.dataset.nodeId, null, txt);
                }
            });
        }

        // Typewriter for new scenes
        if (isNew && narration) {
            state.seenScenes.add(nodeId);
            typeWriter(document.getElementById('detail-narration-text'), narration, 15, () => {
                showDialogueSequence(document.getElementById('detail-dialogue-area'), dialogue, () => {
                    const ch = document.getElementById('detail-choices');
                    const ct = document.getElementById('choices-title');
                    if (ch) { ch.style.display = ''; ch.classList.add('fade-in'); }
                    if (ct) { ct.style.display = ''; }
                });
            });
        }
    }

    function hideDetailPanel() {
        document.getElementById('detail-panel').classList.remove('visible');
        state.selectedNodeId = null;
        document.querySelectorAll('.node-card.selected').forEach(c => c.classList.remove('selected'));
    }

    // ── Typewriter ─────────────────────────────────────────

    function typeWriter(el, text, speed, cb) {
        let i = 0;
        el.classList.add('typewriter-cursor');
        (function tick() {
            if (i < text.length) {
                const chunk = text.substring(i, Math.min(i + 3, text.length));
                el.textContent += chunk;
                i += chunk.length;
                setTimeout(tick, speed);
            } else {
                el.classList.remove('typewriter-cursor');
                if (cb) cb();
            }
        })();
    }

    function showDialogueSequence(container, list, cb) {
        if (!list || !list.length) { if (cb) cb(); return; }
        let i = 0;
        (function next() {
            if (i >= list.length) { if (cb) cb(); return; }
            const d = list[i];
            const el = document.createElement('div');
            el.className = 'dialogue-entry fade-in';
            el.innerHTML = buildDialogueHTML(d);
            container.appendChild(el);
            i++;
            setTimeout(next, 500);
        })();
    }

    function buildDialogueHTML(d) {
        const name = findCharName(d.speaker);
        return `<div class="dialogue-entry">
            <div class="dialogue-speaker">${name}</div>
            <div class="dialogue-text">"${d.text}"</div>
            ${d.delivery ? `<div class="dialogue-delivery">${d.delivery}</div>` : ''}
        </div>`;
    }

    function findCharName(id) {
        if (id === 'narrator') return 'Narrator';
        const c = state.characters.find(ch => ch.id === id);
        return c ? c.name : id;
    }

    // ── Actions ────────────────────────────────────────────

    async function makeChoice(choiceId, customText) {
        disableActions();
        showLoading('The story unfolds…');

        // Show loading placeholder node on canvas
        const parentNode = state.nodes.find(n => n.id === state.currentNodeId);
        const loadingId = showLoadingNode(parentNode);

        try {
            const body = {};
            if (customText) body.custom_text = customText;
            else body.choice_id = choiceId;

            const data = await api('POST', `/api/story/${state.sessionId}/choose`, body);

            state.currentNodeId = data.scene.node_id;
            state.characters = data.characters || state.characters;
            state.nodes = data.timeline;
            state.sceneData[data.scene.node_id] = data.scene;

            hideLoading();
            renderCanvas();
            animateToNode(data.scene.node_id, () => selectNode(data.scene.node_id));
        } catch (err) {
            hideLoading();
            removeLoadingNode(loadingId);
            alert('Failed: ' + err.message);
        }
    }

    async function forkTimeline(nodeId, altChoiceId, customText) {
        disableActions();
        showLoading('Exploring an alternate timeline…');

        const parentNode = state.nodes.find(n => n.id === nodeId);
        const loadingId = showLoadingNode(parentNode);

        try {
            const body = { from_node_id: nodeId };
            if (customText) body.custom_text = customText;
            else body.alt_choice_id = altChoiceId;

            const data = await api('POST', `/api/story/${state.sessionId}/fork`, body);

            state.currentNodeId = data.scene.node_id;
            state.nodes = data.timeline;
            state.sceneData[data.scene.node_id] = data.scene;

            hideLoading();
            renderCanvas();
            animateToNode(data.scene.node_id, () => selectNode(data.scene.node_id));
        } catch (err) {
            hideLoading();
            removeLoadingNode(loadingId);
            alert('Failed: ' + err.message);
        }
    }

    function disableActions() {
        document.querySelectorAll('.detail-choice-btn, .detail-fork-btn, .whatif-submit').forEach(b => {
            b.disabled = true;
            b.style.opacity = '0.4';
        });
    }

    // ── Loading placeholder node ───────────────────────────

    let _loadingNodeId = 0;
    function showLoadingNode(parentNodeData) {
        if (!parentNodeData) return null;
        const id = '__loading_' + (++_loadingNodeId);
        const lay = layoutNodes();
        const parent = lay[parentNodeData.id];
        if (!parent) return null;

        const childCount = (parentNodeData.children || []).length;
        const x = parent._x + (childCount * (NODE_W + H_GAP));
        const y = parent._y + NODE_H + V_GAP;

        const card = document.createElement('div');
        card.className = 'node-card loading-node node-appear';
        card.id = id;
        card.style.left = `${x}px`;
        card.style.top = `${y}px`;
        card.style.width = `${NODE_W}px`;
        card.innerHTML = `<div class="node-loading-content">
            <div class="spinner-small"></div>
            <span>Generating…</span>
        </div>`;
        document.getElementById('canvas-nodes').appendChild(card);

        // Draw edge to it
        const svg = document.getElementById('canvas-edges');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        const x1 = parent._x + NODE_W / 2, y1 = parent._y + NODE_H;
        const x2 = x + NODE_W / 2, y2 = y;
        const my = (y1 + y2) / 2;
        path.setAttribute('d', `M${x1} ${y1} C${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`);
        path.setAttribute('class', 'edge-path');
        path.setAttribute('stroke-dasharray', '6 4');
        path.id = id + '_edge';
        svg.appendChild(path);

        return id;
    }

    function removeLoadingNode(id) {
        if (!id) return;
        const el = document.getElementById(id);
        if (el) el.remove();
        const edge = document.getElementById(id + '_edge');
        if (edge) edge.remove();
    }

    // ── Canvas navigation ──────────────────────────────────

    function centerOnNode(nodeId) {
        const lay = layoutNodes();
        const n = lay[nodeId]; if (!n) return;
        const ct = document.getElementById('canvas-container').getBoundingClientRect();
        const panelW = document.getElementById('detail-panel').classList.contains('visible') ? 480 : 0;
        cam.x = ((ct.width - panelW) / 2) - (n._x + NODE_W / 2) * cam.scale;
        cam.y = (ct.height / 2) - (n._y + NODE_H / 2) * cam.scale;
        applyTransform();
    }

    function animateToNode(nodeId, cb) {
        const lay = layoutNodes();
        const n = lay[nodeId]; if (!n) { if (cb) cb(); return; }
        const ct = document.getElementById('canvas-container').getBoundingClientRect();
        const panelW = 480;
        const tx = ((ct.width - panelW) / 2) - (n._x + NODE_W / 2) * cam.scale;
        const ty = (ct.height / 2) - (n._y + NODE_H / 2) * cam.scale;
        const sx = cam.x, sy = cam.y;
        const t0 = performance.now();
        const dur = 500;
        (function frame(t) {
            const p = Math.min((t - t0) / dur, 1);
            const e = p * (2 - p); // ease-out
            cam.x = sx + (tx - sx) * e;
            cam.y = sy + (ty - sy) * e;
            applyTransform();
            if (p < 1) requestAnimationFrame(frame);
            else if (cb) cb();
        })(t0);
    }

    function fitView() {
        if (!state.nodes.length) return;
        const lay = layoutNodes();
        const ns = Object.values(lay);
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        ns.forEach(n => {
            x0 = Math.min(x0, n._x); y0 = Math.min(y0, n._y);
            x1 = Math.max(x1, n._x + NODE_W); y1 = Math.max(y1, n._y + NODE_H);
        });
        const ct = document.getElementById('canvas-container').getBoundingClientRect();
        const panelW = document.getElementById('detail-panel').classList.contains('visible') ? 480 : 0;
        const pad = 80;
        const cw = x1 - x0 + pad * 2, ch = y1 - y0 + pad * 2;
        const aw = ct.width - panelW, ah = ct.height;
        cam.scale = Math.min(aw / cw, ah / ch, 1.5);
        cam.x = (aw / 2) - ((x0 + x1) / 2) * cam.scale;
        cam.y = (ah / 2) - ((y0 + y1) / 2) * cam.scale;
        applyTransform();
        updateZoomLabel();
    }

    // ── Loading overlay ────────────────────────────────────

    function showLoading(msg) {
        document.getElementById('loading-msg').textContent = msg;
        document.getElementById('loading-overlay').classList.remove('hidden');
    }
    function hideLoading() {
        document.getElementById('loading-overlay').classList.add('hidden');
    }

    // ── Helpers ────────────────────────────────────────────

    function truncate(s, max) {
        return s.length > max ? s.substring(0, max) + '…' : s;
    }

    // ── Init ───────────────────────────────────────────────

    function init() {
        initGenres();
        initCanvas();

        document.getElementById('start-btn').addEventListener('click', startStory);
        document.getElementById('close-detail').addEventListener('click', hideDetailPanel);
        document.getElementById('zoom-in-btn').addEventListener('click', () => {
            cam.scale = Math.min(cam.scale * 1.2, 3); applyTransform(); updateZoomLabel();
        });
        document.getElementById('zoom-out-btn').addEventListener('click', () => {
            cam.scale = Math.max(cam.scale * 0.8, 0.15); applyTransform(); updateZoomLabel();
        });
        document.getElementById('fit-btn').addEventListener('click', fitView);

        // Close detail when clicking empty canvas
        document.getElementById('canvas-container').addEventListener('click', e => {
            if (!e.target.closest('.node-card') && state.selectedNodeId) {
                hideDetailPanel();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
