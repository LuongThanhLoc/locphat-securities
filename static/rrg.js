/**
 * Relative Rotation Graph (RRG) Canvas & Data Controller for Lộc Phát Securities
 *
 * Professional Technical Intelligence Charting System
 * Features:
 *  - Clean multi-pass canvas rendering with high-DPR crispness & symmetric 100,100 centering.
 *  - Ultra-fast & accurate mouse interaction: hover over head dot, label box, OR tail line.
 *  - Focus Isolation Mode on hover: dims non-selected tail web to 0.08 alpha, brings hovered stock to top with glowing halo & thick line.
 *  - Gradient tail strokes with movement direction arrowheads showing rotation vector.
 *  - Dynamic non-overlapping grid & axis ticks based on dynamic range.
 *  - Interactive Quadrant filter badges (Dẫn Dắt, Suy Yếu, Hồi Phục, Tụt Hậu) & quick ticker search.
 *  - Bi-directional hover sync between chart nodes and stock data table.
 */
(() => {
  // State variables
  let rrgData = null;
  let animTimer = null;
  let isPlaying = false;
  let animFrameIndex = 0;
  let hoverItem = null;
  let lastTouchSymbol = null;
  let lastTouchAt = 0;
  let suppressSyntheticClick = false;
  let selectedQuadFilter = null; // null | 'LEADING' | 'WEAKENING' | 'LAGGING' | 'IMPROVING'
  let searchQuery = '';
  let customSymbolsCsv = '';
  let showAllLabels = false;
  let showRotationTails = false;
  const pinnedSymbols = new Set();
  const MAX_PINNED = 5;
  let sortKeys = [
    { key: 'rotation_score', direction: 'desc' },
    { key: 'symbol', direction: 'asc' }
  ];

  // Render cache for hover detection
  let currentRenderData = {
    headPosMap: new Map(), // symbol -> { headX, headY, item, color }
    placedLabels: [],      // { x, y, w, h, symbol, item }
    tailLines: []          // { symbol, item, pts: [{x, y}] }
  };

  // DOM elements
  const canvas = document.getElementById('rrgCanvas');
  const ctx = canvas ? canvas.getContext('2d') : null;
  const tooltipEl = document.getElementById('rrgTooltip');
  const loadingOverlay = document.getElementById('rrgLoadingOverlay');
  const loadingSub = document.getElementById('rrgLoadingSub');
  const chartWrap = document.getElementById('rrgChartWrap');

  const selectGroup = document.getElementById('selectGroup');
  const selectBenchmark = document.getElementById('selectBenchmark');
  const selectPeriod = document.getElementById('selectPeriod');
  const selectTailLength = document.getElementById('selectTailLength');
  const btnPlayAnimation = document.getElementById('btnPlayAnimation');
  const btnToggleTails = document.getElementById('btnToggleTails');
  const tailModeState = document.getElementById('tailModeState');
  const btnRefreshRrg = document.getElementById('btnRefreshRrg');
  const rrgTableBody = document.getElementById('rrgTableBody');
  const groupCountBadge = document.getElementById('groupCountBadge');
  const customPanel = document.getElementById('customSymbolsPanel');
  const customInput = document.getElementById('customSymbolsInput');
  const customApply = document.getElementById('btnApplyCustomSymbols');
  const customCount = document.getElementById('customSymbolsCount');
  const radarGrid = document.getElementById('rrgRadarGrid');
  const pinnedCount = document.getElementById('pinnedCount');
  const dataAlert = document.getElementById('rrgDataAlert');

  // Filter & Search controls
  const searchTickerInput = document.getElementById('searchTickerInput');
  const btnFilterLeading = document.getElementById('btnFilterLeading');
  const btnFilterWeakening = document.getElementById('btnFilterWeakening');
  const btnFilterImproving = document.getElementById('btnFilterImproving');
  const btnFilterLagging = document.getElementById('btnFilterLagging');
  const btnFilterAll = document.getElementById('btnFilterAll');

  // ---------- Canvas sizing ----------
  function resizeCanvas() {
    if (!canvas) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    renderRrgChart();
  }

  // ---------- Loading overlay ----------
  function showLoading(subtext) {
    if (loadingSub) loadingSub.textContent = subtext || 'Đang tải dữ liệu…';
    if (loadingOverlay) loadingOverlay.classList.remove('is-hidden');
    if (chartWrap) chartWrap.classList.add('is-faded');
  }
  function hideLoading() {
    if (loadingOverlay) loadingOverlay.classList.add('is-hidden');
    if (chartWrap) chartWrap.classList.remove('is-faded');
  }

  function renderRadarLoading() {
    if (!radarGrid) return;
    radarGrid.innerHTML = `
      <div class="rrg-radar-status p-5 text-xs text-slate-400 bg-slate-900 flex items-center justify-center font-semibold" role="status" aria-live="polite">
        <i class="fa-solid fa-spinner fa-spin mr-2 text-emerald-400" aria-hidden="true"></i>
        Đang Tải Dữ Liệu RRG…
      </div>`;
  }

  function renderRadarError() {
    if (!radarGrid) return;
    radarGrid.innerHTML = `
      <div class="rrg-radar-status p-5 text-xs text-red-400 bg-slate-900 flex items-center justify-center font-semibold" role="status" aria-live="polite">
        <i class="fa-solid fa-triangle-exclamation mr-2" aria-hidden="true"></i>
        Dữ liệu Rotation Radar đang được đồng bộ. Vui lòng thử lại sau ít phút.
      </div>`;
  }

  // ---------- Numeric helpers ----------
  function safeNum(v, digits = 2) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    return Number(v).toFixed(digits);
  }

  function fmtVnd(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    return Number(v).toLocaleString('vi-VN') + ' đ';
  }

  function fmtPct(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    const sign = v >= 0 ? '+' : '';
    return sign + Number(v).toFixed(2) + '%';
  }

  function dataStatusBadge(status) {
    switch (status) {
      case 'ok':
        return { label: 'đã kiểm định', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' };
      case 'stale_valid':
        return { label: 'dữ liệu dự phòng', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/40' };
      case 'insufficient_history':
        return { label: 'thiếu lịch sử', cls: 'bg-slate-800 text-slate-400 border-slate-700' };
      case 'inactive':
        return { label: 'tạm ngừng', cls: 'bg-slate-800 text-slate-400 border-slate-700' };
      case 'data_invalid':
        return { label: 'dữ liệu lỗi', cls: 'bg-red-500/10 text-red-400 border-red-500/40' };
      case 'source_unavailable':
      case 'no_data':
      default:
        return { label: 'nguồn tạm lỗi', cls: 'bg-red-500/10 text-red-400 border-red-500/40' };
    }
  }

  function showDataAlert(message, tone = 'warning') {
    if (!dataAlert) return;
    const cls = tone === 'success'
      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
      : 'bg-amber-500/10 border-amber-500/30 text-amber-300';
    dataAlert.className = `px-4 py-3 rounded-lg border text-[11px] font-semibold ${cls}`;
    dataAlert.innerHTML = message;
  }

  function hideDataAlert() {
    if (dataAlert) dataAlert.classList.add('hidden');
  }

  // ---------- Data fetch ----------
  async function loadRrgData() {
    const group = selectGroup ? selectGroup.value : 'SMC_TOP';
    const benchmark = selectBenchmark ? selectBenchmark.value : 'VNINDEX';
    const period = selectPeriod ? selectPeriod.value : '14';
    const tailLength = selectTailLength ? selectTailLength.value : '15';

    const hadCompleteDataset = !!(rrgData && rrgData.coverage_status === 'complete' && rrgData.data?.length);
    showLoading(`Đang đồng bộ dữ liệu đã kiểm định cho ${benchmark}…`);

    if (rrgTableBody && !hadCompleteDataset) {
      rrgTableBody.innerHTML = `
        <tr>
          <td colspan="10" class="text-center py-8 text-slate-400 font-semibold">
            <i class="fa-solid fa-spinner fa-spin mr-2 text-emerald-400"></i>
            Đang Tải Dữ Liệu RRG…
          </td>
        </tr>`;
    }
    if (!hadCompleteDataset) renderRadarLoading();

    let url = `/api/rrg/data?group=${encodeURIComponent(group)}&benchmark=${encodeURIComponent(benchmark)}&tail_length=${encodeURIComponent(tailLength)}&period=${encodeURIComponent(period)}`;
    if (group === 'CUSTOM' && customSymbolsCsv) {
      url += `&symbols=${encodeURIComponent(customSymbolsCsv)}`;
    }

    try {
      setTimeout(() => {
        if (loadingOverlay && !loadingOverlay.classList.contains('is-hidden')) {
          if (loadingSub) loadingSub.textContent = 'Đang tính toán LP RS-Ratio / RS-Momentum cho từng mã…';
        }
      }, 1200);

      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) {
        let payload = null;
        try { payload = await res.json(); } catch (_) { /* no-op */ }
        const error = new Error(payload?.detail?.message || `API fetch error: ${res.status}`);
        error.status = res.status;
        error.payload = payload;
        throw error;
      }
      rrgData = await res.json();

      const availableSymbols = new Set((rrgData.data || []).map((item) => item.symbol));
      [...pinnedSymbols].forEach((symbol) => {
        if (!availableSymbols.has(symbol)) pinnedSymbols.delete(symbol);
      });

      rebuildGroupSelector(rrgData.preset_groups || [], group);

      animFrameIndex = (rrgData.tail_length || 15) - 1;
      updateSummaryCounts();
      renderRrgChart();
      renderRotationRadar();
      renderRrgTable();
      if (rrgData.has_stale_data) {
        showDataAlert(`<i class="fa-solid fa-clock-rotate-left mr-1"></i> Dữ liệu dự phòng đã kiểm định, cập nhật đến <b>${rrgData.data_as_of || '—'}</b>. Hệ thống đang tự đồng bộ nguồn trực tiếp.`);
      } else {
        hideDataAlert();
      }
      hideLoading();
    } catch (err) {
      console.error('Failed to load RRG data:', err);
      hideLoading();
      if (hadCompleteDataset) {
        const missing = err.payload?.detail?.missing_symbols || [];
        showDataAlert(`<i class="fa-solid fa-rotate mr-1"></i> Đang đồng bộ dữ liệu${missing.length ? ` cho <b>${missing.join(', ')}</b>` : ''}. Bảng đang giữ nguyên dataset hoàn chỉnh gần nhất; không hiển thị dữ liệu thiếu hoặc sai.`);
        renderRrgChart();
        renderRotationRadar();
        renderRrgTable();
      } else {
        renderRadarError();
        if (rrgTableBody) {
          rrgTableBody.innerHTML = `
            <tr>
              <td colspan="10" class="text-center py-8 text-red-400 font-semibold" role="status" aria-live="polite">
                <i class="fa-solid fa-triangle-exclamation mr-2" aria-hidden="true"></i>
                Dữ liệu RRG đang được đồng bộ và chưa đạt độ phủ 100%. Vui lòng thử lại sau ít phút.
              </td>
            </tr>`;
        }
      }
    }
  }

  // ---------- Dropdown rebuild ----------
  function rebuildGroupSelector(presetGroups, selectedKey) {
    if (!selectGroup) return;

    const previous = selectedKey || selectGroup.value || 'SMC_TOP';

    const frag = document.createDocumentFragment();
    presetGroups.forEach((g) => {
      const opt = document.createElement('option');
      opt.value = g.key;
      opt.textContent = `${g.name} (${g.count})`;
      frag.appendChild(opt);
    });

    const customOpt = document.createElement('option');
    customOpt.value = 'CUSTOM';
    customOpt.textContent = '— Tùy chỉnh (nhập mã) —';
    frag.appendChild(customOpt);

    selectGroup.innerHTML = '';
    selectGroup.appendChild(frag);

    const known = presetGroups.some((g) => g.key === previous);
    if (previous === 'CUSTOM' || !known) {
      selectGroup.value = 'CUSTOM';
    } else {
      selectGroup.value = previous;
    }

    const match = presetGroups.find((g) => g.key === selectGroup.value);
    if (groupCountBadge) {
      groupCountBadge.textContent = match ? `${match.count} mã` : '';
    }
    if (customPanel) {
      customPanel.classList.toggle('hidden', selectGroup.value !== 'CUSTOM');
    }
  }

  // ---------- Summary badges ----------
  function updateSummaryCounts() {
    if (!rrgData) return;
    const q = rrgData.quadrant_counts || {};
    document.getElementById('countLeading').textContent = q.LEADING || 0;
    document.getElementById('countWeakening').textContent = q.WEAKENING || 0;
    document.getElementById('countImproving').textContent = q.IMPROVING || 0;
    document.getElementById('countLagging').textContent = q.LAGGING || 0;

    const timeEl = document.getElementById('tableUpdatedTime');
    if (timeEl && rrgData.updated_at) {
      timeEl.textContent = `Cập nhật: ${rrgData.updated_at}`;
    }
    updateFilterBadgeStyles();
  }

  function updateFilterBadgeStyles() {
    const badges = [
      { el: btnFilterLeading, key: 'LEADING', activeCls: 'ring-2 ring-emerald-400 bg-emerald-500/25' },
      { el: btnFilterWeakening, key: 'WEAKENING', activeCls: 'ring-2 ring-amber-400 bg-amber-500/25' },
      { el: btnFilterImproving, key: 'IMPROVING', activeCls: 'ring-2 ring-blue-400 bg-blue-500/25' },
      { el: btnFilterLagging, key: 'LAGGING', activeCls: 'ring-2 ring-red-400 bg-red-500/25' },
      { el: btnFilterAll, key: null, activeCls: 'ring-2 ring-slate-400 bg-slate-700' }
    ];

    badges.forEach(({ el, key, activeCls }) => {
      if (!el) return;
      const isActive = selectedQuadFilter === key;
      activeCls.split(' ').forEach((cls) => {
        el.classList.toggle(cls, isActive);
      });
      el.style.opacity = (selectedQuadFilter && !isActive && key !== null) ? '0.5' : '1.0';
    });
  }

  // ---------- Rotation score table sorting ----------
  const TEXT_SORT_KEYS = new Set(['symbol', 'sector', 'quadrant']);
  const QUADRANT_ORDER = { LEADING: 4, IMPROVING: 3, WEAKENING: 2, LAGGING: 1 };

  function sortValue(item, key) {
    if (key === 'quadrant') return QUADRANT_ORDER[item.quadrant?.id] ?? null;
    return item[key];
  }

  function compareItems(a, b) {
    for (const rule of sortKeys) {
      const av = sortValue(a, rule.key);
      const bv = sortValue(b, rule.key);
      const aMissing = av === null || av === undefined || Number.isNaN(av);
      const bMissing = bv === null || bv === undefined || Number.isNaN(bv);
      if (aMissing || bMissing) {
        if (aMissing !== bMissing) return aMissing ? 1 : -1;
        continue;
      }
      let comparison = 0;
      if (typeof av === 'string' || typeof bv === 'string') {
        comparison = String(av).localeCompare(String(bv), 'vi', { sensitivity: 'base' });
      } else {
        comparison = Number(av) - Number(bv);
      }
      if (comparison) return rule.direction === 'asc' ? comparison : -comparison;
    }
    return a.symbol.localeCompare(b.symbol);
  }

  function sortedItems() {
    return [...(rrgData?.data || [])].sort(compareItems);
  }

  function updateSortHeaders() {
    document.querySelectorAll('[data-sort-key]').forEach((button) => {
      const key = button.dataset.sortKey;
      const index = sortKeys.findIndex((rule) => rule.key === key);
      const indicator = button.querySelector('.rrg-sort-indicator');
      const th = button.closest('th');
      if (index < 0) {
        if (indicator) indicator.innerHTML = '<i class="fa-solid fa-sort is-idle" aria-hidden="true"></i>';
        if (th) th.removeAttribute('aria-sort');
        return;
      }
      const rule = sortKeys[index];
      const iconClass = rule.direction === 'asc' ? 'fa-arrow-up text-emerald-500' : 'fa-arrow-down text-red-500';
      if (indicator) indicator.innerHTML = `<i class="fa-solid ${iconClass}" aria-hidden="true"></i><sub class="ml-1">${index + 1}</sub>`;
      if (th) th.setAttribute('aria-sort', rule.direction === 'asc' ? 'ascending' : 'descending');
    });
  }

  function applySort(key, additive = false) {
    const existingIndex = sortKeys.findIndex((rule) => rule.key === key);
    const defaultDirection = TEXT_SORT_KEYS.has(key) ? 'asc' : 'desc';
    if (additive) {
      if (existingIndex >= 0) {
        sortKeys[existingIndex].direction = sortKeys[existingIndex].direction === 'asc' ? 'desc' : 'asc';
      } else {
        sortKeys.push({ key, direction: defaultDirection });
      }
      sortKeys = sortKeys.slice(0, 3);
    } else if (existingIndex === 0) {
      sortKeys = [{ key, direction: sortKeys[0].direction === 'asc' ? 'desc' : 'asc' }];
    } else {
      sortKeys = [{ key, direction: defaultDirection }];
    }
    if (key !== 'symbol' && !sortKeys.some((rule) => rule.key === 'symbol') && sortKeys.length < 3) {
      sortKeys.push({ key: 'symbol', direction: 'asc' });
    }
    renderRrgTable();
  }

  function updatePinnedCount() {
    if (pinnedCount) pinnedCount.textContent = `Đã ghim ${pinnedSymbols.size}/${MAX_PINNED} mã`;
  }

  function togglePin(symbol) {
    if (pinnedSymbols.has(symbol)) pinnedSymbols.delete(symbol);
    else if (pinnedSymbols.size < MAX_PINNED) pinnedSymbols.add(symbol);
    else return false;
    updatePinnedCount();
    renderRrgChart();
    renderRrgTable();
    renderRotationRadar();
    return true;
  }

  function renderRotationRadar() {
    if (!radarGrid || !rrgData) return;
    const configs = [
      { key: 'ACCELERATING', title: 'Đang tăng tốc', icon: 'fa-arrow-trend-up', color: '#174e9a' },
      { key: 'SUSTAINED_LEADER', title: 'Dẫn dắt bền', icon: 'fa-crown', color: '#075f35' },
      { key: 'WEAKENING_ALERT', title: 'Cảnh báo suy yếu', icon: 'fa-triangle-exclamation', color: '#a71f1f' }
    ];
    const radar = rrgData.rotation_radar || {};
    radarGrid.innerHTML = configs.map((config) => {
      const items = radar[config.key] || [];
      const chips = items.length ? items.map((item) => {
        const pinned = pinnedSymbols.has(item.symbol);
        return `<button type="button" data-radar-symbol="${item.symbol}" class="rrg-radar-symbol inline-flex items-center gap-2 px-2.5 py-1 border bg-white/70 hover:bg-white text-[11px] font-bold" style="border-color:${config.color}55;color:${config.color}" title="${item.heading_label || '—'} · Điểm xoay ${safeNum(item.rotation_score, 1)}">
          <i class="fa-solid ${pinned ? 'fa-thumbtack' : 'fa-circle-dot'}"></i>${item.symbol}<span class="font-mono opacity-70">${safeNum(item.rotation_score, 0)}</span>
        </button>`;
      }).join('') : '<span class="text-[11px] text-slate-500">Chưa có mã phù hợp</span>';
      return `<div class="rrg-radar-card p-4 bg-slate-900">
        <div class="flex items-center gap-2 mb-3 text-xs font-extrabold" style="color:${config.color}"><i class="fa-solid ${config.icon}"></i>${config.title}</div>
        <div class="flex flex-wrap gap-2">${chips}</div>
      </div>`;
    }).join('');
    updatePinnedCount();
    radarGrid.querySelectorAll('[data-radar-symbol]').forEach((button) => {
      button.addEventListener('click', () => {
        const symbol = button.dataset.radarSymbol;
        togglePin(symbol);
        requestAnimationFrame(() => document.querySelector(`#rrgTableBody tr[data-symbol="${symbol}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
      });
    });
  }

  // ---------- Axis bounds (Symmetric & Dynamic Centering around 100) ----------
  function computeBounds() {
    const fallback = { minVal: 90, maxVal: 110, step: 2, maxDev: 10 };
    if (!rrgData || !rrgData.data || !rrgData.data.length) return fallback;

    const deviations = [];
    rrgData.data.forEach((item) => {
      const tail = item.tail || [];
      const includeTail = showRotationTails;
      const points = includeTail ? tail : tail.slice(-1);
      points.forEach((pt) => {
        if (Number.isFinite(pt.rs_ratio)) deviations.push(Math.abs(pt.rs_ratio - 100));
        if (Number.isFinite(pt.rs_momentum)) deviations.push(Math.abs(pt.rs_momentum - 100));
      });
    });

    deviations.sort((a, b) => a - b);
    const percentileIndex = Math.max(0, Math.ceil(deviations.length * 0.95) - 1);
    let maxDev = Math.min(25, Math.max(4, (deviations[percentileIndex] || 4) * 1.15));
    if (maxDev <= 4) maxDev = 4;
    else if (maxDev <= 6) maxDev = 6;
    else if (maxDev <= 10) maxDev = 10;
    else if (maxDev <= 15) maxDev = 15;
    else maxDev = 25;

    const minVal = 100 - maxDev;
    const maxVal = 100 + maxDev;

    const span = maxVal - minVal;
    let step = 2;
    if (span <= 4) step = 1;
    else if (span <= 8) step = 1;
    else if (span <= 12) step = 2;
    else if (span <= 20) step = 2;
    else if (span <= 30) step = 5;
    else if (span <= 60) step = 10;
    else step = 20;

    return { minVal, maxVal, step, maxDev };
  }

  // ---------- Canvas render ----------
  function renderRrgChart() {
    if (!ctx || !canvas) return;
    try {
      _renderRrgChartImpl();
    } catch (err) {
      console.error('RRG render failed:', err);
    }
  }

  function _renderRrgChartImpl() {
    if (!ctx || !canvas) return;

    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = rect.width;
    const height = rect.height;

    // Reset render cache for hover detection
    currentRenderData = {
      headPosMap: new Map(),
      placedLabels: [],
      tailLines: []
    };

    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.scale(dpr, dpr);

    const pad = 54;
    const { minVal, maxVal, step } = computeBounds();

    function toX(val) {
      if (val === null || val === undefined || Number.isNaN(val)) return null;
      const raw = pad + ((val - minVal) / (maxVal - minVal)) * (width - 2 * pad);
      return Math.max(pad, Math.min(width - pad, raw));
    }
    function toY(val) {
      if (val === null || val === undefined || Number.isNaN(val)) return null;
      const raw = height - pad - ((val - minVal) / (maxVal - minVal)) * (height - 2 * pad);
      return Math.max(pad, Math.min(height - pad, raw));
    }

    const centerX = toX(100);
    const centerY = toY(100);

    // 1. Quadrant background tints
    ctx.fillStyle = 'rgba(16, 185, 129, 0.10)'; // Leading (Top-Right)
    ctx.fillRect(centerX, pad, width - pad - centerX, centerY - pad);

    ctx.fillStyle = 'rgba(245, 158, 11, 0.10)'; // Weakening (Bottom-Right)
    ctx.fillRect(centerX, centerY, width - pad - centerX, height - pad - centerY);

    ctx.fillStyle = 'rgba(239, 68, 68, 0.085)'; // Lagging (Bottom-Left)
    ctx.fillRect(pad, centerY, centerX - pad, height - pad - centerY);

    ctx.fillStyle = 'rgba(59, 130, 246, 0.10)'; // Improving (Top-Left)
    ctx.fillRect(pad, pad, centerX - pad, centerY - pad);

    // 2. Dynamic Grid lines (Non-overlapping)
    ctx.strokeStyle = 'rgba(42, 57, 64, 0.19)';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 4]);

    for (let v = minVal; v <= maxVal + 0.001; v += step) {
      const roundedV = Math.round(v * 10) / 10;
      if (Math.abs(roundedV - 100) < 0.001) continue; // Skip 100 center crosshair

      const gx = toX(roundedV);
      if (gx !== null && gx > pad && gx < width - pad) {
        ctx.beginPath();
        ctx.moveTo(gx, pad);
        ctx.lineTo(gx, height - pad);
        ctx.stroke();
      }

      const gy = toY(roundedV);
      if (gy !== null && gy > pad && gy < height - pad) {
        ctx.beginPath();
        ctx.moveTo(pad, gy);
        ctx.lineTo(width - pad, gy);
        ctx.stroke();
      }
    }
    ctx.setLineDash([]);

    // 3. Central Crosshairs (100, 100)
    ctx.strokeStyle = 'rgba(29, 49, 58, 0.55)';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(centerX, pad);
    ctx.lineTo(centerX, height - pad);
    ctx.moveTo(pad, centerY);
    ctx.lineTo(width - pad, centerY);
    ctx.stroke();
    ctx.setLineDash([]);

    // 4. Quadrant titles
    const compactQuadrantTitles = width < 520;
    ctx.font = `800 ${compactQuadrantTitles ? 10 : 12}px Inter, sans-serif`;
    ctx.textBaseline = 'top';

    ctx.fillStyle = '#10b981';
    ctx.textAlign = 'right';
    ctx.fillText(compactQuadrantTitles ? 'DẪN DẮT' : 'DẪN DẮT (LEADING)', width - pad - 12, pad + 10);

    ctx.fillStyle = '#f59e0b';
    ctx.fillText(compactQuadrantTitles ? 'SUY YẾU' : 'SUY YẾU (WEAKENING)', width - pad - 12, height - pad - 22);

    ctx.fillStyle = '#ef4444';
    ctx.textAlign = 'left';
    ctx.fillText(compactQuadrantTitles ? 'TỤT HẬU' : 'TỤT HẬU (LAGGING)', pad + 12, height - pad - 22);

    ctx.fillStyle = '#3b82f6';
    ctx.fillText(compactQuadrantTitles ? 'HỒI PHỤC' : 'HỒI PHỤC (IMPROVING)', pad + 12, pad + 10);

    // 5. Dynamic Tick Labels on Axes (Non-overlapping!)
    ctx.fillStyle = '#455961';
    ctx.font = '600 10px Inter, sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';

    for (let v = minVal; v <= maxVal + 0.001; v += step) {
      const roundedV = Math.round(v * 10) / 10;
      const y = toY(roundedV);
      if (y !== null && y >= pad && y <= height - pad) {
        ctx.fillText(roundedV.toFixed(roundedV % 1 === 0 ? 0 : 1), pad - 8, y);
      }
    }

    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let v = minVal; v <= maxVal + 0.001; v += step) {
      const roundedV = Math.round(v * 10) / 10;
      const x = toX(roundedV);
      if (x !== null && x >= pad && x <= width - pad) {
        ctx.fillText(roundedV.toFixed(roundedV % 1 === 0 ? 0 : 1), x, height - pad + 10);
      }
    }

    // 6. Axis Titles
    ctx.fillStyle = '#40545d';
    ctx.font = '600 11px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(`LP RS-Ratio (100 = ${rrgData ? rrgData.benchmark || 'VN-Index' : 'VN-Index'})`, width / 2, height - 16);

    ctx.save();
    ctx.translate(18, height / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('LP RS-Momentum (Động lượng)', 0, 0);
    ctx.restore();

    if (!rrgData || !rrgData.data) {
      ctx.restore();
      return;
    }

    // ---------- Pass Preparation: Categorize Stocks ----------
    const hasFocusMode = !!(hoverItem || selectedQuadFilter || searchQuery || pinnedSymbols.size);

    function checkActive(item) {
      if (selectedQuadFilter && (!item.quadrant || item.quadrant.id !== selectedQuadFilter)) return false;
      if (searchQuery && !item.symbol.toUpperCase().includes(searchQuery)) return false;
      return true;
    }

    const items = rrgData.data;

    // Split into background (unfocused) and foreground (focused) items
    const unfocusedItems = [];
    const focusedItems = [];

    items.forEach((item) => {
      const tail = item.tail || [];
      if (!tail.length) return;
      const isHovered = hoverItem && hoverItem.symbol === item.symbol;
      const isActive = checkActive(item);
      const isPinned = pinnedSymbols.has(item.symbol);

      if (hoverItem) {
        if (isHovered || isPinned) focusedItems.push(item);
        else unfocusedItems.push(item);
      } else if (selectedQuadFilter || searchQuery) {
        if (isActive || isPinned) focusedItems.push(item);
        else unfocusedItems.push(item);
      } else if (isPinned) {
        focusedItems.push(item);
      } else {
        unfocusedItems.push(item);
      }
    });

    // Draw order: unfocused items first, then focused items, then hovered item last!
    const drawSequence = [...unfocusedItems, ...focusedItems];

    // ---- PASS 1 & 2: Tail lines and Head dots ----
    drawSequence.forEach((item) => {
      const tail = item.tail || [];
      if (!tail.length) return;

      const currIdx = Math.min(animFrameIndex, tail.length - 1);
      const visibleTail = tail.slice(0, currIdx + 1);
      if (!visibleTail.length) return;

      const isHovered = hoverItem && hoverItem.symbol === item.symbol;
      const isActive = checkActive(item);
      const isPinned = pinnedSymbols.has(item.symbol);
      const shouldDrawTail = showRotationTails;

      const headColor = item.quadrant ? item.quadrant.color : '#10b981';

      // Always show the latest point; tails are opt-in through focus/pinning.
      const screenPts = [];
      const pointsToDraw = shouldDrawTail ? visibleTail : visibleTail.slice(-1);
      pointsToDraw.forEach((pt) => {
        const px = toX(pt.rs_ratio);
        const py = toY(pt.rs_momentum);
        if (px !== null && py !== null) {
          screenPts.push({ x: px, y: py, pt });
        }
      });
      if (!screenPts.length) return;

      // Cache tail line for hover detection
      if (shouldDrawTail && screenPts.length > 1) {
        currentRenderData.tailLines.push({ symbol: item.symbol, item, pts: screenPts });
      }

      const latestPt = screenPts[screenPts.length - 1];
      currentRenderData.headPosMap.set(item.symbol, {
        headX: latestPt.x,
        headY: latestPt.y,
        item,
        color: headColor
      });

      // Tail Opacity & Styling rules
      let lineAlpha = 0.62;
      let lineWidth = 1.5;

      if (hoverItem) {
        if (isHovered) {
          lineAlpha = 1.0;
          lineWidth = 3.5;
        } else if (!isPinned) {
          lineAlpha = 0.06; // Heavy dimming for non-hovered
          lineWidth = 1.0;
        }
      } else if (selectedQuadFilter || searchQuery) {
        if (isActive || isPinned) {
          lineAlpha = 0.85;
          lineWidth = 2.2;
        } else {
          lineAlpha = 0.06; // Heavy dimming for non-matching
          lineWidth = 1.0;
        }
      } else if (isPinned) {
        lineAlpha = 0.9;
        lineWidth = 2.5;
      } else {
        lineAlpha = 0.62;
        lineWidth = 1.5;
      }

      // Draw Gradient Tail Segment by Segment
      if (screenPts.length > 1) {
        ctx.shadowBlur = isHovered ? 14 : 0;
        ctx.shadowColor = isHovered ? headColor : 'transparent';

        for (let i = 0; i < screenPts.length - 1; i++) {
          const p1 = screenPts[i];
          const p2 = screenPts[i + 1];

          const progress = i / Math.max(1, screenPts.length - 1);
          const segmentAlpha = lineAlpha * (0.2 + 0.8 * progress);

          ctx.beginPath();
          ctx.moveTo(p1.x, p1.y);
          ctx.lineTo(p2.x, p2.y);
          ctx.strokeStyle = isHovered ? '#102f3d' : headColor;
          ctx.lineWidth = lineWidth;
          ctx.globalAlpha = segmentAlpha;
          ctx.stroke();
        }

        ctx.shadowBlur = 0;
        ctx.globalAlpha = 1.0;

        // Draw movement direction arrow on tail segment
        if ((isHovered || isPinned || (isActive && !hasFocusMode)) && screenPts.length >= 2) {
          const midIdx = Math.floor((screenPts.length - 1) * 0.7);
          const pA = screenPts[midIdx];
          const pB = screenPts[midIdx + 1];
          if (pA && pB) {
            drawDirectionArrow(ctx, pA.x, pA.y, pB.x, pB.y, headColor, isHovered ? 1.0 : 0.7);
          }
        }
      }

      // Start quadrant transition marker dot
      if (screenPts.length > 1) {
        const startQuad = (item.tail_quadrants || [])[0];
        const startColor = quadColor(startQuad);
        if (startColor && startQuad !== (item.quadrant && item.quadrant.id)) {
          const startPt = screenPts[0];
          ctx.beginPath();
          ctx.arc(startPt.x, startPt.y, isHovered ? 4 : 3, 0, 2 * Math.PI);
          ctx.fillStyle = startColor;
          ctx.globalAlpha = isHovered ? 0.9 : lineAlpha * 0.6;
          ctx.fill();
          ctx.globalAlpha = 1.0;
        }
      }

      // Head Node Dot
      let headRadius = 4.5;
      let headAlpha = 1.0;

      if (hoverItem) {
        headRadius = isHovered ? 8 : 4.5;
        headAlpha = (isHovered || isPinned) ? 1.0 : 0.06;
      } else if (selectedQuadFilter || searchQuery) {
        headRadius = (isActive || isPinned) ? 6 : 4.5;
        headAlpha = (isActive || isPinned) ? 1.0 : 0.06;
      }

      ctx.globalAlpha = headAlpha;

      // Pulsating outer glow ring for hovered stock
      if (isHovered) {
        ctx.beginPath();
        ctx.arc(latestPt.x, latestPt.y, 14, 0, 2 * Math.PI);
        ctx.strokeStyle = headColor;
        ctx.lineWidth = 2.5;
        ctx.globalAlpha = 0.8;
        ctx.stroke();

        ctx.beginPath();
        ctx.arc(latestPt.x, latestPt.y, 14, 0, 2 * Math.PI);
        ctx.fillStyle = headColor;
        ctx.globalAlpha = 0.2;
        ctx.fill();
        ctx.globalAlpha = 1.0;
      }

      // Solid Head Node
      ctx.beginPath();
      ctx.arc(latestPt.x, latestPt.y, headRadius, 0, 2 * Math.PI);
      ctx.fillStyle = isHovered ? '#ffffff' : headColor;
      ctx.fill();
      ctx.strokeStyle = isHovered ? headColor : '#374047';
      ctx.lineWidth = 1.8;
      ctx.stroke();

      const clipped = latestPt.pt.rs_ratio < minVal || latestPt.pt.rs_ratio > maxVal
        || latestPt.pt.rs_momentum < minVal || latestPt.pt.rs_momentum > maxVal;
      if (clipped) {
        ctx.beginPath();
        ctx.arc(latestPt.x, latestPt.y, headRadius + 4, 0, 2 * Math.PI);
        ctx.setLineDash([2, 2]);
        ctx.strokeStyle = headColor;
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
      }

      ctx.globalAlpha = 1.0;
    });

    // ---- PASS 3: Labels with Collision Avoidance ----
    const labelledSet = computeLabelledSet(rrgData.data, hoverItem);

    // Prioritize rendering hovered item label first, then active filter items, then others
    const labelItems = drawSequence.filter((item) => labelledSet.has(item.symbol));

    labelItems.forEach((item) => {
      const headInfo = currentRenderData.headPosMap.get(item.symbol);
      if (!headInfo) return;

      const { headX, headY, color: headColor } = headInfo;
      const isHovered = hoverItem && hoverItem.symbol === item.symbol;
      const isActive = checkActive(item);
      const isPinned = pinnedSymbols.has(item.symbol);

      let labelAlpha = 1.0;
      if (hoverItem) {
        if (!isHovered && !isPinned) labelAlpha = 0.1;
      } else if (selectedQuadFilter || searchQuery) {
        if (!isActive && !isPinned) labelAlpha = 0.1;
      }

      ctx.globalAlpha = labelAlpha;
      ctx.font = isHovered ? 'bold 12px Inter, sans-serif' : 'bold 10px Inter, sans-serif';
      const text = item.symbol;
      const padX = 5;
      const textWidth = ctx.measureText(text).width;
      const w = textWidth + padX * 2 + 4;
      const h = 18;

      // Radial candidates avoid the former single vertical label stack.
      const candidates = [
        [10, 0], [-(w + 10), 0], [10, -22], [-(w + 10), -22],
        [10, 22], [-(w + 10), 22], [-w / 2, -40], [-w / 2, 40]
      ];
      let chosen = null;

      for (const [dx, dy] of candidates) {
        const x = headX + dx;
        const y = headY - h / 2 + dy;
        const rect = { x: x - 4, y: y - 2, w, h };

        const overlaps = currentRenderData.placedLabels.some((p) =>
          rect.x < p.x + p.w &&
          rect.x + rect.w > p.x &&
          rect.y < p.y + p.h &&
          rect.y + rect.h > p.y
        );

        if (!overlaps && rect.x + rect.w < width - 10 && rect.y > 6 && rect.y + rect.h < height - 6) {
          chosen = { x, y, dx, dy };
          break;
        }
      }

      if (!chosen) {
        if (isHovered) {
          chosen = { x: headX + 10, y: headY - h / 2 - 22, dx: 10, dy: -22 };
        } else {
          return; // Skip non-hovered labels that collide completely
        }
      }

      currentRenderData.placedLabels.push({
        x: chosen.x - 4,
        y: chosen.y - 2,
        w,
        h,
        symbol: item.symbol,
        item
      });

      // Leader line if label was offset
      if (Math.abs(chosen.dy) >= 18 || chosen.dx < 0) {
        ctx.beginPath();
        ctx.moveTo(headX, headY);
        ctx.lineTo(chosen.x - 2, chosen.y + h / 2);
        ctx.strokeStyle = headColor;
        ctx.lineWidth = isHovered ? 1.5 : 1.0;
        ctx.stroke();
      }

      // Light editorial pill: colored border + dark label remains readable on every quadrant.
      ctx.fillStyle = (isHovered || (isActive && (selectedQuadFilter || searchQuery))) ? headColor : 'rgba(255, 253, 247, 0.96)';

      if (isHovered) {
        ctx.shadowBlur = 10;
        ctx.shadowColor = headColor;
      } else {
        ctx.shadowBlur = 0;
      }

      ctx.beginPath();
      roundRect(ctx, chosen.x - 4, chosen.y - 2, w, h, 4);
      ctx.fill();

      // Border for inactive
      if (!isHovered && !(isActive && (selectedQuadFilter || searchQuery))) {
        ctx.strokeStyle = headColor;
        ctx.lineWidth = 1.8;
        ctx.stroke();
      }

      ctx.shadowBlur = 0;

      // Label Text
      ctx.fillStyle = (isHovered || (isActive && (selectedQuadFilter || searchQuery))) ? '#ffffff' : '#17323d';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, chosen.x + padX - 2, chosen.y - 2 + h / 2);

      ctx.globalAlpha = 1.0;
    });

    // ---- Top Right Floating Action Chips ----
    drawLabelToggle(width - pad - 8, pad + 36);

    ctx.restore();
  }

  // Draw direction arrowhead along line segment
  function drawDirectionArrow(c, x1, y1, x2, y2, color, alpha) {
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const arrowLen = 7;
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;

    c.save();
    c.translate(mx, my);
    c.rotate(angle);
    c.beginPath();
    c.moveTo(0, 0);
    c.lineTo(-arrowLen, -arrowLen / 2.2);
    c.lineTo(-arrowLen, arrowLen / 2.2);
    c.closePath();
    c.fillStyle = color;
    c.globalAlpha = alpha;
    c.fill();
    c.restore();
  }

  function drawLabelToggle(x, y) {
    const label = showAllLabels ? 'Chỉ nhãn chính' : 'Hiện tất cả nhãn';
    ctx.font = 'bold 10px Inter, sans-serif';
    const w = ctx.measureText(label).width + 18;
    const h = 22;
    ctx.fillStyle = 'rgba(255, 253, 247, 0.94)';
    ctx.strokeStyle = 'rgba(55, 64, 71, 0.38)';
    ctx.lineWidth = 1;
    roundRect(ctx, x - w, y, w, h, 6);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#374047';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    ctx.fillText(label, x - w / 2, y + h / 2 + 0.5);
  }

  function computeLabelledSet(items, hoveredItem) {
    const valid = items.filter((it) => (it.tail || []).length > 0);
    valid.sort((a, b) => {
      return (b.rotation_score || 0) - (a.rotation_score || 0) || a.symbol.localeCompare(b.symbol);
    });
    const set = new Set();
    if (showAllLabels) {
      valid.forEach((it) => set.add(it.symbol));
    } else {
      valid.slice(0, 8).forEach((it) => set.add(it.symbol));
    }
    pinnedSymbols.forEach((symbol) => set.add(symbol));
    if (hoveredItem) set.add(hoveredItem.symbol);
    if (searchQuery) {
      valid.filter((it) => it.symbol.toUpperCase().includes(searchQuery)).forEach((it) => set.add(it.symbol));
    }
    return set;
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.lineTo(x + w - r, y);
    c.quadraticCurveTo(x + w, y, x + w, y + r);
    c.lineTo(x + w, y + h - r);
    c.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    c.lineTo(x + r, y + h);
    c.quadraticCurveTo(x, y + h, x, y + h - r);
    c.lineTo(x, y + r);
    c.quadraticCurveTo(x, y, x + r, y);
    c.closePath();
  }

  function quadColor(id) {
    switch (id) {
      case 'LEADING': return '#10b981';
      case 'WEAKENING': return '#f59e0b';
      case 'LAGGING': return '#ef4444';
      case 'IMPROVING': return '#3b82f6';
      default: return null;
    }
  }

  function quadTextColor(id) {
    switch (id) {
      case 'LEADING': return '#075f35';
      case 'WEAKENING': return '#805000';
      case 'LAGGING': return '#9f2725';
      case 'IMPROVING': return '#174e9a';
      default: return '#4e5a61';
    }
  }

  // ---------- Table render ----------
  function renderRrgTable() {
    if (!rrgTableBody || !rrgData || !rrgData.data) return;

    if (!rrgData.data.length) {
      rrgTableBody.innerHTML = `
        <tr>
          <td colspan="10" class="text-center py-8 text-slate-500 font-semibold">
            <i class="fa-solid fa-circle-info mr-2 text-amber-400"></i>
            ${rrgData.reason === 'benchmark_unavailable'
          ? 'Không thể tải dữ liệu tham chiếu (VNINDEX). Vui lòng thử lại sau ít phút.'
          : 'Không có dữ liệu cho nhóm đã chọn. Hãy thử nhóm khác hoặc nhập mã tùy chỉnh.'}
          </td>
        </tr>`;
      return;
    }

    const totalItems = rrgData.data.length;
    const noDataCount = rrgData.data.filter((it) => !it.quadrant).length;
    const noDataPct = totalItems ? (noDataCount / totalItems) * 100 : 0;

    let warningBanner = '';
    if (noDataPct >= 30) {
      warningBanner = `
        <tr class="bg-amber-500/5 border-b border-amber-500/20">
          <td colspan="10" class="px-4 py-3 text-amber-300 text-[11px]">
            <i class="fa-solid fa-triangle-exclamation"></i>
            <span><b>${noDataCount}/${totalItems} mã</b> chưa đủ 252 phiên thật để tính LP‑RRG. Hệ thống không tạo dữ liệu giả cho các mã này.</span>
          </td>
        </tr>`;
    }

    rrgTableBody.innerHTML = warningBanner + sortedItems().map((item) => {
      const q = item.quadrant || {};
      const hasData = !!(item.rs_ratio !== null && item.rs_ratio !== undefined);
      const chgColor = (item.change_5d_pct !== null && item.change_5d_pct !== undefined && item.change_5d_pct >= 0)
        ? 'text-emerald-400' : 'text-red-400';

      const isHovered = hoverItem && hoverItem.symbol === item.symbol;
      const matchesQuad = !selectedQuadFilter || (q.id === selectedQuadFilter);
      const matchesSearch = !searchQuery || item.symbol.toUpperCase().includes(searchQuery);
      const isDimmed = !matchesQuad || !matchesSearch || !hasData;
      const isPinned = pinnedSymbols.has(item.symbol);

      const rowCls = isHovered
        ? 'bg-emerald-500/20 ring-1 ring-emerald-500/40 text-white font-bold'
        : (isPinned ? 'bg-blue-500/10 ring-1 ring-blue-500/30' : (isDimmed ? 'opacity-40 hover:opacity-100 transition-all' : 'hover:bg-slate-800/50 transition-colors'));

      const badge = dataStatusBadge(item.data_status || (hasData ? 'ok' : 'no_data'));
      const score = item.rotation_score;
      const scoreColor = score >= 75 ? '#075f35' : score >= 50 ? '#174e9a' : score >= 30 ? '#805000' : '#9f2725';
      const quadrantTextColor = quadTextColor(q.id);

      return `
        <tr class="${rowCls} cursor-pointer" data-symbol="${item.symbol}">
          <td class="rrg-symbol-cell px-4 py-3 font-bold text-white">
            <div class="rrg-symbol-cell-inner flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background:${q.color || '#64748b'}"></span>
              <a href="/stock/${encodeURIComponent(item.symbol)}" class="hover:text-emerald-400 transition-colors">${item.symbol}</a>
            </div>
          </td>
          <td class="px-4 py-3 text-slate-400 text-[11px]">
            <div class="flex flex-col gap-1">
              <span>${item.sector || '—'}</span>
              <span class="inline-block self-start text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border ${badge.cls}">${badge.label}</span>
              ${item.data_status === 'insufficient_history' ? `<span class="text-[9px] text-slate-500">${item.history_sessions || 0}/${item.required_sessions || 252} phiên</span>` : ''}
              ${item.data_status === 'stale_valid' ? `<span class="text-[9px] text-amber-400">đến ${item.last_date || '—'} · trễ ${item.freshness_sessions || 0} phiên</span>` : ''}
            </div>
          </td>
          <td class="px-4 py-3">
            <span class="px-2.5 py-1 rounded-full text-[10px] font-bold" style="background:${q.bg || 'rgba(100,116,139,0.12)'}; color:${quadrantTextColor}; border:1px solid ${quadrantTextColor}66;">
              ${q.name || 'Không có dữ liệu'}
            </span>
          </td>
          <td class="px-4 py-3 text-right font-mono font-extrabold" title="Điểm xoay tương đối, không phải điểm mua" style="color:${scoreColor}">${safeNum(score, 1)}</td>
          <td class="px-4 py-3 text-right font-mono font-semibold text-slate-200">${safeNum(item.rs_ratio)}</td>
          <td class="px-4 py-3 text-right font-mono font-semibold text-slate-200">${safeNum(item.rs_momentum)}</td>
          <td class="px-4 py-3 text-right">
            <span class="font-semibold">${item.heading_label || '—'}</span>
            <span class="block text-[9px] text-slate-500 font-mono">v=${safeNum(item.velocity_5d)}</span>
          </td>
          <td class="px-4 py-3 text-right font-mono font-semibold text-slate-100">${fmtVnd(item.close)}</td>
          <td class="px-4 py-3 text-right font-mono font-bold ${chgColor}">${fmtPct(item.change_5d_pct)}</td>
          <td class="px-4 py-3 text-center">
            <div class="inline-flex items-center gap-1.5">
              <a href="/stock/${encodeURIComponent(item.symbol)}" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded font-semibold text-[10px] transition-colors">Xem</a>
              <a href="/backtest?symbol=${encodeURIComponent(item.symbol)}" class="px-2 py-1 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/30 rounded font-semibold text-[10px] transition-colors">RSI</a>
              <button type="button" data-pin-symbol="${item.symbol}" aria-label="${isPinned ? 'Bỏ ghim' : 'Ghim'} ${item.symbol}" class="px-2 py-1 border rounded font-semibold text-[10px] transition-colors ${isPinned ? 'bg-blue-500/20 text-blue-600 border-blue-500/40' : 'bg-slate-800 text-slate-300 border-slate-700'}"><i class="fa-solid fa-thumbtack"></i></button>
            </div>
          </td>
        </tr>`;
    }).join('');

    updateSortHeaders();

    // Attach row mouseenter/mouseleave for bi-directional hover sync
    const rows = rrgTableBody.querySelectorAll('tr[data-symbol]');
    rows.forEach((row) => {
      const sym = row.getAttribute('data-symbol');
      row.addEventListener('mouseenter', () => {
        const item = (rrgData.data || []).find((it) => it.symbol === sym);
        if (item && hoverItem !== item) {
          hoverItem = item;
          renderRrgChart();
        }
      });
      row.addEventListener('mouseleave', () => {
        if (hoverItem && hoverItem.symbol === sym) {
          hoverItem = null;
          renderRrgChart();
        }
      });
    });
    rrgTableBody.querySelectorAll('[data-pin-symbol]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        togglePin(button.dataset.pinSymbol);
      });
    });
  }

  // ---------- Animation toggle ----------
  function toggleAnimation() {
    if (!rrgData || !rrgData.data || !rrgData.data.length) return;

    isPlaying = !isPlaying;
    const textPlay = document.getElementById('textPlay');
    const iconPlay = document.getElementById('iconPlay');

    if (isPlaying) {
      if (textPlay) textPlay.textContent = 'Tạm dừng';
      if (iconPlay) iconPlay.className = 'fa-solid fa-pause';
      animFrameIndex = 0;
      animTimer = setInterval(() => {
        animFrameIndex++;
        if (animFrameIndex >= (rrgData.tail_length || 15)) {
          animFrameIndex = (rrgData.tail_length || 15) - 1;
          toggleAnimation();
        } else {
          renderRrgChart();
        }
      }, 250);
    } else {
      if (textPlay) textPlay.textContent = 'Chạy mô phỏng xoay';
      if (iconPlay) iconPlay.className = 'fa-solid fa-play';
      clearInterval(animTimer);
      animTimer = null;
      renderRrgChart();
    }
  }

  function updateTailModeButton() {
    if (!btnToggleTails) return;
    btnToggleTails.setAttribute('aria-pressed', String(showRotationTails));
    btnToggleTails.classList.toggle('is-active', showRotationTails);
    if (tailModeState) {
      tailModeState.innerHTML = showRotationTails
        ? '<i class="fa-solid fa-toggle-on text-[11px]"></i> ON'
        : '<i class="fa-solid fa-toggle-off text-[11px]"></i> OFF';
    }
    btnToggleTails.title = showRotationTails
      ? 'Tắt toàn bộ đường lịch sử của các cổ phiếu'
      : 'Bật toàn bộ đường lịch sử của các cổ phiếu';
  }

  function toggleRotationTails() {
    showRotationTails = !showRotationTails;
    updateTailModeButton();
    renderRrgChart();
  }

  // ---------- Tooltip / Hover Helpers ----------
  function pctChangeInTail(tail, periods) {
    if (!tail || tail.length < periods + 1) return null;
    const last = tail[tail.length - 1].close;
    const prev = tail[tail.length - 1 - periods].close;
    if (last === null || prev === null || prev === 0) return null;
    return ((last - prev) / prev) * 100;
  }

  function buildTooltipHtml(item) {
    const q = item.quadrant || {};
    const tail = item.tail || [];
    const d1 = pctChangeInTail(tail, 1);
    const d5 = pctChangeInTail(tail, 5);
    const dist = (item.rs_ratio !== null && item.rs_momentum !== null)
      ? Math.sqrt(Math.pow(item.rs_ratio - 100, 2) + Math.pow(item.rs_momentum - 100, 2)).toFixed(2)
      : null;
    const transition = (item.tail_quadrants || []).length
      ? new Set(item.tail_quadrants).size > 1
      : false;
    const badge = dataStatusBadge(item.data_status || 'no_data');
    const src = item.data_source || 'PostgreSQL';

    return `
      <div style="font-weight:800; font-size:13px; color:#064a6b; margin-bottom:4px;">${item.symbol}</div>
      <div style="color:#59656b; font-size:10px; margin-bottom:4px;">${item.sector || '—'} · nguồn: ${src}</div>
      <div style="color:${quadTextColor(q.id)}; font-weight:700;">${q.name || 'Không có dữ liệu'}${transition ? ' ↻' : ''}</div>
      <div style="margin-top:4px;">Điểm xoay: <b>${safeNum(item.rotation_score, 1)}/100</b></div>
      <div>LP RS-Ratio: <b>${safeNum(item.rs_ratio)}</b></div>
      <div>LP RS-Momentum: <b>${safeNum(item.rs_momentum)}</b></div>
      <div>Hướng 5D: <b>${item.heading_label || '—'}</b> · vận tốc <b>${safeNum(item.velocity_5d)}</b></div>
      <div>Chuỗi trạng thái: <b>${item.quadrant_streak || 0} phiên</b> · dữ liệu <b>${item.last_date || '—'}</b></div>
      <div>Giá: <b>${fmtVnd(item.close)}</b> (${fmtPct(item.change_5d_pct)})</div>
      <div>Δ 1 phiên: <b>${fmtPct(d1)}</b> · Δ 5 phiên: <b>${fmtPct(d5)}</b></div>
      <div>Khoảng cách từ (100,100): <b>${dist == null ? '—' : dist}</b></div>
      <div style="margin-top:5px;color:#4e5a61;font-size:9px;">Tín hiệu tương đối, không phải khuyến nghị mua/bán.</div>
      <div style="margin-top:6px;"><span class="px-1.5 py-0.5 text-[9px] font-bold uppercase rounded border ${badge.cls}">${badge.label}</span></div>
    `;
  }

  function distToSegment(px, py, x1, y1, x2, y2) {
    const l2 = (x2 - x1) ** 2 + (y2 - y1) ** 2;
    if (l2 === 0) return Math.hypot(px - x1, py - y1);
    let t = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (x1 + t * (x2 - x1)), py - (y1 + t * (y2 - y1)));
  }

  function pointInChart(e) {
    const rect = canvas.parentElement.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top, rect };
  }

  // ---------- Advanced Mouse Hover Handling ----------
  function handleHover(e) {
    if (!rrgData || !rrgData.data) return;
    const { x: mx, y: my } = pointInChart(e);

    let bestItem = null;
    let minScore = 22; // Maximum pixel distance threshold for activation

    // Check head positions
    currentRenderData.headPosMap.forEach((headInfo, symbol) => {
      const d = Math.hypot(mx - headInfo.headX, my - headInfo.headY);
      if (d < minScore) {
        minScore = d;
        bestItem = headInfo.item;
      }
    });

    // Check placed label boxes
    currentRenderData.placedLabels.forEach((lbl) => {
      if (mx >= lbl.x && mx <= lbl.x + lbl.w && my >= lbl.y && my <= lbl.y + lbl.h) {
        bestItem = lbl.item;
        minScore = 0;
      }
    });

    // Check tail line segments if nothing closer hit
    if (!bestItem || minScore > 10) {
      currentRenderData.tailLines.forEach(({ item, pts }) => {
        for (let i = 0; i < pts.length - 1; i++) {
          const dSeg = distToSegment(mx, my, pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y);
          if (dSeg < minScore - 4) { // Small bias toward head dots over lines
            minScore = dSeg + 4;
            bestItem = item;
          }
        }
      });
    }

    if (bestItem !== hoverItem) {
      hoverItem = bestItem;
      renderRrgChart();
      renderRrgTable();
    }

    if (bestItem && tooltipEl) {
      tooltipEl.style.display = 'block';
      const tipX = Math.min(mx + 18, canvas.parentElement.clientWidth - 220);
      const tipY = Math.min(my + 14, canvas.parentElement.clientHeight - 180);
      tooltipEl.style.left = `${tipX}px`;
      tooltipEl.style.top = `${tipY}px`;
      tooltipEl.innerHTML = buildTooltipHtml(bestItem);
    } else if (tooltipEl) {
      tooltipEl.style.display = 'none';
    }
  }

  function handleChartClick(e) {
    if (suppressSyntheticClick) {
      suppressSyntheticClick = false;
      return;
    }
    if (!rrgData || !rrgData.data) return;
    const { x: mx, y: my } = pointInChart(e);

    const pad = 54;
    const width = canvas.parentElement.clientWidth;

    // Check click on top-right Label Toggle chip
    ctx.font = 'bold 10px Inter, sans-serif';
    const chipText = showAllLabels ? 'Chỉ nhãn chính' : 'Hiện tất cả nhãn';
    const chipW = ctx.measureText(chipText).width + 18;
    const chipH = 22;
    const chipX = width - pad - chipW;
    const chipY = pad + 36;

    if (mx >= chipX && mx <= chipX + chipW && my >= chipY && my <= chipY + chipH) {
      showAllLabels = !showAllLabels;
      renderRrgChart();
      return;
    }

    // Click stock node -> Navigate to stock page
    if (hoverItem) {
      window.location.href = `/stock/${encodeURIComponent(hoverItem.symbol)}`;
    }
  }

  if (canvas) {
    canvas.addEventListener('mousemove', handleHover);
    canvas.addEventListener('pointerdown', (event) => {
      if (event.pointerType !== 'touch') return;
      event.preventDefault();
      const previousSymbol = lastTouchSymbol;
      const previousTapAt = lastTouchAt;
      handleHover(event);
      const touchedSymbol = hoverItem?.symbol || null;
      suppressSyntheticClick = true;
      if (touchedSymbol && previousSymbol === touchedSymbol && Date.now() - previousTapAt < 2200) {
        window.location.href = `/stock/${encodeURIComponent(touchedSymbol)}`;
        return;
      }
      lastTouchSymbol = touchedSymbol;
      lastTouchAt = Date.now();
    }, { passive: false });
    canvas.addEventListener('mouseleave', () => {
      if (hoverItem) {
        hoverItem = null;
        if (tooltipEl) tooltipEl.style.display = 'none';
        renderRrgChart();
        renderRrgTable();
      }
    });
    canvas.addEventListener('click', handleChartClick);
    canvas.style.cursor = 'pointer';
  }

  // ---------- Quadrant Filter Events ----------
  function setQuadrantFilter(quadId) {
    if (selectedQuadFilter === quadId) {
      selectedQuadFilter = null; // Toggle off
    } else if (quadId === 'ALL') {
      selectedQuadFilter = null;
    } else {
      selectedQuadFilter = quadId;
    }
    updateFilterBadgeStyles();
    renderRrgChart();
    renderRrgTable();
  }

  btnFilterLeading?.addEventListener('click', () => setQuadrantFilter('LEADING'));
  btnFilterWeakening?.addEventListener('click', () => setQuadrantFilter('WEAKENING'));
  btnFilterImproving?.addEventListener('click', () => setQuadrantFilter('IMPROVING'));
  btnFilterLagging?.addEventListener('click', () => setQuadrantFilter('LAGGING'));
  btnFilterAll?.addEventListener('click', () => setQuadrantFilter('ALL'));

  // ---------- Ticker Search Input ----------
  searchTickerInput?.addEventListener('input', (e) => {
    searchQuery = e.target.value.trim().toUpperCase();
    renderRrgChart();
    renderRrgTable();
  });

  // ---------- Custom-symbols flow ----------
  function parseCustomSymbols(text) {
    if (!text) return [];
    return [...new Set(text.split(/[,\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean))].slice(0, 30);
  }

  function applyCustomSymbols() {
    const raw = customInput ? customInput.value : '';
    const list = parseCustomSymbols(raw);
    customSymbolsCsv = list.join(',');
    if (customCount) {
      customCount.textContent = list.length ? `${list.length} mã đã nhập` : '';
    }
    if (selectGroup) selectGroup.value = 'CUSTOM';
    loadRrgData();
  }

  // ---------- Event wiring ----------
  selectGroup?.addEventListener('change', () => {
    if (customPanel) {
      customPanel.classList.toggle('hidden', selectGroup.value !== 'CUSTOM');
    }
    loadRrgData();
  });
  selectBenchmark?.addEventListener('change', loadRrgData);
  selectPeriod?.addEventListener('change', loadRrgData);
  selectTailLength?.addEventListener('change', loadRrgData);
  btnRefreshRrg?.addEventListener('click', loadRrgData);
  btnPlayAnimation?.addEventListener('click', toggleAnimation);
  btnToggleTails?.addEventListener('click', toggleRotationTails);
  customApply?.addEventListener('click', applyCustomSymbols);
  document.querySelectorAll('[data-sort-key]').forEach((button) => {
    button.addEventListener('click', (event) => applySort(button.dataset.sortKey, event.shiftKey));
  });

  if (customInput) {
    customInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        applyCustomSymbols();
      }
    });
  }

  window.addEventListener('resize', resizeCanvas);
  window.addEventListener('orientationchange', () => window.setTimeout(resizeCanvas, 180));

  // Initial load
  document.addEventListener('DOMContentLoaded', () => {
    updateTailModeButton();
    showLoading('Đang khởi tạo biểu đồ RRG…');
    resizeCanvas();
    loadRrgData();
  });
})();
