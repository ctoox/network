/* NetSim frontend: SVG topology canvas + WebSocket terminal. */
(function () {
  const canvas = document.getElementById('topo-canvas');
  const term = document.getElementById('term');
  const label = document.getElementById('session-label');
  const modal = document.getElementById('device-modal');

  let topoData = { nodes: [], links: [] };
  let layout = {};        // id -> {x, y} (topology coordinates)
  let view = { x: 0, y: 0, scale: 1 };
  let selected = null;    // selected node id
  let ws = null;
  let currentDevice = null;
  let buf = '';
  let history = [];
  let histIdx = 0;
  let prompt = '';
  let connected = false;
  let baseHTML = '';
  let wsPort = 0;

  // ================= topo layout =================

  function autoLayout(nodes, links) {
    const W = canvas.clientWidth || 800, H = canvas.clientHeight || 500;
    const n = nodes.length;
    if (n === 0) return {};
    const pos = {};
    const cx = W / 2, cy = H / 2;

    // Simple, bounded layout: place nodes on a circle that always fits the canvas.
    // Avoids the runaway force-directed scattering that pushed nodes off-screen.
    const R = Math.min(W, H) * 0.26;
    const nodeH = 60;
    const cyAdj = cy - nodeH / 2;
    nodes.forEach((node, i) => {
      const ang = (2 * Math.PI * i) / n - Math.PI / 2;
      pos[node.id] = { x: cx + Math.cos(ang) * R, y: cyAdj + Math.sin(ang) * R };
    });
    return pos;
  }

  function loadTopology() {
    canvas.innerHTML = '<div id="topo-hint">加载中…</div>';
    fetch('/api/topology')
      .then((r) => r.json())
      .then((data) => {
        topoData = data;
        wsPort = data.ws_port || 0;
        layout = autoLayout(data.nodes || [], data.links || []);
        view = { x: 0, y: 0, scale: 1 };
        renderTopology();
      })
      .catch((e) => {
        canvas.innerHTML = '<div id="topo-hint">无法加载拓扑: ' + e.message + '</div>';
      });
  }

  function renderTopology() {
    canvas.innerHTML = '';
    const W = canvas.clientWidth || 800, H = canvas.clientHeight || 500;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'topo-svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.scale})`);
    svg.appendChild(g);

    // links
    for (const link of topoData.links || []) {
      const a = layout[link.a.device], b = layout[link.b.device];
      if (!a || !b) continue;
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('class', 'link-line');
      line.setAttribute('x1', a.x); line.setAttribute('y1', a.y);
      line.setAttribute('x2', b.x); line.setAttribute('y2', b.y);
      g.appendChild(line);
      // label
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const lbl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      lbl.setAttribute('class', 'link-label');
      lbl.setAttribute('x', mx); lbl.setAttribute('y', my - 4);
      lbl.setAttribute('text-anchor', 'middle');
      lbl.textContent = link.a.iface + ' ⟷ ' + link.b.iface;
      g.appendChild(lbl);
    }

    // nodes
    for (const node of topoData.nodes || []) {
      const p = layout[node.id];
      if (!p) continue;
      const grp = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      grp.setAttribute('class', 'node-group' + (node.id === selected ? ' selected' : ''));
      grp.setAttribute('transform', `translate(${p.x},${p.y})`);
      grp.dataset.id = node.id;

      const fill = node.vendor === 'Huawei' ? '#2a2d18' : '#142c3a';
      const stroke = node.vendor === 'Huawei' ? '#fec92e' : '#00a1e9';

      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      rect.setAttribute('class', 'node-rect');
      rect.setAttribute('x', -52); rect.setAttribute('y', -26);
      rect.setAttribute('width', 104); rect.setAttribute('height', 52);
      rect.setAttribute('rx', 8);
      rect.setAttribute('fill', fill);
      rect.setAttribute('stroke', stroke);
      grp.appendChild(rect);

      // device type icon (simple glyphs)
      const glyph = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      glyph.setAttribute('class', 'icon-glyph');
      glyph.setAttribute('x', -42); glyph.setAttribute('y', -4);
      glyph.textContent = { Router: '⟳', Switch: '▤', Host: '🖥', Firewall: '⬡' }[node.type] || '■';
      grp.appendChild(glyph);

      const name = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      name.setAttribute('class', 'node-name');
      name.setAttribute('x', -20); name.setAttribute('y', -2);
      name.textContent = node.name;
      grp.appendChild(name);

      const type = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      type.setAttribute('class', 'node-type');
      type.setAttribute('x', -20); type.setAttribute('y', 12);
      type.textContent = node.vendor + ' ' + node.type;
      grp.appendChild(type);

      const ip = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      ip.setAttribute('class', 'node-ip');
      ip.setAttribute('x', 0); ip.setAttribute('y', 24);
      ip.setAttribute('text-anchor', 'middle');
      ip.textContent = (node.ips || []).filter(Boolean).join('  ') || '—';
      grp.appendChild(ip);

      grp.addEventListener('mousedown', onNodeMouseDown);
      grp.addEventListener('click', () => selectNode(node.id));
      g.appendChild(grp);
    }

    canvas.appendChild(svg);
  }

  // ================= dragging & zoom =================

  let dragState = null;

  function onNodeMouseDown(e) {
    e.preventDefault();
    const id = e.currentTarget.dataset.id;
    const p = layout[id];
    if (!p) return;
    dragState = { id, sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y };
    canvas.classList.add('dragging');
  }

  document.addEventListener('mousemove', (e) => {
    if (!dragState) return;
    const p = layout[dragState.id];
    const dx = (e.clientX - dragState.sx) / view.scale;
    const dy = (e.clientY - dragState.sy) / view.scale;
    p.x = dragState.ox + dx;
    p.y = dragState.oy + dy;
    renderTopology();
  });

  document.addEventListener('mouseup', () => {
    dragState = null;
    canvas.classList.remove('dragging');
  });

  document.getElementById('btn-zoom-in').addEventListener('click', () => zoom(1.2));
  document.getElementById('btn-zoom-out').addEventListener('click', () => zoom(1 / 1.2));
  document.getElementById('btn-fit').addEventListener('click', () => { view = { x: 0, y: 0, scale: 1 }; renderTopology(); });

  function zoom(factor) {
    const W = canvas.clientWidth, H = canvas.clientHeight;
    const cx = W / 2, cy = H / 2;
    view.x = cx - (cx - view.x) * factor;
    view.y = cy - (cy - view.y) * factor;
    view.scale *= factor;
    renderTopology();
  }

  // ================= device select / modal =================

  function selectNode(id) {
    selected = id;
    renderTopology();
    connectDevice(id);
  }

  // ================= terminal =================

  function renderInput() {
    term.innerHTML = baseHTML;
    const line = document.createElement('span');
    line.innerHTML = '<span class="prompt">' + esc(prompt) + '</span>' + esc(buf) + '<span class="cursor"></span>';
    term.appendChild(line);
    term.scrollTop = term.scrollHeight;
  }

  function appendRaw(text) {
    baseHTML += esc(text).replace(/\n/g, '<br>');
    renderInput();
  }

  function appendHTML(html) {
    baseHTML += html;
    renderInput();
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function clearTerm() {
    baseHTML = '';
    term.innerHTML = '';
  }

  // ================= websocket =================

  function connectDevice(deviceId) {
    currentDevice = deviceId;
    label.textContent = deviceId;
    label.classList.add('active');
    clearTerm();
    buf = '';
    history = [];
    if (ws) ws.close();

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const port = wsPort || location.port || '80';
    ws = new WebSocket(proto + '://' + location.hostname + ':' + port + '/ws');
    ws.onopen = () => ws.send(JSON.stringify({ cmd: 'connect', device: deviceId }));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'banner') {
        connected = true;
        if (msg.text) appendRaw(msg.text);
        prompt = msg.prompt || '';
        renderInput();
      } else if (msg.type === 'exec') {
        if (msg.text) appendRaw(msg.text);
        prompt = msg.prompt || '';
        renderInput();
      } else if (msg.type === 'stream') {
        appendRaw(msg.text);
      } else if (msg.type === 'tab') {
        if (msg.text) { buf = msg.text; renderInput(); }
      } else if (msg.type === 'error') {
        appendHTML('<span class="err">! ' + esc(msg.text) + '</span><br>');
        renderInput();
      }
    };
    ws.onclose = () => {
      if (currentDevice === deviceId) {
        appendHTML('<span class="err">[连接已断开]</span><br>');
        label.classList.remove('active');
        connected = false;
      }
    };
    ws.onerror = () => appendHTML('<span class="err">[WebSocket 错误]</span><br>');
  }

  // ================= keyboard =================

  document.addEventListener('keydown', (e) => {
    if (!connected || !ws) return;
    if (e.ctrlKey && e.key.toLowerCase() === 'c') {
      e.preventDefault();
      buf = '';
      appendRaw('');
      renderInput();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const line = buf;
      buf = '';
      appendRaw('');
      if (line.trim() !== '') {
        history.push(line);
        histIdx = history.length;
        ws.send(JSON.stringify({ cmd: 'exec', line }));
      }
      renderInput();
      return;
    }
    if (e.key === 'Backspace') {
      e.preventDefault();
      buf = buf.slice(0, -1);
      renderInput();
      return;
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      ws.send(JSON.stringify({ cmd: 'tab', line: buf }));
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (history.length) {
        histIdx = Math.max(0, histIdx - 1);
        buf = history[histIdx] || '';
        renderInput();
      }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (history.length) {
        histIdx = Math.min(history.length, histIdx + 1);
        buf = history[histIdx] || '';
        renderInput();
      }
      return;
    }
    if (e.key.length === 1) {
      e.preventDefault();
      buf += e.key;
      renderInput();
    }
  });

  term.addEventListener('click', () => term.focus());
  document.getElementById('btn-refresh').addEventListener('click', loadTopology);
  modal.querySelectorAll('.modal-close').forEach((el) => el.addEventListener('click', () => modal.classList.add('hidden')));

  window.addEventListener('resize', () => { if (topoData.nodes) renderTopology(); });

  loadTopology();
})();