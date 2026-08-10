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
  const signed = (value) => value == null ? '!' : `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
  const isVerified = (node) => node?.data_confidence === 'VERIFIED' && node?.change_pct != null;
  const displayedChange = (node) => isVerified(node) ? signed(node.change_pct) : '!';
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
    SOURCE_QUALITY_FAILED: 'Không đạt đối soát với bảng giá', SOURCE_DISAGREEMENT: 'Vietcap và KBS không đồng nhất',
    INVALID_OHLC: 'Dữ liệu OHLC không hợp lệ', SINGLE_SOURCE: 'Chỉ có một nguồn lịch sử',
    RECENT_CLOSE_MISMATCH: 'Close gần nhất không khớp bảng giá', RECENT_CLOSE_UNVERIFIED: 'Chưa đối soát close gần nhất',
    MISSING_RECENT_CLOSE: 'Thiếu close gần nhất', HISTORY_CACHE_STALE: 'Cache OHLC chưa đồng bộ',
    CORPORATE_ACTION_AUDIT_PENDING: 'Đang kiểm tra sự kiện doanh nghiệp',
    CORPORATE_ACTION_SOURCE_ERROR: 'Nguồn sự kiện doanh nghiệp không khả dụng',
    CORPORATE_ACTION_UNVERIFIED: 'Có sự kiện doanh nghiệp chưa xác minh',
    INVALID_SESSION_REFERENCE: 'Thiếu giá tham chiếu cùng snapshot',
    SESSION_NOT_STARTED: 'Phiên mới chưa bắt đầu',
  }[status] || status || 'Chưa xác minh');
  const sessionLabel = (phase) => ({
    PRE_OPEN: 'Chờ mở cửa', ATO: 'Phiên ATO', CONTINUOUS: 'Khớp lệnh liên tục',
    MORNING: 'Phiên sáng', LUNCH_BREAK: 'Nghỉ trưa', AFTERNOON: 'Phiên chiều',
    ATC: 'Phiên ATC', POST_CLOSE_TRADING: 'Giao dịch sau giờ',
    CLOSED: 'Thị trường đóng cửa', WEEKEND: 'Nghỉ cuối tuần', HOLIDAY: 'Nghỉ lễ',
  }[phase] || 'Trạng thái chưa xác định');
  const basisLabel = (basis) => basis === 'SESSION_REFERENCE'
    ? 'Giá khớp / tham chiếu cùng phiên'
    : basis === 'SOURCE_REPORTED_OHLC' ? 'OHLC do nguồn công bố · giá mở cửa phiên mốc' : 'Chưa xác định';
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
  const itemSectorNames = (item) => {
    const names = (item.sector_memberships || []).map((membership) => (
      typeof membership === 'string' ? membership : membership?.sector || membership?.name
    )).filter(Boolean);
    if (!names.length && item.sector) names.push(item.sector);
    return [...new Set(names)];
  };
  const normalizedGroupKey = (value) => {
    if (!value || value === 'ALL') return 'ALL';
    if (value === 'VN30') return 'INDEX:VN30';
    if (value.startsWith('INDEX:') || value.startsWith('SECTOR:')) return value;
    return `SECTOR:${value}`;
  };
  const itemMatchesGroup = (item, groupKey) => {
    const key = normalizedGroupKey(groupKey);
    if (key === 'ALL') return true;
    if (key === 'INDEX:VN30') return Boolean(item.is_vn30 || (item.index_memberships || []).includes('VN30'));
    if (key.startsWith('SECTOR:')) return itemSectorNames(item).includes(key.slice('SECTOR:'.length));
    return false;
  };
  const fallbackFilterGroups = (data) => {
    const items = data.items || [];
    const sectors = new Map();
    items.forEach((item) => itemSectorNames(item).forEach((name) => {
      const counts = sectors.get(name) || { total_count: 0, active_count: 0 };
      counts.total_count += 1; counts.active_count += Number(Boolean(item.is_active)); sectors.set(name, counts);
    }));
    const vn30Items = items.filter((item) => itemMatchesGroup(item, 'INDEX:VN30'));
    return [{
      key: 'ALL', type: 'all', label: 'Tất cả ngành / chỉ số', total_count: items.length,
      active_count: items.filter((item) => item.is_active).length, enabled: items.length > 0,
    }, {
      key: 'INDEX:VN30', type: 'index', label: 'VN30', total_count: vn30Items.length,
      active_count: vn30Items.filter((item) => item.is_active).length, enabled: vn30Items.length > 0,
      stale: Boolean(data.indices?.VN30?.stale),
    }, ...[...sectors.entries()].map(([name, counts]) => ({
      key: `SECTOR:${name}`, type: 'sector', label: name, ...counts, enabled: true,
    }))];
  };
  const filterGroups = (data = currentDataset()) => Array.isArray(data?.filter_groups)
    ? data.filter_groups : fallbackFilterGroups(data || { items: [] });
  const groupLabel = (key, data = currentDataset()) => (
    filterGroups(data).find((group) => group.key === normalizedGroupKey(key))?.label
    || (normalizedGroupKey(key).startsWith('SECTOR:') ? normalizedGroupKey(key).slice(7) : 'Toàn thị trường')
  );
  const distributedHome = (index, total, width, height, radius = 0) => {
    const padding = Math.max(12, radius + 4);
    const usableWidth = Math.max(1, width - padding * 2);
    const usableHeight = Math.max(1, height - padding * 2);
    const xRatio = (index * .61803398875 + .11) % 1;
    const yRatio = (index * .75487766625 + .37) % 1;
    return { homeX: padding + xRatio * usableWidth, homeY: padding + yRatio * usableHeight };
  };

  function colorFor(node) {
    if (!isVerified(node)) return { fill: '#737d7a', edge: '#d3a13b', glow: 'rgba(211,161,59,.28)' };
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
      if (!itemMatchesGroup(item, state.sector)) return false;
      if (query && !item.symbol.includes(query) && !String(item.name || '').toUpperCase().includes(query)) return false;
      return true;
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
    const metricValues = state.filtered.map((item) => Math.max(0, Number(item[state.sizeMetric] || 0)));
    const metrics = metricValues.filter((value) => value > 0);
    const minMetric = d3.min(metrics) || 1;
    const maxMetric = d3.max(metrics) || minMetric;
    const shortSide = Math.min(state.width || 800, state.height || 600);
    const minRadius = innerWidth <= 767 ? 6 : 8;
    const maxRadius = Math.max(minRadius, shortSide * .22);
    const logMin = Math.log(Math.max(1, minMetric));
    const logSpread = Math.max(.001, Math.log(Math.max(minMetric * 1.01, maxMetric)) - logMin);
    const rawRadii = metricValues.map((metric) => metric > 0
      ? 1 + 2.6 * clamp((Math.log(Math.max(1, metric)) - logMin) / logSpread, 0, 1)
      : .65);
    const rawArea = rawRadii.reduce((sum, radius) => sum + Math.PI * radius * radius, 0) || 1;
    const areaScale = Math.sqrt(((state.width || 800) * (state.height || 600) * .52) / rawArea);
    const radius = d3.scaleLog().domain([minMetric, Math.max(minMetric * 1.01, maxMetric)]).range([areaScale, areaScale * 3.6]).clamp(true);
    const equalRadius = clamp(Math.sqrt(
      ((state.width || 800) * (state.height || 600) * .52)
      / (Math.max(1, state.filtered.length) * Math.PI)
    ), minRadius, maxRadius);
    const radiusFor = (value) => {
      if (!metrics.length) return equalRadius;
      if (value <= 0) return minRadius;
      return clamp(radius(value), minRadius, maxRadius);
    };
    state.nodes = state.filtered.map((item, index) => {
      const old = previous.get(item.symbol);
      const itemRadius = radiusFor(Math.max(0, Number(item[state.sizeMetric] || 0)));
      const home = distributedHome(index, state.filtered.length, state.width, state.height, itemRadius);
      return {
        ...item,
        r: itemRadius,
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
    const empty = $('bubbleEmpty');
    empty.hidden = state.nodes.length > 0;
    if (!state.nodes.length) {
      const details = [state.exchange !== 'ALL' ? `sàn ${state.exchange}` : '', state.query ? `từ khóa “${state.query.trim()}”` : ''].filter(Boolean);
      empty.textContent = `Không có mã thuộc ${groupLabel(state.sector)}${details.length ? ` phù hợp với ${details.join(' và ')}` : ''}.`;
    }
    const rankEnd = Math.min(state.totalFiltered, pageStart + state.filtered.length);
    const equalSize = state.totalFiltered > 0 && state.ranked.every((item) => Number(item[state.sizeMetric] || 0) <= 0);
    $('bubbleRankSummary').textContent = equalSize
      ? `Chưa phát sinh ${metricLabel().toLocaleLowerCase('vi-VN')} · kích thước chia đều`
      : state.totalFiltered ? `Hạng ${(pageStart + 1).toLocaleString('vi-VN')}–${rankEnd.toLocaleString('vi-VN')} theo ${metricLabel()}` : `Không có hạng theo ${metricLabel()}`;
    $('bubbleCount').textContent = `${state.nodes.length.toLocaleString('vi-VN')}/${state.totalFiltered.toLocaleString('vi-VN')} mã`;
    $('bubbleBoardTitle').textContent = `${groupLabel(state.sector)} · ${state.range}`;
    renderRankPages();
    draw();
  }

  function renderRankPages() {
    const select = $('bubbleRankPage');
    if (!state.totalFiltered) {
      select.innerHTML = '<option value="0">Không có mã</option>';
      select.value = '0'; select.disabled = true;
      return;
    }
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
    ctx.lineWidth = node === state.hovered || node === state.selected
      ? Math.max(2.5, 3 / k)
      : (!isVerified(node) ? Math.max(2, 2.6 / k) : Math.max(1.2, 2 / k));
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
        const pct = displayedChange(node);
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
    const warning = !isVerified(node)
      ? `<div class="bubble-tooltip-warning"><strong>!</strong><span>${esc(node.reason_message || statusLabel(node.reason_code || node.calculation_status))}</span></div>`
      : '';
    return `<h3>${esc(node.symbol)} <span class="${isVerified(node) ? changeClass(node.change_pct) : 'warning'}">${esc(displayedChange(node))}</span></h3>
      <p>${esc(node.name)} · ${esc(node.exchange)} · ${esc(node.sector)}</p>
      ${warning}
      <div class="bubble-tooltip-grid">
        <span>Giá gần nhất<b>${Number(node.last_price || 0).toLocaleString('vi-VN')}</b></span>
        <span>Mốc so sánh<b>${esc(node.reference_date || 'Chưa đủ dữ liệu')}</b></span>
        <span>Vốn hóa<b>${esc(compactMoney(node.market_cap))}</b></span>
        <span>Giá trị GD<b>${esc(compactMoney(node.trading_value))}</b></span>
      </div>`;
  }
  function showTooltip(node, event) {
    const tip = $('bubbleTooltip');
    if (!node) { tip.hidden = true; canvas.setAttribute('aria-label', 'Bong bóng thị trường. Di chuyển con trỏ lên mã để xem chi tiết.'); return; }
    tip.innerHTML = tooltipHtml(node); tip.hidden = false;
    canvas.setAttribute('aria-label', `${node.symbol}, ${isVerified(node) ? displayedChange(node) : `không xác minh được: ${node.reason_message || statusLabel(node.reason_code)}`}`);
    const margin = 12; const rect = tip.getBoundingClientRect();
    tip.style.left = `${clamp(event.clientX + 14, margin, innerWidth - rect.width - margin)}px`;
    tip.style.top = `${clamp(event.clientY + 14, margin, innerHeight - rect.height - margin)}px`;
  }

  function formatPrice(value) {
    if (value == null || value === '') return 'Không có';
    const number = Number(value);
    return Number.isFinite(number) ? number.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) : 'Không có';
  }

  function calculationText(node) {
    if (!isVerified(node)) return node.reason_message || statusLabel(node.reason_code || node.calculation_status);
    if (node.session_reset) return 'Phiên mới chưa bắt đầu; biến động, khối lượng và giá trị giao dịch được đặt về 0.';
    if (node.reference_price_field === 'open') {
      return `(${formatPrice(node.last_price)} − ${formatPrice(node.anchor_open)}) / |${formatPrice(node.anchor_open)}| = ${signed(node.change_pct)}`;
    }
    return `${formatPrice(node.last_price)} / ${formatPrice(node.reference_price)} − 1 = ${signed(node.change_pct)}`;
  }

  function openQuickView(node, trigger = canvas) {
    if (!node) return;
    state.dialogOpen = true; state.dialogTrigger = trigger; state.selected = node;
    $('bubbleTooltip').hidden = true;
    const verified = isVerified(node);
    const checkedSources = (node.sources_checked || []).join(' + ') || 'Chưa đủ hai nguồn';
    const actionList = (node.corporate_actions_detected || []).map((event) =>
      `<li>${esc(event.event_date || '--')} · ${esc(event.title || event.type || 'Sự kiện doanh nghiệp')}</li>`
    ).join('');
    const auditPanel = verified
      ? `<div class="bubble-quick-audit">
          <h3>Minh chứng phép tính</h3>
          <p class="bubble-quick-formula">${esc(calculationText(node))}</p>
          <p><strong>Cơ sở:</strong> ${esc(basisLabel(node.price_basis))} · <strong>Trạng thái:</strong> VERIFIED</p>
          <p><strong>Giá hiện tại:</strong> ${esc(sourceLabel(node.current_source))} · ${esc(formatDateTime(node.current_observed_at))}</p>
          <p><strong>Nguồn lịch sử:</strong> ${esc(checkedSources)} · phiên ${esc(node.reference_date || '--')} cho mốc ${esc(node.target_reference_date || '--')}</p>
          <p><strong>Sai lệch nguồn lớn nhất:</strong> ${node.source_agreement_pct == null ? '--' : `${esc(Number(node.source_agreement_pct).toFixed(4))}%`} · <strong>Đối soát close:</strong> ${esc(node.reconciliation_status || '--')}</p>
        </div>`
      : `<div class="bubble-quick-audit bubble-quick-warning" role="alert">
          <h3><span aria-hidden="true">!</span> Không xác minh được biến động</h3>
          <p class="bubble-quick-warning-message">${esc(node.reason_message || statusLabel(node.reason_code || node.calculation_status))}</p>
          <p><strong>Mã nguyên nhân:</strong> ${esc(node.reason_code || node.calculation_status || 'UNVERIFIED')}</p>
          <p><strong>Nguồn đã thử:</strong> ${esc(checkedSources)}</p>
          <p><strong>Mốc yêu cầu:</strong> ${esc(node.target_reference_date || '--')} · <strong>Phiên tìm thấy:</strong> ${esc(node.reference_date || 'Không có')}</p>
          <p><strong>Kiểm tra lúc:</strong> ${esc(formatDateTime(node.quality_checked_at))} · ${node.retryable ? 'Hệ thống sẽ tự thử lại.' : 'Cần xác minh cơ sở điều chỉnh trước khi công bố số.'}</p>
          ${actionList ? `<p><strong>Sự kiện phát hiện:</strong></p><ul>${actionList}</ul>` : ''}
        </div>`;
    const logo = node.logo_url ? `<img class="bubble-quick-logo" id="bubbleQuickLogo" src="${esc(node.logo_url)}" alt="Logo ${esc(node.symbol)}">` : '';
    $('bubbleQuickContent').innerHTML = `<div class="bubble-quick-identity">
        <div id="bubbleQuickLogoWrap">${logo || `<span class="bubble-quick-avatar">${esc(node.symbol.slice(0, 3))}</span>`}</div>
        <div><div class="bubble-quick-symbol"><strong>${esc(node.symbol)}</strong><b class="${verified ? changeClass(node.change_pct) : 'warning'}">${esc(displayedChange(node))}</b></div>
        <p>${esc(node.name || 'Chưa có tên doanh nghiệp')}</p><div class="bubble-quick-tags"><span>${esc(node.exchange || '--')}</span><span>${esc(node.sector || 'Chưa phân ngành')}</span><span>${esc(state.range)}</span></div></div>
      </div>
      <div class="bubble-quick-grid">
        <div class="bubble-quick-metric"><span>Giá gần nhất</span><strong>${esc(formatPrice(node.last_price))}</strong><small>${esc(sourceLabel(node.current_source))}</small></div>
        <div class="bubble-quick-metric"><span>Biến động ${esc(state.range)}</span><strong class="${verified ? changeClass(node.change_pct) : 'warning'}">${esc(displayedChange(node))}</strong><small>${verified ? (node.reference_price_field === 'open' ? 'So với giá mở cửa phiên mốc' : 'So với tham chiếu cùng phiên') : 'Không công bố số chưa xác minh'}</small></div>
        <div class="bubble-quick-metric"><span>${node.reference_price_field === 'open' ? 'Giá mở cửa mốc' : 'Giá tham chiếu'}</span><strong>${esc(formatPrice(node.reference_price))}</strong><small>${esc(node.reference_date || 'Chưa đủ dữ liệu')} · ${esc(sourceLabel(node.reference_source))}</small></div>
        <div class="bubble-quick-metric"><span>Vốn hóa</span><strong>${esc(compactMoney(node.market_cap))}</strong><small>Quy mô doanh nghiệp</small></div>
        <div class="bubble-quick-metric"><span>Giá trị giao dịch</span><strong>${esc(compactMoney(node.trading_value))}</strong><small>Snapshot phiên gần nhất</small></div>
        <div class="bubble-quick-metric"><span>Xếp hạng đang xem</span><strong>#${(state.ranked.findIndex((item) => item.symbol === node.symbol) + 1).toLocaleString('vi-VN')}</strong><small>Theo ${esc(metricLabel())}</small></div>
      </div>
      ${auditPanel}`;
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
    const groups = filterGroups(data);
    const all = groups.find((group) => group.type === 'all') || { key: 'ALL', label: 'Tất cả ngành / chỉ số', total_count: data.items.length, enabled: true };
    const indices = groups.filter((group) => group.type === 'index');
    const sectors = groups.filter((group) => group.type === 'sector').sort((a, b) => a.label.localeCompare(b.label, 'vi'));
    const option = (group) => `<option value="${esc(group.key)}"${group.enabled === false ? ' disabled' : ''}>${esc(group.label)} (${Number(group.total_count || 0).toLocaleString('vi-VN')})${group.stale ? ' · cache' : ''}</option>`;
    select.innerHTML = option(all)
      + (indices.length ? `<optgroup label="Chỉ số">${indices.map(option).join('')}</optgroup>` : '')
      + (sectors.length ? `<optgroup label="Ngành">${sectors.map(option).join('')}</optgroup>` : '');
    const normalizedCurrent = normalizedGroupKey(current);
    const selected = groups.find((group) => group.key === normalizedCurrent && group.enabled !== false);
    state.sector = selected ? normalizedCurrent : 'ALL';
    select.value = state.sector;
  }

  function renderMethodology(data) {
    const method = data.methodology || {};
    const historySources = method.history_source_priority?.join(' → ') || data.sources?.join(', ') || '--';
    const rangeDescription = data.range === '1D'
      ? 'Giá khớp gần nhất so với giá tham chiếu của cùng phiên.'
      : `Lùi ${Number(method.range_days || 0).toLocaleString('vi-VN')} ngày lịch, chọn phiên gần nhất không sau ${data.target_reference_date || '--'} và dùng giá mở cửa của phiên đó; tối đa ${method.max_reference_lag_days ?? 14} ngày.`;
    $('bubbleMethodContent').innerHTML = `<article class="bubble-method-card">
        <h3>${esc(data.range)} được tính thế nào?</h3><p>${esc(rangeDescription)}</p><code>${esc(data.formula || '((current_price - anchor_open) / abs(anchor_open)) * 100')}</code>${data.range === '1D' ? '' : '<p>Cùng định nghĩa Performance của TradingView Screener; dữ liệu gốc vẫn lấy từ Vietcap/KBS, không lấy từ TradingView.</p>'}
      </article>
      <article class="bubble-method-card">
        <h3>Cơ sở và nguồn giá</h3><p>${esc(method.price_basis_label || basisLabel(data.price_basis))}</p><p>Giá hiện tại: ${esc(sourceLabel(method.current_source))}</p><p>Lịch sử: ${esc(historySources)}</p>
      </article>
      <article class="bubble-method-card">
        <h3>Nguyên tắc chất lượng</h3><p>Chỉ hiện % khi Vietcap và KBS cùng có Open/Close, lệch không quá ${esc(method.source_comparison_tolerance_pct ?? .5)}% hoặc ${esc(method.source_comparison_tolerance_vnd ?? 100)} đồng, close gần nhất khớp bảng giá và không có sự kiện doanh nghiệp chưa xác minh.</p><p>Mọi trường hợp khác trả <code>null</code> và hiện dấu <code>!</code>; không nội suy, không dựng số thay thế.</p>
      </article>`;
  }

  function updateMeta(data) {
    const coverage = data.coverage || {};
    $('bubbleCoverage').textContent = `Độ phủ ${Number(coverage.pct || 0).toLocaleString('vi-VN')}%`;
    $('bubbleAsOf').textContent = `Dữ liệu ${data.as_of || '--'}`;
    const session = data.market_session || {};
    const live = Boolean(session.is_live_matching);
    const phase = session.phase;
    const waiting = phase === 'PRE_OPEN';
    const closed = Boolean(data.market_closed || ['CLOSED', 'WEEKEND', 'HOLIDAY'].includes(phase));
    const updatedAt = formatDateTime(data.methodology?.current_observed_at || data.generated_at || new Date().toISOString());
    const storageLabel = data.snapshot_frozen ? 'snapshot DB cuối phiên' : 'dữ liệu thị trường';
    const sessionDate = data.session_date || data.as_of || '--';
    const dotClass = live ? 'live' : (waiting || closed ? 'closed' : 'paused');
    $('bubbleSession').innerHTML = `<span class="bubble-live-dot ${dotClass}"></span><strong>${esc(sessionLabel(phase))}</strong><small>Phiên ${esc(sessionDate)} · ${esc(storageLabel)} · ${esc(updatedAt)}</small><em>${esc(session.detail_label || '')}</em>`;
    $('bubbleEvidenceCurrent').textContent = sourceLabel(data.methodology?.current_source);
    $('bubbleEvidenceHistory').textContent = data.range === '1D'
      ? 'Cùng bảng giá phiên hiện tại'
      : `${data.methodology?.history_source_priority?.join(' → ') || data.sources?.join(', ') || '--'} · ${basisLabel(data.price_basis)}`;
    $('bubbleEvidenceFetched').textContent = updatedAt;
    renderMethodology(data);
    const missing = Number(coverage.missing || 0);
    const unverified = Number(coverage.unverified || 0);
    const unavailable = Number(coverage.unavailable || 0);
    const vn30Group = filterGroups(data).find((group) => group.key === 'INDEX:VN30');
    const vn30Unavailable = vn30Group && vn30Group.enabled === false;
    const status = $('bubbleStatus');
    status.className = `bubble-status ${missing || vn30Unavailable ? 'warning' : ''}`;
    status.textContent = vn30Unavailable
      ? 'Danh sách VN30 hiện chưa có dữ liệu hợp lệ; lựa chọn VN30 đã được tạm khóa để tránh hiển thị biểu đồ rỗng.'
      : data.session_reset_applied
      ? `Phiên mới chưa bắt đầu: toàn bộ biến động 1D, khối lượng và giá trị giao dịch đang ở 0; bong bóng vẫn giữ theo danh sách niêm yết.`
      : missing
      ? `${missing.toLocaleString('vi-VN')} cảnh báo: ${unverified.toLocaleString('vi-VN')} chưa xác minh, ${unavailable.toLocaleString('vi-VN')} không có dữ liệu. Các bong bóng này hiện dấu ! và không công bố %.${live ? ' Giá hiện tại vẫn cập nhật mỗi 5 giây.' : ''}`
      : `Đã xác minh đủ ${Number(coverage.verified || coverage.available || 0).toLocaleString('vi-VN')} mã · ${live ? 'giá hiện tại cập nhật mỗi 5 giây.' : 'không có cảnh báo chất lượng.'}`;
  }

  function applyRealtimePayload(payload, _previousPayload) {
    populateSectors(payload);
    updateMeta(payload);
    buildNodes({ preserveCamera: true, initialAlpha: .16 });
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
      const previousPayload = state.datasets.get(rangeKey);
      state.datasets.set(rangeKey, payload);
      if (state.range !== rangeKey) return;
      if (realtime && state.nodes.length) applyRealtimePayload(payload, previousPayload);
      else { populateSectors(payload); updateMeta(payload); buildNodes({ initialAlpha: .28 }); }
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
    itemSectorNames,
    itemMatchesGroup,
    fallbackFilterGroups,
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
