(() => {
  'use strict';

  const state = {
    data: null,
    aiReport: null,
    colorMode: 'performance',
    sizeMetric: 'market_cap',
    exchange: 'ALL',
    sector: 'ALL',
    activeOnly: false,
    query: '',
  };

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const pctClass = (value) => value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral';
  const signed = (value, digits = 2) => `${value > 0 ? '+' : ''}${Number(value || 0).toFixed(digits)}%`;
  const formatNumber = (value, digits = 0) => Number(value || 0).toLocaleString('vi-VN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
  const formatMoney = (value) => {
    const amount = Number(value || 0);
    if (amount >= 1e15) return `${formatNumber(amount / 1e15, 2)} triệu tỷ`;
    if (amount >= 1e12) return `${formatNumber(amount / 1e12, 1)} nghìn tỷ`;
    if (amount >= 1e9) return `${formatNumber(amount / 1e9, 1)} tỷ`;
    if (amount >= 1e6) return `${formatNumber(amount / 1e6, 1)} triệu`;
    return `${formatNumber(amount)} đ`;
  };
  const scoreColor = (score) => {
    if (score >= 70) return '#21d79a';
    if (score >= 55) return '#76cfae';
    if (score >= 42) return '#f4b942';
    return '#ff5368';
  };
  const regimeLabel = (value) => ({
    LAN_TOA_TICH_CUC: 'Lan tỏa tích cực',
    NGHIENG_TICH_CUC: 'Nghiêng tích cực',
    PHAN_HOA: 'Phân hóa',
    NGHIENG_THAN_TRONG: 'Nghiêng thận trọng',
    RUI_RO_CAO: 'Rủi ro cao',
  }[value] || String(value || 'Chưa xác định').replaceAll('_', ' '));
  const statusLabel = (value) => ({
    DAN_DAT: 'Dẫn dắt', TICH_CUC: 'Tích cực', PHAN_HOA: 'Phân hóa',
    THAN_TRONG: 'Thận trọng', SUY_YEU: 'Suy yếu',
  }[value] || String(value || '').replaceAll('_', ' '));
  const sessionLabel = (phase) => ({
    PRE_OPEN: 'Chờ mở cửa', MORNING: 'Phiên sáng', LUNCH_BREAK: 'Nghỉ trưa',
    AFTERNOON: 'Phiên chiều', ATC: 'Phiên ATC', POST_CLOSE_TRADING: 'Giao dịch sau giờ',
    CLOSED: 'Thị trường đóng cửa', WEEKEND: 'Nghỉ cuối tuần', HOLIDAY: 'Nghỉ lễ',
  }[phase] || 'Trạng thái chưa xác định');

  function allStocks() {
    if (!state.data?.sectors) return [];
    return state.data.sectors.flatMap((sector) => sector.stocks || []);
  }

  function filteredSectors() {
    if (!state.data?.sectors) return [];
    const query = state.query.trim().toUpperCase();
    return state.data.sectors.map((sector) => ({
      ...sector,
      stocks: (sector.stocks || []).filter((stock) => {
        if (state.sector !== 'ALL' && sector.name !== state.sector) return false;
        if (state.exchange !== 'ALL' && stock.exchange !== state.exchange) return false;
        if (state.activeOnly && Number(stock.volume || 0) <= 0 && Number(stock.trading_value || 0) <= 0) return false;
        if (query && !stock.symbol.includes(query) && !String(stock.name || '').toUpperCase().includes(query)) return false;
        return Number(stock[state.sizeMetric] || 0) > 0;
      }),
    })).filter((sector) => sector.stocks.length > 0);
  }

  function performanceColor(stock) {
    if (stock.status === 'CEILING') return '#7847c8';
    if (stock.status === 'FLOOR') return '#1687a6';
    const change = clamp(Number(stock.change_pct || 0), -7, 7);
    return d3.scaleLinear()
      .domain([-7, -3, 0, 3, 7])
      .range(['#ef3152', '#a51634', '#34424b', '#087c58', '#17c989'])
      .clamp(true)(change);
  }

  function flowColor(stock) {
    return d3.scaleLinear()
      .domain([0, 35, 50, 65, 100])
      .range(['#da3550', '#8e2539', '#34424b', '#15755b', '#20d69a'])
      .clamp(true)(Number(stock.flow_score || 50));
  }

  function stockColor(stock) {
    return state.colorMode === 'flow' ? flowColor(stock) : performanceColor(stock);
  }

  function renderLegend() {
    const legend = $('heatLegend');
    const entries = state.colorMode === 'flow'
      ? [['Yếu', '#da3550'], ['', '#8e2539'], ['50', '#34424b'], ['', '#15755b'], ['Mạnh', '#20d69a']]
      : [['Giảm mạnh', '#ef3152'], ['', '#a51634'], ['TC', '#34424b'], ['', '#087c58'], ['Tăng mạnh', '#17c989']];
    legend.innerHTML = entries.map(([label, color]) => `${label ? `<span>${esc(label)}</span>` : ''}<i class="legend-swatch" style="background:${color}"></i>`).join('');
  }

  function renderSummary() {
    const data = state.data;
    const summary = data.summary || {};
    const quant = data.quant_snapshot || {};
    $('temperatureValue').textContent = `${formatNumber(quant.market_temperature, 1)}/100`;
    $('regimeValue').textContent = regimeLabel(quant.market_regime);
    $('breadthValue').textContent = `${formatNumber(quant.breadth_pct, 1)}%`;
    $('advanceDeclineValue').textContent = `A/D ${formatNumber(quant.advance_decline_ratio, 2)}`;
    $('liquidityValue').textContent = formatMoney(summary.total_trading_value);
    $('activeRatioValue').textContent = `${formatNumber(quant.active_ratio_pct, 1)}% mã có giao dịch`;
    $('concentrationValue').textContent = `${formatNumber(quant.top10_liquidity_share_pct, 1)}%`;
    $('ceilingCount').textContent = formatNumber(summary.ceilings);
    $('advanceCount').textContent = formatNumber(summary.advances);
    $('flatCount').textContent = formatNumber(summary.unchanged);
    $('declineCount').textContent = formatNumber(summary.declines);
    $('floorCount').textContent = formatNumber(summary.floors);

    const phase = data.market_session?.phase;
    const live = Boolean(data.market_session?.is_live_matching);
    const closed = Boolean(data.market_closed || ['CLOSED', 'WEEKEND', 'HOLIDAY', 'PRE_OPEN'].includes(phase));
    $('statusDot').className = `status-dot ${live ? 'live' : closed ? 'closed' : ''}`;
    $('marketStatus').textContent = sessionLabel(phase);
    const tradeDate = data.data_lineage?.latest_trading_date || 'không rõ ngày';
    const storageLabel = data.snapshot_frozen ? 'snapshot DB cuối phiên' : 'dữ liệu thị trường';
    $('sessionTimestamp').textContent = `Phiên ${tradeDate} · ${storageLabel} · tải lúc ${data.timestamp || '--'}`;
    $('lineageText').textContent = `${data.data_lineage?.price_source || 'Nguồn bảng giá'} · ICB 4 cấp`;
    const coverage = data.data_lineage?.coverage || {};
    $('coverageText').textContent = `${formatNumber(coverage.accepted_listings ?? summary.total_stocks)} mã niêm yết · ${formatNumber(data.data_lineage?.sector_count)} nhóm ngành · Snapshot ${quant.snapshot_id || '--'}`;
    $('refreshButton').disabled = Boolean(data.snapshot_frozen && closed);
    $('refreshButton').title = data.snapshot_frozen && closed
      ? 'Dữ liệu cuối phiên đã khóa trong database'
      : 'Làm mới dữ liệu';
  }

  function renderFilters() {
    const select = $('sectorFilter');
    const current = state.sector;
    select.innerHTML = '<option value="ALL">Tất cả ngành</option>' + state.data.sectors
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name, 'vi'))
      .map((sector) => `<option value="${esc(sector.name)}">${esc(sector.name)} (${sector.stocks.length})</option>`)
      .join('');
    select.value = current;
  }

  function renderRadar() {
    const sectors = state.data.sectors.slice().sort((a, b) => Number(b.flow_score || 0) - Number(a.flow_score || 0));
    $('sectorRadar').innerHTML = sectors.slice(0, 12).map((sector) => {
      const color = scoreColor(Number(sector.flow_score || 0));
      const secSignals = sector._sector_signals || {};
      const stabilityHtml = secSignals.breadth_stability !== undefined
        ? `<span title="Độ ổn định độ rộng">Ổn định ${formatNumber(secSignals.breadth_stability * 100, 0)}%</span>`
        : '';
      const concHtml = secSignals.concentration_signal !== undefined
        ? `<span title="Phân bổ dòng tiền (cao=lan tỏa)">PB ${formatNumber(secSignals.concentration_signal * 100, 0)}%</span>`
        : '';
      return `<div class="radar-row">
        <div class="radar-row-top">
          <span class="radar-name" title="${esc(sector.name)}">${esc(sector.name)}</span>
          <span class="radar-score" style="color:${color}">${formatNumber(sector.flow_score, 1)}</span>
        </div>
        <div class="radar-meta">
          <span class="${pctClass(sector.avg_change_pct)}">${signed(sector.avg_change_pct)}</span>
          <span>Rộng ${formatNumber(sector.breadth_pct, 1)}%</span>
          <span>GTGD ${formatNumber(sector.liquidity_share_pct, 1)}%</span>
          <span>Tin cậy ${esc(sector.confidence)}</span>
          ${stabilityHtml}${concHtml}
        </div>
        <div class="score-track"><span style="width:${clamp(sector.flow_score, 0, 100)}%;background:${color}"></span></div>
      </div>`;
    }).join('');

    const watchlist = state.data.quant_snapshot?.watchlist || [];
    $('stockRadar').innerHTML = watchlist.map((stock) => `<a class="stock-radar-row" href="/stock/${encodeURIComponent(stock.symbol)}">
      <strong>${esc(stock.symbol)}</strong>
      <div><span>${formatMoney(stock.trading_value)}</span><small>${esc(stock.sector)} · hạng TK #${formatNumber(stock.liquidity_rank)}</small></div>
      <em class="${pctClass(stock.change_pct)}">${signed(stock.change_pct)}</em>
    </a>`).join('') || '<div class="empty-state">Chưa có mã đạt điều kiện radar.</div>';
  }

  function showTooltip(event, stock) {
    const tooltip = $('stockTooltip');
    tooltip.hidden = false;
    
    // Extract signals if available
    const signals = stock._signals || {};
    const signalsHtml = Object.keys(signals).length > 0
      ? `<div class="tooltip-signals">
          <span title="Tín hiệu giá chuẩn hóa">Giá: <b>${signals.price_signal?.toFixed(2) || '--'}</b></span>
          <span title="Khớp khối lượng với giá">KL-Giá: <b>${signals.vol_align?.toFixed(2) || '--'}</b></span>
          <span title="Vị trí trong floor-ceiling">Vị trí: <b>${signals.position_signal?.toFixed(2) || '--'}</b></span>
        </div>`
      : '';
    
    tooltip.innerHTML = `<div class="tooltip-head"><div><strong>${esc(stock.symbol)}</strong><div class="tooltip-name">${esc(stock.name)}</div></div><span>${esc(stock.exchange)}</span></div>
      <div class="tooltip-grid">
        <div><span>Giá gần nhất</span><b>${formatNumber(stock.price_vnd)} đ</b></div>
        <div><span>Biến động</span><b class="${pctClass(stock.change_pct)}">${signed(stock.change_pct)}</b></div>
        <div><span>Giá trị GD</span><b>${formatMoney(stock.trading_value)}</b></div>
        <div><span>Vốn hóa</span><b>${formatMoney(stock.market_cap)}</b></div>
        <div><span>Điểm dòng tiền</span><b style="color:${scoreColor(stock.flow_score)}">${formatNumber(stock.flow_score, 1)}</b></div>
        <div><span>Hạng thanh khoản</span><b>#${formatNumber(stock.liquidity_rank)}</b></div>
      </div>${signalsHtml}<div class="tooltip-foot">Nhấp để mở phân tích ${esc(stock.symbol)}</div>`;
    moveTooltip(event);
  }

  function moveTooltip(event) {
    const tooltip = $('stockTooltip');
    const pad = 12;
    const rect = tooltip.getBoundingClientRect();
    tooltip.style.left = `${Math.min(event.clientX + 14, window.innerWidth - rect.width - pad)}px`;
    tooltip.style.top = `${Math.min(event.clientY + 14, window.innerHeight - rect.height - pad)}px`;
  }

  function hideTooltip() {
    $('stockTooltip').hidden = true;
  }

  function renderTreemap() {
    if (!state.data || typeof d3 === 'undefined') return;
    const sectors = filteredSectors();
    const svg = d3.select('#treemap');
    svg.selectAll('*').remove();
    $('emptyState').hidden = sectors.length > 0;
    if (!sectors.length) return;

    const stage = $('mapStage');
    const width = Math.max(stage.clientWidth, 320);
    const height = Math.max(stage.clientHeight, 420);
    svg.attr('viewBox', `0 0 ${width} ${height}`);

    const hierarchyData = {
      name: 'MARKET',
      children: sectors.map((sector) => ({
        ...sector,
        children: sector.stocks.map((stock) => ({ ...stock, value: Math.max(Number(stock[state.sizeMetric] || 0), 1) })),
      })),
    };
    const root = d3.hierarchy(hierarchyData)
      .sum((node) => node.value || 0)
      .sort((a, b) => b.value - a.value);
    d3.treemap()
      .size([width, height])
      .tile(d3.treemapSquarify.ratio(1.2))
      .paddingOuter(3)
      .paddingInner(2)
      .paddingTop((node) => node.depth === 1 ? 22 : 0)
      .round(true)(root);

    const sectorGroups = svg.append('g').selectAll('g')
      .data(root.children || [])
      .join('g');
    sectorGroups.append('rect')
      .attr('x', (d) => d.x0)
      .attr('y', (d) => d.y0)
      .attr('width', (d) => Math.max(d.x1 - d.x0, 0))
      .attr('height', (d) => Math.max(d.y1 - d.y0, 0))
      .attr('fill', '#0b1217')
      .attr('stroke', '#2a3942');
    sectorGroups.append('text')
      .attr('class', 'treemap-sector-label')
      .attr('x', (d) => d.x0 + 6)
      .attr('y', (d) => d.y0 + 15)
      .text((d) => {
        const widthAvailable = d.x1 - d.x0;
        const label = `${d.data.name}  ${signed(d.data.avg_change_pct)}`;
        return widthAvailable > 130 ? label : d.data.name;
      })
      .each(function(d) {
        const maxWidth = Math.max(d.x1 - d.x0 - 10, 0);
        const node = d3.select(this);
        let text = node.text();
        while (this.getComputedTextLength() > maxWidth && text.length > 5) {
          text = `${text.slice(0, -2)}…`;
          node.text(text);
        }
      });

    const leaves = svg.append('g').selectAll('g')
      .data(root.leaves())
      .join('g')
      .attr('class', 'treemap-stock')
      .attr('transform', (d) => `translate(${d.x0},${d.y0})`)
      .on('mouseenter', (event, d) => showTooltip(event, d.data))
      .on('mousemove', (event) => moveTooltip(event))
      .on('mouseleave', hideTooltip)
      .on('click', (_, d) => { window.location.href = `/stock/${encodeURIComponent(d.data.symbol)}`; });
    leaves.append('rect')
      .attr('width', (d) => Math.max(d.x1 - d.x0, 0))
      .attr('height', (d) => Math.max(d.y1 - d.y0, 0))
      .attr('fill', (d) => stockColor(d.data));

    leaves.each(function(d) {
      const group = d3.select(this);
      const tileWidth = d.x1 - d.x0;
      const tileHeight = d.y1 - d.y0;

      // Luôn hiển thị đủ 3 ký tự mã cổ phiếu cho mọi ô từ 6x6px trở lên
      if (tileWidth >= 6 && tileHeight >= 6) {
        const showChange = tileWidth >= 26 && tileHeight >= 20;

        const symbolSize = clamp(Math.min(tileWidth / 2.7, tileHeight / (showChange ? 2.0 : 1.25)), 5, 24);
        const strokeW = symbolSize < 9 ? '1px' : (symbolSize < 13 ? '1.5px' : '2.5px');
        const symY = showChange ? (tileHeight / 2 - symbolSize * 0.18) : (tileHeight / 2 + symbolSize * 0.35);

        group.append('text')
          .attr('class', 'treemap-symbol')
          .attr('x', tileWidth / 2)
          .attr('y', symY)
          .style('font-size', `${symbolSize}px`)
          .style('stroke-width', strokeW)
          .text(d.data.symbol);

        if (showChange) {
          const changeSize = clamp(symbolSize * 0.58, 6, 12);
          const changeY = tileHeight / 2 + symbolSize * 0.75 + 1;
          group.append('text')
            .attr('class', 'treemap-change')
            .attr('x', tileWidth / 2)
            .attr('y', changeY)
            .style('font-size', `${changeSize}px`)
            .text(state.colorMode === 'flow' ? `F${formatNumber(d.data.flow_score, 0)}` : signed(d.data.change_pct));
        }
      }
    });
  }

  function renderAll() {
    renderSummary();
    renderFilters();
    renderLegend();
    renderRadar();
    renderTreemap();
    lucide?.createIcons();
  }

  async function loadData(force = false) {
    const refresh = $('refreshButton');
    if (refresh) refresh.classList.add('spinning');
    if (!state.data) $('loadingState').hidden = false;
    try {
      const response = await fetch(`/api/heatmap/data${force ? '?refresh=true' : ''}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!data.quant_snapshot || !Array.isArray(data.sectors)) throw new Error('Payload heatmap thiếu Quant snapshot');
      const snapshotChanged = state.data?.quant_snapshot?.snapshot_id !== data.quant_snapshot.snapshot_id;
      state.data = data;
      if (snapshotChanged) state.aiReport = null;
      $('loadingState').hidden = true;
      renderAll();
    } catch (error) {
      $('loadingState').innerHTML = `<strong class="negative">Không tải được dữ liệu heatmap</strong><span>${esc(error.message)}</span>`;
    } finally {
      refresh.classList.remove('spinning');
    }
  }

  function evidenceItem(label, value) {
    return `<div class="evidence-item"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
  }

  function renderAiReport(report) {
    const evidence = report.evidence || {};
    const flow = report.money_flow_matrix || {};
    const sectors = report.sector_momentum_matrix || [];
    const watchlist = report.radar_watchlist || [];
    const scenarios = report.scenarios || {};
    const guardrail = report.capital_allocation_guardrail || {};
    const histContext = report.historical_context || {};
    const anomalies = report.anomalies || [];
    const trendRead = report.trend_read || '';
    const aiAnomalyNotes = report.ai_anomaly_notes || [];

    const evidenceItem = (label, value) => `<div class="evidence-item"><span>${esc(label)}</span><b>${esc(String(value))}</b></div>`;

    // Trend badge helper
    const trendBadge = (trend, label) => {
      if (!trend || trend === 'KHONG_CO_DU_LIEU') return '';
      const colors = {
        'TANG_NHANH': '#21d79a', 'TANG_CHAM': '#76cfae',
        'GIAM_NHANH': '#ff5368', 'GIAM_CHAM': '#f4b942',
        'ON_DINH': '#9aa5ad'
      };
      const color = colors[trend] || '#9aa5ad';
      const icon = trend.startsWith('TANG') ? '↑' : trend.startsWith('GIAM') ? '↓' : '→';
      return `<span class="trend-badge" style="background:${color}22;color:${color}">${icon} ${esc(label || trend)}</span>`;
    };

    // Render anomalies section
    const anomaliesHtml = anomalies.length > 0 ? `
      <section class="ai-section anomalies-section">
        <h3><i data-lucide="alert-triangle"></i> Cảnh báo bất thường</h3>
        <div class="anomaly-grid">
          ${anomalies.map(a => `<div class="anomaly-card ${a.severity?.toLowerCase() || 'medium'}">
            <strong>${esc(a.title)}</strong>
            <p>${esc(a.detail)}</p>
          </div>`).join('')}
        </div>
        ${aiAnomalyNotes.length > 0 ? `<p class="anomaly-ai-note">${aiAnomalyNotes.map(n => esc(n)).join(' ')}</p>` : ''}
      </section>
    ` : '';

    // Render historical context
    const histHtml = histContext.available ? `
      <section class="ai-section hist-section">
        <h3><i data-lucide="trending-up"></i> Xu hướng 5 ngày</h3>
        <div class="hist-summary">
          ${(() => {
            const mkt = histContext.market_summary || {};
            const tempTrend = mkt.temperature_trend || '';
            const breadthTrend = mkt.breadth_trend || '';
            return `
              <div class="hist-metric">
                <span>Nhiệt</span>
                <b>${formatNumber(mkt.temperature_current, 1)}</b>
                <small>avg ${formatNumber(mkt.temperature_avg_5d, 1)}</small>
                ${trendBadge(tempTrend, mkt.temperature_change > 0 ? `+${formatNumber(mkt.temperature_change, 1)}` : formatNumber(mkt.temperature_change, 1))}
              </div>
              <div class="hist-metric">
                <span>Độ rộng</span>
                <b>${formatNumber(mkt.breadth_current, 1)}%</b>
                <small>avg ${formatNumber(mkt.breadth_avg_5d, 1)}%</small>
                ${trendBadge(breadthTrend, mkt.breadth_change > 0 ? `+${formatNumber(mkt.breadth_change, 1)}%` : `${formatNumber(mkt.breadth_change, 1)}%`)}
              </div>
            `;
          })()}
        </div>
        ${histContext.top_momentum_sectors?.length > 0 ? `
          <div class="hist-sectors">
            <span class="hist-label positive">Momentum tăng:</span>
            ${histContext.top_momentum_sectors.map(s => `${esc(s.sector)} ${trendBadge(s.trend, '')}`).join(' ')}
          </div>
        ` : ''}
        ${histContext.weak_momentum_sectors?.length > 0 ? `
          <div class="hist-sectors">
            <span class="hist-label negative">Momentum yếu:</span>
            ${histContext.weak_momentum_sectors.map(s => `${esc(s.sector)} ${trendBadge(s.trend, '')}`).join(' ')}
          </div>
        ` : ''}
        ${trendRead ? `<p class="hist-insight">${esc(trendRead)}</p>` : ''}
      </section>
    ` : '';

    // Sector cards with trend
    const sectorCards = sectors.map((sector) => {
      const trendBadgeHtml = trendBadge(sector.momentum_trend, sector.trend_label);
      return `<article class="ai-data-card">
        <header>
          <span>${esc(sector.sector)}</span>
          <b style="color:${scoreColor(sector.flow_score)}">${formatNumber(sector.flow_score, 1)} · ${esc(statusLabel(sector.status))}</b>
          ${trendBadgeHtml}
        </header>
        <p>${esc(sector.ai_note)}</p>
        <small>${(sector.key_tickers || []).map(esc).join(' · ')} · rộng ${formatNumber(sector.breadth_pct, 1)}% · GTGD ${formatNumber(sector.liquidity_share_pct, 1)}%</small>
      </article>`;
    }).join('');

    $('aiReport').innerHTML = `
      <section class="ai-hero">
        <div class="ai-temperature"><span>Nhiệt thị trường</span><strong>${formatNumber(report.market_temperature, 1)}</strong><small>${esc(regimeLabel(report.market_regime))}</small></div>
        <div class="ai-thesis"><h3>${esc(report.headline)}</h3><p>${esc(report.market_read)}</p></div>
      </section>
      <section class="evidence-grid">
        ${evidenceItem('Độ rộng tăng', `${formatNumber(evidence.breadth_pct, 1)}%`)}
        ${evidenceItem('Tỷ lệ A/D', formatNumber(evidence.advance_decline_ratio, 2))}
        ${evidenceItem('Mã có GD', `${formatNumber(evidence.active_ratio_pct, 1)}%`)}
        ${evidenceItem('Biến động vốn hóa', signed(evidence.market_cap_weighted_change_pct))}
        ${evidenceItem('Top 10 thanh khoản', `${formatNumber(evidence.top10_liquidity_share_pct, 1)}%`)}
      </section>
      ${anomaliesHtml}
      ${histHtml}
      <section class="ai-section"><h3>Đọc thị trường</h3><div class="read-grid">
        <div class="read-block"><span>Thanh khoản</span><p>${esc(flow.liquidity_concentration)}</p></div>
        <div class="read-block"><span>Độ rộng</span><p>${esc(flow.market_breadth_eval)}</p></div>
      </div><p class="ai-disclaimer">${esc(flow.scope_warning)}</p></section>
      <section class="ai-section"><h3><i data-lucide="layers"></i> Ngành đáng chú ý tuần này</h3><div class="ai-sector-grid improved">
        ${sectorCards}
      </div></section>
      <section class="ai-section"><h3><i data-lucide="target"></i> Mã gợi ý theo dõi</h3><div class="ai-watch-grid improved">
        ${watchlist.map((stock) => `<article class="stock-pick-card ${stock.signal_type || 'neutral'}">
          <header class="pick-header">
            <div class="pick-symbol">
              <strong>${esc(stock.symbol)}</strong>
              <span class="flow-score">F${formatNumber(stock.flow_score, 1)}</span>
            </div>
            <div class="pick-badges">
              <span class="signal-badge ${pctClass(stock.change_pct)}">${signed(stock.change_pct)}</span>
              <span class="pick-tag ${stock.signal_type || 'neutral'}">${esc(stock.signal_label || 'Theo dõi')}</span>
            </div>
          </header>
          <p class="pick-note">${esc(stock.ai_note)}</p>
          <div class="pick-zones">
            ${stock.entry_zone ? `<div class="zone"><span class="zone-label">Entry zone</span><b>${esc(stock.entry_zone)}</b></div>` : ''}
            ${stock.stop_loss ? `<div class="zone"><span class="zone-label">Cắt lỗ</span><b class="negative">${esc(stock.stop_loss)}</b></div>` : ''}
            ${stock.target_price ? `<div class="zone"><div class="zone-label">Mục tiêu</div><b class="positive">${esc(stock.target_price)}</b></div>` : ''}
          </div>
          <footer class="pick-footer" style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <small>${esc(stock.validation_rule || 'Chỉ radar, không phải khuyến nghị')}</small>
            <a href="/backtest?symbol=${encodeURIComponent(stock.symbol)}" style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:4px;background:rgba(16,185,129,0.15);border:1px solid rgba(16,185,129,0.4);color:#35d4a4;font-size:11px;font-weight:700;text-decoration:none;">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 3v18h18"></path>
                <path d="m19 9-5 5-4-4-3 3"></path>
              </svg>
              <span>RSI Backtest</span>
            </a>
          </footer>
        </article>`).join('')}
      </div></section>
      <section class="ai-section"><h3><i data-lucide="git-branch"></i> 3 kịch bản thị trường</h3><div class="scenario-grid improved">
        <article class="scenario-card positive">
          <div class="scenario-icon">📈</div>
          <strong>Lạc quan</strong>
          <p>${esc(scenarios.positive_confirmation)}</p>
          <div class="scenario-action">Hành động: <b>${esc(scenarios.positive_action || 'Có thể tăng tỷ trọng cổ phiếu')}</b></div>
        </article>
        <article class="scenario-card neutral">
          <div class="scenario-icon">➡️</div>
          <strong>Cơ sở</strong>
          <p>${esc(scenarios.base_case)}</p>
          <div class="scenario-action">Hành động: <b>${esc(scenarios.base_action || 'Giữ tỷ trọng hiện tại, chờ tín hiệu rõ')}</b></div>
        </article>
        <article class="scenario-card negative">
          <div class="scenario-icon">📉</div>
          <strong>Thận trọng</strong>
          <p>${esc(scenarios.risk_trigger)}</p>
          <div class="scenario-action">Hành động: <b>${esc(scenarios.risk_action || 'Giảm tỷ trọng, bảo toàn vốn')}</b></div>
        </article>
      </div></section>
      <section class="ai-section"><h3><i data-lucide="shield-check"></i> Hướng dẫn đầu tư cá nhân</h3>
        <div class="guardrail-grid">
          <div class="guardrail-card">
            <span class="guardrail-label">💰 Tỷ trọng cổ phiếu</span>
            <p>${esc(guardrail.reference_equity_band)}</p>
          </div>
          <div class="guardrail-card">
            <span class="guardrail-label">📋 Quy tắc vị thế</span>
            <p>${esc(guardrail.position_rule)}</p>
          </div>
        </div>
        <div class="investor-checklist">
          <h4>✅ Checklist trước khi vào lệnh</h4>
          <ul>
            <li>${esc(guardrail.checklist_1 || 'Đọc kỹ báo cáo và hiểu rõ ngành bạn đang quan tâm')}</li>
            <li>${esc(guardrail.checklist_2 || 'Kiểm tra thanh khoản: khối lượng giao dịch cao hơn trung bình 20 phiên')}</li>
            <li>${esc(guardrail.checklist_3 || 'Xác nhận xu hướng: giá đang trên MA20 hoặc đang pullback về hỗ trợ')}</li>
            <li>${esc(guardrail.checklist_4 || 'Không all-in: chia vốn tối đa 20-30% cho một vị thế')}</li>
            <li>${esc(guardrail.checklist_5 || 'Đặt stop-loss ngay từ đầu, không hold hy vọng')}</li>
          </ul>
        </div>
        ${(report.risk_radar || []).length ? `
        <div class="risk-alerts">
          <h4>⚠️ Cảnh báo cần lưu ý</h4>
          <ul class="risk-list">${report.risk_radar.map((risk) => `<li>${esc(risk)}</li>`).join('')}</ul>
        </div>
        ` : ''}
      </section>
      <p class="ai-disclaimer">${esc(report.disclaimer)} · Snapshot ${esc(report.snapshot_id)} · ${esc(report.ai_engine_source)}</p>`;
    lucide?.createIcons();
    $('aiLoading').hidden = true;
    $('aiError').hidden = true;
    $('aiReport').hidden = false;
  }

  function checkWeeklyAnalysisAvailability() {
    const now = new Date();
    const day = now.getDay(); // 0=Sunday, 6=Saturday
    const hours = now.getHours();
    const minutes = now.getMinutes();
    const currentMinutes = hours * 60 + minutes;

    // VN timezone: if it's Friday (5) and after 15:00 (900 minutes)
    const isFridayAfter15 = day === 5 && currentMinutes >= 900;
    const isWeekend = day === 0 || day === 6;

    return isFridayAfter15 || isWeekend;
  }

  function updateWeeklyButtonState() {
    const btn = $('weeklyAnalysisBtn');
    const hint = $('weeklyHint');
    if (!btn || !hint) return;

    const now = new Date();
    const dayNames = ['CN', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'];
    const dayName = dayNames[now.getDay()];

    if (checkWeeklyAnalysisAvailability()) {
      btn.disabled = false;
      hint.textContent = `Có thể phân tích tuần này (${dayName})`;
    } else {
      btn.disabled = true;
      hint.textContent = 'Chỉ mở sau 15:00 thứ 6 hàng tuần';
    }
  }

  async function runWeeklyAnalysis() {
    const btn = $('weeklyAnalysisBtn');
    const hint = $('weeklyHint');

    btn.classList.add('loading');
    btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg><span>Đang phân tích...</span>';
    hint.textContent = 'Lộc Phát AI đang tổng hợp 5 ngày giao dịch...';

    try {
      const response = await fetch('/api/heatmap/weekly_analysis', {
        method: 'POST',
        headers: { 'X-LP-User-Action': 'weekly_analysis' },
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);

      // Show the weekly report in a special section or replace AI report
      renderWeeklyReport(payload);

      hint.textContent = 'Đã phân tích thành công tuần này';
    } catch (error) {
      hint.textContent = `Lỗi: ${error.message}`;
      btn.classList.remove('loading');
      btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="4" rx="2" ry="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/><path d="m9 16 2 2 4-4"/></svg><span>Phân Tích Tuần</span>';
    }
  }

  function renderWeeklyReport(report) {
    const btn = $('weeklyAnalysisBtn');
    btn.classList.remove('loading');
    btn.disabled = true;
    btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg><span>Đã phân tích</span>';

    // Render weekly report content
    const reportEl = $('aiReport');
    reportEl.hidden = false;

    const weekRange = report.week_range || '';
    const marketChange = report.market_change_pct || 0;
    const changeClass = pctClass(marketChange);

    reportEl.innerHTML = `
      <section class="ai-hero">
        <div class="ai-temperature">
          <span>Tuần giao dịch</span>
          <strong class="${changeClass}">${signed(marketChange)}</strong>
          <small>${esc(weekRange)}</small>
        </div>
        <div class="ai-thesis">
          <h3>${esc(report.headline || 'Báo cáo tuần')}</h3>
          <p>${esc(report.summary || '')}</p>
        </div>
      </section>
      <section class="ai-section">
        <h3>Độ rộng theo ngày</h3>
        <div class="breadth-evolution">
          ${(report.daily_breadth || []).map(day => `
            <div class="breadth-day">
              <span class="breadth-label">${esc(day.day)}</span>
              <div class="breadth-bar-wrap">
                <div class="breadth-bar ${pctClass(day.breadth_pct - 50)}" style="width: ${Math.abs(day.breadth_pct - 50)}%"></div>
              </div>
              <span class="breadth-value ${pctClass(day.breadth_pct - 50)}">${day.breadth_pct.toFixed(0)}%</span>
            </div>
          `).join('')}
        </div>
      </section>
      <section class="ai-section">
        <h3>Top sectors tuần</h3>
        <div class="ai-sector-grid">
          ${(report.top_sectors || []).map(sector => `
            <article class="ai-data-card">
              <header>
                <span>${esc(sector.sector)}</span>
                <b class="${pctClass(sector.change_pct)}">${signed(sector.change_pct)}</b>
              </header>
              <p>${esc(sector.note || '')}</p>
            </article>
          `).join('')}
        </div>
      </section>
      <section class="ai-section">
        <h3>Dòng tiền tuần</h3>
        <div class="read-grid">
          <div class="read-block">
            <span>Xu hướng dòng tiền</span>
            <p>${esc(report.money_flow_trend || 'Không có dữ liệu')}</p>
          </div>
          <div class="read-block">
            <span>Đánh giá</span>
            <p>${esc(report.weekly_verdict || '')}</p>
          </div>
        </div>
      </section>
      <section class="ai-section">
        <h3>Rủi ro & Cơ hội tuần</h3>
        <div class="scenario-grid">
          <article class="scenario-card"><strong class="positive">Cơ hội</strong><p>${esc(report.opportunities || 'Không có')}</p></article>
          <article class="scenario-card"><strong class="negative">Rủi ro</strong><p>${esc(report.risks || 'Không có')}</p></article>
        </div>
      </section>
    `;
  }

  async function openAiReport() {
    const dialog = $('aiDialog');
    dialog.showModal();
    if (state.aiReport) {
      renderAiReport(state.aiReport);
      return;
    }
    $('aiLoading').hidden = false;
    $('aiReport').hidden = true;
    $('aiError').hidden = true;
    try {
      const response = await fetch('/api/heatmap/ai_insight', {
        method: 'POST',
        headers: { 'X-LP-User-Action': 'deepseek' },
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      state.aiReport = payload;
      renderAiReport(payload);
    } catch (error) {
      $('aiLoading').hidden = true;
      $('aiError').hidden = false;
      $('aiError').textContent = error.message.includes('DEEPSEEK_API_KEY')
        ? 'Lộc Phát AI chưa được kích hoạt trên máy chủ. Vui lòng kiểm tra cấu hình trong Environment của Render rồi khởi động lại dịch vụ.'
        : `Không thể tạo báo cáo AI: ${error.message}`;
    }
  }

  function bindEvents() {
    document.querySelectorAll('#colorMode button').forEach((button) => button.addEventListener('click', () => {
      document.querySelectorAll('#colorMode button').forEach((item) => item.classList.toggle('active', item === button));
      state.colorMode = button.dataset.mode;
      renderLegend();
      renderTreemap();
    }));
    $('sizeMetric').addEventListener('change', (event) => { state.sizeMetric = event.target.value; renderTreemap(); });
    $('exchangeFilter').addEventListener('change', (event) => { state.exchange = event.target.value; renderTreemap(); });
    $('sectorFilter').addEventListener('change', (event) => { state.sector = event.target.value; renderTreemap(); });
    $('activeOnly').addEventListener('change', (event) => { state.activeOnly = event.target.checked; renderTreemap(); });
    $('refreshButton').addEventListener('click', () => loadData(true));
    $('resetFilters').addEventListener('click', () => {
      state.colorMode = 'performance'; state.sizeMetric = 'market_cap'; state.exchange = 'ALL'; state.sector = 'ALL'; state.activeOnly = false; state.query = '';
      $('sizeMetric').value = state.sizeMetric; $('exchangeFilter').value = state.exchange; $('sectorFilter').value = state.sector; $('activeOnly').checked = false; $('symbolSearch').value = '';
      document.querySelectorAll('#colorMode button').forEach((button) => button.classList.toggle('active', button.dataset.mode === 'performance'));
      renderLegend(); renderTreemap();
    });
    document.querySelectorAll('.radar-tabs button').forEach((button) => button.addEventListener('click', () => {
      document.querySelectorAll('.radar-tabs button').forEach((item) => item.classList.toggle('active', item === button));
      $('sectorRadar').hidden = button.dataset.tab !== 'sectors';
      $('stockRadar').hidden = button.dataset.tab !== 'watchlist';
    }));
    $('aiButton').addEventListener('click', openAiReport);
    document.querySelectorAll('[data-lp-heatmap-ai]').forEach((button) => {
      button.addEventListener('click', () => {
        if (window.LPGlobalSearch && typeof window.LPGlobalSearch.closeMobileNav === 'function') {
          window.LPGlobalSearch.closeMobileNav();
        }
        openAiReport();
      });
    });
    $('closeAiDialog').addEventListener('click', () => $('aiDialog').close());
    $('methodButton').addEventListener('click', () => $('methodDialog').showModal());
    $('closeMethodDialog').addEventListener('click', () => $('methodDialog').close());
    $('weeklyAnalysisBtn').addEventListener('click', runWeeklyAnalysis);

    // Update weekly button state when AI dialog opens
    const originalShowModal = HTMLDialogElement.prototype.showModal;
    HTMLDialogElement.prototype.showModal = function() {
      originalShowModal.call(this);
      if (this.id === 'aiDialog') {
        updateWeeklyButtonState();
      }
    };

    function updateFullscreenButtonState(isFS) {
      const btn = $('fullscreenToggle');
      if (!btn) return;
      if (isFS) {
        btn.innerHTML = `<i data-lucide="minimize-2"></i><span>Thu nhỏ</span>`;
      } else {
        btn.innerHTML = `<i data-lucide="maximize-2"></i><span>Toàn màn hình</span>`;
      }
      lucide?.createIcons();
    }

    function toggleFullscreen() {
      const mapPanel = $('mapPanel');
      if (!mapPanel) return;
      const isFS = !!(document.fullscreenElement || mapPanel.classList.contains('is-fullscreen'));

      if (isFS) {
        if (document.exitFullscreen && document.fullscreenElement) {
          document.exitFullscreen().catch(() => {});
        }
        mapPanel.classList.remove('is-fullscreen');
        updateFullscreenButtonState(false);
      } else {
        if (mapPanel.requestFullscreen) {
          mapPanel.requestFullscreen().then(() => {
            mapPanel.classList.add('is-fullscreen');
          }).catch(() => {
            mapPanel.classList.add('is-fullscreen');
          });
        } else {
          mapPanel.classList.add('is-fullscreen');
        }
        updateFullscreenButtonState(true);
      }
      setTimeout(renderTreemap, 100);
    }

    function handleFullscreenChange() {
      const mapPanel = $('mapPanel');
      if (!mapPanel) return;
      const isFS = !!(document.fullscreenElement || document.webkitFullscreenElement);
      if (!isFS) {
        mapPanel.classList.remove('is-fullscreen');
        updateFullscreenButtonState(false);
        setTimeout(renderTreemap, 100);
      }
    }

    $('fullscreenToggle')?.addEventListener('click', toggleFullscreen);
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    document.addEventListener('webkitfullscreenchange', handleFullscreenChange);

    let resizeTimer;
    window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderTreemap, 120); });
    if ('ResizeObserver' in window) {
      new ResizeObserver(() => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(renderTreemap, 80);
      }).observe($('mapStage'));
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    lucide?.createIcons();
    loadData();
    window.setInterval(() => {
      if (!document.hidden) loadData();
    }, 5000);
  });
})();
