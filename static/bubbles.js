(() => {
  'use strict';

  const state = {
    range: '1D', sizeMetric: 'market_cap', exchange: 'ALL', sector: 'ALL', query: '',
    datasets: new Map(), nodes: [], filtered: [], ranked: [], simulation: null,
    pageIndex: 0, pageSize: 110, totalFiltered: 0,
    transform: { x: 0, y: 0, k: 1 }, hovered: null, selected: null,
    pointer: null,
    dialogOpen: false, dialogTrigger: null, resizeFrame: 0, retryTimer: 0,
    marketRefreshTimer: 0, liveRefreshInFlight: false,
    reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
  };
  const $ = (id) => document.getElementById(id);
  const canvas = $('bubbleCanvas');
  const stage = $('bubbleStage');
  const ctx = canvas.getContext('2d');
  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const signed = (value) => value == null ? '--' : `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
  const compactMoney = (value) => {
    const number = Number(value || 0);
    if (number >= 1e15) return `${(number / 1e15).toLocaleString('vi-VN', { maximumFractionDigits: 2 })} triệu tỷ`;
    if (number >= 1e12) return `${(number / 1e12).toLocaleString('vi-VN', { maximumFractionDigits: 2 })} nghìn tỷ`;
    if (number >= 1e9) return `${(number / 1e9).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} tỷ`;
    return number.toLocaleString('vi-VN');
  };
  const changeClass = (value) => value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral';
  const statusLabel = (status) => ({
    OK: 'Đã xác minh', MISSING_HISTORY: 'Thiếu lịch sử', REFERENCE_TOO_OLD: 'Mốc lịch sử quá cũ',
    UNKNOWN_PRICE_BASIS: 'Chưa xác định cơ sở giá', INVALID_REFERENCE_DATE: 'Ngày tham chiếu không hợp lệ',
    INVALID_REFERENCE_PRICE: 'Giá tham chiếu không hợp lệ', MISSING_CURRENT_PRICE: 'Thiếu giá hiện tại',
    SOURCE_QUALITY_FAILED: 'Không đạt kiểm tra chéo nguồn',
  }[status] || status || 'Chưa xác minh');
  const basisLabel = (basis) => basis === 'SESSION_REFERENCE'
    ? 'Giá khớp / tham chiếu cùng phiên'
    : basis === 'ADJUSTED_CLOSE' ? 'Giá đóng cửa điều chỉnh' : 'Chưa xác định';
  const sourceLabel = (value) => String(value || 'Chưa có nguồn').replace('Vietcap public price board', 'Vietcap bảng giá công khai');
  const formatDateTime = (value) => {
    if (!value) return '--';
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return parsed.toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'medium' });
    return String(value);
  };
  const pageSizeForWidth = (width) => {
    if (width <= 359) return 50;
    if (width <= 767) return 70;
    if (width <= 1023) return 90;
    if (width <= 1535) return 110;
    return 140;
  };
  const metricLabel = () => state.sizeMetric === 'trading_value' ? 'Giá trị giao dịch' : 'Vốn hóa';
  const distributedHome = (index, total, width, height, radius = 0) => {
    const padding = Math.max(12, radius + 4);
    const usableWidth = Math.max(1, width - padding * 2);
    const usableHeight = Math.max(1, height - padding * 2);
    const xRatio = (index * .61803398875 + .11) % 1;
    const yRatio = (index * .75487766625 + .37) % 1;
    return { homeX: padding + xRatio * usableWidth, homeY: padding + yRatio * usableHeight };
  };

  function colorFor(node) {
    if (node.change_pct == null) return { fill: '#737d7a', edge: '#a6aeab', glow: 'rgba(115,125,122,.26)' };
    if (state.range === '1D' && node.status === 'CEILING') return { fill: '#6940a8', edge: '#9b6be2', glow: 'rgba(105,64,168,.3)' };
    if (state.range === '1D' && node.status === 'FLOOR') return { fill: '#197a9b', edge: '#52b7d8', glow: 'rgba(25,122,155,.28)' };
    const value = clamp(Number(node.change_pct), -12, 12);
    if (value > .05) {
      const t = Math.min(1, value / 7);
      return { fill: d3.interpolateRgb('#46635a', '#087b50')(t), edge: d3.interpolateRgb('#7ba293', '#18d88e')(t), glow: `rgba(8,123,80,${.16 + t * .2})` };
    }
    if (value < -.05) {
      const t = Math.min(1, Math.abs(value) / 7);
      return { fill: d3.interpolateRgb('#70585b', '#a71f35')(t), edge: d3.interpolateRgb('#aa858a', '#f34a63')(t), glow: `rgba(167,31,53,${.16 + t * .2})` };
    }
    return { fill: '#52635f', edge: '#8b9d98', glow: 'rgba(82,99,95,.2)' };
  }

  function resizeCanvas() {
    const rect = stage.getBoundingClientRect();
    const previousPageSize = state.pageSize;
    const previousFirstRank = state.pageIndex * previousPageSize;
    state.pageSize = pageSizeForWidth(innerWidth);
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.floor(rect.width * ratio));
    canvas.height = Math.max(1, Math.floor(rect.height * ratio));
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    state.width = rect.width;
    state.height = rect.height;
    if (state.simulation && previousPageSize !== state.pageSize) {
      state.pageIndex = Math.floor(previousFirstRank / state.pageSize);
      buildNodes();
      return;
    }
    if (state.simulation) {
      state.nodes.forEach((node, index) => Object.assign(node, distributedHome(index, state.nodes.length, rect.width, rect.height, node.r)));
      state.simulation.force('x', d3.forceX((node) => node.homeX).strength(.016));
      state.simulation.force('y', d3.forceY((node) => node.homeY).strength(.016));
      state.simulation.alpha(.06).restart();
    }
    draw();
  }

  function currentDataset() { return state.datasets.get(state.range); }

  function filteredItems() {
    const data = currentDataset();
    if (!data) return [];
    const query = state.query.trim().toUpperCase();
    return data.items.filter((item) => {
      if (state.exchange !== 'ALL' && item.exchange !== state.exchange) return false;
      if (state.sector === 'VN30' && !item.is_vn30) return false;
      if (state.sector !== 'ALL' && state.sector !== 'VN30' && item.sector !== state.sector) return false;
      if (query && !item.symbol.includes(query) && !String(item.name || '').toUpperCase().includes(query)) return false;
      return Number(item[state.sizeMetric] || 0) > 0;
    });
  }

  function gentleDriftForce() {
    let nodes = [];
    function force() {
      const time = performance.now() / 1000;
      for (const node of nodes) {
        node.vx += Math.sin(time * .42 + node.driftSeed) * .022;
        node.vy += Math.cos(time * .36 + node.driftSeed * 1.37) * .018;
      }
    }
    force.initialize = (nextNodes) => { nodes = nextNodes || []; };
    return force;
  }

  function buildNodes({ preserveCamera = true, initialAlpha = .28 } = {}) {
    const previous = new Map(state.nodes.map((node) => [node.symbol, node]));
    state.ranked = filteredItems().sort((a, b) => {
      const difference = Number(b[state.sizeMetric] || 0) - Number(a[state.sizeMetric] || 0);
      return difference || String(a.symbol).localeCompare(String(b.symbol));
    });
    state.totalFiltered = state.ranked.length;
    const pageCount = Math.max(1, Math.ceil(state.totalFiltered / state.pageSize));
    state.pageIndex = clamp(state.pageIndex, 0, pageCount - 1);
    const pageStart = state.pageIndex * state.pageSize;
    state.filtered = state.ranked.slice(pageStart, pageStart + state.pageSize);
    const metrics = state.filtered.map((item) => Number(item[state.sizeMetric] || 0)).filter((value) => value > 0);
    const minMetric = d3.min(metrics) || 1;
    const maxMetric = d3.max(metrics) || minMetric + 1;
    const shortSide = Math.min(state.width || 800, state.height || 600);
    const minRadius = innerWidth <= 767 ? 6 : 8;
    const maxRadius = Math.max(minRadius, shortSide * .22);
    const logMin = Math.log(Math.max(1, minMetric));
    const logSpread = Math.max(.001, Math.log(Math.max(minMetric * 1.01, maxMetric)) - logMin);
    const rawRadii = metrics.map((metric) => 1 + 2.6 * clamp((Math.log(Math.max(1, metric)) - logMin) / logSpread, 0, 1));
    const rawArea = rawRadii.reduce((sum, radius) => sum + Math.PI * radius * radius, 0) || 1;
    const areaScale = Math.sqrt(((state.width || 800) * (state.height || 600) * .52) / rawArea);
    const radius = d3.scaleLog().domain([minMetric, Math.max(minMetric * 1.01, maxMetric)]).range([areaScale, areaScale * 3.6]).clamp(true);
    state.nodes = state.filtered.map((item, index) => {
      const old = previous.get(item.symbol);
      const home = distributedHome(index, state.filtered.length, state.width, state.height, clamp(radius(Math.max(minMetric, Number(item[state.sizeMetric] || 0))), minRadius, maxRadius));
      return {
        ...item,
        r: clamp(radius(Math.max(minMetric, Number(item[state.sizeMetric] || 0))), minRadius, maxRadius),
        ...home,
        driftSeed: [...String(item.symbol)].reduce((sum, char) => sum + char.charCodeAt(0), 0) * .173,
        x: old?.x ?? home.homeX,
        y: old?.y ?? home.homeY,
        vx: old?.vx ? clamp(old.vx, -2, 2) : 0, vy: old?.vy ? clamp(old.vy, -2, 2) : 0,
      };
    });
    if (!preserveCamera) state.transform = { x: 0, y: 0, k: 1 };
    if (state.simulation) state.simulation.stop();
    state.simulation = d3.forceSimulation(state.nodes)
      .alpha(initialAlpha)
      .alphaTarget(state.reducedMotion ? 0 : .012)
      .alphaMin(.01)
      .velocityDecay(.58)
      .force('charge', d3.forceManyBody().strength((node) => -Math.max(.5, node.r * .04)))
      .force('collide', d3.forceCollide().radius((node) => node.r + 2).iterations(1))
      .force('x', d3.forceX((node) => node.homeX).strength(.016))
      .force('y', d3.forceY((node) => node.homeY).strength(.016))
      .force('drift', gentleDriftForce())
      .alphaDecay(state.reducedMotion ? .18 : .10)
      .on('tick', () => {
        for (const node of state.nodes) {
          const left = node.r + 3; const right = Math.max(left, state.width - node.r - 3);
          const top = node.r + 3; const bottom = Math.max(top, state.height - node.r - 3);
          if (node.x < left) { node.x = left; node.vx = Math.abs(node.vx || 0) * .18; }
          if (node.x > right) { node.x = right; node.vx = -Math.abs(node.vx || 0) * .18; }
          if (node.y < top) { node.y = top; node.vy = Math.abs(node.vy || 0) * .18; }
          if (node.y > bottom) { node.y = bottom; node.vy = -Math.abs(node.vy || 0) * .18; }
        }
        draw();
      });
    $('bubbleEmpty').hidden = state.nodes.length > 0;
    const rankEnd = Math.min(state.totalFiltered, pageStart + state.filtered.length);
    $('bubbleRankSummary').textContent = state.totalFiltered ? `Hạng ${(pageStart + 1).toLocaleString('vi-VN')}–${rankEnd.toLocaleString('vi-VN')} theo ${metricLabel()}` : `Không có hạng theo ${metricLabel()}`;
    $('bubbleCount').textContent = `${state.nodes.length.toLocaleString('vi-VN')}/${state.totalFiltered.toLocaleString('vi-VN')} mã`;
    $('bubbleBoardTitle').textContent = `${state.sector === 'ALL' ? 'Toàn thị trường' : state.sector} · ${state.range}`;
    renderRankPages();
    draw();
  }

  function renderRankPages() {
    const select = $('bubbleRankPage');
    const pageCount = Math.max(1, Math.ceil(state.totalFiltered / state.pageSize));
    select.innerHTML = Array.from({ length: pageCount }, (_, index) => {
      const start = index * state.pageSize + 1;
      const end = Math.min(state.totalFiltered, (index + 1) * state.pageSize);
      return `<option value="${index}">${start.toLocaleString('vi-VN')}–${Math.max(start, end).toLocaleString('vi-VN')}</option>`;
    }).join('');
    select.value = String(state.pageIndex);
    select.disabled = state.totalFiltered <= state.pageSize;
  }

  function roundedText(text, maxChars) {
    return String(text || '').slice(0, Math.max(1, maxChars));
  }

  function drawNode(node) {
    const { k } = state.transform;
    const r = node.r;
    const visualRadius = r * k;
    const colors = colorFor(node);
    ctx.save();
    ctx.shadowColor = colors.glow;
    ctx.shadowBlur = Math.min(16, Math.max(3, r * .18));
    const gradient = ctx.createRadialGradient(node.x - r * .28, node.y - r * .32, r * .08, node.x, node.y, r);
    gradient.addColorStop(0, d3.color(colors.fill).brighter(.55).formatHex());
    gradient.addColorStop(.68, colors.fill);
    gradient.addColorStop(1, d3.color(colors.fill).darker(.5).formatHex());
    ctx.beginPath(); ctx.arc(node.x, node.y, r, 0, Math.PI * 2); ctx.fillStyle = gradient; ctx.fill();
    ctx.shadowBlur = 0;
    ctx.lineWidth = node === state.hovered || node === state.selected ? Math.max(2.5, 3 / k) : Math.max(1.2, 2 / k);
    ctx.strokeStyle = node === state.hovered || node === state.selected ? '#f7d77e' : colors.edge;
    ctx.stroke();

    if (visualRadius >= 12) {
      const symbolSize = clamp(r * .42, 7 / k, 24 / k);
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = `800 ${symbolSize}px Inter, sans-serif`;
      ctx.fillStyle = '#fff'; ctx.strokeStyle = 'rgba(8,18,15,.55)'; ctx.lineWidth = Math.max(1, 2 / k);
      const symbolY = node.y - (visualRadius >= 27 ? symbolSize * .2 : 0);
      const symbol = roundedText(node.symbol, 6);
      ctx.strokeText(symbol, node.x, symbolY); ctx.fillText(symbol, node.x, symbolY);
      if (visualRadius >= 27) {
        const pctSize = clamp(r * .25, 6 / k, 14 / k);
        ctx.font = `700 ${pctSize}px Inter, sans-serif`;
        const pct = signed(node.change_pct);
        ctx.strokeText(pct, node.x, node.y + symbolSize * .72); ctx.fillText(pct, node.x, node.y + symbolSize * .72);
      }
    }
    ctx.restore();
  }

  function draw() {
    if (!state.width || !state.height) return;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, state.width, state.height);
    ctx.save();
    ctx.translate(state.transform.x, state.transform.y);
    ctx.scale(state.transform.k, state.transform.k);
    state.nodes.slice().sort((a, b) => a.r - b.r).forEach(drawNode);
    ctx.restore();
  }

  function screenPoint(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  function worldPoint(point) {
    return { x: (point.x - state.transform.x) / state.transform.k, y: (point.y - state.transform.y) / state.transform.k };
  }
  function hitTest(point) {
    const world = worldPoint(point);
    let hit = null;
    for (const node of state.nodes) {
      const dx = world.x - node.x; const dy = world.y - node.y;
      if (dx * dx + dy * dy <= node.r * node.r && (!hit || node.r < hit.r)) hit = node;
    }
    return hit;
  }

  function tooltipHtml(node) {
    return `<h3>${esc(node.symbol)} <span class="${changeClass(node.change_pct)}">${esc(signed(node.change_pct))}</span></h3>
      <p>${esc(node.name)} · ${esc(node.exchange)} · ${esc(node.sector)}</p>
      <div class="bubble-tooltip-grid">
        <span>Giá gần nhất<b>${Number(node.last_price || 0).toLocaleString('vi-VN')}</b></span>
        <span>Mốc so sánh<b>${esc(node.reference_date || 'Chưa đủ dữ liệu')}</b></span>
        <span>Vốn hóa<b>${esc(compactMoney(node.market_cap))}</b></span>
        <span>Giá trị GD<b>${esc(compactMoney(node.trading_value))}</b></span>
      </div>`;
  }
  function showTooltip(node, event) {
    const tip = $('bubbleTooltip');
    if (!node) { tip.hidden = true; return; }
    tip.innerHTML = tooltipHtml(node); tip.hidden = false;
    const margin = 12; const rect = tip.getBoundingClientRect();
    tip.style.left = `${clamp(event.clientX + 14, margin, innerWidth - rect.width - margin)}px`;
    tip.style.top = `${clamp(event.clientY + 14, margin, innerHeight - rect.height - margin)}px`;
  }

  function formatPrice(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) : '--';
  }

  function calculationText(node) {
    if (node.calculation_status !== 'OK' || node.change_pct == null) return statusLabel(node.calculation_status);
    return `${formatPrice(node.last_price)} / ${formatPrice(node.reference_price)} − 1 = ${signed(node.change_pct)}`;
  }

  function openQuickView(node, trigger = canvas) {
    if (!node) return;
    state.dialogOpen = true; state.dialogTrigger = trigger; state.selected = node;
    $('bubbleTooltip').hidden = true;
    const logo = node.logo_url ? `<img class="bubble-quick-logo" id="bubbleQuickLogo" src="${esc(node.logo_url)}" alt="Logo ${esc(node.symbol)}">` : '';
    $('bubbleQuickContent').innerHTML = `<div class="bubble-quick-identity">
        <div id="bubbleQuickLogoWrap">${logo || `<span class="bubble-quick-avatar">${esc(node.symbol.slice(0, 3))}</span>`}</div>
        <div><div class="bubble-quick-symbol"><strong>${esc(node.symbol)}</strong><b class="${changeClass(node.change_pct)}">${esc(signed(node.change_pct))}</b></div>
        <p>${esc(node.name || 'Chưa có tên doanh nghiệp')}</p><div class="bubble-quick-tags"><span>${esc(node.exchange || '--')}</span><span>${esc(node.sector || 'Chưa phân ngành')}</span><span>${esc(state.range)}</span></div></div>
      </div>
      <div class="bubble-quick-grid">
        <div class="bubble-quick-metric"><span>Giá gần nhất</span><strong>${esc(formatPrice(node.last_price))}</strong><small>${esc(sourceLabel(node.current_source))}</small></div>
        <div class="bubble-quick-metric"><span>Biến động ${esc(state.range)}</span><strong class="${changeClass(node.change_pct)}">${esc(signed(node.change_pct))}</strong><small>So với mốc tham chiếu</small></div>
        <div class="bubble-quick-metric"><span>Giá tham chiếu</span><strong>${esc(formatPrice(node.reference_price))}</strong><small>${esc(node.reference_date || 'Chưa đủ dữ liệu')} · ${esc(sourceLabel(node.reference_source))}</small></div>
        <div class="bubble-quick-metric"><span>Vốn hóa</span><strong>${esc(compactMoney(node.market_cap))}</strong><small>Quy mô doanh nghiệp</small></div>
        <div class="bubble-quick-metric"><span>Giá trị giao dịch</span><strong>${esc(compactMoney(node.trading_value))}</strong><small>Snapshot phiên gần nhất</small></div>
        <div class="bubble-quick-metric"><span>Xếp hạng đang xem</span><strong>#${(state.ranked.findIndex((item) => item.symbol === node.symbol) + 1).toLocaleString('vi-VN')}</strong><small>Theo ${esc(metricLabel())}</small></div>
      </div>
      <div class="bubble-quick-audit">
        <h3>Minh chứng phép tính</h3>
        <p class="bubble-quick-formula">${esc(calculationText(node))}</p>
        <p><strong>Cơ sở:</strong> ${esc(basisLabel(node.price_basis))} · <strong>Trạng thái:</strong> ${esc(statusLabel(node.calculation_status))}</p>
        <p><strong>Giá hiện tại:</strong> ${esc(sourceLabel(node.current_source))} · ${esc(formatDateTime(node.current_observed_at))}</p>
        <p><strong>Giá mốc:</strong> ${esc(sourceLabel(node.reference_source))} · lấy ngày ${esc(node.reference_date || '--')} cho mốc yêu cầu ${esc(node.target_reference_date || '--')} · tải ${esc(formatDateTime(node.reference_fetched_at))}</p>
      </div>`;
    const image = $('bubbleQuickLogo');
    if (image) image.addEventListener('error', () => { $('bubbleQuickLogoWrap').innerHTML = `<span class="bubble-quick-avatar">${esc(node.symbol.slice(0, 3))}</span>`; }, { once: true });
    $('bubbleQuickTitle').textContent = `${node.symbol} · ${node.name || 'Chi tiết cổ phiếu'}`;
    $('bubbleQuickCta').textContent = `Mở phân tích ${node.symbol}`;
    $('bubbleQuickCta').href = `/stock/${encodeURIComponent(node.symbol)}`;
    const overlay = $('bubbleQuickView');
    if (document.fullscreenElement === $('bubbleBoard')) $('bubbleBoard').appendChild(overlay);
    overlay.classList.add('is-open'); overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('bubble-dialog-open');
    requestAnimationFrame(() => $('bubbleQuickClose').focus());
    draw();
  }

  function closeQuickView() {
    if (!state.dialogOpen) return;
    state.dialogOpen = false; state.selected = null;
    $('bubbleQuickView').classList.remove('is-open'); $('bubbleQuickView').setAttribute('aria-hidden', 'true');
    if ($('bubbleQuickView').parentElement !== document.body) document.body.appendChild($('bubbleQuickView'));
    document.body.classList.remove('bubble-dialog-open');
    const trigger = state.dialogTrigger; state.dialogTrigger = null;
    requestAnimationFrame(() => (trigger?.isConnected ? trigger : canvas).focus());
    draw();
  }

  function trapDialogFocus(event) {
    if (!state.dialogOpen || event.key !== 'Tab') return;
    const dialog = document.querySelector('.bubble-quick-dialog');
    const focusable = [...dialog.querySelectorAll('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])')];
    if (!focusable.length) return;
    const first = focusable[0]; const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function onPointerDown(event) {
    if (state.pointer && state.pointer.id !== event.pointerId) return;
    canvas.setPointerCapture(event.pointerId);
    const point = screenPoint(event);
    const node = hitTest(point); const world = worldPoint(point);
    state.pointer = { id: event.pointerId, start: point, last: point, moved: false, node, offsetX: node ? node.x - world.x : 0, offsetY: node ? node.y - world.y : 0 };
    if (node) { node.fx = node.x; node.fy = node.y; state.selected = node; state.simulation?.alphaTarget(.02).restart(); }
    stage.classList.add('dragging');
  }
  function onPointerMove(event) {
    const point = screenPoint(event);
    if (state.pointer && state.pointer.id === event.pointerId) {
      const pointer = state.pointer;
      if (Math.hypot(point.x - pointer.start.x, point.y - pointer.start.y) > 5) pointer.moved = true;
      if (pointer.node) {
        const world = worldPoint(point);
        pointer.node.fx = world.x + pointer.offsetX; pointer.node.fy = world.y + pointer.offsetY;
      } else {
        state.transform.x += point.x - pointer.last.x; state.transform.y += point.y - pointer.last.y; draw();
      }
      pointer.last = point;
      return;
    }
    state.hovered = hitTest(point);
    canvas.style.cursor = state.hovered ? 'pointer' : 'grab';
    showTooltip(state.hovered, event); draw();
  }
  function onPointerUp(event) {
    if (!state.pointer || state.pointer.id !== event.pointerId) return;
    const pointer = state.pointer;
    if (pointer.node) { pointer.node.fx = null; pointer.node.fy = null; state.simulation?.alphaTarget(state.reducedMotion ? 0 : .012); }
    const cancelled = event.type === 'pointercancel';
    if (!cancelled && pointer.node && !pointer.moved) openQuickView(pointer.node, canvas);
    else if (!state.dialogOpen) state.selected = null;
    state.pointer = null; stage.classList.remove('dragging');
  }

  function populateSectors(data) {
    const select = $('bubbleSector'); const current = state.sector;
    const sectors = [...new Set(data.items.map((item) => item.sector).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'vi'));
    select.innerHTML = '<option value="ALL">Tất cả ngành</option><option value="VN30">VN30</option>' + sectors.map((sector) => `<option value="${esc(sector)}">${esc(sector)}</option>`).join('');
    if (current === 'VN30' || sectors.includes(current)) select.value = current; else { state.sector = 'ALL'; select.value = 'ALL'; }
  }

  function renderMethodology(data) {
    const method = data.methodology || {};
    const historySources = method.history_source_priority?.join(' → ') || data.sources?.join(', ') || '--';
    const rangeDescription = data.range === '1D'
      ? 'Giá khớp gần nhất so với giá tham chiếu của cùng phiên.'
      : `Lùi ${Number(method.range_days || 0).toLocaleString('vi-VN')} ngày lịch, chọn phiên gần nhất không sau ${data.target_reference_date || '--'}; tối đa ${method.max_reference_lag_days ?? 14} ngày.`;
    $('bubbleMethodContent').innerHTML = `<article class="bubble-method-card">
        <h3>${esc(data.range)} được tính thế nào?</h3><p>${esc(rangeDescription)}</p><code>${esc(data.formula || '((last_price / reference_price) - 1) * 100')}</code>
      </article>
      <article class="bubble-method-card">
        <h3>Cơ sở và nguồn giá</h3><p>${esc(method.price_basis_label || basisLabel(data.price_basis))}</p><p>Giá hiện tại: ${esc(sourceLabel(method.current_source))}</p><p>Lịch sử: ${esc(historySources)}</p>
      </article>
      <article class="bubble-method-card">
        <h3>Nguyên tắc chất lượng</h3><p>Không nội suy, không tạo số thay thế. Thiếu dữ liệu, mốc quá cũ hoặc không qua kiểm tra chéo sẽ trả <code>null</code> và hiển thị màu xám.</p><p>Cache cũ chưa xác định cơ sở giá bị bỏ qua.</p>
      </article>`;
  }

  function updateMeta(data) {
    const coverage = data.coverage || {};
    $('bubbleCoverage').textContent = `Độ phủ ${Number(coverage.pct || 0).toLocaleString('vi-VN')}%`;
    $('bubbleAsOf').textContent = `Dữ liệu ${data.as_of || '--'}`;
    const session = data.market_session || {};
    const live = Boolean(session.is_live_matching);
    const updatedAt = formatDateTime(data.methodology?.current_observed_at || data.generated_at || new Date().toISOString());
    $('bubbleSession').innerHTML = `<span class="bubble-live-dot ${live ? 'live' : ''}"></span><strong>${esc(live ? 'Đang giao dịch' : 'Dữ liệu gần nhất')}</strong><small>Phiên ${esc(data.as_of || '--')} · ${esc(updatedAt)}</small>`;
    $('bubbleEvidenceCurrent').textContent = sourceLabel(data.methodology?.current_source);
    $('bubbleEvidenceHistory').textContent = data.range === '1D'
      ? 'Cùng bảng giá phiên hiện tại'
      : `${data.methodology?.history_source_priority?.join(' → ') || data.sources?.join(', ') || '--'} · ${basisLabel(data.price_basis)}`;
    $('bubbleEvidenceFetched').textContent = updatedAt;
    renderMethodology(data);
    const missing = Number(coverage.missing || 0);
    const status = $('bubbleStatus');
    status.className = `bubble-status ${missing ? 'warning' : ''}`;
    status.textContent = missing
      ? `${missing.toLocaleString('vi-VN')} mã chưa đủ lịch sử cho ${data.range}. Hệ thống đang đồng bộ nền; các mã này được hiển thị màu xám.${live ? ' Dữ liệu giá vẫn tự động cập nhật mỗi 5 giây.' : ''}`
      : `Đã tải đủ ${Number(coverage.available || 0).toLocaleString('vi-VN')} mã · nguồn ${data.sources?.join(', ') || 'bảng giá thị trường'}${live ? ' · tự động cập nhật mỗi 5 giây.' : '.'}`;
  }

  function applyRealtimePayload(payload) {
    const freshBySymbol = new Map(payload.items.map((item) => [item.symbol, item]));
    state.nodes.forEach((node) => {
      const fresh = freshBySymbol.get(node.symbol);
      if (!fresh) return;
      const { x, y, vx, vy, r, homeX, homeY, driftSeed, index } = node;
      Object.assign(node, fresh, { x, y, vx, vy, r, homeX, homeY, driftSeed, index });
    });
    state.ranked = state.ranked.map((item) => freshBySymbol.get(item.symbol) || item);
    state.filtered = state.filtered.map((item) => freshBySymbol.get(item.symbol) || item);
    updateMeta(payload);
    draw();
  }

  function armMarketRefresh() {
    clearTimeout(state.marketRefreshTimer);
    const live = Boolean(currentDataset()?.market_session?.is_live_matching);
    state.marketRefreshTimer = setTimeout(async () => {
      if (document.hidden) { armMarketRefresh(); return; }
      await loadRange(state.range, { force: true, silent: true, realtime: true });
    }, live ? 5000 : 60000);
  }

  async function loadRange(rangeKey, { force = false, silent = false, realtime = false } = {}) {
    if (realtime && state.liveRefreshInFlight) return;
    if (realtime) state.liveRefreshInFlight = true;
    state.range = rangeKey;
    document.querySelectorAll('[data-range]').forEach((button) => {
      const active = button.dataset.range === rangeKey;
      button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    });
    if (!force && state.datasets.has(rangeKey)) {
      const data = state.datasets.get(rangeKey); populateSectors(data); updateMeta(data); buildNodes(); armMarketRefresh(); return;
    }
    if (!silent) $('bubbleLoading').hidden = false;
    clearTimeout(state.retryTimer);
    try {
      const response = await fetch(`/api/market-bubbles/data?range=${encodeURIComponent(rangeKey)}`, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      state.datasets.set(rangeKey, payload);
      if (state.range !== rangeKey) return;
      populateSectors(payload);
      if (realtime && state.nodes.length) applyRealtimePayload(payload);
      else { updateMeta(payload); buildNodes({ initialAlpha: .28 }); }
      if (payload.refreshing && Number(payload.coverage?.missing || 0) > 0 && rangeKey !== '1D') {
        state.retryTimer = setTimeout(() => { if (state.range === rangeKey) loadRange(rangeKey, { force: true, silent: true }); }, 8000);
      }
    } catch (error) {
      $('bubbleStatus').className = 'bubble-status warning';
      $('bubbleStatus').textContent = `Không thể tải dữ liệu bong bóng: ${error.message}`;
      if (!state.nodes.length) $('bubbleEmpty').hidden = false;
    } finally {
      if (realtime) state.liveRefreshInFlight = false;
      if (!silent) $('bubbleLoading').hidden = true;
      armMarketRefresh();
    }
  }

  function resetFilters() {
    state.sizeMetric = 'market_cap'; state.exchange = 'ALL'; state.sector = 'ALL'; state.query = '';
    state.pageIndex = 0;
    $('bubbleSizeMetric').value = 'market_cap'; $('bubbleExchange').value = 'ALL'; $('bubbleSector').value = 'ALL'; $('bubbleSearch').value = '';
    state.transform = { x: 0, y: 0, k: 1 }; buildNodes({ preserveCamera: false });
  }
  async function toggleFullscreen(forceClose = false) {
    const board = $('bubbleBoard');
    try {
      if (!forceClose && !document.fullscreenElement && board.requestFullscreen) await board.requestFullscreen();
      else if (document.fullscreenElement && document.exitFullscreen) await document.exitFullscreen();
      else board.classList.toggle('is-fullscreen', !forceClose && !board.classList.contains('is-fullscreen'));
    } catch { board.classList.toggle('is-fullscreen', !forceClose && !board.classList.contains('is-fullscreen')); }
    const open = document.fullscreenElement === board || board.classList.contains('is-fullscreen');
    document.body.classList.toggle('bubble-fullscreen-open', open);
    $('bubbleFullscreen').setAttribute('aria-pressed', String(open));
    requestAnimationFrame(resizeCanvas);
  }

  function bindEvents() {
    document.querySelectorAll('[data-range]').forEach((button) => button.addEventListener('click', () => loadRange(button.dataset.range)));
    $('bubbleSizeMetric').addEventListener('change', (event) => { state.sizeMetric = event.target.value; state.pageIndex = 0; buildNodes({ preserveCamera: false }); });
    $('bubbleExchange').addEventListener('change', (event) => { state.exchange = event.target.value; state.pageIndex = 0; buildNodes({ preserveCamera: false }); });
    $('bubbleSector').addEventListener('change', (event) => { state.sector = event.target.value; state.pageIndex = 0; buildNodes({ preserveCamera: false }); });
    $('bubbleRankPage').addEventListener('change', (event) => { state.pageIndex = Number(event.target.value) || 0; buildNodes({ preserveCamera: false }); });
    let searchTimer = 0;
    $('bubbleSearch').addEventListener('input', (event) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.query = event.target.value; state.pageIndex = 0; buildNodes({ preserveCamera: false }); }, 120); });
    $('bubbleReset').addEventListener('click', resetFilters);
    $('bubbleFullscreen').addEventListener('click', () => toggleFullscreen());
    $('bubbleCloseFullscreen').addEventListener('click', () => toggleFullscreen(true));
    $('bubbleQuickClose').addEventListener('click', closeQuickView);
    $('bubbleQuickBackdrop').addEventListener('click', closeQuickView);
    $('bubbleFilterToggle').addEventListener('click', () => {
      const controls = $('bubbleControls'); const open = controls.classList.toggle('is-mobile-open');
      $('bubbleFilterToggle').setAttribute('aria-expanded', String(open));
    });
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointercancel', onPointerUp);
    canvas.addEventListener('pointerleave', () => { if (!state.pointer) { state.hovered = null; $('bubbleTooltip').hidden = true; draw(); } });
    canvas.addEventListener('keydown', (event) => {
      if ((event.key === 'Enter' || event.key === ' ') && state.hovered) { event.preventDefault(); openQuickView(state.hovered, canvas); }
    });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) armMarketRefresh(); });
    document.addEventListener('fullscreenchange', () => {
      const open = document.fullscreenElement === $('bubbleBoard');
      if (!open && $('bubbleQuickView').parentElement !== document.body) document.body.appendChild($('bubbleQuickView'));
      document.body.classList.toggle('bubble-fullscreen-open', open);
      $('bubbleFullscreen').setAttribute('aria-pressed', String(open));
      requestAnimationFrame(resizeCanvas);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && state.dialogOpen) { event.preventDefault(); closeQuickView(); return; }
      trapDialogFocus(event);
      if (event.key === 'Escape' && $('bubbleBoard').classList.contains('is-fullscreen')) toggleFullscreen(true);
    });
    new ResizeObserver(() => { cancelAnimationFrame(state.resizeFrame); state.resizeFrame = requestAnimationFrame(resizeCanvas); }).observe(stage);
  }

  window.__LP_BUBBLES_TEST__ = {
    pageSizeForWidth,
    snapshot: () => ({
      alpha: state.simulation?.alpha() || 0,
      pageIndex: state.pageIndex,
      pageSize: state.pageSize,
      nodes: state.nodes.map((node) => ({ symbol: node.symbol, x: node.x, y: node.y, vx: node.vx || 0, vy: node.vy || 0, r: node.r })),
      transform: { ...state.transform },
    }),
  };
  bindEvents();
  resizeCanvas();
  loadRange('1D');
})();
