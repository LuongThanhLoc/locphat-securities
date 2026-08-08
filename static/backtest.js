// Kiểm Định Phân Kỳ RSI - Logic phía Client

/* ==========================================================================
   TRẠNG THÁI TOÀN CỤC
   ========================================================================== */
let backtestData = null;
let equityChart = null;
let distributionChart = null;
let divergenceSortColumn = 'date';
let divergenceSortDirection = 'desc';
let tradeSortColumn = 'entry_date';
let tradeSortDirection = 'desc';

/* ==========================================================================
   KHỞI TẠO
   ========================================================================== */
document.addEventListener('DOMContentLoaded', function() {
  const today = new Date();
  const threeYearsAgo = new Date();
  threeYearsAgo.setFullYear(today.getFullYear() - 3);

  document.getElementById('endDate').value = formatDate(today);
  document.getElementById('startDate').value = formatDate(threeYearsAgo);

  initSymbolSearch();

  // Format initial capital input with thousand separators
  formatInitialCapitalDisplay();

  // Live-format capital input on every keystroke (cursor-aware)
  document.getElementById('initialCapital').addEventListener('input', function () {
    liveFormatCapital(this);
  });

  document.getElementById('symbolInput').addEventListener('keypress', function(e) {
    if (e.key === 'Enter') runBacktest();
  });

  // Watch advanced toggles
  document.getElementById('confirmTimeframe').addEventListener('change', toggleConfirmTimeframeFields);
  document.getElementById('positionMode').addEventListener('change', updatePositionSizeField);
});

function formatDate(date) {
  return date.toISOString().split('T')[0];
}

/* ==========================================================================
   TÌM KIẾM MÃ CỔ PHIẾU
   ========================================================================== */
let stockSearchTimeout = null;
let allStocks = [];

async function initSymbolSearch() {
  try {
    const resp = await fetch('/api/all_stocks');
    if (resp.ok) allStocks = await resp.json();
  } catch (e) { console.warn('Could not load stock list:', e); }
}

function showSymbolSuggestions(query) {
  const container = document.getElementById('symbolSuggestions');
  if (!query || query.length < 1) { container.classList.add('hidden'); return; }
  const q = query.toUpperCase();
  const matches = allStocks.filter(s =>
    s.symbol.startsWith(q) || s.symbol.includes(q) || s.name_norm?.includes(q.toLowerCase())
  ).slice(0, 8);
  if (matches.length === 0) { container.classList.add('hidden'); return; }
  container.innerHTML = matches.map(s => `
    <div class="suggestion-item px-3 py-2.5 hover:bg-slate-700/80 cursor-pointer flex items-center justify-between border-b border-slate-700/30 last:border-0 transition-colors gap-3"
         onclick="selectSuggestion('${s.symbol}', '${s.name || s.symbol}')">
      <span class="font-bold text-emerald-400 text-sm flex-none">${s.symbol}</span>
      <span class="text-slate-400 text-xs flex-1 min-w-0 text-right truncate ml-2">${s.name || ''}</span>
    </div>
  `).join('');
  container.classList.remove('hidden');
}

function selectSuggestion(symbol, name) {
  document.getElementById('symbolInput').value = symbol;
  document.getElementById('symbolSuggestions').classList.add('hidden');
  runBacktest();
}

document.getElementById('symbolInput').addEventListener('input', function(e) {
  const value = e.target.value.trim().toUpperCase();
  clearTimeout(stockSearchTimeout);
  stockSearchTimeout = setTimeout(() => showSymbolSuggestions(value), 200);
});

document.addEventListener('click', function(e) {
  if (!e.target.closest('#symbolInput') && !e.target.closest('#symbolSuggestions')) {
    document.getElementById('symbolSuggestions').classList.add('hidden');
  }
});

/* ==========================================================================
   ADVANCED UI CONTROLS
   ========================================================================== */
function toggleAdvanced() {
  const section = document.getElementById('advancedSection');
  const icon = document.getElementById('advancedToggleIcon');
  const chevron = document.getElementById('advancedChevron');
  section.classList.toggle('open');
  if (section.classList.contains('open')) {
    icon.style.transform = 'rotate(90deg)';
    chevron.style.transform = 'rotate(180deg)';
  } else {
    icon.style.transform = 'rotate(0deg)';
    chevron.style.transform = 'rotate(0deg)';
  }
  window.setTimeout(() => window.dispatchEvent(new Event('resize')), 320);
}

function toggleSwitch(el) {
  el.classList.toggle('active');
  const label = document.getElementById('includeShortLabel');
  if (el.classList.contains('active')) {
    label.textContent = 'Bật';
  } else {
    label.textContent = 'Tắt';
  }
}

function toggleConfirmTimeframeFields() {
  const val = document.getElementById('confirmTimeframe').value;
  const minRow = document.getElementById('confirmRsiMinRow');
  const maxRow = document.getElementById('confirmRsiMaxRow');
  if (val) {
    minRow.style.display = 'block';
    maxRow.style.display = 'block';
  } else {
    minRow.style.display = 'none';
    maxRow.style.display = 'none';
  }
}

function updatePositionSizeField() {
  const mode = document.getElementById('positionMode').value;
  const input = document.getElementById('positionSizePct');
  const label = document.getElementById('positionSizeLabel');
  if (mode === 'fixed') {
    label.textContent = 'Số vốn / Giao dịch (VNĐ)';
    input.removeAttribute('max');
    input.min = '1000';
    input.step = '1000000';
    if (Number(input.value) <= 100) input.value = '10000000';
  } else {
    label.textContent = '% Vốn / Giao dịch';
    input.min = '1';
    input.max = '100';
    input.step = '1';
    if (Number(input.value) > 100) input.value = '100';
  }
}

/* ==========================================================================
   CHẠY BACKTEST
   ========================================================================== */
async function runBacktest() {
  const symbol = document.getElementById('symbolInput').value.trim().toUpperCase();
  if (!symbol) { showError('Vui lòng nhập mã cổ phiếu'); return; }

  const params = {
    start: document.getElementById('startDate').value || undefined,
    end: document.getElementById('endDate').value || undefined,
    initial_capital: parseCapitalInput(),
    rsi_period: parseInt(document.getElementById('rsiPeriod').value) || 14,
    lookback: parseInt(document.getElementById('lookback').value) || 20,
    exit_strategy: document.getElementById('exitStrategy').value,
    holding_days: parseInt(document.getElementById('holdingDays').value) || 20,
    rsi_entry_min: parseFloat(document.getElementById('rsiEntryMin').value) || 40,
    rsi_entry_max: parseFloat(document.getElementById('rsiEntryMax').value) || 60,
    include_short: document.getElementById('includeShortToggle').classList.contains('active'),
    max_concurrent_trades: parseInt(document.getElementById('maxConcurrentTrades').value) || 1,
    commission_pct: parseFloat(document.getElementById('commissionPct').value) || 0,
    slippage_pct: parseFloat(document.getElementById('slippagePct').value) || 0,
    position_mode: document.getElementById('positionMode').value,
    position_size_pct: parseFloat(document.getElementById('positionSizePct').value) || 100,
    confirm_timeframe: document.getElementById('confirmTimeframe').value || '',
    confirm_rsi_min: parseFloat(document.getElementById('confirmRsiMin').value) || 50,
    confirm_rsi_max: parseFloat(document.getElementById('confirmRsiMax').value) || 50,
    trend_filter: document.getElementById('trendFilter').value,
    market_index: document.getElementById('marketIndex').value,
  };

  showLoading('Đang chạy kiểm định RSI cho ' + symbol + '...');
  hideError();
  document.getElementById('emptyState').classList.add('hidden');
  document.getElementById('resultsSection').classList.add('hidden');

  try {
    const cleanParams = Object.fromEntries(
      Object.entries(params).filter(([k, v]) => v !== undefined && v !== '' && v !== null)
    );
    const query = new URLSearchParams(cleanParams).toString();
    const resp = await fetch(`/api/backtest/rsi/${symbol}?${query}`);

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || 'Lỗi không xác định');
    }

    backtestData = await resp.json();
    if (backtestData.error) throw new Error(backtestData.error);

    hideLoading();
    renderResults(backtestData);

  } catch (e) {
    hideLoading();
    showError(e.message || 'Lỗi khi chạy kiểm định');
  }
}

/* ==========================================================================
   HIỂN THỊ KẾT QUẢ
   ========================================================================== */
function renderResults(data) {
  document.getElementById('emptyState').classList.add('hidden');
  document.getElementById('resultsSection').classList.remove('hidden');

  // Show weekly RSI column if confirm_timeframe was used
  const showWeekly = data.parameters && data.parameters.confirm_timeframe;
  const weeklyHeader = document.getElementById('weeklyRsiHeader');
  if (showWeekly) {
    weeklyHeader.classList.remove('hidden');
    weeklyHeader.textContent = 'RSI Tuần';
  } else {
    weeklyHeader.classList.add('hidden');
  }

  renderSummaryCards(data.summary, data);
  renderEquityChart(data.equity_curve, data.trades);
  renderDistributionChart(data.trades, data);
  renderDivergenceTable(data.divergences, showWeekly);
  renderTradesTable(data.trades, data);
}

function renderSummaryCards(summary, data) {
  const container = document.getElementById('summaryCards');

  if (!summary || summary.total_trades === 0) {
    const state = getNoTradeState(data);
    const quality = data?.data_quality || {};
    const source = escapeHtml(quality.source || 'Không xác định');
    const firstSession = escapeHtml(quality.first_session || '—');
    const lastSession = escapeHtml(quality.last_session || '—');
    const sessions = quality.verified_trading_sessions || 0;
    const initialCapital = summary?.initial_capital || data?.parameters?.initial_capital || 0;
    const zeroTradeCards = [
      { label: 'Phiên thực đã xác minh', value: sessions, icon: 'fa-calendar-check', color: 'text-cyan-400' },
      { label: 'Tổng tín hiệu', value: summary?.total_signals || 0, icon: 'fa-wave-square', color: 'text-white' },
      { label: 'Phân kỳ tăng giá', value: summary?.bullish_signals || 0, icon: 'fa-arrow-trend-up', color: 'text-emerald-400' },
      { label: 'Phân kỳ giảm giá', value: summary?.bearish_signals || 0, icon: 'fa-arrow-trend-down', color: 'text-rose-400' },
      { label: 'Giao dịch tạo được', value: 0, icon: 'fa-chart-bar', color: 'text-slate-400' },
      { label: 'Tiền mặt cuối kỳ', value: formatVND(initialCapital) + ' đ', icon: 'fa-wallet', color: 'text-white' },
      { label: 'Tỷ lệ thắng', value: '—', icon: 'fa-percentage', color: 'text-slate-400' },
      { label: 'Tỷ số Sharpe', value: '—', icon: 'fa-scale-balanced', color: 'text-slate-400' },
      { label: 'Hệ số lợi nhuận', value: '—', icon: 'fa-dollar-sign', color: 'text-slate-400' },
    ];
    container.innerHTML = `
      <div class="col-span-full border border-sky-200 bg-sky-50 rounded-xl p-4 sm:p-5 text-slate-700">
        <div class="flex items-start gap-3">
          <i class="fa-solid fa-circle-info mt-1 text-sky-700"></i>
          <div class="min-w-0">
            <p class="font-semibold text-slate-800">${state.message}</p>
            <p class="mt-1 text-sm text-slate-500">Nguồn: ${source} · Phiên đầu: ${firstSession} · Phiên cuối: ${lastSession} · ${sessions} phiên đã xác minh.</p>
            ${state.cta}
          </div>
        </div>
      </div>
      ${zeroTradeCards.map(card => summaryCardMarkup({ ...card, bg: 'bg-slate-800/50' })).join('')}
    `;
    return;
  }

  const isProfitTotal = (summary.total_return_vnd || 0) >= 0;
  const cards = [
    // VND cards (prominent, shown first)
    { label: 'Số dư cuối', value: formatVND(summary.final_balance) + ' đ', icon: 'fa-wallet', color: isProfitTotal ? 'text-emerald-400' : 'text-rose-400', bg: isProfitTotal ? 'bg-emerald-500/10' : 'bg-rose-500/10' },
    { label: 'Lãi/Lỗ (VNĐ)', value: (isProfitTotal ? '+' : '') + formatVND(summary.total_return_vnd) + ' đ', icon: isProfitTotal ? 'fa-arrow-trend-up' : 'fa-arrow-trend-down', color: isProfitTotal ? 'text-emerald-400' : 'text-rose-400', bg: isProfitTotal ? 'bg-emerald-500/10' : 'bg-rose-500/10' },
    { label: 'Vốn ban đầu', value: formatVND(summary.initial_capital) + ' đ', icon: 'fa-coins', color: 'text-white', bg: 'bg-slate-800/50' },
    // Existing metric cards
    { label: 'Tổng giao dịch', value: summary.total_trades, icon: 'fa-chart-bar', color: 'text-white', bg: 'bg-slate-800/50' },
    { label: 'Tỷ lệ thắng', value: summary.win_rate + '%', icon: 'fa-percentage', color: summary.win_rate >= 50 ? 'text-emerald-400' : 'text-rose-400', bg: summary.win_rate >= 50 ? 'bg-emerald-500/10' : 'bg-rose-500/10' },
    { label: 'P&L TB', value: (summary.avg_pnl_pct >= 0 ? '+' : '') + summary.avg_pnl_pct + '%', icon: 'fa-coins', color: summary.avg_pnl_pct >= 0 ? 'text-emerald-400' : 'text-rose-400', bg: summary.avg_pnl_pct >= 0 ? 'bg-emerald-500/10' : 'bg-rose-500/10' },
    { label: 'Giao dịch tốt nhất', value: '+' + summary.best_trade_pct + '%', icon: 'fa-arrow-up', color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
    { label: 'Giao dịch kém nhất', value: summary.worst_trade_pct + '%', icon: 'fa-arrow-down', color: 'text-rose-400', bg: 'bg-rose-500/10' },
    { label: 'Sụt giảm tối đa', value: summary.max_drawdown_pct + '%', icon: 'fa-arrow-down-right', color: 'text-amber-400', bg: 'bg-amber-500/10' },
    { label: 'Tỷ số Sharpe', value: summary.sharpe_ratio, icon: 'fa-scale-balanced', color: summary.sharpe_ratio >= 1 ? 'text-emerald-400' : 'text-slate-400', bg: 'bg-slate-800/50' },
    { label: 'CAGR', value: summary.cagr + '%', icon: 'fa-chart-column', color: summary.cagr >= 0 ? 'text-emerald-400' : 'text-rose-400', bg: summary.cagr >= 0 ? 'bg-emerald-500/10' : 'bg-rose-500/10' },
    { label: 'Phân kỳ/Năm', value: summary.divergences_per_year, icon: 'fa-calendar', color: 'text-cyan-400', bg: 'bg-cyan-500/10' },
    { label: 'RSI TB tín hiệu', value: summary.avg_rsi_at_signal, icon: 'fa-gauge-high', color: 'text-purple-400', bg: 'bg-purple-500/10' },
    { label: 'GD Tăng giá', value: summary.long_trades, icon: 'fa-arrow-trend-up', color: 'text-emerald-400', bg: 'bg-emerald-500/10', sub: summary.avg_bullish_pnl >= 0 ? '+' + summary.avg_bullish_pnl + '%' : summary.avg_bullish_pnl + '%' },
    { label: 'GD Giảm giá', value: summary.short_trades, icon: 'fa-arrow-trend-down', color: 'text-rose-400', bg: 'bg-rose-500/10', sub: summary.avg_bearish_pnl >= 0 ? '+' + summary.avg_bearish_pnl + '%' : summary.avg_bearish_pnl + '%' },
    { label: 'Hệ số lợi nhuận', value: summary.profit_factor >= 100 ? '∞' : summary.profit_factor, icon: 'fa-dollar-sign', color: summary.profit_factor >= 1.5 ? 'text-emerald-400' : 'text-slate-400', bg: 'bg-slate-800/50' },
    { label: 'Tín hiệu lọc', value: summary.filtered_signals || 0, icon: 'fa-filter', color: 'text-orange-400', bg: 'bg-orange-500/10' },
    { label: 'Hoa hồng TB', value: summary.avg_commission_pct + '%', icon: 'fa-percent', color: 'text-slate-400', bg: 'bg-slate-800/50' },
  ];

  container.innerHTML = cards.map(summaryCardMarkup).join('');
}

function summaryCardMarkup(card) {
  return `
    <div class="${card.bg} border border-slate-700/50 rounded-xl p-3 summary-card">
      <div class="flex items-center gap-2 mb-1">
        <i class="fa-solid ${card.icon} text-xs ${card.color}"></i>
        <span class="text-xs text-slate-400 font-medium">${card.label}</span>
      </div>
      <div class="${card.color} text-xl font-bold">${card.value}</div>
      ${card.sub ? `<div class="text-xs text-slate-400 mt-0.5">${card.sub}</div>` : ''}
    </div>
  `;
}

function getNoTradeState(data) {
  const summary = data?.summary || {};
  const audit = data?.execution_audit || {};
  const skipped = audit.skipped || {};
  const sessions = data?.data_quality?.verified_trading_sessions || 0;
  const total = summary.total_signals || audit.total_detected_signals || 0;
  const bullish = summary.bullish_signals || 0;
  const bearish = summary.bearish_signals || 0;
  const shortDisabled = skipped.short_disabled || 0;
  const cta = shortDisabled > 0 ? `
    <button type="button" onclick="enableShortAndRerun()"
      class="mt-3 inline-flex items-center gap-2 border border-rose-300 bg-white px-3 py-2 text-sm font-semibold text-rose-700 hover:bg-rose-50 transition-colors">
      <i class="fa-solid fa-rotate-right"></i>Bật mô phỏng Short (giả định) và chạy lại
    </button>` : '';

  if (sessions > 0 && total === 0) {
    return {
      message: `Đã kiểm tra ${sessions} phiên thực. Có dữ liệu giá nhưng không phát hiện phân kỳ theo cấu hình hiện tại.`,
      cta: '',
    };
  }
  if (sessions > 0 && shortDisabled > 0 && shortDisabled === total) {
    const signalText = bearish > 0 && bullish === 0
      ? `${bearish} phân kỳ giảm giá`
      : `${total} tín hiệu (${bullish} tăng giá, ${bearish} giảm giá)`;
    return {
      message: `Đã kiểm tra ${sessions} phiên thực, phát hiện ${signalText}. Không tạo giao dịch vì mô phỏng Short đang tắt.`,
      cta,
    };
  }

  const reasons = [
    ['bộ lọc thị trường', skipped.market_regime_filter],
    ['giới hạn lệnh trùng thời gian', skipped.concurrency_limit],
    ['không có phiên kế tiếp', skipped.no_next_session],
    ['chưa đủ dữ liệu để thoát', skipped.incomplete_exit_window],
    ['giá vào không hợp lệ', skipped.invalid_entry_price],
  ].filter(([, count]) => count > 0).map(([label, count]) => `${count} ${label}`);
  return {
    message: sessions > 0
      ? `Đã kiểm tra ${sessions} phiên thực và phát hiện ${total} tín hiệu, nhưng không tạo được giao dịch${reasons.length ? `: ${reasons.join(', ')}` : '.'}`
      : 'Nguồn OHLC không trả đủ dữ liệu phiên thực để chạy kiểm định.',
    cta,
  };
}

function enableShortAndRerun() {
  const toggle = document.getElementById('includeShortToggle');
  const label = document.getElementById('includeShortLabel');
  if (toggle) toggle.classList.add('active');
  if (label) label.textContent = 'Bật';
  runBacktest();
}

/* ==========================================================================
   BIỂU ĐỒ ĐƯỜNG CONG VỐN
   ========================================================================== */
function renderEquityChart(equityCurve, trades) {
  const container = document.getElementById('equityChart');
  if (!equityCurve || equityCurve.length === 0) {
    container.innerHTML = '<div class="flex items-center justify-center h-full text-slate-500">Không có dữ liệu</div>';
    return;
  }

  const dates = equityCurve.map(d => d.date);
  const equity = equityCurve.map(d => d.equity);
  const benchmark = equityCurve.map(d => d.benchmark);

  const options = {
    series: [
      { name: trades && trades.length ? 'Vốn Backtest' : 'Tiền mặt (không có lệnh)', data: equity },
      { name: 'Mua & Nắm giữ', data: benchmark }
    ],
    chart: {
      type: 'line', height: 280,
      toolbar: { show: true, tools: { download: false, selection: true, zoom: true, zoomin: true, zoomout: true, pan: true } },
      zoom: { enabled: true }, animations: { enabled: true, speed: 300 }
    },
    colors: ['#10b981', '#38bdf8'],
    stroke: { curve: 'smooth', width: 2 },
    fill: {
      type: 'gradient', gradient: {
        shadeIntensity: 1, opacityFrom: 0.3, opacityTo: 0.05, stops: [0, 90, 100]
      }
    },
    dataLabels: { enabled: false },
    xaxis: {
      type: 'datetime',
      labels: { style: { colors: '#64748b', fontSize: '11px' } },
      axisBorder: { show: false }, axisTicks: { show: false }
    },
    yaxis: { labels: { style: { colors: '#64748b', fontSize: '11px' }, formatter: v => new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 }).format(v) } },
    legend: { show: true, position: 'top', horizontalAlign: 'right', labels: { colors: '#59656b' } },
    grid: { borderColor: '#ded9cc', strokeDashArray: 4 },
    tooltip: { theme: 'light', x: { format: 'dd MMM yyyy' }, y: { formatter: v => new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 }).format(v) } },
  };

  if (equityChart) {
    equityChart.updateOptions(options, true);
  } else {
    equityChart = new ApexCharts(container, options);
    equityChart.render();
  }
}

/* ==========================================================================
   BIỂU ĐỒ PHÂN BỔ LỢI NHUẬN
   ========================================================================== */
function renderDistributionChart(trades, data) {
  const container = document.getElementById('distributionChart');
  if (!trades || trades.length === 0) {
    if (distributionChart) {
      distributionChart.destroy();
      distributionChart = null;
    }
    const state = getNoTradeState(data);
    container.innerHTML = `<div class="flex h-full flex-col items-center justify-center px-5 text-center text-slate-500">
      <i class="fa-solid fa-chart-column mb-3 text-xl text-slate-400"></i>
      <p>Không có phân phối P&amp;L vì không có giao dịch mô phỏng.</p>
      <p class="mt-1 text-sm">${state.message}</p>
      ${state.cta}
    </div>`;
    return;
  }

  const pnls = trades.map(t => t.pnl_pct);
  const binSize = 2;
  const min = Math.floor(Math.min(...pnls) / binSize) * binSize;
  const max = Math.ceil(Math.max(...pnls) / binSize) * binSize;
  const bins = [];

  for (let i = min; i < max; i += binSize) {
    const count = pnls.filter(p => p >= i && p < i + binSize).length;
    bins.push({ x: i + (binSize / 2), y: count, fillColor: i >= 0 ? '#10b981' : '#f87171' });
  }

  const options = {
    series: [{ data: bins }],
    chart: { type: 'bar', height: 280, toolbar: { show: false }, animations: { enabled: true, speed: 300 } },
    plotOptions: { bar: { borderRadius: 4, columnWidth: '70%' } },
    dataLabels: { enabled: true, style: { fontSize: '11px', colors: ['#59656b'] }, formatter: v => v > 0 ? v : '' },
    legend: { show: false },
    xaxis: {
      type: 'numeric',
      tickAmount: 8,
      labels: { style: { colors: '#64748b', fontSize: '11px' }, formatter: v => v.toFixed(0) + '%' },
      axisBorder: { show: false }, axisTicks: { show: false }
    },
    yaxis: { labels: { style: { colors: '#64748b', fontSize: '11px' } } },
    grid: { borderColor: '#ded9cc', strokeDashArray: 4 },
    tooltip: { theme: 'light', x: { formatter: v => v.toFixed(0) + '%' }, y: { formatter: v => v + ' giao dịch' } }
  };

  if (distributionChart) {
    distributionChart.updateOptions(options, true);
  } else {
    distributionChart = new ApexCharts(container, options);
    distributionChart.render();
  }
}

/* ==========================================================================
   BẢNG TÍN HIỆU PHÂN KỲ
   ========================================================================== */
function renderDivergenceTable(divergences, showWeekly) {
  const tbody = document.getElementById('divergenceTableBody');
  const countEl = document.getElementById('divergenceCount');
  divergences = Array.isArray(divergences) ? divergences : [];

  countEl.textContent = divergences.length;

  if (!divergences || divergences.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="${showWeekly ? 7 : 6}" class="text-center py-6 text-slate-500">
        Không có tín hiệu phân kỳ RSI nào được phát hiện
      </td></tr>
    `;
    return;
  }

  const sorted = [...divergences].sort((a, b) => compareTableValues(
    a[divergenceSortColumn],
    b[divergenceSortColumn],
    divergenceSortDirection
  ));
  updateSortIndicators('divergenceTableHead', divergenceSortColumn, divergenceSortDirection);

  tbody.innerHTML = sorted.map(div => {
    const isBullish = div.type === 'bullish';
    const weeklyVal = showWeekly ? `<td class="py-2.5 px-2 text-right text-slate-400">${div.weekly_rsi || '-'}</td>` : '';
    return `
      <tr class="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
        <td class="py-2.5 px-2 text-slate-300">${div.date}</td>
        <td class="py-2.5 px-2">
          <span class="${isBullish ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'} text-xs font-bold px-2 py-0.5 rounded">
            ${isBullish ? 'Tăng giá' : 'Giảm giá'}
          </span>
        </td>
        <td class="py-2.5 px-2 text-right text-white font-medium">${formatPrice(div.price_at_signal)}</td>
        <td class="py-2.5 px-2 text-right ${div.rsi_at_signal < 50 ? 'text-emerald-400' : 'text-rose-400'}">${div.rsi_at_signal}</td>
        <td class="py-2.5 px-2 text-right text-slate-400">${formatPrice(isBullish ? div.lookback_low_price : div.lookback_high_price)}</td>
        <td class="py-2.5 px-2 text-right text-slate-400">${isBullish ? div.lookback_low_rsi : div.lookback_high_rsi}</td>
        ${weeklyVal}
      </tr>
    `;
  }).join('');
}

function sortDivergences(column) {
  if (divergenceSortColumn === column) {
    divergenceSortDirection = divergenceSortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    divergenceSortColumn = column;
    divergenceSortDirection = 'desc';
  }
  if (backtestData) renderDivergenceTable(
    backtestData.divergences,
    Boolean(backtestData.parameters && backtestData.parameters.confirm_timeframe)
  );
}

/* ==========================================================================
   BẢNG GIAO DỊCH
   ========================================================================== */
function renderTradesTable(trades, data) {
  const tbody = document.getElementById('tradesTableBody');
  const countEl = document.getElementById('tradeCount');
  trades = Array.isArray(trades) ? trades : [];

  countEl.textContent = trades.length;

  if (!trades || trades.length === 0) {
    const state = getNoTradeState(data);
    tbody.innerHTML = `
      <tr><td colspan="10" class="text-center py-6 text-slate-500">
        <div class="flex flex-col items-center px-4">
          <span>Không có giao dịch nào được mô phỏng.</span>
          <span class="mt-1 text-sm">${state.message}</span>
          ${state.cta}
        </div>
      </td></tr>
    `;
    return;
  }

  const sorted = [...trades].sort((a, b) => compareTableValues(
    a[tradeSortColumn], b[tradeSortColumn], tradeSortDirection
  ));
  updateSortIndicators('tradesTableHead', tradeSortColumn, tradeSortDirection);

  const exitReasonLabels = {
    'time_exit': 'Hết hạn',
    'rsi_overbought': 'RSI quá mua',
    'rsi_oversold': 'RSI quá bán',
    'trailing_stop': 'Dừng lỗ ATR',
    'max_days': 'Tối đa ngày'
  };

  tbody.innerHTML = sorted.map(trade => {
    const isBullish = trade.divergence_type === 'bullish';
    const isProfit = trade.pnl_pct >= 0;
    return `
      <tr class="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors cursor-pointer"
          onclick="highlightTrade('${trade.entry_date}')">
        <td class="py-2.5 px-2 text-slate-300">${trade.entry_date}</td>
        <td class="py-2.5 px-2">
          <span class="${isBullish ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'} text-xs font-bold px-2 py-0.5 rounded">
            ${isBullish ? 'Long' : 'Short'}
          </span>
        </td>
        <td class="py-2.5 px-2 text-right text-white font-medium">${formatPrice(trade.entry_price)}</td>
        <td class="py-2.5 px-2 text-right font-bold ${isProfit ? 'text-emerald-400' : 'text-rose-400'}">
          ${isProfit ? '+' : ''}${trade.pnl_pct}%
        </td>
        <td class="py-2.5 px-2 text-right text-white font-medium">${formatPrice(trade.exit_price)}</td>
        <td class="py-2.5 px-2 text-slate-300">${trade.exit_date}</td>
        <td class="py-2.5 px-2 text-right text-slate-400">${trade.holding_days}</td>
        <td class="py-2.5 px-2">
          <span class="text-xs text-slate-400">${exitReasonLabels[trade.exit_reason] || trade.exit_reason}</span>
        </td>
        <td class="py-2.5 px-2 text-right text-amber-400">${trade.max_drawdown_pct}%</td>
        <td class="py-2.5 px-2 text-right text-emerald-400">${trade.max_runup_pct}%</td>
      </tr>
    `;
  }).join('');
}

function sortTrades(column) {
  if (tradeSortColumn === column) {
    tradeSortDirection = tradeSortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    tradeSortColumn = column;
    tradeSortDirection = 'desc';
  }
  if (backtestData) renderTradesTable(backtestData.trades, backtestData);
}

function highlightTrade(entryDate) {
  console.log('Highlight trade:', entryDate);
}

function compareTableValues(valA, valB, direction) {
  const missingA = valA === null || valA === undefined || valA === '';
  const missingB = valB === null || valB === undefined || valB === '';
  if (missingA || missingB) {
    if (missingA && missingB) return 0;
    return missingA ? 1 : -1;
  }
  const numericA = Number(valA);
  const numericB = Number(valB);
  if (Number.isFinite(numericA) && Number.isFinite(numericB)) {
    return direction === 'asc' ? numericA - numericB : numericB - numericA;
  }
  const result = String(valA).localeCompare(String(valB), 'vi', { numeric: true });
  return direction === 'asc' ? result : -result;
}

function updateSortIndicators(headId, activeColumn, direction) {
  const head = document.getElementById(headId);
  if (!head) return;
  head.querySelectorAll('th[data-sort-column]').forEach(th => {
    const active = th.dataset.sortColumn === activeColumn;
    th.setAttribute('aria-sort', active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none');
    const icon = th.querySelector('i');
    if (!icon) return;
    icon.className = `fa-solid ${active ? (direction === 'asc' ? 'fa-sort-up' : 'fa-sort-down') : 'fa-sort'} ml-1 ${active ? 'text-emerald-400' : 'text-slate-600'}`;
  });
}

/* ==========================================================================
   TIỆN ÍCH
   ========================================================================== */
function formatPrice(price) {
  if (!price && price !== 0) return '-';
  const num = parseFloat(price);
  if (isNaN(num)) return '-';
  return num.toLocaleString('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));
}

function showLoading(message) {
  document.getElementById('loadingText').textContent = message || 'Đang xử lý...';
  document.getElementById('loading').classList.remove('hidden');
}

function hideLoading() {
  document.getElementById('loading').classList.add('hidden');
}

function formatVND(num) {
  if (!num && num !== 0) return '-';
  return new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 }).format(num);
}

function liveFormatCapital(input) {
  if (!input) return;

  // Save cursor & selection state before any DOM change
  const selStart = input.selectionStart;
  const selEnd = input.selectionEnd;
  const rawPrev = input.value.replace(/\D/g, '');

  // Build formatted value from raw digits only
  const digits = rawPrev.replace(/\D/g, '');
  const maxDigits = 15;
  const trimmed = digits.slice(0, maxDigits);

  if (trimmed === '') {
    input.value = '';
    return;
  }

  const formatted = new Intl.NumberFormat('vi-VN', {
    maximumFractionDigits: 0,
  }).format(parseInt(trimmed, 10));

  // Only touch DOM if the visible string actually changed
  if (formatted === input.value) return;
  input.value = formatted;

  // Restore cursor to the character position that corresponds
  // to the same number of digits that were before the cursor.
  const digitCountBeforeCursor = rawPrev.slice(0, selStart).replace(/\D/g, '').length;

  // Re-count digits in the new formatted string up to that digit count
  let digitCount = 0;
  let newPos = 0;
  for (; newPos < formatted.length && digitCount < digitCountBeforeCursor; newPos++) {
    if (/\d/.test(formatted[newPos])) digitCount++;
  }
  // If cursor was at the very end of digits, snap to end
  if (digitCountBeforeCursor >= trimmed.length) {
    newPos = formatted.length;
  }

  input.setSelectionRange(newPos, newPos);
}

function formatInitialCapitalDisplay() {
  const input = document.getElementById('initialCapital');
  if (!input) return;
  const raw = parseInt(input.value.replace(/\./g, '')) || 100000000;
  input.value = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 }).format(raw);
}

function parseCapitalInput() {
  const input = document.getElementById('initialCapital');
  if (!input) return 100000000;
  return parseInt(input.value.replace(/\./g, '')) || 100000000;
}

function showError(message) {
  document.getElementById('errorMessage').textContent = message;
  document.getElementById('errorBanner').classList.remove('hidden');
  document.getElementById('errorBanner').classList.add('flex');
}

function hideError() {
  document.getElementById('errorBanner').classList.add('hidden');
  document.getElementById('errorBanner').classList.remove('flex');
}
