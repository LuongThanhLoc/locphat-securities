(() => {
  'use strict';

  const PERIODS = [20, 50, 100, 200];
  const COLORS = { 20: '#f59e0b', 50: '#0284c7', 100: '#8b5cf6', 200: '#dc2626' };
  const WINDOWS = { '1D': 1, '3D': 3, '1W': 5, '1M': 22, '3M': 65, '1Y': 250 };
  const number = (value) => Number(value);
  const validNumber = (value) => Number.isFinite(number(value));
  const displayNumber = (value, maximumFractionDigits = 2) => validNumber(value)
    ? number(value).toLocaleString('vi-VN', { minimumFractionDigits: 0, maximumFractionDigits }) : '--';

  function calculateEMA(data, period) {
    if (!Array.isArray(data) || data.length < period) return [];
    const multiplier = 2 / (period + 1);
    let previous = number(data[0].close);
    const result = [{ time: data[0].time, value: Number(previous.toFixed(2)) }];
    for (let index = 1; index < data.length; index += 1) {
      previous = (number(data[index].close) - previous) * multiplier + previous;
      result.push({ time: data[index].time, value: Number(previous.toFixed(2)) });
    }
    return result;
  }

  function normalizeSessions(rows) {
    const byDate = new Map();
    (rows || []).forEach((row) => {
      const time = String(row.date || row.time || '').slice(0, 10);
      const normalized = {
        time, open: number(row.open), high: number(row.high), low: number(row.low),
        close: number(row.close), volume: number(row.volume ?? row.market_volume ?? 0),
        isProvisional: Boolean(row.is_provisional || row.isProvisional),
      };
      if (!/^\d{4}-\d{2}-\d{2}$/.test(time)) return;
      if (![normalized.open, normalized.high, normalized.low, normalized.close, normalized.volume].every(Number.isFinite)) return;
      if (normalized.open <= 0 || normalized.close <= 0 || normalized.volume < 0) return;
      if (normalized.high < Math.max(normalized.open, normalized.close) || normalized.low > Math.min(normalized.open, normalized.close)) return;
      byDate.set(time, normalized);
    });
    return [...byDate.values()].sort((left, right) => left.time.localeCompare(right.time));
  }

  class PriceChart {
    constructor(host, options = {}) {
      if (!host) throw new Error('Price chart host is required');
      this.host = host;
      this.symbol = String(options.symbol || '').toUpperCase();
      this.exchange = this.normalizeExchange(options.exchange);
      this.baseSessions = normalizeSessions(options.sessions || []);
      this.liveSession = null;
      this.timeframe = options.timeframe || 'ALL';
      this.engine = options.engine || 'tradingview';
      this.emaVisible = { 20: true, 50: true, 100: true, 200: true };
      this.chart = null;
      this.apex = null;
      this.resizeObserver = null;
      this.emaSeries = {};
      this.listeners = [];
      this.renderShell(options);
      this.render();
    }

    normalizeExchange(value) {
      const exchange = String(value || 'HOSE').toUpperCase();
      return exchange === 'HSX' ? 'HOSE' : (['HOSE', 'HNX', 'UPCOM'].includes(exchange) ? exchange : 'HOSE');
    }

    renderShell(options) {
      this.host.classList.add('lp-price-chart');
      this.host.innerHTML = `
        <header class="lp-price-chart__head">
          <div class="lp-price-chart__title"><h3>Diễn Biến Giá &amp; Khối Lượng</h3><p data-role="subtitle"></p></div>
          <div class="lp-price-chart__actions">
            <div class="lp-price-chart__engine" role="group" aria-label="Công cụ biểu đồ">
              <button type="button" data-engine="tradingview">⚡ TradingView Engine</button>
              <button type="button" data-engine="apex">▰ Chart Nội Bộ</button>
            </div>
            <a class="lp-price-chart__external" data-role="external" target="_blank" rel="noopener noreferrer">TradingView.com ↗</a>
          </div>
        </header>
        <div class="lp-price-chart__toolbar">
          <div class="lp-price-chart__ema" role="group" aria-label="Đường trung bình EMA">
            ${PERIODS.map((period) => `<button type="button" data-period="${period}">EMA ${period}</button>`).join('')}
          </div>
          <div class="lp-price-chart__timeframes" role="group" aria-label="Khung thời gian">
            ${['1D', '3D', '1W', '1M', '3M', '1Y', 'ALL'].map((value) => `<button type="button" data-timeframe="${value}">${value === 'ALL' ? 'Tất cả' : value}</button>`).join('')}
          </div>
        </div>
        <div class="lp-price-chart__canvas-wrap">
          <div class="lp-price-chart__legend" data-role="legend"></div>
          <div class="lp-price-chart__canvas" data-role="lightweight"></div>
          <div class="lp-price-chart__canvas" data-role="apex" hidden></div>
          <div class="lp-price-chart__empty" data-role="empty" hidden>Không có đủ lịch sử giá hợp lệ để hiển thị.</div>
        </div>
        <p class="lp-price-chart__note" data-role="note">Nguồn Vietcap · Giá giao dịch thực tế chưa điều chỉnh.</p>`;
      this.lightweightHost = this.host.querySelector('[data-role="lightweight"]');
      this.apexHost = this.host.querySelector('[data-role="apex"]');
      this.legend = this.host.querySelector('[data-role="legend"]');
      this.empty = this.host.querySelector('[data-role="empty"]');
      this.subtitle = this.host.querySelector('[data-role="subtitle"]');
      this.note = this.host.querySelector('[data-role="note"]');
      const external = this.host.querySelector('[data-role="external"]');
      external.href = `https://www.tradingview.com/chart/?symbol=${this.exchange}:${this.symbol}`;
      this.bind(this.host, 'click', (event) => {
        const ema = event.target.closest('button[data-period]');
        if (ema) this.toggleEMA(Number(ema.dataset.period));
        const timeframe = event.target.closest('button[data-timeframe]');
        if (timeframe) this.setTimeframe(timeframe.dataset.timeframe);
        const engine = event.target.closest('button[data-engine]');
        if (engine) this.switchEngine(engine.dataset.engine);
      });
    }

    bind(target, event, handler) {
      target.addEventListener(event, handler);
      this.listeners.push(() => target.removeEventListener(event, handler));
    }

    sessions() {
      const rows = [...this.baseSessions];
      if (this.liveSession) {
        const index = rows.findIndex((row) => row.time === this.liveSession.time);
        if (index >= 0) rows[index] = this.liveSession;
        else if (!rows.length || this.liveSession.time > rows[rows.length - 1].time) rows.push(this.liveSession);
      }
      return rows;
    }

    setData({ symbol, exchange, sessions, liveSession } = {}) {
      if (symbol) this.symbol = String(symbol).toUpperCase();
      if (exchange) this.exchange = this.normalizeExchange(exchange);
      if (sessions) this.baseSessions = normalizeSessions(sessions);
      this.liveSession = liveSession ? normalizeSessions([liveSession])[0] || null : null;
      const external = this.host.querySelector('[data-role="external"]');
      external.href = `https://www.tradingview.com/chart/?symbol=${this.exchange}:${this.symbol}`;
      this.render();
    }

    setLiveSession(row) {
      this.liveSession = row ? normalizeSessions([row])[0] || null : null;
      this.render();
    }

    render() {
      this.destroyCharts();
      const rows = this.sessions();
      this.empty.hidden = Boolean(rows.length);
      this.legend.hidden = !rows.length;
      this.lightweightHost.hidden = this.engine !== 'tradingview' || !rows.length;
      this.apexHost.hidden = this.engine !== 'apex' || !rows.length;
      const provisional = rows.some((row) => row.isProvisional);
      this.subtitle.textContent = rows.length
        ? `${this.symbol} · ${rows.length - (provisional ? 1 : 0)} phiên EOD${provisional ? ' + 1 phiên tạm tính' : ''}`
        : `${this.symbol} · Chưa có dữ liệu`;
      this.note.textContent = `Nguồn Vietcap · Giá giao dịch thực tế chưa điều chỉnh${provisional ? ' · Phiên cuối và EMA tương ứng đang tạm tính' : ''}.`;
      this.refreshControls(rows.length);
      if (!rows.length) return;
      if (this.engine === 'apex') this.renderApex(rows);
      else this.renderLightweight(rows);
    }

    refreshControls(count) {
      this.host.querySelectorAll('button[data-engine]').forEach((button) => {
        const active = button.dataset.engine === this.engine;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
      });
      this.host.querySelectorAll('button[data-timeframe]').forEach((button) => {
        const active = button.dataset.timeframe === this.timeframe;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
      });
      this.host.querySelectorAll('button[data-period]').forEach((button) => {
        const period = Number(button.dataset.period);
        button.disabled = count < period;
        button.classList.toggle('off', !this.emaVisible[period]);
        button.setAttribute('aria-pressed', String(Boolean(this.emaVisible[period])));
        button.title = count < period ? `Cần tối thiểu ${period} phiên` : `${this.emaVisible[period] ? 'Ẩn' : 'Hiện'} EMA ${period}`;
        button.hidden = this.engine === 'apex';
      });
    }

    renderLightweight(rows) {
      if (!window.LightweightCharts) { this.engine = 'apex'; this.render(); return; }
      const height = Math.max(this.lightweightHost.clientHeight, 400);
      this.chart = LightweightCharts.createChart(this.lightweightHost, {
        width: Math.max(this.lightweightHost.clientWidth, 280), height,
        layout: { background: { color: '#fffdf7' }, textColor: '#68727a', fontFamily: 'Inter, sans-serif', fontSize: 11 },
        grid: { vertLines: { color: 'rgba(55,64,71,.08)' }, horzLines: { color: 'rgba(55,64,71,.08)' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { color: '#064a6b', style: LightweightCharts.LineStyle.Dashed }, horzLine: { color: '#064a6b', style: LightweightCharts.LineStyle.Dashed } },
        rightPriceScale: { borderColor: '#c9c5ba', scaleMargins: { top: .08, bottom: .24 } },
        timeScale: { borderColor: '#c9c5ba', timeVisible: false, rightOffset: 2, minBarSpacing: 2 },
        localization: { locale: 'vi-VN', priceFormatter: (price) => displayNumber(price) },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      });
      this.candleSeries = this.chart.addCandlestickSeries({ upColor: '#08713c', downColor: '#be2d2a', borderUpColor: '#08713c', borderDownColor: '#be2d2a', wickUpColor: '#08713c', wickDownColor: '#be2d2a' });
      this.volumeSeries = this.chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume', priceLineVisible: false, lastValueVisible: true });
      this.chart.priceScale('volume').applyOptions({ scaleMargins: { top: .84, bottom: 0 } });
      this.candleSeries.setData(rows.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
      this.volumeSeries.setData(rows.map((row) => ({ time: row.time, value: row.volume, color: row.close >= row.open ? 'rgba(8,113,60,.4)' : 'rgba(190,45,42,.4)' })));
      PERIODS.forEach((period) => {
        const ema = calculateEMA(rows, period);
        if (!ema.length) return;
        const series = this.chart.addLineSeries({ color: COLORS[period], lineWidth: period === 200 ? 2 : 1.5, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: true, title: `EMA ${period}`, visible: this.emaVisible[period] });
        series.setData(ema);
        this.emaSeries[period] = { series, data: ema };
      });
      this.updateLegend(rows[rows.length - 1], rows);
      this.chart.subscribeCrosshairMove((param) => {
        const candle = param?.seriesData?.get(this.candleSeries);
        const selected = candle && param.time ? rows.find((row) => row.time === param.time) : rows[rows.length - 1];
        this.updateLegend(selected, rows);
      });
      this.applyTimeframe();
      if ('ResizeObserver' in window) {
        this.resizeObserver = new ResizeObserver(([entry]) => {
          if (entry && this.chart) this.chart.resize(Math.max(280, entry.contentRect.width), Math.max(400, entry.contentRect.height));
        });
        this.resizeObserver.observe(this.lightweightHost);
      }
    }

    updateLegend(row, rows) {
      if (!row) return;
      const change = row.close - row.open;
      const pct = row.open ? change / row.open * 100 : 0;
      const color = change >= 0 ? '#08713c' : '#be2d2a';
      const emaLabels = PERIODS.map((period) => {
        const point = this.emaSeries[period]?.data?.find((item) => item.time === row.time);
        return `<span>EMA ${period}: <b>${point ? displayNumber(point.value) : '--'}</b></span>`;
      }).join('');
      this.legend.innerHTML = `<div class="lp-price-chart__legend-main"><b>${this.exchange}:${this.symbol}</b><span>${row.time}${row.isProvisional ? ' · Tạm tính' : ''}</span><span>Mở: <b>${displayNumber(row.open)}</b></span><span>Cao: <b>${displayNumber(row.high)}</b></span><span>Thấp: <b>${displayNumber(row.low)}</b></span><span>Đóng: <b style="color:${color}">${displayNumber(row.close)} (${change >= 0 ? '+' : ''}${displayNumber(pct)}%)</b></span><span>KL: <b>${displayNumber(row.volume, 0)}</b></span></div><div class="lp-price-chart__legend-ema">${emaLabels}</div>`;
    }

    renderApex(rows) {
      if (!window.ApexCharts) { this.empty.hidden = false; this.empty.textContent = 'Không thể khởi tạo Chart Nội Bộ.'; return; }
      const sliced = this.sliceRows(rows);
      this.apex = new ApexCharts(this.apexHost, {
        series: [{ name: 'Giá CP (VND)', data: sliced.map((row) => ({ x: new Date(`${row.time}T00:00:00`).getTime(), y: [row.open, row.high, row.low, row.close] })) }],
        chart: { type: 'candlestick', height: Math.max(this.apexHost.clientHeight, 400), background: '#fffdf7', toolbar: { show: true }, animations: { enabled: false } },
        theme: { mode: 'light' },
        plotOptions: { candlestick: { colors: { upward: '#08713c', downward: '#be2d2a' } } },
        xaxis: { type: 'datetime', labels: { style: { colors: '#68727a' } } },
        yaxis: { tooltip: { enabled: true }, labels: { formatter: (value) => displayNumber(value), style: { colors: '#68727a' } } },
        grid: { borderColor: 'rgba(55,64,71,.14)' },
      });
      this.apex.render();
    }

    sliceRows(rows) {
      const count = WINDOWS[this.timeframe];
      return count ? rows.slice(-count) : rows;
    }

    applyTimeframe() {
      if (!this.chart) return;
      const rows = this.sessions();
      const count = WINDOWS[this.timeframe];
      if (!count || count >= rows.length) this.chart.timeScale().fitContent();
      else this.chart.timeScale().setVisibleLogicalRange({ from: rows.length - count, to: rows.length - 1 });
    }

    setTimeframe(value) {
      if (![...Object.keys(WINDOWS), 'ALL'].includes(value)) return;
      this.timeframe = value;
      this.refreshControls(this.sessions().length);
      if (this.engine === 'apex') this.render(); else this.applyTimeframe();
    }

    toggleEMA(period) {
      if (!PERIODS.includes(period) || !this.emaSeries[period]) return;
      this.emaVisible[period] = !this.emaVisible[period];
      this.emaSeries[period].series.applyOptions({ visible: this.emaVisible[period] });
      this.refreshControls(this.sessions().length);
    }

    switchEngine(engine) {
      if (!['tradingview', 'apex'].includes(engine) || engine === this.engine) return;
      this.engine = engine;
      this.render();
    }

    destroyCharts() {
      if (this.resizeObserver) this.resizeObserver.disconnect();
      this.resizeObserver = null;
      if (this.chart) { try { this.chart.remove(); } catch (_) {} }
      if (this.apex) { try { this.apex.destroy(); } catch (_) {} }
      this.chart = null;
      this.apex = null;
      this.emaSeries = {};
      if (this.lightweightHost) this.lightweightHost.replaceChildren();
      if (this.apexHost) this.apexHost.replaceChildren();
    }

    destroy() {
      this.destroyCharts();
      this.listeners.splice(0).forEach((remove) => remove());
      this.host.replaceChildren();
      this.host.classList.remove('lp-price-chart');
    }
  }

  window.LPPriceChart = { create: (host, options) => new PriceChart(host, options), calculateEMA, normalizeSessions };
})();
