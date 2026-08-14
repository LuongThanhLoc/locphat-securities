(() => {
  'use strict';

  const root = document.querySelector('[data-market-ribbon]');
  if (!root) return;
  const track = root.querySelector('[data-market-ribbon-track]');
  const statusText = root.querySelector('[data-market-ribbon-status]');
  const statusDot = root.querySelector('[data-market-ribbon-dot]');
  const updatedAt = root.querySelector('[data-market-ribbon-updated]');
  if (!track || !statusText || !statusDot || !updatedAt) return;

  const state = {controller: null, timer: 0, requestId: 0, signature: '', nodes: new Map(), payload: null};
  const tickerClass = item => {
    if (item.status === 'CEILING') return 'ceiling';
    if (item.status === 'FLOOR') return 'floor';
    const change = Number(item.change_percent);
    return Number.isFinite(change) ? (change > 0 ? 'up' : change < 0 ? 'down' : 'neutral') : 'unavailable';
  };
  const tickerPrice = item => {
    if (item.last_price === null || item.last_price === undefined) return '--';
    if (item.value_display) return String(item.value_display);
    return Number(item.last_price).toLocaleString('vi-VN', {
      minimumFractionDigits: item.type === 'index' ? 2 : 0,
      maximumFractionDigits: item.type === 'index' ? 2 : 0,
    });
  };

  function updateNode(node, item) {
    const direction = tickerClass(item);
    const change = Number(item.change_percent);
    const hasChange = Number.isFinite(change);
    const arrow = direction === 'up' || direction === 'ceiling' ? '▲' : direction === 'down' || direction === 'floor' ? '▼' : '•';
    node.className = `ticker-item ${direction}${item.stale ? ' is-stale' : ''}`;
    node.title = `${item.symbol} • ${item.source || 'Chưa có nguồn'} • ${item.as_of || item.observed_at || '--'}`;
    node.querySelector('.ticker-sym').textContent = item.symbol;
    node.querySelector('.ticker-val').textContent = tickerPrice(item);
    const changeNode = node.querySelector('.ticker-chg');
    changeNode.className = `ticker-chg ${direction}`;
    node.querySelector('.ticker-arrow').textContent = arrow;
    node.querySelector('.ticker-percent').textContent = hasChange ? `${change > 0 ? '+' : ''}${change.toFixed(2)}%` : '--';
    node.querySelector('.ticker-stale').hidden = !item.stale;
  }

  function createNode(item) {
    const node = document.createElement('div'); node.dataset.symbol = item.symbol; node.tabIndex = 0;
    const symbol = document.createElement('span'); symbol.className = 'ticker-sym';
    const value = document.createElement('span'); value.className = 'ticker-val';
    const change = document.createElement('span'); change.className = 'ticker-chg';
    const arrow = document.createElement('span'); arrow.className = 'ticker-arrow';
    const percent = document.createElement('span'); percent.className = 'ticker-percent';
    const stale = document.createElement('span'); stale.className = 'ticker-stale'; stale.textContent = 'Đã cũ';
    change.append(arrow, percent); node.append(symbol, value, change, stale); updateNode(node, item);
    return node;
  }

  function rebuild(items, signature) {
    state.nodes = new Map();
    const fragment = document.createDocumentFragment();
    for (let copy = 0; copy < 2; copy += 1) {
      const group = document.createElement('div'); group.className = 'ticker-group';
      group.setAttribute('aria-hidden', copy ? 'true' : 'false');
      items.forEach(item => {
        const node = createNode(item); if (copy) node.tabIndex = -1;
        const nodes = state.nodes.get(item.symbol) || []; nodes.push(node); state.nodes.set(item.symbol, nodes); group.append(node);
      });
      fragment.append(group);
    }
    track.replaceChildren(fragment); track.classList.add('is-running'); state.signature = signature;
    requestAnimationFrame(() => {
      const width = track.querySelector('.ticker-group')?.scrollWidth || 1800;
      track.style.setProperty('--ticker-duration', `${Math.max(64, width / 28).toFixed(1)}s`);
    });
  }

  function updateStatus(payload) {
    const live = Boolean(payload.market_session?.is_live_matching);
    const stale = Boolean(payload.stale || payload.last_known_good);
    statusDot.className = `ticker-status-dot ${stale ? 'stale' : live ? 'live' : 'closed'}`;
    statusText.textContent = stale ? 'Đã cũ' : live ? 'Trực tiếp' : 'Đóng cửa';
    const stamp = payload.generated_at ? new Date(payload.generated_at) : null;
    updatedAt.textContent = stamp && !Number.isNaN(stamp.getTime())
      ? stamp.toLocaleTimeString('vi-VN', {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '';
    updatedAt.dateTime = payload.generated_at || '';
  }

  function render(payload) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    if (items.length !== 32 || Number(payload?.membership?.count) !== 30) throw new Error(`Độ phủ bảng giá không hợp lệ (${items.length}/32)`);
    const signature = items.map(item => item.symbol).join('|');
    if (signature !== state.signature) rebuild(items, signature);
    else items.forEach(item => (state.nodes.get(item.symbol) || []).forEach(node => updateNode(node, item)));
    state.payload = payload; updateStatus(payload);
  }

  function schedule(seconds, elapsedMs = 0) {
    clearTimeout(state.timer);
    if (document.hidden) return;
    const interval = Math.max(10, Number(seconds) || 10) * 1000;
    state.timer = window.setTimeout(load, Math.max(1000, interval - Math.max(0, elapsedMs)));
  }

  async function load() {
    if (document.hidden) return;
    state.controller?.abort();
    const controller = new AbortController(); const requestId = ++state.requestId; const startedAt = performance.now();
    state.controller = controller;
    try {
      const response = await fetch('/api/market-ribbon', {cache: 'no-store', signal: controller.signal});
      const payload = await response.json();
      if (!response.ok) {
        const failure = new Error(payload.detail || `HTTP ${response.status}`);
        failure.authRequired = response.status === 401;
        throw failure;
      }
      if (requestId !== state.requestId) return;
      render(payload); schedule(payload.refresh_after_seconds, performance.now() - startedAt);
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (state.payload) updateStatus({...state.payload, stale: true, last_known_good: true});
      else {
        const loading = document.createElement('div'); loading.className = 'ticker-loading';
        loading.textContent = error.authRequired ? 'Đăng nhập để xem bảng giá VN30.' : 'Bảng giá VN30 tạm thời chưa sẵn sàng.';
        track.replaceChildren(loading); statusText.textContent = 'Mất kết nối'; statusDot.className = 'ticker-status-dot stale';
      }
      schedule(10, performance.now() - startedAt);
    } finally {
      if (state.controller === controller) state.controller = null;
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(state.timer); state.controller?.abort(); }
    else load();
  });
  load();
})();
