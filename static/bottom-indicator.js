(() => {
  'use strict';

  const state = {
    analysis: null,
    backtest: null,
    charts: {},
    series: {},
    isSyncing: false,
    isCrosshairSyncing: false,
    resizeObserver: null,
    controller: null,
    searchTimer: null,
    dialogTrigger: null
  };

  const $ = (id) => document.getElementById(id);
  const STATE_COLORS = {
    FALLING_CONTRACTION: '#a71f35', BOTTOM_WATCH: '#b7791f', TOP_WATCH: '#ea580c', EARLY_EXPANSION: '#0f8b5f',
    CONFIRMED_EXPANSION: '#087b50', OVEREXTENDED: '#8b5cf6', DISTRIBUTION_CONTRACTION: '#d13b4f', NEUTRAL: '#7b8984'
  };

  const ACTION_COLORS = {
    TEST_BUY: '#087b50',
    ADD_BUY: '#059669',
    HOLD: '#2563eb',
    TRIM: '#d97706',
    EXIT: '#dc2626',
    WATCH: '#65736e',
  };

  const ACTION_TEXTS = {
    TEST_BUY: '⚡ TEST BUY (Gom thăm dò)',
    ADD_BUY: '🚀 ADD BUY (Gia tăng vị thế)',
    HOLD: '🔒 HOLD (Nắm giữ)',
    TRIM: '⚠️ TRIM (Hạ tỷ trọng)',
    EXIT: '🛑 EXIT (Thoát vị thế)',
    WATCH: '👀 QUAN SÁT',
  };

  const SCORE_TYPE_LABELS = {
    BOTTOM_QUALITY: 'Độ tin cậy Đáy',
    BREAKOUT_QUALITY: 'Chất lượng Breakout',
    PULLBACK_QUALITY: 'Chất lượng Pullback',
    DISTRIBUTION_QUALITY: 'Xác suất Phân phối',
    BREAKDOWN_QUALITY: 'Xác suất Thủng nền',
  };

  function updateSmartMoneyHeader(s) {
    const coreVal = s?.core_pct != null ? formatNumber(s.core_pct, 1) : '—';
    const flowVal = s?.flow_pct != null ? formatNumber(s.flow_pct, 1) : '—';
    const pulseVal = s?.pulse_pct != null ? formatNumber(s.pulse_pct, 1) : '—';

    const valCore = $('smValCore');
    if (valCore) valCore.textContent = coreVal;

    const valFlow = $('smValFlow');
    if (valFlow) valFlow.textContent = flowVal;

    const valPulse = $('smValPulse');
    if (valPulse) valPulse.textContent = pulseVal;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }
  function formatNumber(value, digits = 2) {
    return value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('vi-VN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }
  function formatVnd(value) {
    return value === null || value === undefined ? '—' : `${Math.round(Number(value)).toLocaleString('vi-VN')} đ`;
  }
  function setBusy(busy, message = '') {
    $('bottomLoading').hidden = !busy;
    $('bottomSubmit').disabled = busy;
    if ($('runBottomBacktest')) $('runBottomBacktest').disabled = busy;
    if (busy && message) $('bottomLoading').querySelector('strong').textContent = message;
  }
  function showError(message) {
    $('bottomError').textContent = message;
    $('bottomError').hidden = false;
  }
  function clearError() { $('bottomError').hidden = true; $('bottomError').textContent = ''; }
  function currentRequest() {
    const symbol = $('bottomSymbol').value.trim().toUpperCase();
    const barLimit = Number.parseInt($('bottomBarLimit').value, 10) || 748;
    if (!/^[A-Z][A-Z0-9]{1,9}$/.test(symbol)) throw new Error('Mã cổ phiếu không hợp lệ.');
    return { symbol, barLimit };
  }
  async function fetchJson(url) {
    if (state.controller) state.controller.abort();
    state.controller = new AbortController();
    const response = await fetch(url, { cache: 'no-store', signal: state.controller.signal });
    let payload = {};
    try { payload = await response.json(); } catch { payload = {}; }
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Không thể tải dữ liệu.');
    return payload;
  }

  function destroyCharts() {
    ['price', 'flow', 'aperture', 'equity'].forEach((name) => {
      if (state.charts[name]) {
        state.charts[name].remove();
        delete state.charts[name];
      }
    });
    state.series = {};
  }



  function renderTradePlan(tradeSetup) {
    if (!tradeSetup) return;

    const verdictBadge = $('tradeVerdictBadge');
    if (verdictBadge) {
      verdictBadge.textContent = tradeSetup.verdict_badge || tradeSetup.verdict_title || 'QUAN SÁT';
      verdictBadge.className = `trade-verdict-badge ${tradeSetup.verdict_tone || 'neutral'}`;
    }

    const wyckoffBadge = $('wyckoffPhaseBadge');
    if (wyckoffBadge) {
      wyckoffBadge.textContent = tradeSetup.wyckoff_phase || '—';
    }

    const disparityBadge = $('disparityBadge');
    if (disparityBadge) {
      const dispScore = tradeSetup.disparity_score != null ? (tradeSetup.disparity_score > 0 ? `+${tradeSetup.disparity_score}` : `${tradeSetup.disparity_score}`) : '0';
      disparityBadge.textContent = `Lệch pha: ${dispScore} · ${tradeSetup.disparity_status || 'Cân bằng'}`;
      if (tradeSetup.disparity_score >= 20) {
        disparityBadge.style.color = '#087b50';
        disparityBadge.style.background = 'rgba(8, 123, 80, 0.12)';
        disparityBadge.style.borderColor = 'rgba(8, 123, 80, 0.3)';
      } else if (tradeSetup.disparity_score <= -20) {
        disparityBadge.style.color = '#dc2626';
        disparityBadge.style.background = 'rgba(220, 38, 38, 0.12)';
        disparityBadge.style.borderColor = 'rgba(220, 38, 38, 0.3)';
      } else {
        disparityBadge.style.color = '#2563eb';
        disparityBadge.style.background = '#f0f4f8';
        disparityBadge.style.borderColor = '#dbeafe';
      }
    }

    const instCostEl = $('tradeInstCost');
    if (instCostEl) {
      instCostEl.textContent = tradeSetup.institutional_cost || '—';
    }

    const entryEl = $('tradeEntryZone');
    if (entryEl) {
      entryEl.textContent = tradeSetup.entry_zone || '—';
    }

    const stopEl = $('tradeStopLoss');
    if (stopEl) {
      stopEl.textContent = tradeSetup.stop_loss_text || '—';
    }

    const t1El = $('tradeTarget1');
    if (t1El) {
      t1El.textContent = tradeSetup.target_1_text || '—';
    }

    const t2El = $('tradeTarget2');
    if (t2El) {
      t2El.textContent = tradeSetup.target_2_text || '—';
    }

    const rrEl = $('tradeRrRatio');
    if (rrEl) {
      rrEl.textContent = tradeSetup.rr_ratio_text || '—';
    }

    const posEl = $('tradePositionSize');
    if (posEl) {
      posEl.textContent = tradeSetup.position_size || 'Khuyến nghị phân bổ';
    }

    const adviceEl = $('tradeActionAdvice');
    if (adviceEl) {
      adviceEl.textContent = tradeSetup.action_advice || 'Đang phân tích cấu trúc giá và dòng tiền…';
    }
  }

  function updateRsiDisplay(rsiVal) {
    const rsiEl = $('currentRsi');
    const statusEl = $('rsiStatus');
    if (!rsiEl) return;
    if (rsiVal == null || Number.isNaN(Number(rsiVal))) {
      rsiEl.textContent = '—';
      if (statusEl) { statusEl.textContent = '—'; statusEl.style.color = '#65736e'; }
      return;
    }
    const val = Number(rsiVal);
    rsiEl.textContent = formatNumber(val, 1);
    if (statusEl) {
      if (val < 30) {
        statusEl.textContent = 'Quá bán (Gom)';
        statusEl.style.color = '#087b50';
      } else if (val <= 50) {
        statusEl.textContent = 'Vùng thấp';
        statusEl.style.color = '#2563eb';
      } else if (val <= 70) {
        statusEl.textContent = 'Vùng cao';
        statusEl.style.color = '#d97706';
      } else {
        statusEl.textContent = 'Quá mua (Cẩn trọng)';
        statusEl.style.color = '#dc2626';
      }
    }
  }

  function updateEmaLegend(bar, stateItem) {
    const e20 = bar?.ema20 ?? stateItem?.ema20;
    const e50 = bar?.ema50 ?? stateItem?.ema50;
    const e100 = bar?.ema100 ?? stateItem?.ema100;
    const e200 = bar?.ema200 ?? stateItem?.ema200;

    if ($('valEma20')) $('valEma20').textContent = e20 != null ? formatNumber(e20, 0) : '—';
    if ($('valEma50')) $('valEma50').textContent = e50 != null ? formatNumber(e50, 0) : '—';
    if ($('valEma100')) $('valEma100').textContent = e100 != null ? formatNumber(e100, 0) : '—';
    if ($('valEma200')) $('valEma200').textContent = e200 != null ? formatNumber(e200, 0) : '—';
  }

  function inspectSession(index) {
    if (!state.analysis || !state.analysis.states || index < 0 || index >= state.analysis.states.length) return;
    const s = state.analysis.states[index];
    const dateStr = s.date || '—';
    $('currentState').textContent = s.label || '—';
    $('currentState').title = s.label || '';
    $('currentState').style.color = STATE_COLORS[s.state] || '#173a34';
    $('currentDate').textContent = `Phiên ${dateStr}`;

    const actionBadge = $('currentActionBadge');
    if (actionBadge) {
      const act = s.action_code || 'WATCH';
      actionBadge.textContent = ACTION_TEXTS[act] || act;
      actionBadge.style.color = ACTION_COLORS[act] || '#65736e';
    }

    $('opportunityScore').textContent = s.opportunity_score ?? '—';
    $('riskScore').textContent = s.risk_score ?? '—';
    $('bottomConfidence').textContent = s.quality_score ?? s.bottom_confidence ?? '—';
    
    const scoreTypeEl = $('scoreTypeLabel');
    if (scoreTypeEl) {
      const st = s.score_type || 'BOTTOM_QUALITY';
      scoreTypeEl.textContent = `(${SCORE_TYPE_LABELS[st] || '/100'})`;
    }

    $('currentAperture').textContent = formatNumber(s.aperture, 1);
    updateRsiDisplay(s.rsi14);

    if ($('priceDateBadge')) $('priceDateBadge').textContent = `Phiên: ${dateStr}`;
    updateEmaLegend(state.analysis.bars?.[index], s);
    updateSmartMoneyHeader(s);

    const crowdEl = $('crowdSentiment');
    if (crowdEl) {
      crowdEl.classList.add('is-active');
      const valEl = crowdEl.querySelector('.bottom-crowd-value');
      const stateEl = crowdEl.querySelector('.bottom-crowd-state');
      const dotEl = crowdEl.querySelector('.bottom-crowd-dot');
      if (valEl) valEl.textContent = formatNumber(s.aperture ?? s.market_emotion_score, 1);
      if (stateEl) {
        stateEl.textContent = s.emotion_state_label || s.crowd_sentiment || '—';
        const color = s.emotion_state_color || (
          Number(s.aperture) >= 80 ? '#8b5cf6' :
          Number(s.aperture) >= 65 ? '#087b50' :
          Number(s.aperture) >= 55 ? '#2563eb' :
          Number(s.aperture) >= 45 ? '#64748b' :
          Number(s.aperture) >= 35 ? '#d97706' : '#dc2626'
        );
        stateEl.style.color = color;
        if (dotEl) dotEl.style.backgroundColor = color;
      }
    }

    const ts = s.trade_setup || (state.analysis.current && state.analysis.current.trade_setup);
    if (ts) renderTradePlan(ts);

    const conditions = s.conditions || [];
    if ($('conditionList')) {
      $('conditionList').innerHTML = conditions.length ? conditions.map((item) => `<li>${escapeHtml(item)}</li>`).join('') : '<li>Chưa có đủ điều kiện đồng thuận.</li>';
    }
  }

  function renderQuality(data) {
    const quality = data.data_quality || {};
    const line = $('bottomQualityLine');
    const status = quality.status || 'unknown';
    const sessions = data.metadata?.actual_bars || 0;
    line.classList.toggle('is-warning', status !== 'valid');
    const warning = (quality.warnings || []).join(' ');
    line.textContent = `Nguồn: ${quality.source || 'Không xác định'} · ${sessions} phiên · Chất lượng: ${status}${warning ? ` · ${warning}` : ''}`;
  }

  function renderCurrent(data) {
    const current = data.current;
    const dateStr = current?.date || '—';
    $('currentState').textContent = current?.label || '—';
    $('currentState').title = current?.label || '';
    $('currentState').style.color = STATE_COLORS[current?.state] || '#173a34';
    $('currentDate').textContent = current?.date ? `Phiên ${current.date}` : '—';

    const actionBadge = $('currentActionBadge');
    if (actionBadge) {
      const act = current?.action_code || 'WATCH';
      actionBadge.textContent = ACTION_TEXTS[act] || act;
      actionBadge.style.color = ACTION_COLORS[act] || '#65736e';
    }

    $('opportunityScore').textContent = current?.opportunity_score ?? '—';
    $('riskScore').textContent = current?.risk_score ?? '—';
    $('bottomConfidence').textContent = current?.quality_score ?? current?.bottom_confidence ?? '—';
    
    const scoreTypeEl = $('scoreTypeLabel');
    if (scoreTypeEl) {
      const st = current?.score_type || 'BOTTOM_QUALITY';
      scoreTypeEl.textContent = `(${SCORE_TYPE_LABELS[st] || '/100'})`;
    }

    $('currentAperture').textContent = formatNumber(current?.aperture, 1);
    updateRsiDisplay(current?.rsi14 ?? data.bars?.[data.bars.length - 1]?.rsi14);
    const conditions = current?.conditions || [];
    if ($('conditionList')) {
      $('conditionList').innerHTML = conditions.length ? conditions.map((item) => `<li>${escapeHtml(item)}</li>`).join('') : '<li>Chưa có đủ điều kiện đồng thuận.</li>';
    }
    if ($('invalidationText')) {
      $('invalidationText').textContent = current?.invalidation || '—';
    }

    if ($('priceDateBadge')) $('priceDateBadge').textContent = `Phiên: ${dateStr}`;
    updateEmaLegend(data.bars?.[data.bars.length - 1], current);
    updateSmartMoneyHeader(current);

    const crowdEl = $('crowdSentiment');
    if (crowdEl) {
      crowdEl.classList.remove('is-active');
      const valEl = crowdEl.querySelector('.bottom-crowd-value');
      const stateEl = crowdEl.querySelector('.bottom-crowd-state');
      const dotEl = crowdEl.querySelector('.bottom-crowd-dot');
      if (valEl) valEl.textContent = current?.aperture != null ? formatNumber(current.aperture, 1) : '—';
      if (stateEl) {
        stateEl.textContent = current?.emotion_state_label || current?.crowd_sentiment || '—';
        const color = current?.emotion_state_color || (
          Number(current?.aperture) >= 80 ? '#8b5cf6' :
          Number(current?.aperture) >= 65 ? '#087b50' :
          Number(current?.aperture) >= 55 ? '#2563eb' :
          Number(current?.aperture) >= 45 ? '#64748b' :
          Number(current?.aperture) >= 35 ? '#d97706' : '#dc2626'
        );
        stateEl.style.color = color;
        if (dotEl) dotEl.style.backgroundColor = color;
      }
    }

    const ts = current?.trade_setup || data.trade_setup;
    if (ts) renderTradePlan(ts);
  }

  function getBaseTvOptions(height, hideTimeScale = false) {
    return {
      width: 800,
      height,
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#65736e', fontFamily: 'Inter, sans-serif', fontSize: 11 },
      grid: { vertLines: { color: 'rgba(23, 58, 52, 0.06)' }, horzLines: { color: 'rgba(23, 58, 52, 0.06)' } },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: {
          color: '#087b50',
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: '#087b50',
          visible: true
        },
        horzLine: {
          color: '#087b50',
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: '#087b50',
          visible: true
        }
      },
      rightPriceScale: { borderColor: '#d8ddd8', scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: '#d8ddd8', visible: !hideTimeScale, timeVisible: true, secondsVisible: false, rightOffset: 6 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true }
    };
  }

  function syncTimeScales(charts) {
    const list = Object.values(charts).filter(Boolean);
    list.forEach((chart) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range || state.isSyncing) return;
        state.isSyncing = true;
        list.forEach((target) => { if (target !== chart) target.timeScale().setVisibleLogicalRange(range); });
        state.isSyncing = false;
      });
    });
  }

  function normalizeChartTime(time) {
    if (!time) return null;
    if (typeof time === 'string') return time;
    if (typeof time === 'object' && time.year) {
      return `${time.year}-${String(time.month).padStart(2, '0')}-${String(time.day).padStart(2, '0')}`;
    }
    return String(time);
  }

  function syncCrosshairs(charts, data) {
    const dateToIndex = new Map();
    (data.states || []).forEach((s, idx) => dateToIndex.set(s.date, idx));
    (data.bars || []).forEach((b, idx) => {
      if (!dateToIndex.has(b.date)) dateToIndex.set(b.date, idx);
    });

    const chartKeys = ['price', 'flow', 'aperture'];

    function clearAllProgrammaticCrosshairs() {
      state.isCrosshairSyncing = true;
      chartKeys.forEach((key) => {
        if (charts[key]) {
          try { charts[key].clearCrosshairPosition(); } catch (_) {}
        }
      });
      state.isCrosshairSyncing = false;
    }

    chartKeys.forEach((sourceKey) => {
      const chart = charts[sourceKey];
      if (!chart) return;

      chart.subscribeCrosshairMove((param) => {
        if (state.isCrosshairSyncing) return;

        if (!param || !param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
          clearAllProgrammaticCrosshairs();
          renderCurrent(data);
          return;
        }

        const dateStr = normalizeChartTime(param.time);
        const idx = dateToIndex.get(dateStr);
        if (idx === undefined) return;

        inspectSession(idx);

        const bar = data.bars?.[idx];
        const stateItem = data.states?.[idx];
        const seriesItem = data.series?.[idx];

        const priceVal = bar?.close ?? stateItem?.close;
        const flowVal = seriesItem?.flow_pct ?? stateItem?.flow_pct ?? 50;
        const apVal = seriesItem?.aperture ?? stateItem?.aperture ?? 50;

        state.isCrosshairSyncing = true;
        try {
          if (sourceKey !== 'price' && charts.price && state.series.price && priceVal != null) {
            charts.price.setCrosshairPosition(priceVal, param.time, state.series.price);
          }
          if (sourceKey !== 'flow' && charts.flow && state.series.flow && flowVal != null) {
            charts.flow.setCrosshairPosition(flowVal, param.time, state.series.flow);
          }
          if (sourceKey !== 'aperture' && charts.aperture && state.series.aperture && apVal != null) {
            charts.aperture.setCrosshairPosition(apVal, param.time, state.series.aperture);
          }
        } catch (_) {}
        state.isCrosshairSyncing = false;
      });

      const container = $(`${sourceKey}Chart`);
      if (container) {
        container.addEventListener('mouseleave', () => {
          if (state.isCrosshairSyncing) return;
          clearAllProgrammaticCrosshairs();
          renderCurrent(data);
        });
      }
    });
  }

  function renderPriceTvChart(data) {
    const container = $('priceChart');
    container.innerHTML = '';
    const chart = LightweightCharts.createChart(container, { ...getBaseTvOptions(380, false), width: container.clientWidth || 800 });
    state.charts.price = chart;
    const candleSeries = chart.addCandlestickSeries({ upColor: '#087b50', downColor: '#dc2626', borderUpColor: '#087b50', borderDownColor: '#dc2626', wickUpColor: '#087b50', wickDownColor: '#dc2626' });
    const ema20Series = chart.addLineSeries({ color: '#f59e0b', lineWidth: 1.8, priceLineVisible: false, lastValueVisible: false, title: 'EMA20' });
    const ema50Series = chart.addLineSeries({ color: '#087b50', lineWidth: 1.8, priceLineVisible: false, lastValueVisible: false, title: 'EMA50' });
    const ema100Series = chart.addLineSeries({ color: '#2563eb', lineWidth: 1.8, priceLineVisible: false, lastValueVisible: false, title: 'EMA100' });
    const ema200Series = chart.addLineSeries({ color: '#8b5cf6', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: 'EMA200' });

    // Integrated Volume Histogram
    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume_scale',
      scaleMargins: { top: 0.80, bottom: 0 },
    });
    chart.priceScale('volume_scale').applyOptions({
      scaleMargins: { top: 0.80, bottom: 0 },
    });
    const volData = (data.bars || []).map((b) => {
      const clv = Number(b.clv || 0);
      const rvol = Number(b.volume_ratio20 || 1);
      let color = 'rgba(148, 163, 184, 0.40)';
      if (clv >= 0.25) {
        color = rvol >= 1.5 ? 'rgba(8, 123, 80, 0.80)' : 'rgba(8, 123, 80, 0.50)';
      } else if (clv <= -0.25) {
        color = rvol >= 1.5 ? 'rgba(220, 38, 38, 0.80)' : 'rgba(220, 38, 38, 0.50)';
      }
      return { time: b.date, value: b.volume, color };
    });
    volumeSeries.setData(volData);

    candleSeries.setData((data.bars || []).map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close })));
    ema20Series.setData((data.bars || []).filter(b => b.ema20 != null).map((b) => ({ time: b.date, value: b.ema20 })));
    ema50Series.setData((data.bars || []).filter(b => b.ema50 != null).map((b) => ({ time: b.date, value: b.ema50 })));
    ema100Series.setData((data.bars || []).filter(b => b.ema100 != null).map((b) => ({ time: b.date, value: b.ema100 })));
    ema200Series.setData((data.bars || []).filter(b => b.ema200 != null).map((b) => ({ time: b.date, value: b.ema200 })));

    const markers = [];
    const seenMarkerDates = new Set();
    (data.states || []).forEach((s) => {
      const isConfirmed = s.lifecycle_event === 'CONFIRMED' || s.signal_stage === 'CONFIRMED';
      const isCreated = s.lifecycle_event === 'CREATED' || s.signal_stage === 'CREATED' || (s.is_event && (s.state === 'BOTTOM_WATCH' || s.state === 'TOP_WATCH'));

      if (s.signal === 'BB') {
        const subtypeText = s.signal_subtype ? s.signal_subtype.replace('BB1_SPRING_CONFIRM', 'BB1').replace('BB2_SOS_BREAKOUT', 'BB2').replace('BB3_LPS_PULLBACK', 'BB3') : 'BB';
        markers.push({
          time: s.date,
          position: 'belowBar',
          color: '#087b50',
          shape: 'arrowUp',
          text: subtypeText,
          size: 1.3
        });
        seenMarkerDates.add(`${s.date}_BB`);
      } else if (s.signal === 'BS') {
        const subtypeText = s.signal_subtype ? s.signal_subtype.replace('BS1_CLIMAX_DISTRIBUTION', 'BS1').replace('BS2_SOW_BREAKDOWN', 'BS2') : 'BS';
        markers.push({
          time: s.date,
          position: 'aboveBar',
          color: '#dc2626',
          shape: 'arrowDown',
          text: subtypeText,
          size: 1.3
        });
        seenMarkerDates.add(`${s.date}_BS`);
      } else if ((s.state === 'BOTTOM_WATCH' || s.candidate_id?.startsWith('BOT_')) && isCreated && !seenMarkerDates.has(`${s.date}_BOT`)) {
        markers.push({
          time: s.date,
          position: 'belowBar',
          color: '#d97706',
          shape: 'circle',
          text: 'Dò đáy',
          size: 1
        });
        seenMarkerDates.add(`${s.date}_BOT`);
      } else if ((s.state === 'TOP_WATCH' || s.candidate_id?.startsWith('TOP_')) && isCreated && !seenMarkerDates.has(`${s.date}_TOP`)) {
        markers.push({
          time: s.date,
          position: 'aboveBar',
          color: '#ea580c',
          shape: 'circle',
          text: 'Cảnh báo đỉnh',
          size: 1
        });
        seenMarkerDates.add(`${s.date}_TOP`);
      } else if (s.outflow_event === 'OUTFLOW_CONFIRMED' && !seenMarkerDates.has(`${s.date}_OUTFLOW`)) {
        markers.push({
          time: s.date,
          position: 'aboveBar',
          color: '#ef4444',
          shape: 'arrowDown',
          text: 'Outflow',
          size: 1.2
        });
        seenMarkerDates.add(`${s.date}_OUTFLOW`);
      }
    });
    candleSeries.setMarkers(markers.sort((a, b) => (a.time > b.time ? 1 : -1)));
    state.series.price = candleSeries;
    state.series.volume = volumeSeries;
    state.series.ema20 = ema20Series;
    state.series.ema50 = ema50Series;
    state.series.ema100 = ema100Series;
    state.series.ema200 = ema200Series;

    updateEmaLegend(data.bars?.[data.bars.length - 1], data.current);
  }

  function renderFlowTvChart(data) {
    const container = $('flowChart');
    container.innerHTML = '';
    const chart = LightweightCharts.createChart(container, { ...getBaseTvOptions(220, false), width: container.clientWidth || 800 });
    state.charts.flow = chart;
    const pulseSeries = chart.addLineSeries({ color: '#eab308', lineWidth: 2, priceLineVisible: false });
    const flowSeries = chart.addLineSeries({ color: '#06b6d4', lineWidth: 2.2, priceLineVisible: false });
    const coreSeries = chart.addLineSeries({ color: '#10b981', lineWidth: 3, priceLineVisible: false });
    const rows = data.series || [];
    pulseSeries.setData(rows.map((r) => ({ time: r.date, value: r.pulse_pct ?? 50 })));
    flowSeries.setData(rows.map((r) => ({ time: r.date, value: r.flow_pct ?? 50 })));
    coreSeries.setData(rows.map((r) => ({ time: r.date, value: r.core_pct ?? 50 })));
    [20, 35, 50, 65, 80].forEach(p => pulseSeries.createPriceLine({
      price: p,
      color: (p === 50 ? 'rgba(100,116,139,0.85)' : (p === 35 || p === 65 ? 'rgba(148,163,184,0.45)' : 'rgba(157,168,163,0.7)')),
      lineWidth: (p === 50 ? 1.5 : 1),
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: (p === 65 ? 'Markup (65)' : (p === 35 ? 'Markdown (35)' : (p === 50 ? 'Center (50)' : '')))
    }));
    const divMarkers = (data.divergences || []).map((div) => {
      const isBull = div.type && div.type.includes('BULLISH');
      const isTriple = div.type && div.type.startsWith('TRIPLE');
      const isDual = div.type && (div.type.startsWith('DUAL') || div.type.startsWith('MACD_RSI'));
      let color;
      if (isTriple) {
        color = isBull ? '#059669' : '#b91c1c';
      } else if (isDual) {
        color = isBull ? '#087b50' : '#dc2626';
      } else if (div.type && div.type.startsWith('MACD')) {
        color = isBull ? '#0891b2' : '#ea580c';
      } else {
        color = isBull ? '#087b50' : '#dc2626';
      }
      return {
        time: div.date,
        position: 'inBar',
        color,
        shape: isTriple ? (isBull ? 'arrowUp' : 'arrowDown') : 'circle',
        size: isTriple ? 3 : (isDual ? 2 : 1.5)
      };
    });
    pulseSeries.setMarkers(divMarkers.sort((a, b) => (a.time > b.time ? 1 : -1)));
    state.series.flow = pulseSeries;
  }

  function renderApertureTvChart(data) {
    const container = $('apertureChart');
    container.innerHTML = '';
    const chart = LightweightCharts.createChart(container, { ...getBaseTvOptions(190, false), width: container.clientWidth || 800 });
    state.charts.aperture = chart;
    const hist = chart.addHistogramSeries({ priceFormat: { type: 'custom', formatter: (p) => `${Math.round(p)}` }, priceLineVisible: false });
    const rows = data.series || [];
    hist.setData(rows.map((r) => {
      const val = r.aperture ?? r.market_emotion_score ?? 50;
      const color = r.emotion_state_color || (
        val < 20 ? '#991b1b' :
        val < 35 ? '#dc2626' :
        val < 45 ? '#d97706' :
        val < 55 ? '#64748b' :
        val < 65 ? '#0284c7' :
        val < 80 ? '#087b50' : '#8b5cf6'
      );
      return { time: r.date, value: val, color };
    }));
    [
      { price: 20, color: '#dc2626', title: 'Hoảng loạn (20)' },
      { price: 45, color: 'rgba(217, 119, 6, 0.65)', title: 'Thận trọng (45)' },
      { price: 55, color: 'rgba(37, 99, 235, 0.65)', title: 'Hồi phục (55)' },
      { price: 80, color: '#8b5cf6', title: 'FOMO (80)' }
    ].forEach((line) => {
      hist.createPriceLine({
        price: line.price,
        color: line.color,
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: line.title
      });
    });
    state.series.aperture = hist;
  }

  function setupResizeObserver() {
    if (state.resizeObserver) state.resizeObserver.disconnect();
    state.resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const width = entry.contentRect.width;
        if (width > 0) Object.values(state.charts).forEach(c => c.applyOptions({ width }));
      }
    });
    const panel = $('analysisPanel');
    if (panel) state.resizeObserver.observe(panel);
  }

  function renderTimeline(events) {
    const el = $('eventTimeline');
    if (el) el.innerHTML = events.length ? events.slice().reverse().map((event) => `<article class="bottom-event" style="--event-color:${STATE_COLORS[event.state] || '#65736e'}"><strong>${escapeHtml(event.date)}</strong><span>${escapeHtml(event.label)}</span><small>Cơ hội ${event.opportunity_score} · Rủi ro ${event.risk_score} · Đáy ${event.bottom_confidence}</small></article>`).join('') : '<p>Chưa có lần chuyển trạng thái đủ điều kiện.</p>';
  }

  function renderAnalysis(data) {
    renderQuality(data);
    $('analysisEmpty').hidden = true;
    if (data.status !== 'ok') {
      $('analysisResults').hidden = true;
      $('analysisEmpty').hidden = false;
      $('analysisEmpty').innerHTML = `<strong>Không đủ dữ liệu</strong><p>Nguồn chỉ trả ${data.metadata?.actual_bars || 0} phiên hợp lệ; cần tối thiểu 60 phiên.</p>`;
      return;
    }
    $('analysisResults').hidden = false;
    renderCurrent(data);
    destroyCharts();
    renderPriceTvChart(data);
    renderFlowTvChart(data);
    renderApertureTvChart(data);
    syncTimeScales(state.charts);
    syncCrosshairs(state.charts, data);
    setupResizeObserver();
    renderTimeline(data.events || []);
  }

  async function runAnalysis(event) {
    event?.preventDefault();
    clearError();
    try {
      const { symbol, barLimit } = currentRequest();
      $('bottomSymbol').value = symbol;
      setBusy(true, `Đang phân tích ${symbol}…`);
      const data = await fetchJson(`/api/bottom-indicator/${encodeURIComponent(symbol)}?bar_limit=${barLimit}`);
      state.analysis = data;
      state.backtest = null;
      renderAnalysis(data);
    } catch (error) { if (error.name !== 'AbortError') showError(error.message || 'Không thể phân tích mã này.'); } finally { setBusy(false); }
  }

  function metricCard(label, value, suffix = '') { return `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(suffix)}</small></article>`; }
  function renderBacktestMetrics(summary) {
    $('backtestMetrics').innerHTML = [metricCard('Lợi nhuận', `${formatNumber(summary.return_pct)}%`), metricCard('Buy & Hold', `${formatNumber(summary.buy_hold_return_pct)}%`), metricCard('CAGR', `${formatNumber(summary.cagr_pct)}%`), metricCard('Sharpe', formatNumber(summary.sharpe)), metricCard('Max drawdown', `${formatNumber(summary.max_drawdown_pct)}%`), metricCard('Số giao dịch', String(summary.total_trades ?? 0)), metricCard('Win rate', summary.win_rate_pct == null ? '—' : `${formatNumber(summary.win_rate_pct)}%`), metricCard('Profit factor', formatNumber(summary.profit_factor)), metricCard('Vốn cuối kỳ', formatVnd(summary.final_equity))].join('');
  }

  function renderEquityCurve(equityCurve) {
    const container = $('equityChart');
    if (!container) return;
    container.innerHTML = '';
    if (!equityCurve || !equityCurve.length) return;
    const chart = LightweightCharts.createChart(container, { ...getBaseTvOptions(320, false), width: container.clientWidth || 800 });
    state.charts.equity = chart;
    const strategySeries = chart.addLineSeries({ color: '#087b50', lineWidth: 2.5, title: 'Chiến lược BB/BS' });
    const benchmarkSeries = chart.addLineSeries({ color: '#d97706', lineWidth: 1.8, lineStyle: LightweightCharts.LineStyle.Dashed, title: 'Buy & Hold' });
    strategySeries.setData(equityCurve.map((e) => ({ time: e.date, value: e.equity })));
    benchmarkSeries.setData(equityCurve.map((e) => ({ time: e.date, value: e.benchmark })));
  }

  function renderEventStudy(study) {
    const container = $('eventStudyTable');
    if (!container || !study) return;
    const labels = {
      BOTTOM_WATCH: 'Theo dõi đáy (Candidate Watch)',
      BB: 'Xác nhận Big Boy Mua (BB1/BB2/BB3)',
      DISTRIBUTION_CONTRACTION: 'Cảnh báo phân phối (Top Warning)',
      BS: 'Xác nhận Big Boy Bán (BS1/BS2)',
    };
    const horizons = [3, 5, 10, 20, 60];
    let html = `<table><thead><tr>
      <th>Tín hiệu / Danh mục</th>
      <th>Mốc</th>
      <th>Số mẫu (N)</th>
      <th>Lợi nhuận TB</th>
      <th>Tỷ lệ thắng</th>
      <th>MFE TB</th>
      <th>MAE TB</th>
      <th>Đáy giả</th>
    </tr></thead><tbody>`;

    let hasAny = false;
    Object.entries(study).forEach(([cat, data]) => {
      const catLabel = labels[cat] || cat;
      const metrics = data.metrics || {};
      horizons.forEach((h) => {
        const m = metrics[`${h}d`];
        if (!m || m.sample_size === 0) return;
        hasAny = true;
        const retColor = (m.median_return_pct || 0) >= 0 ? 'color:#087b50;font-weight:700;' : 'color:#dc2626;font-weight:700;';
        html += `<tr>
          <td><strong>${escapeHtml(catLabel)}</strong></td>
          <td>T+${h} phiên</td>
          <td>${m.sample_size}</td>
          <td style="${retColor}">${m.median_return_pct != null ? formatNumber(m.median_return_pct, 2) + '%' : '—'}</td>
          <td>${m.hit_rate_pct != null ? formatNumber(m.hit_rate_pct, 1) + '%' : '—'}</td>
          <td style="color:#087b50;">${m.median_mfe_pct != null ? '+' + formatNumber(m.median_mfe_pct, 2) + '%' : '—'}</td>
          <td style="color:#dc2626;">${m.median_mae_pct != null ? formatNumber(m.median_mae_pct, 2) + '%' : '—'}</td>
          <td>${m.false_rate_pct != null ? formatNumber(m.false_rate_pct, 1) + '%' : '—'}</td>
        </tr>`;
      });
    });

    if (!hasAny) {
      html += `<tr><td colspan="8" style="text-align:center;padding:18px;color:#65736e;">Chưa có đủ dữ liệu sự kiện để thống kê.</td></tr>`;
    }
    html += `</tbody></table>`;
    container.innerHTML = html;
  }

  function renderTrades(trades) {
    const rows = (trades || []).map((t) => {
      const ret = t.return_pct != null ? Number(t.return_pct) : 0;
      const retColor = ret >= 0 ? 'color:#087b50;font-weight:700;' : 'color:#dc2626;font-weight:700;';
      return `<tr>
        <td>${escapeHtml(t.entry_date)}</td>
        <td>${escapeHtml(t.exit_date)}</td>
        <td><strong style="color:#087b50;">${escapeHtml(t.subtype || 'BB')}</strong></td>
        <td>${Number(t.shares).toLocaleString('vi-VN')}</td>
        <td>${formatNumber(t.entry_price)}</td>
        <td>${formatNumber(t.exit_price)}</td>
        <td style="${retColor}">${formatVnd(t.pnl)}</td>
        <td style="${retColor}">${formatNumber(t.return_pct, 2)}%</td>
        <td>${escapeHtml(t.exit_reason || '')} (${t.holding_sessions || 0}P)</td>
      </tr>`;
    }).join('');
    $('tradeTable').innerHTML = `<table><thead><tr><th>Ngày vào</th><th>Ngày ra</th><th>Loại lệnh</th><th>Số lượng</th><th>Giá vào</th><th>Giá ra</th><th>Lãi/Lỗ</th><th>Lợi nhuận</th><th>Lý do thoát</th></tr></thead><tbody>${rows || '<tr><td colspan="9" style="text-align:center;padding:18px;">Không phát sinh giao dịch</td></tr>'}</tbody></table>`;
  }

  function renderBacktest(data) {
    renderQuality(data);
    if (data.status !== 'ok' || !data.summary) { $('backtestResults').hidden = true; $('backtestEmpty').hidden = false; return; }
    $('backtestEmpty').hidden = true; $('backtestResults').hidden = false;
    renderBacktestMetrics(data.summary);
    renderEquityCurve(data.equity_curve);
    renderEventStudy(data.event_study);
    renderTrades(data.trades);
  }

  async function runBacktest() {
    clearError();
    try {
      const { symbol, barLimit } = currentRequest();
      setBusy(true, `Đang kiểm định ${symbol}…`);
      const data = await fetchJson(`/api/bottom-indicator/${encodeURIComponent(symbol)}/backtest?bar_limit=${barLimit}`);
      state.backtest = data;
      renderBacktest(data);
    } catch (error) { if (error.name !== 'AbortError') showError(error.message || 'Không thể chạy kiểm định.'); } finally { setBusy(false); }
  }

  function selectTab(name) {
    const analysis = name === 'analysis';
    $('analysisTab').classList.toggle('is-active', analysis);
    $('analysisTab').setAttribute('aria-selected', String(analysis));
    $('backtestTab').classList.toggle('is-active', !analysis);
    $('backtestTab').setAttribute('aria-selected', String(!analysis));
    $('analysisPanel').hidden = !analysis;
    $('backtestPanel').hidden = analysis;
    if (!analysis && !state.backtest && state.analysis) {
      runBacktest();
    }
  }

  async function searchSymbols() {
    const query = $('bottomSymbol').value.trim();
    const suggestions = $('bottomSuggestions');
    if (query.length < 1) { suggestions.hidden = true; return; }
    try {
      const res = await fetch(`/api/search_suggest?q=${encodeURIComponent(query)}`, { cache: 'no-store' });
      const data = await res.json();
      const rows = (data.results || []).slice(0, 8);
      suggestions.innerHTML = rows.map((r) => `<button class="bottom-suggestion" type="button" role="option" data-symbol="${escapeHtml(r.symbol)}"><strong>${escapeHtml(r.symbol)}</strong><small>${escapeHtml(r.name || r.organ_name || '')}</small></button>`).join('');
      suggestions.hidden = rows.length === 0;
    } catch { suggestions.hidden = true; }
  }

  function focusable(dialog) { return [...dialog.querySelectorAll('button,[href],input,select,[tabindex]:not([tabindex="-1"])')].filter((i) => !i.disabled && !i.hidden); }
  function openMethodDialog() {
    const dialog = $('methodDialog');
    state.dialogTrigger = document.activeElement;
    dialog.hidden = false;
    dialog.setAttribute('aria-hidden', 'false');
    document.body.classList.add('bottom-dialog-open');
    dialog.querySelector('.bottom-dialog-panel').focus();
  }
  function closeMethodDialog() {
    const dialog = $('methodDialog');
    if (dialog.hidden) return;
    dialog.hidden = true;
    dialog.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('bottom-dialog-open');
    state.dialogTrigger?.focus?.({ preventScroll: true });
  }
  function handleDialogKey(event) {
    const dialog = $('methodDialog');
    if (dialog.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeMethodDialog(); return; }
    if (event.key !== 'Tab') return;
    const items = focusable(dialog);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function init() {
    $('bottomAnalysisForm').addEventListener('submit', runAnalysis);
    if ($('runBottomBacktest')) $('runBottomBacktest').addEventListener('click', runBacktest);
    $('analysisTab').addEventListener('click', () => selectTab('analysis'));
    $('backtestTab').addEventListener('click', () => selectTab('backtest'));
    $('bottomSymbol').addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(searchSymbols, 140); });
    $('bottomSymbol').addEventListener('keydown', (event) => { if (event.key === 'Escape') $('bottomSuggestions').hidden = true; });
    $('bottomSuggestions').addEventListener('click', (event) => { const button = event.target.closest('[data-symbol]'); if (!button) return; $('bottomSymbol').value = button.dataset.symbol; $('bottomSuggestions').hidden = true; $('bottomSubmit').focus(); });
    document.addEventListener('click', (event) => { if (!event.target.closest('.bottom-symbol-field')) $('bottomSuggestions').hidden = true; });
    $('openMethodDialog').addEventListener('click', openMethodDialog);
    $('closeMethodDialog').addEventListener('click', closeMethodDialog);
    document.querySelector('[data-close-method]').addEventListener('click', closeMethodDialog);
    document.addEventListener('keydown', handleDialogKey);
  }
  init();
})();
