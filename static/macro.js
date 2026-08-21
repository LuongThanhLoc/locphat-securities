(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const PREF_KEY = 'lps_macro_filters_v2';
  const els = {
    ticker: $('tickerBarInner'), tickerStatus: $('tickerStatusText'), tickerDot: $('tickerStatusDot'),
    tickerUpdated: $('tickerUpdatedAt'), tabs: $('dateTabs'), custom: $('customRangeSection'),
    start: $('customStartDate'), end: $('customEndDate'), apply: $('applyCustomRangeBtn'),
    categories: $('categoryFilters'), importance: $('importanceFilter'), search: $('macroSearchInput'),
    clear: $('clearMacroFiltersBtn'), coverage: $('macroCoverageStatus'), timeline: $('macroTimelineContent'),
    export: $('exportIcsBtn'), refresh: $('refreshMacroBtn'),
    today: $('kpiTodayCount'), week: $('kpiWeekCount'), high: $('kpiHighImpactCount'),
    nextTime: $('kpiNextEventDistance'), nextTitle: $('kpiNextEventTitle'),
    lastWeek: $('badgeLastWeek'), yesterday: $('badgeYesterday'), todayBadge: $('badgeToday'),
    tomorrow: $('badgeTomorrow'), thisWeek: $('badgeThisWeek'),
    dialog: $('macroDetailDialog'), backdrop: $('dialogBackdrop'), close: $('dialogCloseBtn'),
    closeBottom: $('dialogCloseBottomBtn'), flag: $('dialogFlag'), title: $('dialogTitle'),
    subtitle: $('dialogSubtitle'), actual: $('dialogActualVal'), forecast: $('dialogVerificationVal'),
    previous: $('dialogPreviousVal'), overview: $('dialogOverviewText'), macroImpact: $('dialogMacroImpactText'),
    vnImpact: $('dialogVnMarketText'), sourceText: $('dialogSourceText'), sourceLink: $('dialogSourceLink'),
    historySection: $('dialogHistorySection'), historyChart: $('dialogHistoryChart'), historyTable: $('dialogHistoryTable'),
  };

  const state = {
    range: 'this_week', importance: 0, category: 'all', search: '', customStart: '', customEnd: '',
    events: [], counts: {}, today: new Date().toISOString().slice(0, 10), currentTime: '00:00',
    activeTrigger: null, controller: null, requestId: 0, detailController: null,
    tickerController: null, tickerTimer: 0, tickerRequestId: 0, tickerSignature: '',
    tickerNodes: new Map(), tickerPayload: null,
  };

  const esc = value => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const pad = value => String(value).padStart(2, '0');
  const iso = value => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
  const displayValue = value => (value === null || value === undefined || value === '' || value === '-' || value === 'None') ? '-' : String(value);

  function dayOffset(offset) {
    const value = new Date(); value.setDate(value.getDate() + offset); return iso(value);
  }
  function weekRange(offset) {
    const now = new Date(); const weekday = (now.getDay() + 6) % 7;
    const monday = new Date(now); monday.setDate(now.getDate() - weekday + offset * 7);
    const sunday = new Date(monday); sunday.setDate(monday.getDate() + 6);
    return [iso(monday), iso(sunday)];
  }
  function dateRange() {
    if (state.range === 'today') return [dayOffset(0), dayOffset(0)];
    if (state.range === 'yesterday') return [dayOffset(-1), dayOffset(-1)];
    if (state.range === 'tomorrow') return [dayOffset(1), dayOffset(1)];
    if (state.range === 'this_week') return weekRange(0);
    if (state.range === 'last_week') return weekRange(-1);
    if (state.range === 'next_week') return weekRange(1);
    if (state.range === 'custom' && state.customStart && state.customEnd) return [state.customStart, state.customEnd];
    return weekRange(0);
  }

  function restore() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(PREF_KEY) || '{}'); } catch {}
    const query = new URLSearchParams(location.search);
    state.range = query.get('range') || saved.range || 'this_week';
    state.importance = Number.parseInt(query.get('importance') || saved.importance || '0', 10) || 0;
    state.category = query.get('category') || saved.category || 'all';
    state.search = query.get('q') || saved.search || '';
    state.customStart = query.get('start') || saved.customStart || '';
    state.customEnd = query.get('end') || saved.customEnd || '';
    els.importance.value = String(state.importance); els.search.value = state.search;
    els.start.value = state.customStart; els.end.value = state.customEnd;
    els.tabs.querySelectorAll('button').forEach(button => button.classList.toggle('active', button.dataset.range === state.range));
    els.categories.querySelectorAll('.cat-btn').forEach(button => button.classList.toggle('active', button.dataset.category === state.category));
    els.custom.hidden = state.range !== 'custom';
  }

  function save() {
    const payload = {range: state.range, importance: state.importance, category: state.category, search: state.search, customStart: state.customStart, customEnd: state.customEnd};
    try { localStorage.setItem(PREF_KEY, JSON.stringify(payload)); } catch {}
    const query = new URLSearchParams();
    if (state.range !== 'today') query.set('range', state.range);
    if (state.importance) query.set('importance', String(state.importance));
    if (state.category !== 'all') query.set('category', state.category);
    if (state.search) query.set('q', state.search);
    if (state.range === 'custom' && state.customStart) query.set('start', state.customStart);
    if (state.range === 'custom' && state.customEnd) query.set('end', state.customEnd);
    history.replaceState(null, '', `${location.pathname}${query.size ? `?${query}` : ''}`);
  }

  function verificationLabel(value) {
    if (!value || value === 'none' || value === 'unknown') return 'N/A';
    if (value === 'official') return 'Nguồn chính thức';
    if (value === 'aggregator') return 'Nguồn tổng hợp';
    if (value === 'official_aggregator') return 'Nguồn FRED';
    if (value === 'market_provider') return 'Nguồn thị trường';
    return 'Đã kiểm chứng';
  }

  function tickerClass(item) {
    if (item.status === 'CEILING') return 'ceiling';
    if (item.status === 'FLOOR') return 'floor';
    const change = Number(item.change_percent);
    return Number.isFinite(change) ? (change > 0 ? 'up' : change < 0 ? 'down' : 'neutral') : 'unavailable';
  }

  function tickerPrice(item) {
    if (item.last_price === null || item.last_price === undefined) return '--';
    if (item.value_display) return String(item.value_display);
    return Number(item.last_price).toLocaleString('vi-VN', {
      minimumFractionDigits: item.type === 'index' ? 2 : 0,
      maximumFractionDigits: item.type === 'index' ? 2 : 0,
    });
  }

  function updateTickerNode(node, item) {
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

  function createTickerNode(item) {
    const node = document.createElement('div');
    node.dataset.symbol = item.symbol;
    node.tabIndex = 0;
    const symbol = document.createElement('span'); symbol.className = 'ticker-sym';
    const value = document.createElement('span'); value.className = 'ticker-val';
    const change = document.createElement('span'); change.className = 'ticker-chg';
    const arrow = document.createElement('span'); arrow.className = 'ticker-arrow';
    const percent = document.createElement('span'); percent.className = 'ticker-percent';
    const stale = document.createElement('span'); stale.className = 'ticker-stale'; stale.textContent = 'Đã cũ';
    change.append(arrow, percent); node.append(symbol, value, change, stale);
    updateTickerNode(node, item);
    return node;
  }

  function rebuildTickerTrack(items, signature) {
    state.tickerNodes = new Map();
    const fragment = document.createDocumentFragment();
    for (let copy = 0; copy < 2; copy += 1) {
      const group = document.createElement('div');
      group.className = 'ticker-group';
      group.setAttribute('aria-hidden', copy ? 'true' : 'false');
      items.forEach(item => {
        const node = createTickerNode(item);
        if (copy) node.tabIndex = -1;
        const nodes = state.tickerNodes.get(item.symbol) || [];
        nodes.push(node); state.tickerNodes.set(item.symbol, nodes); group.append(node);
      });
      fragment.append(group);
    }
    els.ticker.replaceChildren(fragment);
    els.ticker.classList.add('is-running');
    state.tickerSignature = signature;
    requestAnimationFrame(() => {
      const width = els.ticker.querySelector('.ticker-group')?.scrollWidth || 1800;
      // Khoảng 28 px/giây: đủ chậm để đọc giá nhưng vẫn giữ chuyển động liên tục.
      els.ticker.style.setProperty('--ticker-duration', `${Math.max(64, width / 28).toFixed(1)}s`);
    });
  }

  function updateTickerStatus(payload) {
    const live = Boolean(payload.market_session?.is_live_matching);
    const stale = Boolean(payload.stale || payload.last_known_good);
    els.tickerDot.className = `ticker-status-dot ${stale ? 'stale' : live ? 'live' : 'closed'}`;
    els.tickerStatus.textContent = stale ? 'Đã cũ' : live ? 'Trực tiếp' : 'Đóng cửa';
    const stamp = payload.generated_at ? new Date(payload.generated_at) : null;
    els.tickerUpdated.textContent = stamp && !Number.isNaN(stamp.getTime())
      ? stamp.toLocaleTimeString('vi-VN', {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '';
    els.tickerUpdated.dateTime = payload.generated_at || '';
  }

  function renderTickers(payload) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    if (items.length !== 32 || Number(payload?.membership?.count) !== 30) {
      throw new Error(`Độ phủ bảng giá không hợp lệ (${items.length}/32)`);
    }
    const signature = items.map(item => item.symbol).join('|');
    if (signature !== state.tickerSignature) rebuildTickerTrack(items, signature);
    else items.forEach(item => (state.tickerNodes.get(item.symbol) || []).forEach(node => updateTickerNode(node, item)));
    state.tickerPayload = payload;
    updateTickerStatus(payload);
  }

  function scheduleTickerRefresh(seconds, elapsedMs = 0) {
    clearTimeout(state.tickerTimer);
    if (document.hidden) return;
    const interval = Math.max(10, Number(seconds) || 10) * 1000;
    const delay = Math.max(1000, interval - Math.max(0, elapsedMs));
    state.tickerTimer = window.setTimeout(fetchTickers, delay);
  }

  async function fetchTickers() {
    if (document.hidden) return;
    state.tickerController?.abort();
    const controller = new AbortController();
    const requestId = ++state.tickerRequestId;
    const startedAt = performance.now();
    state.tickerController = controller;
    try {
      const response = await fetch('/api/market-ribbon', {cache: 'no-store', signal: controller.signal});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      if (requestId !== state.tickerRequestId) return;
      renderTickers(payload);
      scheduleTickerRefresh(payload.refresh_after_seconds, performance.now() - startedAt);
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (state.tickerPayload) {
        updateTickerStatus({...state.tickerPayload, stale: true, last_known_good: true});
      } else {
        const loading = document.createElement('div');
        loading.className = 'ticker-loading'; loading.textContent = 'Bảng giá VN30 tạm thời chưa sẵn sàng.';
        els.ticker.replaceChildren(loading);
        els.tickerStatus.textContent = 'Mất kết nối'; els.tickerDot.className = 'ticker-status-dot stale';
      }
      scheduleTickerRefresh(10, performance.now() - startedAt);
    } finally {
      if (state.tickerController === controller) state.tickerController = null;
    }
  }

  function vietnameseDate(value) {
    const parsed = new Date(`${value}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) return value;
    const names = ['CHỦ NHẬT', 'THỨ HAI', 'THỨ BA', 'THỨ TƯ', 'THỨ NĂM', 'THỨ SÁU', 'THỨ BẢY'];
    return `${names[parsed.getDay()]}, ${parsed.getDate()} THÁNG ${parsed.getMonth() + 1}, ${parsed.getFullYear()}`;
  }

  function stars(value) {
    const count = Number.parseInt(value, 10) || 1;
    return `<span class="impact-stars impact-${Math.min(3, count)}" title="Mức độ ảnh hưởng">${'★'.repeat(count)}${'☆'.repeat(3 - count)}</span>`;
  }

  function renderEvents(events) {
    if (!events || !events.length) {
      els.timeline.innerHTML = '<div class="macro-empty-state"><div class="empty-icon">📅</div><div class="empty-title">Không có sự kiện phù hợp</div><div class="empty-desc">Thử điều chỉnh lại bộ lọc độ quan trọng hoặc chọn khoảng thời gian khác.</div></div>';
      return;
    }
    const groups = events.reduce((result, event) => {
      (result[event.event_date] ||= []).push(event); return result;
    }, {});
    let html = '';
    Object.keys(groups).sort().forEach(day => {
      const dayEvents = groups[day]; const isToday = day === state.today; let marker = false;
      html += `<div class="day-group"><header class="day-header"><div class="day-title ${isToday ? 'is-today' : ''}">${esc(vietnameseDate(day))}</div><div class="day-count-badge">${dayEvents.length} sự kiện</div></header>
        <table class="macro-table" aria-label="Lịch sự kiện ${esc(vietnameseDate(day))}"><thead><tr><th class="col-time">GIỜ</th><th class="col-country">QUỐC GIA</th><th class="col-impact">ĐỘ QT</th><th class="col-event">SỰ KIỆN</th><th class="col-val">THỰC TẾ</th><th class="col-val">DỰ BÁO</th><th class="col-val">KỲ TRƯỚC</th><th class="col-action"></th></tr></thead><tbody>`;
      dayEvents.forEach(event => {
        const eventTime = event.event_time || '99:99';
        if (isToday && !marker && eventTime >= state.currentTime) {
          html += `<tr class="current-time-row"><td colspan="8"><div class="current-time-marker"><span class="current-time-pill">• HIỆN TẠI</span><span class="current-time-val">${esc(state.currentTime)}</span></div></td></tr>`;
          marker = true;
        }
        const direction = event.change_vs_previous ? `direction-${esc(event.change_vs_previous)}` : '';
        const stale = event.stale ? '<span class="event-quality is-stale">Đã cũ</span>' : '';
        const sourceClass = event.verification === 'official' ? 'is-official' : 'is-aggregator';
        const actualStr = displayValue(event.actual);
        const forecastStr = displayValue(event.forecast);
        const previousStr = displayValue(event.previous);

        const arrow = (actualStr !== '-' && event.change_vs_previous === 'up') ? ' ↗' : (actualStr !== '-' && event.change_vs_previous === 'down') ? ' ↘' : '';

        html += `<tr class="event-row" data-event-id="${esc(event.id)}" tabindex="0" role="button" aria-label="Xem chi tiết ${esc(event.title_vi || event.title)}">
          <td class="col-time">${esc(event.event_time || 'Cả ngày')}</td><td class="col-country"><span class="country-flag-code"><span class="country-flag">🇺🇸</span><span>USD</span></span></td>
          <td class="col-impact">${stars(event.impact_stars)}</td>
          <td class="col-event">
            <span class="event-title-main">${esc(event.title_vi || event.title)}</span>
            <span class="event-title-sub">${esc(event.title)}</span>
            <div class="event-badges-row">
              <span class="source-badge ${sourceClass}">${esc(verificationLabel(event.verification))}</span>
              ${stale}
            </div>
          </td>
          <td class="col-val"><span class="actual-val ${direction}">${esc(actualStr)}${arrow}</span></td>
          <td class="col-val"><span class="forecast-val">${esc(forecastStr)}</span></td>
          <td class="col-val"><span class="previous-val">${esc(previousStr)}</span></td>
          <td class="col-action"><button class="view-detail-btn" type="button" tabindex="-1" aria-hidden="true" title="Xem nguồn và lịch sử">ⓘ</button></td></tr>`;
      });
      if (isToday && !marker) html += `<tr class="current-time-row"><td colspan="8"><div class="current-time-marker"><span class="current-time-pill">• HIỆN TẠI</span><span class="current-time-val">${esc(state.currentTime)}</span></div></td></tr>`;
      html += '</tbody></table></div>';
    });
    els.timeline.innerHTML = html;
    els.timeline.querySelectorAll('.event-row').forEach(row => {
      const open = () => openDetail(row.dataset.eventId, row);
      row.addEventListener('click', open);
      row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    });
  }

  function renderHistory(rows, title) {
    const valid = (rows || []).filter(row => Number.isFinite(Number(row.value)));
    if (valid.length < 2) { els.historySection.hidden = true; return; }
    els.historySection.hidden = false;
    const values = valid.map(row => Number(row.value)); const min = Math.min(...values); const max = Math.max(...values);
    const width = 600; const height = 170; const range = max - min || 1;
    const points = values.map((value, index) => `${20 + index * (width - 40) / Math.max(1, values.length - 1)},${15 + (max - value) * (height - 35) / range}`).join(' ');
    els.historyChart.setAttribute('aria-label', `Diễn biến ${title}, từ ${valid[0].period} đến ${valid.at(-1).period}`);
    els.historyChart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true" focusable="false"><line x1="20" y1="${height - 20}" x2="${width - 20}" y2="${height - 20}" class="history-axis"/><polyline points="${points}" class="history-line"/></svg>`;
    els.historyTable.innerHTML = valid.slice(-12).reverse().map(row => `<tr><td>${esc(row.period)}</td><td>${esc(row.value)} ${esc(row.unit || '')}</td></tr>`).join('');
  }

  function populateDialog(event) {
    els.flag.textContent = event.flag || '🇺🇸'; els.title.textContent = event.title_vi || event.title || 'Chi tiết sự kiện';
    els.subtitle.textContent = `Mỹ (USD) • ${event.event_time || 'Cả ngày'} GMT+7 • ${event.reference_period ? `Kỳ ${event.reference_period}` : 'Chưa xác định kỳ'}`;
    els.actual.textContent = displayValue(event.actual);
    if (els.forecast) els.forecast.textContent = displayValue(event.forecast);
    els.previous.textContent = displayValue(event.previous);
    els.overview.textContent = event.overview_vi || 'Sự kiện kinh tế Mỹ quan trọng.';
    els.macroImpact.textContent = event.impact_analysis_vi || 'Đánh giá tác động cần dựa trên nội dung công bố và bối cảnh nhiều chỉ báo.';
    els.vnImpact.textContent = event.vn_market_impact_vi || 'Kênh truyền dẫn tới Việt Nam gồm tỷ giá USD/VND, lãi suất và dòng vốn quốc tế.';
    els.sourceText.textContent = `${event.source || 'Chưa rõ nguồn'} • Xác minh: ${verificationLabel(event.verification)}${event.stale ? ' • Dữ liệu đã cũ' : ''}`;
    if (event.source_url) { els.sourceLink.href = event.source_url; els.sourceLink.hidden = false; } else { els.sourceLink.hidden = true; els.sourceLink.removeAttribute('href'); }
    renderHistory(event.history || [], event.title_vi || event.title || 'chỉ báo');
  }

  async function openDetail(id, trigger) {
    state.activeTrigger = trigger; const fallback = state.events.find(event => event.id === id);
    if (fallback) populateDialog(fallback);
    els.dialog.showModal(); document.body.style.overflow = 'hidden'; els.close.focus();
    state.detailController?.abort(); state.detailController = new AbortController();
    try {
      const response = await fetch(`/api/macro-event/${encodeURIComponent(id)}`, {cache: 'no-store', signal: state.detailController.signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      populateDialog(await response.json());
    } catch (error) { if (error.name !== 'AbortError' && !fallback) els.sourceText.textContent = 'Không tải được chi tiết sự kiện.'; }
  }

  function closeDialog() {
    state.detailController?.abort();
    if (els.dialog.open) els.dialog.close();
    document.body.style.overflow = '';
    state.activeTrigger?.focus();
  }

  function updateSummary(data) {
    state.counts = data.counts || {}; state.today = data.today || state.today; state.currentTime = data.current_time || state.currentTime;
    els.today.textContent = state.counts.today ?? 0; els.week.textContent = state.counts.this_week ?? 0; els.high.textContent = state.counts.high_impact ?? 0;
    els.lastWeek.textContent = state.counts.last_week ?? 0; els.yesterday.textContent = state.counts.yesterday ?? 0;
    els.todayBadge.textContent = state.counts.today ?? 0; els.tomorrow.textContent = state.counts.tomorrow ?? 0; els.thisWeek.textContent = state.counts.this_week ?? 0;
    const next = data.next_event;
    els.nextTime.textContent = next?.event_time || 'Đã hết'; els.nextTitle.textContent = next ? `${next.flag || '🇺🇸'} ${next.title_vi || next.title}` : 'Không còn sự kiện có giờ hôm nay';
  }

  async function loadData() {
    state.controller?.abort(); const controller = new AbortController(); state.controller = controller;
    const requestId = ++state.requestId; const [start, end] = dateRange();
    els.coverage.textContent = 'Đang tải dữ liệu vĩ mô...';
    const params = new URLSearchParams({start_date: start, end_date: end, country: 'USD', importance: String(state.importance), category: state.category, search: state.search});
    try {
      const response = await fetch(`/api/macro-calendar?${params}`, {cache: 'no-store', signal: controller.signal});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`);
      if (requestId !== state.requestId) return;
      state.events = data.events || []; updateSummary(data); renderEvents(state.events);
      const quality = data.data_quality || {}; const verified = data.coverage?.official_events ?? 0; const aggregate = data.coverage?.aggregator_events ?? 0;
      els.coverage.textContent = `${state.events.length} sự kiện • ${verified} nguồn chính thức • ${aggregate} nguồn tổng hợp • Cập nhật ${quality.as_of ? quality.as_of.slice(0, 10) : 'Hôm nay'}`;
    } catch (error) {
      if (error.name === 'AbortError') return;
      els.coverage.textContent = `Không tải được lịch: ${error.message}`;
      els.timeline.innerHTML = '<div class="macro-empty-state"><div class="empty-title">Không thể tải dữ liệu</div><div class="empty-desc">Hệ thống đang kết nối lại nguồn cấp dữ liệu vĩ mô.</div></div>';
    }
  }

  async function requestRefresh() {
    els.refresh.disabled = true; els.refresh.classList.add('is-loading'); els.coverage.textContent = 'Đang đồng bộ nguồn dữ liệu vĩ mô...';
    try {
      const response = await fetch('/api/macro-refresh', {method: 'POST'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      els.coverage.textContent = 'Đang đồng bộ dữ liệu mới nhất...';
      window.setTimeout(loadData, 1200);
    } catch (error) { els.coverage.textContent = `Không thể đồng bộ: ${error.message}`; }
    finally { els.refresh.disabled = false; els.refresh.classList.remove('is-loading'); }
  }

  els.tabs.addEventListener('click', event => {
    const button = event.target.closest('button[data-range]'); if (!button) return;
    els.tabs.querySelectorAll('button').forEach(item => item.classList.remove('active')); button.classList.add('active');
    state.range = button.dataset.range; els.custom.hidden = state.range !== 'custom'; save(); loadData();
  });
  els.categories.addEventListener('click', event => {
    const button = event.target.closest('.cat-btn[data-category]'); if (!button) return;
    els.categories.querySelectorAll('.cat-btn').forEach(item => item.classList.remove('active')); button.classList.add('active');
    state.category = button.dataset.category; save(); loadData();
  });
  els.importance.addEventListener('change', () => { state.importance = Number.parseInt(els.importance.value, 10) || 0; save(); loadData(); });
  let searchTimer;
  els.search.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.search = els.search.value.trim(); save(); loadData(); }, 300); });
  els.clear.addEventListener('click', () => {
    state.importance = 0; state.category = 'all'; state.search = ''; els.importance.value = '0'; els.search.value = '';
    els.categories.querySelectorAll('.cat-btn').forEach(button => button.classList.toggle('active', button.dataset.category === 'all')); save(); loadData();
  });
  els.apply.addEventListener('click', () => {
    if (!els.start.value || !els.end.value || els.end.value < els.start.value) { els.coverage.textContent = 'Khoảng ngày tùy chọn không hợp lệ.'; return; }
    state.customStart = els.start.value; state.customEnd = els.end.value; save(); loadData();
  });
  els.refresh.addEventListener('click', requestRefresh);
  els.export.addEventListener('click', () => { const [start, end] = dateRange(); window.open(`/api/macro-calendar/ics?start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}`, '_blank', 'noopener'); });
  els.close.addEventListener('click', closeDialog); els.closeBottom.addEventListener('click', closeDialog); els.backdrop.addEventListener('click', closeDialog);
  els.dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(); });

  restore(); loadData();
})();
