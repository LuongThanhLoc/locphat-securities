(() => {
  'use strict';

  const state = {
    data: null,
    participant: 'foreign',
    metric: 'value',
    priceChart: null,
    priceHistory: null,
    priceHistoryError: '',
    priceHistoryController: null,
    flowChart: null,
    requestController: null,
    suggestController: null,
    suggestions: [],
    suggestionIndex: -1,
    suggestTimer: 0,
    currentSymbol: null,
    live: null,
    liveTimer: 0,
    liveController: null,
    liveInFlight: false,
    liveError: '',
  };

  const $ = (id) => document.getElementById(id);
  const form = $('volumeSearchForm');
  const input = $('volumeSymbolInput');
  const suggestionBox = $('volumeSuggestions');
  const content = $('volumeContent');
  const status = $('volumeState');

  const formatDate = (value) => {
    if (!value) return '--';
    const parts = String(value).slice(0, 10).split('-');
    return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : String(value);
  };

  const fullNumber = (value) => Number(value).toLocaleString('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  const compact = (value, metric = 'value', signed = false) => {
    if (value == null || value === '') return '--';
    const number = Number(value);
    if (!Number.isFinite(number)) return '--';
    const abs = Math.abs(number);
    const sign = signed && number > 0 ? '+' : '';
    let rendered;
    if (metric === 'value') {
      if (abs >= 1e12) rendered = `${(number / 1e12).toLocaleString('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} nghìn tỷ`;
      else if (abs >= 1e9) rendered = `${(number / 1e9).toLocaleString('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} tỷ`;
      else if (abs >= 1e6) rendered = `${(number / 1e6).toLocaleString('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} triệu`;
      else rendered = fullNumber(number);
    } else {
      if (abs >= 1e6) rendered = `${(number / 1e6).toLocaleString('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} triệu cp`;
      else if (abs >= 1e3) rendered = `${(number / 1e3).toLocaleString('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} nghìn cp`;
      else rendered = `${fullNumber(number)} cp`;
    }
    return `${sign}${rendered}`;
  };

  const setStatus = (kind, title, message) => {
    status.className = `vf-state ${kind || ''}`.trim();
    status.querySelector('strong').textContent = title;
    status.querySelector('span:last-child').textContent = message || '';
  };

  const netClass = (element, value) => {
    element.classList.remove('positive', 'negative', 'neutral');
    element.classList.add(value != null && Number(value) > 0 ? 'positive' : value != null && Number(value) < 0 ? 'negative' : 'neutral');
  };

  const liveSession = () => state.live?.live_session || null;
  const displaySessions = () => {
    if (!state.data) return [];
    const rows = [...state.data.sessions];
    const live = liveSession();
    if (live && (!rows.length || live.date > rows[rows.length - 1].date)) rows.push(live);
    return rows;
  };

  const sourceError = async (response) => {
    try {
      const body = await response.json();
      return body.detail || 'Không tải được dữ liệu Tổng quan KLGD.';
    } catch (_) {
      return 'Không tải được dữ liệu Tổng quan KLGD.';
    }
  };

  function renderParticipant(name) {
    const card = document.querySelector(`.vf-participant-card[data-participant="${name}"]`);
    const summary = state.data.summary[name];
    const live = name === 'foreign' ? liveSession() : null;
    const latest = live ? live.foreign : summary.latest;
    const period = summary.period;
    card.querySelector('[data-field="date"]').textContent = live
      ? `Phiên ${formatDate(live.date)} · tạm tính`
      : `Phiên ${formatDate(state.data.as_of)}`;
    card.querySelector('[data-field="latest-buy-volume"]').textContent = compact(latest.buy_volume, 'volume');
    card.querySelector('[data-field="latest-sell-volume"]').textContent = compact(latest.sell_volume, 'volume');
    const latestNetVolume = card.querySelector('[data-field="latest-net-volume"]');
    latestNetVolume.textContent = compact(latest.net_volume, 'volume', true);
    netClass(latestNetVolume, latest.net_volume);
    card.querySelector('[data-field="latest-buy-value"]').textContent = compact(latest.buy_value, 'value');
    card.querySelector('[data-field="latest-sell-value"]').textContent = compact(latest.sell_value, 'value');
    const latestNetValue = card.querySelector('[data-field="latest-net-value"]');
    latestNetValue.textContent = latest.net_value == null
      ? '--' : `${compact(latest.net_value, 'value', true)} ròng`;
    netClass(latestNetValue, latest.net_value);
    const periodNet = card.querySelector('[data-field="period-net-value"]');
    periodNet.textContent = compact(period.net_value, 'value', true);
    netClass(periodNet, period.net_value);
    card.querySelector('[data-field="period-ratio"]').textContent = period.buy_sell_value_ratio == null
      ? 'Không xác định' : `${Number(period.buy_sell_value_ratio).toLocaleString('vi-VN', { maximumFractionDigits: 2 })}x`;
    card.querySelector('[data-field="period-days"]').textContent = period.complete
      ? `${period.net_buy_sessions} / ${period.net_sell_sessions}`
      : `${period.coverage_count}/${period.target_count} phiên có nguồn`;
  }

  function renderLive() {
    const envelope = state.live;
    const live = liveSession();
    const panel = $('volumeLive');
    if (!live) {
      panel.hidden = false;
      $('liveLabel').textContent = envelope?.poll_enabled ? 'Đang chờ bảng giá' : 'Ngoài phiên giao dịch';
      $('liveDate').textContent = '';
      $('liveObserved').textContent = state.liveError || 'Không có phiên tạm tính mới hơn EOD.';
      $('liveMetrics').hidden = true;
      return;
    }
    panel.hidden = false;
    $('liveMetrics').hidden = false;
    $('liveLabel').textContent = live.display_label || (envelope?.poll_enabled ? 'Tạm tính trong phiên' : 'Tạm tính sau đóng cửa');
    $('liveDate').textContent = `· ${formatDate(live.date)}`;
    const observed = live.observed_at ? new Date(live.observed_at) : null;
    const observedLabel = observed && !Number.isNaN(observed.getTime())
      ? `Nguồn lúc ${observed.toLocaleTimeString('vi-VN')} · ${live.source_session || 'bảng giá'}`
      : `${live.source_session || 'Snapshot bảng giá Vietcap'}`;
    $('liveObserved').textContent = `${observedLabel}${state.liveError ? ' · lần cập nhật mới lỗi' : ''}`;
    $('liveBuy').textContent = `${compact(live.foreign.buy_value, 'value')} · ${compact(live.foreign.buy_volume, 'volume')}`;
    $('liveSell').textContent = `${compact(live.foreign.sell_value, 'value')} · ${compact(live.foreign.sell_volume, 'volume')}`;
    $('liveNet').textContent = `${compact(live.foreign.net_value, 'value', true)} · ${compact(live.foreign.net_volume, 'volume', true)}`;
    netClass($('liveNet'), live.foreign.net_value);
  }

  function destroyPriceChart() {
    if (state.priceChart) state.priceChart.destroy();
    state.priceChart = null;
  }

  function renderPriceChart() {
    const host = $('volumeSharedPriceChart');
    if (!window.LPPriceChart || !state.data) return;
    const sessions = state.priceHistory?.sessions || state.data.sessions;
    const live = liveSession();
    const liveRow = live ? {
      date: live.date, open: live.open, high: live.high, low: live.low,
      close: live.close, volume: live.market_volume, is_provisional: true,
    } : null;
    if (!state.priceChart) {
      state.priceChart = window.LPPriceChart.create(host, {
        symbol: state.data.symbol, exchange: state.data.exchange,
        sessions, liveSession: liveRow, timeframe: 'ALL', engine: 'tradingview',
      });
    } else {
      state.priceChart.setData({
        symbol: state.data.symbol, exchange: state.data.exchange,
        sessions, liveSession: liveRow,
      });
    }
    if (state.priceHistoryError) {
      const note = host.querySelector('[data-role="note"]');
      if (note) note.textContent += ` · ${state.priceHistoryError}`;
    }
  }

  async function loadPriceHistory(symbol) {
    if (state.priceHistoryController) state.priceHistoryController.abort();
    state.priceHistoryController = new AbortController();
    const requestedSymbol = symbol;
    try {
      const response = await fetch(`/api/price-chart/${encodeURIComponent(symbol)}`, {
        cache: 'no-store', signal: state.priceHistoryController.signal,
      });
      if (!response.ok) throw new Error(await sourceError(response));
      const payload = await response.json();
      if (requestedSymbol !== state.currentSymbol || payload.symbol !== requestedSymbol) return;
      state.priceHistory = payload;
      state.priceHistoryError = payload.sync?.stale ? 'Lịch sử giá đang dùng bản lưu gần nhất' : '';
      renderPriceChart();
    } catch (error) {
      if (error.name === 'AbortError' || requestedSymbol !== state.currentSymbol) return;
      state.priceHistoryError = 'Không tải được lịch sử 3 năm; tạm hiển thị dữ liệu EOD hiện có.';
      renderPriceChart();
    }
  }

  function destroyFlowChart() {
    if (state.flowChart) state.flowChart.destroy();
    state.flowChart = null;
  }

  function renderFlowChart() {
    const container = $('volumeFlowChart');
    destroyFlowChart();
    const sessions = state.participant === 'foreign' ? displaySessions() : state.data.sessions;
    if (!window.ApexCharts || !sessions.length) {
      container.textContent = 'Không thể khởi tạo biểu đồ dòng tiền.';
      return;
    }
    container.textContent = '';
    const suffix = state.metric === 'value' ? 'value' : 'volume';
    const participant = state.participant;
    const dailyNet = sessions.map((row) => {
      const value = row[participant][`net_${suffix}`];
      return value == null ? null : Number(value);
    });
    const isForeign = participant === 'foreign';
    const participantCoverage = state.data.summary[participant].period;
    const ytdComplete = Boolean(state.data.foreign_ytd?.complete);
    let cumulativeName;
    let cumulativeNet;
    if (isForeign) {
      cumulativeName = 'Lũy kế YTD';
      cumulativeNet = ytdComplete
        ? sessions.map((row) => row.foreign[`ytd_net_${suffix}`] == null ? null : Number(row.foreign[`ytd_net_${suffix}`]))
        : null;
    } else {
      cumulativeName = 'Lũy kế 20 phiên';
      if (participantCoverage.complete) {
        let cumulative = 0;
        cumulativeNet = dailyNet.map((value) => {
          cumulative += value;
          return cumulative;
        });
      } else {
        cumulativeNet = null;
      }
    }
    const categories = sessions.map((row, index, rows) => {
      if (innerWidth < 640 && index % 3 !== 0 && index !== rows.length - 1) return '';
      return formatDate(row.date).slice(0, 5);
    });
    const metric = state.metric;
    const note = $('flowDataNote');
    const hasProvisional = Boolean(isForeign && liveSession());
    if (hasProvisional && cumulativeNet) cumulativeName = 'Lũy kế YTD tạm tính';
    $('flowChartTitle').textContent = isForeign ? 'NET theo ngày và lũy kế YTD' : 'NET theo ngày và lũy kế 20 phiên';
    $('flowCumulativeLegend').textContent = cumulativeName;
    $('flowCumulativeLegendItem').hidden = !cumulativeNet;
    if (isForeign && !ytdComplete) {
      note.hidden = false;
      note.textContent = 'Không hiển thị lũy kế YTD vì nguồn chưa chứng minh đủ dữ liệu từ phiên đầu năm.';
    } else if (!isForeign && !participantCoverage.complete) {
      note.hidden = false;
      note.textContent = `Không tính lũy kế tự doanh: nguồn chỉ có ${participantCoverage.coverage_count}/${participantCoverage.target_count} phiên.`;
    } else if (hasProvisional) {
      note.hidden = false;
      note.textContent = `Phiên ${formatDate(liveSession().date)} lấy từ bảng giá, chưa ghi PostgreSQL; NET và lũy kế YTD đều là tạm tính.`;
    } else {
      note.hidden = true;
      note.textContent = '';
    }
    const series = [{ name: 'NET trong ngày', type: 'column', data: dailyNet }];
    if (cumulativeNet) series.push({ name: cumulativeName, type: 'line', data: cumulativeNet });
    const chart = new ApexCharts(container, {
      series,
      chart: {
        type: 'line', height: Math.max(container.clientHeight, 330), background: '#fffdf7',
        fontFamily: 'Inter, sans-serif', parentHeightOffset: 0,
        toolbar: { show: false }, zoom: { enabled: false },
        animations: { enabled: !matchMedia('(prefers-reduced-motion: reduce)').matches },
      },
      theme: { mode: 'light' },
      colors: ['#087b50', '#064a6b'],
      stroke: { width: cumulativeNet ? [0, 2.5] : [0], curve: 'smooth' },
      plotOptions: { bar: { columnWidth: '58%', borderRadius: 2, colors: { ranges: [
        { from: Number.MIN_SAFE_INTEGER, to: -0.0000001, color: '#b72c32' },
        { from: 0, to: Number.MAX_SAFE_INTEGER, color: '#087b50' },
      ] } } },
      dataLabels: { enabled: false },
      legend: { show: false },
      grid: { borderColor: '#e7e0d4', strokeDashArray: 3, padding: { left: 6, right: 7 } },
      xaxis: { categories, axisBorder: { color: '#cbc5b8' }, axisTicks: { show: false }, labels: { rotate: -45, hideOverlappingLabels: true, trim: true, style: { colors: '#68727a', fontSize: '10px' } } },
      yaxis: [
        { seriesName: 'NET trong ngày', labels: { formatter: (value) => compact(value, metric), style: { colors: '#68727a', fontSize: '10px' } } },
        ...(cumulativeNet ? [{ opposite: true, seriesName: cumulativeName, labels: { formatter: (value) => compact(value, metric), style: { colors: '#064a6b', fontSize: '10px' } } }] : []),
      ],
      tooltip: {
        shared: true,
        intersect: false,
        theme: 'light',
        y: { formatter: (value, context) => {
          const isLast = hasProvisional && context?.dataPointIndex === sessions.length - 1;
          const label = context?.seriesIndex === 0
            ? (isLast ? 'NET tạm tính' : 'NET trong ngày')
            : (isLast ? 'Lũy kế YTD tạm tính' : cumulativeName);
          return `${label}: ${compact(value, metric, true)}`;
        } },
      },
      annotations: { yaxis: [{ y: 0, borderColor: '#9ea5a5', strokeDashArray: 2 }] },
      noData: { text: 'Không có dữ liệu' },
    });
    chart.render().catch(() => {
      container.textContent = 'Không thể hiển thị biểu đồ dòng tiền trên trình duyệt này.';
    });
    state.flowChart = chart;
  }

  function renderTable() {
    const metric = state.metric;
    const suffix = metric === 'value' ? 'value' : 'volume';
    const rows = displaySessions();
    const provisionalCount = rows.length - state.data.sessions.length;
    $('volumeTableTitle').textContent = `${state.data.coverage_count} phiên EOD${provisionalCount ? ' + 1 phiên tạm tính' : ''} · ${metric === 'value' ? 'Giá trị' : 'Khối lượng'}`;
    const body = $('volumeTableBody');
    body.replaceChildren();
    [...rows].reverse().forEach((row) => {
      const tr = document.createElement('tr');
      if (row.is_provisional) tr.classList.add('provisional');
      const values = [
        formatDate(row.date),
        Number(row.close).toLocaleString('vi-VN'),
        compact(row.market_volume, 'volume'),
        compact(row.foreign[`buy_${suffix}`], metric),
        compact(row.foreign[`sell_${suffix}`], metric),
        compact(row.foreign[`net_${suffix}`], metric, true),
        compact(row.proprietary[`buy_${suffix}`], metric),
        compact(row.proprietary[`sell_${suffix}`], metric),
        compact(row.proprietary[`net_${suffix}`], metric, true),
      ];
      values.forEach((value, index) => {
        const td = document.createElement('td');
        td.textContent = value;
        if (index === 5) netClass(td, row.foreign[`net_${suffix}`]);
        if (index === 8) netClass(td, row.proprietary[`net_${suffix}`]);
        tr.appendChild(td);
      });
      const statusCell = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = `vf-source-badge${row.proprietary.source_record ? '' : ' missing'}`;
      badge.textContent = row.is_provisional
        ? 'Tạm tính · Tự doanh chưa công bố'
        : row.proprietary.source_record ? 'Có bản ghi nguồn' : 'Không có bản ghi nguồn';
      statusCell.appendChild(badge);
      tr.appendChild(statusCell);
      body.appendChild(tr);
    });
  }

  function render(payload) {
    state.data = payload;
    state.priceHistory = null;
    state.priceHistoryError = '';
    state.live = payload.live || null;
    state.liveError = '';
    content.hidden = false;
    $('volumeTicker').textContent = payload.symbol;
    $('volumeStockTitle').textContent = payload.company_name;
    $('volumeExchange').textContent = payload.exchange;
    $('volumeCoverage').textContent = `${payload.coverage_count}/${payload.target_session_count} phiên`;
    $('volumeAsOf').textContent = formatDate(payload.as_of);
    $('volumeFreshness').textContent = payload.sync.stale
      ? `Bản lưu ${payload.sync.last_success_at ? new Date(payload.sync.last_success_at).toLocaleString('vi-VN') : ''}`
      : payload.sync.refreshed ? 'Vừa đồng bộ vào cơ sở dữ liệu' : 'Đã xác minh từ cơ sở dữ liệu';
    renderLive();
    renderParticipant('foreign');
    renderParticipant('proprietary');
    renderPriceChart();
    renderFlowChart();
    renderTable();
    scheduleLivePolling();
    loadPriceHistory(payload.symbol);
  }

  function stopLivePolling() {
    clearTimeout(state.liveTimer);
    state.liveTimer = 0;
  }

  function scheduleLivePolling() {
    stopLivePolling();
    if (!state.live?.poll_enabled || document.hidden || !state.currentSymbol || state.data?.symbol !== state.currentSymbol) return;
    const delay = Math.max(1, Number(state.live.poll_after_seconds) || 5) * 1000;
    state.liveTimer = window.setTimeout(() => fetchLiveSnapshot(false), delay);
  }

  function rerenderLiveViews() {
    renderLive();
    renderParticipant('foreign');
    renderPriceChart();
    renderFlowChart();
    renderTable();
  }

  function applyLiveEnvelope(envelope) {
    if (!state.data || envelope.symbol !== state.currentSymbol) return;
    const previous = liveSession();
    const next = envelope.live_session;
    if (!next && previous && envelope.official_eod_date < previous.date) {
      envelope.live_session = previous;
    }
    state.live = envelope;
    state.liveError = '';
    rerenderLiveViews();
  }

  async function fetchLiveSnapshot(manual = false) {
    if (!state.data || state.data.symbol !== state.currentSymbol || !state.currentSymbol || state.liveInFlight || (document.hidden && !manual)) return;
    state.liveInFlight = true;
    stopLivePolling();
    if (state.liveController) state.liveController.abort();
    state.liveController = new AbortController();
    const refreshButton = $('volumeManualRefresh');
    refreshButton.disabled = true;
    const symbol = state.currentSymbol;
    try {
      const response = await fetch(`/api/volume-overview/${encodeURIComponent(symbol)}/live`, {
        cache: 'no-store', signal: state.liveController.signal,
      });
      if (!response.ok) throw new Error(await sourceError(response));
      const envelope = await response.json();
      if (symbol === state.currentSymbol) applyLiveEnvelope(envelope);
    } catch (error) {
      if (error.name !== 'AbortError' && symbol === state.currentSymbol) {
        state.liveError = error.message || 'Nguồn realtime tạm thời lỗi; đang giữ snapshot tốt gần nhất.';
        renderLive();
      }
    } finally {
      state.liveInFlight = false;
      refreshButton.disabled = false;
      if (symbol === state.currentSymbol && state.data?.symbol === symbol) scheduleLivePolling();
    }
  }

  async function loadSymbol(rawSymbol) {
    const symbol = String(rawSymbol || '').toUpperCase().trim();
    if (!/^[A-Z][A-Z0-9]{1,5}$/.test(symbol)) {
      setStatus('error', 'Mã cổ phiếu không hợp lệ', 'Hãy chọn một mã HOSE, HNX hoặc UPCoM từ danh sách gợi ý.');
      return;
    }
    closeSuggestions();
    input.value = symbol;
    stopLivePolling();
    if (state.liveController) state.liveController.abort();
    if (state.requestController) state.requestController.abort();
    state.currentSymbol = symbol;
    state.requestController = new AbortController();
    setStatus('loading', `Đang tải ${symbol}`, 'Kiểm tra dữ liệu EOD và bản lưu PostgreSQL…');
    try {
      const response = await fetch(`/api/volume-overview/${encodeURIComponent(symbol)}`, {
        cache: 'no-store', signal: state.requestController.signal,
      });
      if (!response.ok) throw new Error(await sourceError(response));
      const payload = await response.json();
      if (symbol !== state.currentSymbol) return;
      render(payload);
      const query = new URLSearchParams(location.search);
      query.set('symbol', payload.symbol);
      history.replaceState({}, '', `${location.pathname}?${query.toString()}`);
      if (payload.sync.stale) {
        setStatus('stale', 'Đang dùng dữ liệu đã lưu', payload.sync.warning || `Dữ liệu gần nhất đến ${formatDate(payload.as_of)}.`);
      } else if (payload.data_status === 'partial_history') {
        setStatus('partial', `Có ${payload.coverage_count} phiên dữ liệu`, 'Mã mới niêm yết hoặc chưa đủ 20 phiên giao dịch đã chốt.');
      } else {
        const storeLabel = payload.sync.served_from === 'database' ? 'PostgreSQL' : 'kho dữ liệu';
        setStatus('ok', 'Dữ liệu đã xác minh', `${payload.coverage_count} phiên · EOD ${formatDate(payload.as_of)} · đọc từ ${storeLabel}.`);
      }
    } catch (error) {
      if (error.name === 'AbortError') return;
      setStatus('error', `Không tải được ${symbol}`, error.message || 'Vui lòng thử lại sau.');
    }
  }

  function closeSuggestions() {
    suggestionBox.hidden = true;
    suggestionBox.replaceChildren();
    state.suggestions = [];
    state.suggestionIndex = -1;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }

  function drawSuggestions(rows) {
    state.suggestions = rows;
    state.suggestionIndex = -1;
    suggestionBox.replaceChildren();
    if (!rows.length) {
      closeSuggestions();
      return;
    }
    rows.forEach((row, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'vf-suggestion';
      button.id = `volumeSuggestion${index}`;
      button.setAttribute('role', 'option');
      button.dataset.index = String(index);
      const symbol = document.createElement('b');
      symbol.textContent = row.symbol;
      const name = document.createElement('span');
      name.textContent = row.name || '';
      button.append(symbol, name);
      button.addEventListener('click', () => loadSymbol(row.symbol));
      suggestionBox.appendChild(button);
    });
    suggestionBox.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  async function suggest(query) {
    if (state.suggestController) state.suggestController.abort();
    state.suggestController = new AbortController();
    try {
      const response = await fetch(`/api/search_suggest?q=${encodeURIComponent(query)}`, {
        cache: 'no-store', signal: state.suggestController.signal,
      });
      if (!response.ok) return closeSuggestions();
      const payload = await response.json();
      drawSuggestions((payload.results || []).slice(0, 8));
    } catch (error) {
      if (error.name !== 'AbortError') closeSuggestions();
    }
  }

  function moveSuggestion(direction) {
    if (!state.suggestions.length) return;
    state.suggestionIndex = (state.suggestionIndex + direction + state.suggestions.length) % state.suggestions.length;
    suggestionBox.querySelectorAll('.vf-suggestion').forEach((button, index) => {
      const active = index === state.suggestionIndex;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
      if (active) {
        input.setAttribute('aria-activedescendant', button.id);
        button.scrollIntoView({ block: 'nearest' });
      }
    });
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const selected = state.suggestionIndex >= 0 ? state.suggestions[state.suggestionIndex] : null;
    loadSymbol(selected?.symbol || input.value);
  });
  input.addEventListener('input', () => {
    clearTimeout(state.suggestTimer);
    const query = input.value.trim();
    if (!query) return closeSuggestions();
    state.suggestTimer = setTimeout(() => suggest(query), 160);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); moveSuggestion(1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); moveSuggestion(-1); }
    else if (event.key === 'Escape') { event.preventDefault(); closeSuggestions(); }
  });
  document.addEventListener('pointerdown', (event) => {
    if (!form.contains(event.target)) closeSuggestions();
  });

  $('participantTabs').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-participant]');
    if (!button || !state.data) return;
    state.participant = button.dataset.participant;
    $('participantTabs').querySelectorAll('button').forEach((item) => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-pressed', String(active));
    });
    renderFlowChart();
  });
  $('metricTabs').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-metric]');
    if (!button || !state.data) return;
    state.metric = button.dataset.metric;
    $('metricTabs').querySelectorAll('button').forEach((item) => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-pressed', String(active));
    });
    renderFlowChart();
    renderTable();
  });

  $('volumeManualRefresh').addEventListener('click', () => fetchLiveSnapshot(true));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopLivePolling();
      return;
    }
    if (state.live?.poll_enabled) fetchLiveSnapshot(false);
  });

  window.addEventListener('pagehide', () => {
    stopLivePolling();
    destroyPriceChart();
    destroyFlowChart();
    if (state.requestController) state.requestController.abort();
    if (state.suggestController) state.suggestController.abort();
    if (state.liveController) state.liveController.abort();
    if (state.priceHistoryController) state.priceHistoryController.abort();
  }, { once: true });

  const initial = new URLSearchParams(location.search).get('symbol') || 'FPT';
  loadSymbol(initial);
})();
