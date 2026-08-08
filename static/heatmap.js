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
    timeline: {
      snapshots: [],
      cursor: 0,
      isLive: true,
      isPlaying: false,
      speed: 1,
      baseIntervalMs: 250,
      lastSnapshotTime: null,
      liveTimer: null,
      playTimer: null,
    },
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
  const formatOptional = (value, digits = 0) => value === null || value === undefined
    ? '--'
    : formatNumber(value, digits);
  const concentrationLabel = (value) => ({
    LAN_TOA: 'Lan tỏa', CAN_BANG: 'Cân bằng', TAP_TRUNG: 'Tập trung',
    RAT_TAP_TRUNG: 'Rất tập trung', KHONG_DU_DU_LIEU: 'Chưa đủ dữ liệu',
  }[value] || 'Chưa xác định');
  const confidenceLabel = (value) => ({ CAO: 'cao', VUA: 'vừa', THAP: 'thấp' }[value] || 'chưa rõ');
  const formatMoney = (value) => {
    const amount = Number(value || 0);
    if (amount >= 1e15) return `${formatNumber(amount / 1e15, 2)} triệu tỷ`;
    // Market-wide liquidity (Vĩ mô card): 3 decimals so traders see e.g.
    // "19.362,054 tỷ" or "19,362.054 nghìn tỷ" instead of rounded "19,362.0".
    if (amount >= 1e12) return `${formatNumber(amount / 1e12, 3)} nghìn tỷ`;
    if (amount >= 1e9) return `${formatNumber(amount / 1e9, 3)} tỷ`;
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
    $('regimeValue').textContent = `${regimeLabel(quant.market_regime)} · tin cậy ${confidenceLabel(quant.heat_confidence)}`;
    $('breadthValue').textContent = quant.breadth_available === false ? '--' : `${formatOptional(quant.breadth_pct, 1)}%`;
    const adValue = quant.advance_decline_state === 'NO_DECLINES'
      ? '∞'
      : quant.advance_decline_state === 'NO_DIRECTIONAL_ISSUES'
        ? '--'
        : formatOptional(quant.advance_decline_ratio, 2);
    $('advanceDeclineValue').textContent = `A/D ${adValue} · ${formatOptional(quant.advance_share_active_pct, 1)}% mã GD tăng · ${formatOptional(quant.directional_participation_pct, 1)}% có hướng`;
    $('liquidityValue').textContent = formatMoney(summary.total_trading_value);
    $('activeRatioValue').textContent = `${formatNumber(quant.active_ratio_pct, 1)}% mã có giao dịch`;
    $('concentrationValue').textContent = quant.top10_liquidity_share_pct === null || quant.top10_liquidity_share_pct === undefined
      ? '--'
      : `${formatNumber(quant.top10_liquidity_share_pct, 1)}%`;
    $('concentrationDetail').textContent = `Top 10 khớp lệnh · hiệu dụng ${formatOptional(quant.effective_stock_count, 1)} mã · ${concentrationLabel(quant.concentration_state)}`;
    $('concentrationDetail').title = `Top 5 ${formatOptional(quant.top5_liquidity_share_pct, 1)}% · Top 20 ${formatOptional(quant.top20_liquidity_share_pct, 1)}% · HHI ${formatOptional(quant.liquidity_hhi, 4)}`;
    $('ceilingCount').textContent = formatNumber(summary.ceilings);
    $('advanceCount').textContent = formatNumber(summary.advances);
    $('flatCount').textContent = formatNumber(summary.unchanged);
    $('declineCount').textContent = formatNumber(summary.declines);
    $('floorCount').textContent = formatNumber(summary.floors);
    $('inactiveCount').textContent = formatNumber(summary.inactive_count);

    const phase = data.market_session?.phase;
    const live = Boolean(data.market_session?.is_live_matching);
    const closed = Boolean(data.market_closed || ['CLOSED', 'WEEKEND', 'HOLIDAY', 'PRE_OPEN'].includes(phase));
    $('statusDot').className = `status-dot ${live ? 'live' : closed ? 'closed' : ''}`;
    $('marketStatus').textContent = sessionLabel(phase);
    const tradeDate = data.data_lineage?.latest_trading_date || 'không rõ ngày';
    const storageLabel = data.snapshot_frozen ? 'snapshot DB cuối phiên' : 'dữ liệu thị trường';
    $('sessionTimestamp').textContent = `Phiên ${tradeDate} · ${storageLabel} · tải lúc ${data.timestamp || '--'}`;
    if ($('lineageText')) $('lineageText').textContent = `${data.data_lineage?.price_source || 'Nguồn bảng giá'} · ICB 4 cấp`;
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
      const stabilityHtml = sector.directional_participation_pct !== undefined
        ? `<span title="Tỷ lệ mã có giao dịch đang tăng hoặc giảm">Có hướng ${formatNumber(sector.directional_participation_pct, 0)}%</span>`
        : '';
      const concHtml = secSignals.effective_stock_count !== undefined
        ? `<span title="Số mã thanh khoản hiệu dụng trong ngành">Hiệu dụng ${formatOptional(secSignals.effective_stock_count, 1)} mã</span>`
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
    if (!tooltip) return;
    tooltip.hidden = false;
    tooltip.style.display = 'block';
    
    // Extract signals if available
    const signals = stock._signals || {};
    const signalsHtml = Object.keys(signals).length > 0
      ? `<div class="tooltip-signals">
          <span title="Tín hiệu giá chuẩn hóa">Giá: <b>${signals.price_signal?.toFixed(2) || '--'}</b></span>
          <span title="Khớp khối lượng với giá">KL-Giá: <b>${signals.vol_align?.toFixed(2) || '--'}</b></span>
          <span title="Vị trí trong floor-ceiling">Vị trí: <b>${signals.position_signal?.toFixed(2) || '--'}</b></span>
        </div>`
      : '';
    
    tooltip.innerHTML = `
      <div class="tooltip-sector-category">${esc(stock.sector || 'CỔ PHIẾU')}</div>
      <div class="tooltip-head">
        <div>
          <strong class="tooltip-symbol-text">${esc(stock.symbol)}</strong>
          <div class="tooltip-name">${esc(stock.name)}</div>
        </div>
        <div class="tooltip-badge-wrap">
          <span class="tooltip-exchange">${esc(stock.exchange)}</span>
          <b class="tooltip-pct-badge ${pctClass(stock.change_pct)}">${signed(stock.change_pct)}</b>
        </div>
      </div>
      <div class="tooltip-grid">
        <div><span>Giá gần nhất</span><b>${formatNumber(stock.price_vnd)} đ</b></div>
        <div><span>Biến động</span><b class="${pctClass(stock.change_pct)}">${signed(stock.change_pct)}</b></div>
        <div><span>Giá trị GD</span><b>${formatMoney(stock.trading_value)}</b></div>
        <div><span>Vốn hóa</span><b>${formatMoney(stock.market_cap)}</b></div>
        <div><span>Điểm dòng tiền</span><b style="color:${scoreColor(stock.flow_score)}">${formatNumber(stock.flow_score, 1)}</b></div>
        <div><span>Hạng thanh khoản</span><b>#${formatNumber(stock.liquidity_rank)}</b></div>
      </div>${signalsHtml}<div class="tooltip-foot">Nhấp để mở phân tích ${esc(stock.symbol)} →</div>`;
    moveTooltip(event);
  }

  function moveTooltip(event) {
    const tooltip = $('stockTooltip');
    if (!tooltip) return;
    const pad = 12;
    const rect = tooltip.getBoundingClientRect();
    tooltip.style.left = `${Math.min(event.clientX + 14, window.innerWidth - rect.width - pad)}px`;
    tooltip.style.top = `${Math.min(event.clientY + 14, window.innerHeight - rect.height - pad)}px`;
  }

  function hideTooltip() {
    const tooltip = $('stockTooltip');
    if (tooltip) {
      tooltip.hidden = true;
      tooltip.style.display = 'none';
    }
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
      .paddingTop((node) => node.depth === 1 ? 20 : 0)
      .round(false)(root);

    const sectorGroups = svg.append('g').selectAll('g')
      .data(root.children || [])
      .join('g');
    sectorGroups.append('rect')
      .attr('x', (d) => d.x0)
      .attr('y', (d) => d.y0)
      .attr('width', (d) => Math.max(d.x1 - d.x0, 0))
      .attr('height', (d) => Math.max(d.y1 - d.y0, 0))
      .attr('class', 'treemap-sector-bg')
      .style('fill', 'var(--heatmap-sector-bg, #0b1217)')
      .style('stroke', 'var(--heatmap-sector-stroke, #2a3942)');
    
    // Header strip for sectors (matching Finviz layout)
    sectorGroups.append('rect')
      .attr('class', 'treemap-sector-header')
      .attr('x', (d) => d.x0)
      .attr('y', (d) => d.y0)
      .attr('width', (d) => Math.max(d.x1 - d.x0, 0))
      .attr('height', (d) => (d.x1 - d.x0) >= 60 ? 20 : 0)
      .style('fill', 'var(--heatmap-header-bg, #141e26)')
      .style('stroke', 'none');

    sectorGroups.append('text')
      .attr('class', 'treemap-sector-label')
      .attr('x', (d) => d.x0 + 6)
      .attr('y', (d) => d.y0 + 13)
      .attr('dy', '0.05em')
      .text((d) => {
        const widthAvailable = d.x1 - d.x0;
        if (widthAvailable < 60) return '';
        return `${d.data.name} ${signed(d.data.avg_change_pct)}`;
      })
      .each(function(d) {
        const maxWidth = Math.max(d.x1 - d.x0 - 10, 0);
        const node = d3.select(this);
        if (maxWidth <= 0) { node.text(''); return; }
        let text = node.text();
        try {
          if (this.getComputedTextLength() > maxWidth && text.length > 6) {
            const half = Math.floor((maxWidth / this.getComputedTextLength()) * text.length) - 1;
            if (half > 2 && half < text.length - 2) {
              text = `${text.slice(0, half - 1)}…${text.slice(-(text.length - half - 1))}`;
              node.text(text);
            }
          }
          while (text.length > 3 && this.getComputedTextLength() > maxWidth) {
            text = `${text.slice(0, -2)}…`;
            node.text(text);
          }
        } catch (_) {}
      });

    svg.on('mouseleave', hideTooltip);
    d3.select('#mapStage').on('mouseleave', hideTooltip);

    const leaves = svg.append('g').selectAll('g')
      .data(root.leaves())
      .join('g')
      .attr('class', 'treemap-stock')
      .attr('transform', (d) => `translate(${d.x0},${d.y0})`)
      .on('mouseenter', function (event, d) {
        showTooltip(event, d.data);
        d3.select(this).select('rect')
          .transition().duration(60)
          .style('stroke', 'var(--heatmap-node-hover, #ffffff)')
          .style('stroke-width', '2px');
      })
      .on('mousemove', (event) => moveTooltip(event))
      .on('mouseleave', function (event, d) {
        hideTooltip();
        d3.select(this).select('rect')
          .transition().duration(60)
          .style('stroke', 'var(--heatmap-node-stroke, rgba(255,255,255,.14))')
          .style('stroke-width', '1px');
      })
      .on('click', function (event, d) {
        const isTouch = event.pointerType === 'touch' || (window.matchMedia && window.matchMedia('(hover: none)').matches);
        const pinned = this.classList.contains('pinned');
        if (isTouch) {
          event.preventDefault();
          d3.selectAll('.treemap-stock.pinned').each(function () {
            this.classList.remove('pinned');
          });
          if (!pinned) {
            this.classList.add('pinned');
            showTooltip(event, d.data);
          } else {
            hideTooltip();
          }
          return;
        }
        window.location.href = `/stock/${encodeURIComponent(d.data.symbol)}`;
      });

    leaves.append('rect')
      .attr('width', (d) => Math.max(d.x1 - d.x0, 0))
      .attr('height', (d) => Math.max(d.y1 - d.y0, 0))
      .attr('fill', (d) => stockColor(d.data));

    leaves.each(function(d) {
      const group = d3.select(this);
      const tileWidth = d.x1 - d.x0;
      const tileHeight = d.y1 - d.y0;

      const symbol = String(d.data.symbol || '').trim();
      const changeText = state.colorMode === 'flow'
        ? `F${formatNumber(d.data.flow_score, 0)}`
        : signed(d.data.change_pct);

      // Precise character fitting estimation:
      // Uppercase 3-char symbol width: ~ (symbol.length * 0.65 * font_size)
      // Change string (e.g. "+0.31%", "-10.45%"): ~ (changeText.length * 0.62 * font_size)

      let fitsBoth = false;
      let symbolSize = 0;
      let changeSize = 0;

      // Tier 1: Fits Both Symbol (primary, max 17px) & % Change (secondary, max 12px) stacked
      if (tileWidth >= 46 && tileHeight >= 32) {
        const maxSymByWidth = (tileWidth - 8) / (symbol.length * 0.65);
        const maxSymByHeight = (tileHeight - 8) * 0.38;
        const testSymSize = clamp(Math.min(maxSymByWidth, maxSymByHeight), 10, 17);

        const maxChgByWidth = (tileWidth - 8) / (changeText.length * 0.62);
        const maxChgByHeight = (tileHeight - 8) * 0.28;
        const testChgSize = clamp(Math.min(maxChgByWidth, maxChgByHeight), 8.5, 12);

        const symWidthEst = symbol.length * 0.65 * testSymSize;
        const chgWidthEst = changeText.length * 0.62 * testChgSize;

        if (symWidthEst <= (tileWidth - 6) && chgWidthEst <= (tileWidth - 6)) {
          fitsBoth = true;
          symbolSize = testSymSize;
          changeSize = testChgSize;
        }
      }

      // Tier 2: Fits Symbol ONLY in center (max 13px)
      let fitsSymOnly = false;
      if (!fitsBoth && tileWidth >= 22 && tileHeight >= 15) {
        const maxSymByWidth = (tileWidth - 6) / (symbol.length * 0.65);
        const maxSymByHeight = (tileHeight - 4) * 0.50;
        const testSymSize = clamp(Math.min(maxSymByWidth, maxSymByHeight), 8.5, 13);

        const symWidthEst = symbol.length * 0.65 * testSymSize;
        if (symWidthEst <= (tileWidth - 4)) {
          fitsSymOnly = true;
          symbolSize = testSymSize;
          changeSize = clamp(testSymSize * 0.82, 7, 11);
        }
      }

      let defaultSymbolOpacity = 0;
      let defaultChangeOpacity = 0;

      if (fitsBoth) {
        defaultSymbolOpacity = 1;
        defaultChangeOpacity = 1;
      } else if (fitsSymOnly) {
        defaultSymbolOpacity = 1;
        defaultChangeOpacity = 0;
      } else {
        // Tier 3: Micro / Tiny tile -> Text hidden
        defaultSymbolOpacity = 0;
        defaultChangeOpacity = 0;
      }

      d._defaultSymbolOpacity = defaultSymbolOpacity;
      d._defaultChangeOpacity = defaultChangeOpacity;

      if (tileWidth >= 6 && tileHeight >= 6) {
        const cy = tileHeight / 2;
        const cx = tileWidth / 2;

        let symY, changeY;

        if (fitsBoth) {
          symY    = cy - changeSize * 0.35;
          changeY = cy + changeSize * 0.9;
        } else {
          symY    = cy + symbolSize * 0.35;
          changeY = cy + changeSize * 0.85;
        }

        const strokeW = symbolSize < 10 ? '1px' : (symbolSize < 14 ? '1.5px' : '2px');

        // Ticker Symbol (Primary Label — 3 characters)
        group.append('text')
          .attr('class', 'treemap-symbol')
          .attr('x', cx)
          .attr('y', symY)
          .style('font-size', `${symbolSize}px`)
          .style('stroke-width', strokeW)
          .style('opacity', defaultSymbolOpacity)
          .text(symbol);

        // % Change (Secondary Label — only rendered when fitsBoth)
        if (fitsBoth) {
          group.append('text')
            .attr('class', 'treemap-change')
            .attr('x', cx)
            .attr('y', changeY)
            .style('font-size', `${changeSize}px`)
            .style('opacity', defaultChangeOpacity)
            .text(changeText);
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
      // Timeline bootstrapping — first time only. Subsequent refreshes
      // reuse the loaded checkpoints; the live tail poll picks up new ones.
      if (!state.timeline.snapshots.length) {
        loadTimeline().catch((err) => console.warn('Timeline initial load failed:', err));
      }
    } catch (error) {
      $('loadingState').innerHTML = `<strong class="negative">Không tải được dữ liệu heatmap</strong><span>${esc(error.message)}</span>`;
    } finally {
      refresh.classList.remove('spinning');
    }
  }

  // ============================================================================
  // MARKET TIMELINE SCRUBBER (Market Radar 4.0)
  // --------------------------------------------------------------------------
  // Bottom-of-page interactive bar. The backend poller writes
  // `heatmap_intraday_snapshots` rows every 1m (ATO/ATC) / 5m (continuous) /
  // 15m (lunch). This file only reads them via /api/heatmap/timeline and
  // re-renders the existing treemap + radar against the chosen checkpoint.
  // ============================================================================
  const PHASE_LABEL = {
    ATO: 'ATO',
    CONTINUOUS: 'Liên tục',
    ATC: 'ATC',
    LUNCH_BREAK: 'Nghỉ trưa',
    POST_CLOSE_TRADING: 'Sau giờ',
    CLOSED: 'Đóng cửa',
    PRE_OPEN: 'Chờ mở',
  };

  function phaseClockBounds(phase) {
    if (phase === 'ATO') return { start: '09:00', end: '09:15' };
    if (phase === 'CONTINUOUS') return { start: '09:15', end: '14:30' };
    if (phase === 'ATC') return { start: '14:30', end: '14:45' };
    if (phase === 'LUNCH_BREAK') return { start: '11:30', end: '13:00' };
    if (phase === 'POST_CLOSE_TRADING') return { start: '14:45', end: '15:00' };
    return { start: '--:--', end: '--:--' };
  }

  function timelineCount() {
    return state.timeline.snapshots.length;
  }

  function liveCursor() {
    const total = timelineCount();
    return Math.max(0, total - 1);
  }

  function isSessionLive() {
    const session = state.data?.market_session;
    if (!session) return false;
    return Boolean(session.is_live_matching) || ['ATO', 'ATC', 'CONTINUOUS', 'POST_CLOSE_TRADING', 'LUNCH_BREAK'].includes(session.phase);
  }

  function setTimelineCursor(index, options = {}) {
    const total = timelineCount();
    if (!total) {
      updateTimelineChrome();
      return;
    }
    const cursor = clamp(index, 0, total - 1);
    state.timeline.cursor = cursor;
    const slider = $('timelineSlider');
    if (slider) slider.value = String(cursor);
    // Scrubbing always takes the user out of live-tracking. The "Về Live"
    // button (or auto-promotion when the cursor reaches the end during a
    // live session) re-enables it.
    state.timeline.isLive = options.live === true;
    $('timelineLive')?.toggleAttribute('hidden', state.timeline.isLive);
    renderAtCursor();
  }

  function updateTimelineChrome() {
    const total = timelineCount();
    const cursor = state.timeline.cursor;
    const slider = $('timelineSlider');
    if (slider) {
      slider.min = '0';
      slider.max = String(Math.max(total - 1, 0));
      slider.value = String(clamp(cursor, 0, Math.max(total - 1, 0)));
      slider.disabled = total <= 1;
    }
    $('timelineCounter').textContent = `${Math.min(cursor + 1, total)} / ${total}`;
    // Disable play/step controls when there is nothing to play — this gives
    // users a clear "the timeline is empty" signal rather than a button that
    // silently no-ops on click.
    const playDisabled = total <= 1;
    $('timelinePlay')?.toggleAttribute('disabled', playDisabled);
    $('timelineStepBack')?.toggleAttribute('disabled', playDisabled);
    $('timelineStepForward')?.toggleAttribute('disabled', playDisabled);
    if (!total) {
      $('timelinePhase').textContent = '—';
      $('timelinePhase').dataset.phase = 'PRE_OPEN';
      $('timelineClock').textContent = '--:--';
      $('timelineCursor').style.left = '0%';
      $('timelineStatus').textContent = 'Chưa có dữ liệu intraday. Poller sẽ ghi sau khi thị trường mở cửa.';
      return;
    }
    const item = state.timeline.snapshots[cursor];
    if (!item) return;
    $('timelinePhase').textContent = PHASE_LABEL[item.session_phase] || item.session_phase;
    $('timelinePhase').dataset.phase = item.session_phase;
    const timeText = String(item.snapshot_time || '').slice(11, 16) || '--:--';
    $('timelineClock').textContent = timeText;
    $('timelineCursor').style.left = `${(cursor / Math.max(total - 1, 1)) * 100}%`;
    const isLive = state.timeline.isLive;
    $('timelineStatus').textContent = isLive
      ? `Live · cập nhật mỗi 6 giây (${total} snapshot)`
      : `Đang tua lại @ ${timeText}`;
  }

  function renderAtCursor() {
    const total = timelineCount();
    if (!total) {
      updateTimelineChrome();
      return;
    }
    const cursor = clamp(state.timeline.cursor, 0, total - 1);
    const item = state.timeline.snapshots[cursor];
    updateTimelineChrome();
    if (!item || !item.payload) return;
    // Merge the timeline summary back into `state.data` so all existing
    // renderers (treemap, radar, summary grid) work without modification.
    // Per-stock rows are absent in the summarized payload, but the treemap
    // gracefully degrades to "no stocks" tiles — that's expected when
    // scrubbing because we never want to ship hundreds of KB per tick.
    if (cursor === liveCursor() && isSessionLive()) {
      // We're parked on the latest checkpoint during a live session: leave
      // `state.data` pointing at the live /api/heatmap/data payload so
      // per-stock tooltips keep working.
      if (!state.timeline.isLive) state.timeline.isLive = true;
    }
    if (!state.timeline.isLive) {
      const synthetic = {
        ...state.data,
        sectors: (item.payload.sectors || []).map((sec) => ({
          ...sec,
          // No per-stock rows in the summary — give the treemap an empty
          // bucket so it shows the sector label without trying to lay out
          // tiles. The radar panel still renders the per-sector flow
          // score / breadth / change %, which is the scrubber's purpose.
          stocks: sec.stocks || [],
        })),
        quant_snapshot: item.payload.quant_snapshot || state.data.quant_snapshot,
        summary: item.payload.summary || state.data.summary,
        market_session: item.payload.market_session || state.data.market_session,
        timestamp: item.payload.timestamp || state.data.timestamp,
        data_lineage: item.payload.data_lineage || state.data.data_lineage,
        snapshot_frozen: Boolean(item.payload.snapshot_frozen),
        served_from: 'TIMELINE_SCRUB',
        is_market_open: Boolean(item.payload.is_market_open),
        market_closed: Boolean(item.payload.market_closed),
      };
      state.data = synthetic;
      renderAll();
    } else {
      $('timelineLive')?.toggleAttribute('hidden', true);
    }
  }

  async function loadTimeline(dateOverride) {
    const date = dateOverride || new Date().toISOString().slice(0, 10);
    $('timelineStatus').textContent = 'Đang tải timeline…';
    try {
      const response = await fetch(`/api/heatmap/timeline?date=${encodeURIComponent(date)}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const items = Array.isArray(data.items) ? data.items : [];
      const previousLast = state.timeline.snapshots[state.timeline.snapshots.length - 1]?.snapshot_time || null;
      state.timeline.snapshots = items;
      state.timeline.lastSnapshotTime = items[items.length - 1]?.snapshot_time || null;
      // Pick the cursor: live sessions auto-park on the freshest snapshot;
      // closed sessions land on the last checkpoint (typically ATC).
      if (state.timeline.isLive || items.length === 0) {
        state.timeline.cursor = liveCursor();
      } else {
        state.timeline.cursor = clamp(state.timeline.cursor, 0, Math.max(items.length - 1, 0));
      }
      // Build hour ticks once we know the span. We mark the "major" ticks at
      // each phase boundary (9:00 / 9:15 / 11:30 / 13:00 / 14:30 / 14:45).
      renderTimelineTicks();
      renderAtCursor();
      // If we're live and the tail advanced, briefly flash the status.
      const newLast = state.timeline.lastSnapshotTime;
      if (newLast && previousLast && newLast !== previousLast && state.timeline.isLive) {
        $('timelineStatus').textContent = `Live · snapshot mới ${newLast.slice(11, 16)}`;
      }
      if (!state.timeline.liveTimer && state.timeline.isLive) startLiveTail();
    } catch (error) {
      $('timelineStatus').textContent = `Lỗi tải timeline: ${error.message}`;
      console.warn('loadTimeline failed:', error);
    }
  }

  function renderTimelineTicks() {
    const ticks = $('timelineTicks');
    if (!ticks) return;
    const boundaries = [
      { time: '09:00', label: '09:00', major: true },
      { time: '09:15', label: '09:15', major: true },
      { time: '11:30', label: '11:30', major: false },
      { time: '13:00', label: '13:00', major: false },
      { time: '14:30', label: '14:30', major: true },
      { time: '14:45', label: '14:45', major: true },
    ];
    const minutesInDay = 24 * 60;
    const startMin = 9 * 60;       // 09:00
    const endMin = 14 * 60 + 45;   // 14:45
    const span = endMin - startMin;
    ticks.innerHTML = boundaries.map(({ time, label, major }) => {
      const [hh, mm] = time.split(':').map(Number);
      const minutesFromStart = hh * 60 + mm - startMin;
      const pct = clamp((minutesFromStart / span) * 100, 0, 100);
      return `<span class="timeline-tick${major ? ' major' : ''}" style="left:${pct}%"></span>` +
             `<span class="timeline-tick-label" style="left:${pct}%">${label}</span>`;
    }).join('');
    // Suppress unused-variable lint warning for minutesInDay without changing behaviour.
    void minutesInDay;
  }

  function attachTimelineEvents() {
    const slider = $('timelineSlider');
    if (!slider || slider.dataset.bound === '1') return;
    slider.dataset.bound = '1';
    slider.addEventListener('input', (event) => {
      const value = Number(event.target.value);
      setTimelineCursor(value, { live: false });
    });
    $('timelinePlay')?.addEventListener('click', togglePlay);
    $('timelineStepBack')?.addEventListener('click', () => {
      stopPlay();
      setTimelineCursor(state.timeline.cursor - 1, { live: false });
    });
    $('timelineStepForward')?.addEventListener('click', () => {
      stopPlay();
      setTimelineCursor(state.timeline.cursor + 1, { live: false });
    });
    $('timelineLive')?.addEventListener('click', () => {
      stopPlay();
      const total = timelineCount();
      if (total > 0) {
        state.timeline.isLive = true;
        setTimelineCursor(liveCursor(), { live: true });
        // Refresh state.data from the canonical /api/heatmap/data endpoint
        // so per-stock tooltips are populated again.
        loadData(false);
      }
    });
    document.querySelectorAll('.timeline-speed button').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.timeline-speed button').forEach((b) => b.classList.toggle('active', b === btn));
        state.timeline.speed = Number(btn.dataset.speed || 1);
        if (state.timeline.isPlaying) startPlay();
      });
    });
    // Keyboard shortcuts: Space toggles play, ← / → step, L jumps to live.
    document.addEventListener('keydown', (event) => {
      const tag = (event.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (event.code === 'Space') {
        event.preventDefault();
        togglePlay();
      } else if (event.code === 'ArrowLeft') {
        stopPlay();
        setTimelineCursor(state.timeline.cursor - 1, { live: false });
      } else if (event.code === 'ArrowRight') {
        stopPlay();
        setTimelineCursor(state.timeline.cursor + 1, { live: false });
      } else if (event.key === 'l' || event.key === 'L') {
        stopPlay();
        state.timeline.isLive = true;
        setTimelineCursor(liveCursor(), { live: true });
        loadData(false);
      }
    });
    // Click a segment to jump to its first snapshot.
    document.querySelectorAll('.timeline-segment').forEach((seg) => {
      seg.addEventListener('click', () => {
        const phase = seg.dataset.phase;
        const idx = state.timeline.snapshots.findIndex((it) => it.session_phase === phase);
        if (idx >= 0) {
          stopPlay();
          setTimelineCursor(idx, { live: false });
        }
      });
    });
    $('timelineBar')?.classList.toggle('is-playing', false);
  }

  function togglePlay() {
    if (state.timeline.isPlaying) {
      stopPlay();
    } else {
      startPlay();
    }
  }

  function startPlay() {
    const total = timelineCount();
    if (total <= 1) return;
    if (state.timeline.cursor >= liveCursor()) {
      // Replaying at the end — restart from 0 unless user prefers to stay.
      // We restart so users see the day unfold instead of just sitting still.
      setTimelineCursor(0, { live: false });
    }
    state.timeline.isPlaying = true;
    $('timelineBar')?.classList.add('is-playing');
    const playBtn = $('timelinePlay');
    if (playBtn) {
      playBtn.innerHTML = '<i data-lucide="pause"></i><span class="timeline-control-label">Tạm dừng</span>';
      lucide?.createIcons();
    }
    stopPlayTimer();
    const tick = () => {
      const next = state.timeline.cursor + 1;
      if (next > liveCursor()) {
        stopPlay();
        return;
      }
      setTimelineCursor(next, { live: false });
    };
    const intervalMs = Math.max(40, state.timeline.baseIntervalMs / state.timeline.speed);
    state.timeline.playTimer = setInterval(tick, intervalMs);
  }

  function stopPlayTimer() {
    if (state.timeline.playTimer) {
      clearInterval(state.timeline.playTimer);
      state.timeline.playTimer = null;
    }
  }

  function stopPlay() {
    stopPlayTimer();
    if (!state.timeline.isPlaying) return;
    state.timeline.isPlaying = false;
    $('timelineBar')?.classList.remove('is-playing');
    const playBtn = $('timelinePlay');
    if (playBtn) {
      playBtn.innerHTML = '<i data-lucide="play"></i><span class="timeline-control-label">Phát</span>';
      lucide?.createIcons();
    }
  }

  function startLiveTail() {
    if (state.timeline.liveTimer) return;
    state.timeline.liveTimer = setInterval(async () => {
      try {
        const response = await fetch('/api/heatmap/timeline/latest', { cache: 'no-store' });
        if (!response.ok) return;
        const data = await response.json();
        if (!data?.snapshot_time) return;
        const last = state.timeline.snapshots[state.timeline.snapshots.length - 1];
        if (last && last.snapshot_time === data.snapshot_time) return;
        // New checkpoint arrived — reload the day's timeline so the slider
        // max updates and the cursor advances if the user is parked on live.
        await loadTimeline();
      } catch (err) {
        console.warn('Live tail poll failed:', err);
      }
    }, 6000);
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

    const evidenceItem = (label, value) => {
      const valStr = String(value);
      let colorClass = 'val-navy';
      if (valStr.startsWith('+')) colorClass = 'val-positive';
      else if (valStr.startsWith('-')) colorClass = 'val-negative';
      return `<div class="evidence-item ${colorClass}">
        <span>${esc(label)}</span>
        <b class="${colorClass}">${esc(valStr)}</b>
      </div>`;
    };

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
        headers: { 'X-LP-User-Action': 'deepseek' },
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
    const mobileFilterToggle = $('heatmapFilterToggle');
    const mobileFilterPanel = $('heatmapControlbar');
    mobileFilterToggle?.addEventListener('click', () => {
      const isOpen = mobileFilterPanel?.classList.toggle('is-mobile-open') || false;
      mobileFilterToggle.setAttribute('aria-expanded', String(isOpen));
      const chevron = mobileFilterToggle.querySelector('[data-lucide="chevron-down"], [data-lucide="chevron-up"]');
      if (chevron) chevron.setAttribute('data-lucide', isOpen ? 'chevron-up' : 'chevron-down');
      lucide?.createIcons();
      window.setTimeout(renderTreemap, 80);
    });
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
    document.querySelectorAll('#aiButton, .ai-button').forEach((btn) => {
      btn.addEventListener('click', openAiReport);
    });
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
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (window.innerWidth >= 1024) {
          mobileFilterPanel?.classList.remove('is-mobile-open');
          mobileFilterToggle?.setAttribute('aria-expanded', 'false');
        }
        renderTreemap();
      }, 120);
    });
    window.addEventListener('orientationchange', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(renderTreemap, 180);
    });
    if ('ResizeObserver' in window) {
      new ResizeObserver(() => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(renderTreemap, 80);
      }).observe($('mapStage'));
    }

    // Global mousemove guard: instantly hide tooltip if mouse leaves all treemap stock tiles
    document.addEventListener('mousemove', (event) => {
      const tooltip = $('stockTooltip');
      if (tooltip && !tooltip.hidden && tooltip.style.display !== 'none') {
        if (document.querySelector('.treemap-stock.pinned')) return;
        const stockTile = event.target.closest('.treemap-stock');
        if (!stockTile) {
          hideTooltip();
        }
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    lucide?.createIcons();
    attachTimelineEvents();
    loadData();
    window.setInterval(() => {
      if (!document.hidden) loadData();
    }, 5000);
  });
})();
