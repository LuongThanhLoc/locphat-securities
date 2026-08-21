/* =========================================================
   WATCHLIST.JS — Theo Dõi Của Tôi
   Personal stock watchlist using localStorage & Batch Quotes Endpoint
   NO DeepSeek API calls — NEVER
   ========================================================= */

(() => {
  'use strict';

  const WATCHLIST_KEY = 'lps_personal_watchlist_v1';
  const BATCH_SIZE = 50;
  const NOTE_MAX_LEN = 300;
  /* ---------- Helpers ---------- */
  function loadWatchlist() {
    try {
      const raw = localStorage.getItem(WATCHLIST_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.map(normalizeRecord);
    } catch {
      return [];
    }
  }

  function saveWatchlist(items) {
    try {
      localStorage.setItem(WATCHLIST_KEY, JSON.stringify(items));
    } catch (e) {
      console.error('[Watchlist] Save failed:', e);
    }
  }

  function normalizeOptionalAiValue(value) {
    if (value === null || value === undefined) return null;
    const text = String(value).trim();
    if (!text) return null;
    const normalized = text.toUpperCase();
    if (['N/A', 'NA', 'NULL', 'UNDEFINED', '--', 'CHƯA CÓ'].includes(normalized)) {
      return null;
    }
    return value;
  }

  function normalizeRecord(item) {
    if (!item || typeof item !== 'object') item = {};
    const sym = String(item.symbol || '').toUpperCase().trim();

    const oldAi = item.ai_analysis || {};
    const oldEntry = normalizeOptionalAiValue(oldAi.entry_zone || oldAi.buy_zone_low);
    const targetPrice = normalizeOptionalAiValue(oldAi.target_price);
    const stopLoss = normalizeOptionalAiValue(oldAi.stop_loss);

    const hasSetup = !!(oldAi.trade_setup_enabled || oldEntry || targetPrice || stopLoss);

    const latestPrice = finiteNumberOrNull(item.latest_price);
    const refPrice = finiteNumberOrNull(item.reference_price);
    let priceChange = finiteNumberOrNull(item.price_change);
    let priceChangePct = finiteNumberOrNull(item.price_change_pct);

    if (!Number.isFinite(priceChangePct) && Number.isFinite(latestPrice) && Number.isFinite(refPrice) && refPrice > 0) {
      priceChange = Math.round((latestPrice - refPrice) * 100) / 100;
      priceChangePct = Math.round(((latestPrice - refPrice) / refPrice) * 10000) / 100;
    }

    return {
      symbol: sym,
      company_name: String(item.company_name || item.name || '').trim(),
      exchange: item.exchange || null,

      latest_price: latestPrice,
      reference_price: refPrice,
      ceiling_price: finiteNumberOrNull(item.ceiling_price),
      floor_price: finiteNumberOrNull(item.floor_price),

      price_change: priceChange,
      price_change_pct: priceChangePct,

      price_type: item.price_type || null,
      price_label: item.price_label || null,
      price_source: item.price_source || null,

      trading_date: item.trading_date || null,
      price_updated_at: item.price_updated_at || null,
      price_data_quality: item.price_data_quality || null,
      price_stale: Boolean(item.price_stale),

      note: String(item.note || '').substring(0, NOTE_MAX_LEN),
      added_at: item.added_at || new Date().toISOString(),

      ai_analysis: {
        available: Boolean(oldAi.available),
        analyzed_at: oldAi.analyzed_at || null,
        quant_status: normalizeOptionalAiValue(oldAi.quant_status),
        score: finiteNumberOrNull(oldAi.score),
        trade_setup_enabled: hasSetup,
        entry_zone: oldEntry,
        target_price: targetPrice,
        stop_loss: stopLoss
      }
    };
  }

  function finiteNumberOrNull(val) {
    if (val === null || val === undefined) return null;
    const num = Number(val);
    return Number.isFinite(num) ? num : null;
  }

  function sortableNumber(val, fallback) {
    const num = finiteNumberOrNull(val);
    return num === null ? fallback : num;
  }

  function escHtml(str) {
    return String(str || '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  function formatMoney(num) {
    const val = Number(num);
    if (!Number.isFinite(val) || val <= 0) return '—';
    return Math.round(val).toLocaleString('vi-VN');
  }

  function formatPct(val) {
    const num = Number(val);
    if (!Number.isFinite(num)) return '—';
    const sign = num > 0 ? '+' : '';
    return `${sign}${num.toFixed(2)}%`;
  }

  function formatTime(isoStr) {
    if (!isoStr) return '—';
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return '—';
      const pad = n => String(n).padStart(2, '0');
      return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    } catch {
      return '—';
    }
  }

  function showToast(msg, type = 'success') {
    const container = document.getElementById('wlToastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `wl-page-toast ${type === 'error' ? 'error' : ''}`;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('fadeout');
      setTimeout(() => toast.remove(), 300);
    }, 2400);
  }

  /* ---------- State ---------- */
  let currentFilterText = '';
  let currentAiFilter = 'all';
  let currentSortBy = 'newest';
  let isFetchingPrices = false;
  let pendingDeleteSymbol = null;

  /* ---------- DOM Elements ---------- */
  const elTableWrap = document.getElementById('wlTableWrap');
  const elTableBody = document.getElementById('wlTableBody');
  const elCards = document.getElementById('wlCards');
  const elEmpty = document.getElementById('wlEmptyState');
  const elErrorBanner = document.getElementById('wlErrorBanner');
  const elErrorText = document.getElementById('wlErrorText');
  const elAddInput = document.getElementById('wlAddInput');
  const elAddBtn = document.getElementById('wlAddBtn');
  const elSuggest = document.getElementById('wlSuggestDropdown');
  const elSearchFilter = document.getElementById('wlSearchFilter');
  const elSortBy = document.getElementById('wlSortBy');
  const elRefreshBtn = document.getElementById('wlRefreshBtn');
  const elExportBtn = document.getElementById('wlExportBtn');
  const elImportBtn = document.getElementById('wlImportBtn');
  const elImportFile = document.getElementById('wlImportFile');

  /* ---------- KPIs ---------- */
  function renderKpis() {
    const items = loadWatchlist();
    document.getElementById('wlKpiTotal').textContent = items.length;
    let up = 0, down = 0, ai = 0;
    for (const item of items) {
      if (Number.isFinite(item.price_change_pct)) {
        if (item.price_change_pct > 0) up++;
        else if (item.price_change_pct < 0) down++;
      }
      if (item.ai_analysis?.available) ai++;
    }
    document.getElementById('wlKpiUp').textContent = up;
    document.getElementById('wlKpiDown').textContent = down;
    document.getElementById('wlKpiAi').textContent = ai;
  }

  /* ---------- Filtering & Sorting ---------- */
  function getFilteredItems() {
    let items = loadWatchlist();

    if (currentAiFilter === 'ai_yes') {
      items = items.filter(i => i.ai_analysis?.available);
    } else if (currentAiFilter === 'ai_no') {
      items = items.filter(i => !i.ai_analysis?.available);
    }

    if (currentFilterText) {
      const q = currentFilterText.toLowerCase();
      items = items.filter(i =>
        i.symbol.toLowerCase().includes(q) ||
        (i.company_name && i.company_name.toLowerCase().includes(q)) ||
        (i.note && i.note.toLowerCase().includes(q))
      );
    }

    switch (currentSortBy) {
      case 'alpha':
        items.sort((a, b) => a.symbol.localeCompare(b.symbol));
        break;
      case 'gain_desc':
        items.sort((a, b) => sortableNumber(b.price_change_pct, -Infinity) - sortableNumber(a.price_change_pct, -Infinity));
        break;
      case 'loss_desc':
        items.sort((a, b) => sortableNumber(a.price_change_pct, Infinity) - sortableNumber(b.price_change_pct, Infinity));
        break;
      case 'updated':
        items.sort((a, b) => new Date(b.price_updated_at || 0) - new Date(a.price_updated_at || 0));
        break;
      case 'newest':
      default:
        items.sort((a, b) => new Date(b.added_at || 0) - new Date(a.added_at || 0));
        break;
    }
    return items;
  }

  /* ---------- Price Colors & Badges ---------- */
  function getPriceClass(item) {
    if (!Number.isFinite(item.price_change_pct)) return 'neutral';
    if (item.price_change_pct > 0) return 'up';
    if (item.price_change_pct < 0) return 'down';
    return 'flat';
  }

  function renderPriceCell(item) {
    const cls = getPriceClass(item);
    const priceStr = formatMoney(item.latest_price);
    const label = item.price_label || 'Giá khớp';
    let dateStr = '';
    if (item.trading_date && !label.includes(item.trading_date)) {
      dateStr = ` · ${item.trading_date}`;
    }
    return `<div class="wl-price-cell">
      <strong class="wl-price ${cls}">${priceStr}</strong>
      <small class="wl-price-sub">${escHtml(label)}${escHtml(dateStr)}</small>
    </div>`;
  }

  function renderChangeCell(item) {
    let pct = item.price_change_pct;

    if (!Number.isFinite(pct) && Number.isFinite(item.latest_price) && Number.isFinite(item.reference_price) && item.reference_price > 0) {
      pct = Math.round(((item.latest_price - item.reference_price) / item.reference_price) * 10000) / 100;
    }

    if (!Number.isFinite(pct)) {
      return `<span class="wl-change neutral">—</span>`;
    }
    const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
    return `<span class="wl-change ${cls}">${escHtml(formatPct(pct))}</span>`;
  }

  function renderAiCell(item) {
    const ai = item.ai_analysis;
    if (!ai || !ai.available) {
      return `<div class="wl-ai-cell unanalyzed">
        <span class="wl-ai-badge unanalyzed">Chưa phân tích AI</span>
      </div>`;
    }
    const scoreStr = ai.score !== null ? `${ai.score}/100` : '—';
    const statusStr = ai.quant_status || 'Đã phân tích';

    let setupHtml = '';
    if (ai.trade_setup_enabled) {
      const entry = ai.entry_zone ? escHtml(ai.entry_zone) : '—';
      const target = ai.target_price ? escHtml(ai.target_price) : '—';
      const stop = ai.stop_loss ? escHtml(ai.stop_loss) : '—';
      setupHtml = `<div class="wl-ai-details">
        <span>Mua: <b>${entry}</b></span>
        <span>Target: <b>${target}</b></span>
        <span>Cắt lỗ: <b>${stop}</b></span>
      </div>`;
    }

    return `<div class="wl-ai-cell">
      <div class="wl-ai-head">
        <span class="wl-ai-badge active">${escHtml(statusStr)}</span>
        <span class="wl-ai-score-num">${escHtml(scoreStr)}</span>
      </div>
      ${setupHtml}
    </div>`;
  }

  function renderNoteCell(symbol, noteText, surface) {
    const hasNote = Boolean(noteText);
    const pref = surface === 'mobile' ? 'mobile' : 'desktop';
    const displayId = `${pref}-note-display-${symbol}`;
    const editorId = `${pref}-note-editor-${symbol}`;

    return `<div class="wl-note-cell">
      <div class="wl-note-display" id="${displayId}">
        <span class="${hasNote ? '' : 'placeholder'}">${hasNote ? escHtml(noteText) : '+ Thêm ghi chú'}</span>
        <button class="wl-note-edit-btn" onclick="window._wlOpenNote('${escHtml(symbol)}', '${surface}')" title="Sửa ghi chú">✎</button>
      </div>
      <div class="wl-note-editor" id="${editorId}">
        <textarea placeholder="Ghi chú cá nhân (tối đa 300 ký tự)...">${escHtml(noteText)}</textarea>
        <div class="wl-note-editor-actions">
          <button class="wl-btn-save" onclick="window._wlSaveNote('${escHtml(symbol)}', '${surface}')">Lưu</button>
          <button class="wl-btn-cancel" onclick="window._wlCancelNote('${escHtml(symbol)}', '${surface}')">Hủy</button>
        </div>
      </div>
    </div>`;
  }

  /* ---------- Render Table & Cards ---------- */
  function renderList() {
    renderKpis();
    const items = getFilteredItems();
    const allItems = loadWatchlist();

    if (allItems.length === 0) {
      elTableBody.innerHTML = '';
      elCards.innerHTML = '';
      elTableWrap.style.display = 'none';
      elCards.style.display = 'none';
      elEmpty.style.display = 'flex';
      return;
    }

    elEmpty.style.display = 'none';
    elTableWrap.style.display = '';
    elCards.style.display = '';

    if (items.length === 0) {
      elTableBody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:30px;color:#74868e;">Không tìm thấy mã cổ phiếu phù hợp với bộ lọc.</td></tr>`;
      elCards.innerHTML = `<div style="text-align:center;padding:30px;color:#74868e;grid-column:1/-1;">Không tìm thấy mã cổ phiếu phù hợp với bộ lọc.</div>`;
      return;
    }

    // Render Table Body
    elTableBody.innerHTML = items.map(item => `
      <tr data-symbol="${escHtml(item.symbol)}">
        <td>
          <a class="wl-sym" href="/stock/${encodeURIComponent(item.symbol)}" title="Xem phân tích ${escHtml(item.symbol)}">
            <img class="wl-logo" src="https://cdn.simplize.vn/simplizevn/logo/${escHtml(item.symbol)}.jpeg" onerror="window.handleLogoFallback(this, '${escHtml(item.symbol)}')" alt="">
            <strong>${escHtml(item.symbol)}</strong>
          </a>
        </td>
        <td>
          <div class="wl-company-cell">
            <span>${escHtml(item.company_name || 'Doanh nghiệp niêm yết')}</span>
            <small>${escHtml(item.exchange || 'HOSE')}</small>
          </div>
        </td>
        <td>${renderPriceCell(item)}</td>
        <td>${renderChangeCell(item)}</td>
        <td>
          <div class="wl-update-cell" style="font-size:11px;color:#74868e;">
            <div>${escHtml(formatTime(item.price_updated_at))}</div>
            <small class="wl-update-source" style="font-size:10px;color:#52646c;">${escHtml(item.price_source || 'Cache')}</small>
          </div>
        </td>
        <td>${renderAiCell(item)}</td>
        <td>${renderNoteCell(item.symbol, item.note, 'desktop')}</td>
        <td>
          <div class="wl-actions">
            <a class="wl-btn-view" href="/stock/${encodeURIComponent(item.symbol)}">Xem</a>
            <a class="wl-btn-view wl-btn-rsi" href="/backtest?symbol=${encodeURIComponent(item.symbol)}" style="display:inline-flex;align-items:center;gap:4px;" title="Kiểm định phân kỳ RSI">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 3v18h18"></path>
                <path d="m19 9-5 5-4-4-3 3"></path>
              </svg>
              <span>RSI</span>
            </a>
            <button class="wl-btn-delete" onclick="window._wlConfirmDelete('${escHtml(item.symbol)}')">Xóa</button>
          </div>
        </td>
      </tr>
    `).join('');

    // Render Mobile Cards
    elCards.innerHTML = items.map(item => `
      <div class="wl-card" data-symbol="${escHtml(item.symbol)}">
        <div class="wl-card-head">
          <a class="wl-sym" href="/stock/${encodeURIComponent(item.symbol)}">
            <img class="wl-logo" src="https://cdn.simplize.vn/simplizevn/logo/${escHtml(item.symbol)}.jpeg" onerror="window.handleLogoFallback(this, '${escHtml(item.symbol)}')" alt="">
            <strong>${escHtml(item.symbol)}</strong>
          </a>
          ${renderChangeCell(item)}
        </div>
        <div class="wl-card-company">${escHtml(item.company_name || 'Doanh nghiệp niêm yết')}</div>
        <div class="wl-card-body">
          <div><span>Giá gần nhất</span><b>${formatMoney(item.latest_price)}</b></div>
          <div><span>Cập nhật</span><b>${formatTime(item.price_updated_at)}</b></div>
        </div>
        <div style="margin-top:10px;">${renderAiCell(item)}</div>
        <div style="margin-top:10px;">${renderNoteCell(item.symbol, item.note, 'mobile')}</div>
        <div class="wl-card-foot">
          <a class="wl-btn-view" href="/stock/${encodeURIComponent(item.symbol)}">Xem phân tích</a>
          <a class="wl-btn-view wl-btn-rsi" href="/backtest?symbol=${encodeURIComponent(item.symbol)}" style="display:inline-flex;align-items:center;gap:4px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 3v18h18"></path>
              <path d="m19 9-5 5-4-4-3 3"></path>
            </svg>
            <span>Kiểm định RSI</span>
          </a>
          <button class="wl-btn-delete" onclick="window._wlConfirmDelete('${escHtml(item.symbol)}')">Xóa</button>
        </div>
      </div>
    `).join('');
  }

  /* ---------- Batch Price Fetching ---------- */
  async function fetchPricesBatch() {
    const items = loadWatchlist();
    if (!items.length || isFetchingPrices) return;
    isFetchingPrices = true;
    elRefreshBtn.classList.add('loading');
    elErrorBanner.style.display = 'none';

    try {
      const symbols = items.map(i => i.symbol);
      const symbolBatches = [];
      for (let i = 0; i < symbols.length; i += BATCH_SIZE) {
        symbolBatches.push(symbols.slice(i, i + BATCH_SIZE));
      }

      let updatedCount = 0;
      let hasError = false;

      for (const batch of symbolBatches) {
        const url = `/api/watchlist/quotes?symbols=${encodeURIComponent(batch.join(','))}`;
        try {
          const res = await fetch(url);
          if (!res.ok) {
            hasError = true;
            continue;
          }
          const data = await res.json();
          const quoteMap = data.items || {};
          const currentItems = loadWatchlist();

          for (const item of currentItems) {
            const quote = quoteMap[item.symbol];
            if (quote && quote.price_vnd !== undefined) {
              if (quote.price_vnd !== null) item.latest_price = quote.price_vnd;
              if (quote.reference_price_vnd !== null) item.reference_price = quote.reference_price_vnd;
              if (quote.ceiling_price_vnd !== null) item.ceiling_price = quote.ceiling_price_vnd;
              if (quote.floor_price_vnd !== null) item.floor_price = quote.floor_price_vnd;

              if (quote.company_name) item.company_name = quote.company_name;
              if (quote.exchange) item.exchange = quote.exchange;

              if (quote.change_vnd !== null && quote.change_vnd !== undefined) {
                item.price_change = quote.change_vnd;
              }
              if (quote.change_pct !== null && quote.change_pct !== undefined) {
                item.price_change_pct = quote.change_pct;
              }

              if (!Number.isFinite(item.price_change_pct) && Number.isFinite(item.latest_price) && Number.isFinite(item.reference_price) && item.reference_price > 0) {
                item.price_change = Math.round((item.latest_price - item.reference_price) * 100) / 100;
                item.price_change_pct = Math.round(((item.latest_price - item.reference_price) / item.reference_price) * 10000) / 100;
              }

              item.price_type = quote.price_type || item.price_type;
              item.price_label = quote.price_label || item.price_label;
              item.price_source = quote.price_source || item.price_source;
              item.trading_date = quote.trading_date || item.trading_date;
              item.price_updated_at = quote.updated_at || new Date().toISOString();
              item.price_data_quality = quote.data_quality || 'VERIFIED';
              item.price_stale = Boolean(quote.stale);
              updatedCount++;
            }
          }
          saveWatchlist(currentItems);
          renderList();
        } catch (err) {
          console.error('[Watchlist] Batch request error:', err);
          hasError = true;
        }
      }

      if (hasError && updatedCount === 0) {
        elErrorBanner.style.display = 'flex';
      }
    } finally {
      isFetchingPrices = false;
      elRefreshBtn.classList.remove('loading');
    }
  }

  /* ---------- Add Stock & Verification ---------- */
  async function addStockManual(inputSymbol) {
    const sym = String(inputSymbol || '').toUpperCase().trim();
    if (!sym) return;

    if (!/^[A-Z][A-Z0-9]{1,5}$/.test(sym)) {
      showToast('Mã cổ phiếu không hợp lệ (1–6 ký tự chữ/số)', 'error');
      return;
    }

    const current = loadWatchlist();
    if (current.some(i => i.symbol === sym)) {
      showToast(`${sym} đã có trong danh mục theo dõi`, 'error');
      elAddInput.value = '';
      return;
    }

    // Verify ticker via search suggest exact match before adding
    try {
      const res = await fetch(`/api/search_suggest?q=${encodeURIComponent(sym)}`);
      if (!res.ok) throw new Error('API suggest failed');
      const data = await res.json();
      const results = data.results || [];
      const exactMatch = results.find(r => String(r.symbol || '').toUpperCase() === sym);

      if (!exactMatch) {
        showToast('Không tìm thấy mã cổ phiếu này trong danh sách niêm yết', 'error');
        return;
      }

      const newItem = normalizeRecord({
        symbol: exactMatch.symbol,
        company_name: exactMatch.name || exactMatch.organ_name || '',
        added_at: new Date().toISOString()
      });

      current.unshift(newItem);
      saveWatchlist(current);
      elAddInput.value = '';
      hideSuggest();
      showToast(`Đã thêm ${sym} vào danh mục`, 'success');
      renderList();
      fetchPricesBatch();
    } catch (e) {
      showToast('Không thể xác minh mã cổ phiếu lúc này. Vui lòng thử lại.', 'error');
    }
  }

  /* ---------- Autocomplete Suggest ---------- */
  let suggestTimer = null;
  function handleSuggestInput() {
    clearTimeout(suggestTimer);
    const q = elAddInput.value.trim();
    if (!q) { hideSuggest(); return; }
    suggestTimer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/search_suggest?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        renderSuggest(data.results || []);
      } catch {
        hideSuggest();
      }
    }, 120);
  }

  function renderSuggest(list) {
    if (!list.length) { hideSuggest(); return; }
    elSuggest.innerHTML = list.slice(0, 6).map(item => `
      <div class="wl-suggest-item" data-symbol="${escHtml(item.symbol)}" data-name="${escHtml(item.name || item.organ_name || '')}">
        <strong>${escHtml(item.symbol)}</strong>
        <span>${escHtml(item.name || item.organ_name || '')}</span>
      </div>
    `).join('');
    elSuggest.style.display = 'block';

    elSuggest.querySelectorAll('.wl-suggest-item').forEach(el => {
      el.addEventListener('click', () => {
        addStockManual(el.dataset.symbol);
      });
    });
  }

  function hideSuggest() {
    elSuggest.style.display = 'none';
    elSuggest.innerHTML = '';
  }

  /* ---------- CRUD Operations ---------- */
  function deleteStock(symbol) {
    const current = loadWatchlist();
    const filtered = current.filter(i => i.symbol !== symbol);
    saveWatchlist(filtered);
    showToast(`Đã xóa ${symbol} khỏi danh mục`, 'success');
    renderList();
  }

  function updateWatchlistNote(symbol, newNote) {
    const current = loadWatchlist();
    const item = current.find(i => i.symbol === symbol);
    if (item) {
      item.note = String(newNote || '').substring(0, NOTE_MAX_LEN);
      saveWatchlist(current);
    }
  }

  /* ---------- Export & Import ---------- */
  function exportWatchlist() {
    const items = loadWatchlist();
    if (!items.length) {
      showToast('Danh mục theo dõi đang rỗng', 'error');
      return;
    }
    const today = new Date().toISOString().split('T')[0];
    const exportData = {
      format: 'locphat-watchlist',
      version: 1,
      exported_at: new Date().toISOString(),
      items: items
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `locphat-watchlist-${today}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('Đã xuất danh mục ra file JSON', 'success');
  }

  function importWatchlist(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const parsed = JSON.parse(e.target.result);
        const importedItems = Array.isArray(parsed) ? parsed : (Array.isArray(parsed?.items) ? parsed.items : []);
        if (!importedItems.length) {
          showToast('File JSON không chứa danh mục hợp lệ', 'error');
          return;
        }

        const currentItems = loadWatchlist();
        const itemMap = new Map(currentItems.map(i => [i.symbol, i]));

        let addedCount = 0;
        let updatedCount = 0;

        for (const raw of importedItems) {
          const norm = normalizeRecord(raw);
          if (!norm.symbol) continue;

          if (itemMap.has(norm.symbol)) {
            const existing = itemMap.get(norm.symbol);
            if (!existing.note && norm.note) existing.note = norm.note;
            if (!existing.company_name && norm.company_name) existing.company_name = norm.company_name;

            const existingDate = new Date(existing.price_updated_at || 0);
            const normDate = new Date(norm.price_updated_at || 0);
            if (normDate > existingDate && norm.latest_price !== null) {
              existing.latest_price = norm.latest_price;
              existing.price_change_pct = norm.price_change_pct;
              existing.price_updated_at = norm.price_updated_at;
            }

            const existingAiDate = new Date(existing.ai_analysis?.analyzed_at || 0);
            const normAiDate = new Date(norm.ai_analysis?.analyzed_at || 0);
            if (normAiDate > existingAiDate && norm.ai_analysis?.available) {
              existing.ai_analysis = norm.ai_analysis;
            }
            updatedCount++;
          } else {
            itemMap.set(norm.symbol, norm);
            addedCount++;
          }
        }

        const mergedList = Array.from(itemMap.values());
        saveWatchlist(mergedList);
        renderList();
        fetchPricesBatch();
        showToast(`Nhập thành công: ${addedCount} mã mới, ${updatedCount} mã cập nhật`, 'success');
      } catch (err) {
        showToast('Lỗi đọc file JSON: Định dạng không đúng', 'error');
      }
    };
    reader.readAsText(file);
  }

  /* ---------- Global Note Handlers (Desktop & Mobile) ---------- */
  window._wlOpenNote = (symbol, surface) => {
    const pref = surface === 'mobile' ? 'mobile' : 'desktop';
    document.querySelectorAll('.wl-note-editor.open').forEach(el => el.classList.remove('open'));
    document.querySelectorAll('.wl-note-display').forEach(el => el.style.display = '');

    const editor = document.getElementById(`${pref}-note-editor-${symbol}`);
    const display = document.getElementById(`${pref}-note-display-${symbol}`);
    if (editor) {
      editor.classList.add('open');
      if (display) display.style.display = 'none';
      const textarea = editor.querySelector('textarea');
      if (textarea) textarea.focus();
    }
  };

  window._wlSaveNote = (symbol, surface) => {
    const pref = surface === 'mobile' ? 'mobile' : 'desktop';
    const editor = document.getElementById(`${pref}-note-editor-${symbol}`);
    if (!editor) return;
    const textarea = editor.querySelector('textarea');
    const val = (textarea?.value || '').trim().substring(0, NOTE_MAX_LEN);
    updateWatchlistNote(symbol, val);
    showToast('Đã lưu ghi chú', 'success');
    renderList();
  };

  window._wlCancelNote = (symbol, surface) => {
    renderList();
  };

  window._wlConfirmDelete = (symbol) => {
    pendingDeleteSymbol = symbol;
    document.getElementById('wlConfirmTitle').textContent = `Xóa ${symbol} khỏi danh mục?`;
    document.getElementById('wlConfirmOverlay').classList.add('open');
  };

  /* ---------- Event Listeners ---------- */
  function initEvents() {
    elAddBtn.addEventListener('click', () => addStockManual(elAddInput.value));
    elAddInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') addStockManual(elAddInput.value);
    });
    elAddInput.addEventListener('input', handleSuggestInput);

    document.addEventListener('click', e => {
      if (!elAddInput.contains(e.target) && !elSuggest.contains(e.target)) {
        hideSuggest();
      }
    });

    const segmented = document.getElementById('wlSegmentedFilter');
    if (segmented) {
      segmented.querySelectorAll('button').forEach(btn => {
        btn.addEventListener('click', () => {
          segmented.querySelectorAll('button').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          currentAiFilter = btn.dataset.filter || 'all';
          renderList();
        });
      });
    }

    elSearchFilter.addEventListener('input', () => {
      currentFilterText = elSearchFilter.value.trim();
      renderList();
    });

    elSortBy.addEventListener('change', () => {
      currentSortBy = elSortBy.value;
      renderList();
    });

    elRefreshBtn.addEventListener('click', fetchPricesBatch);
    elExportBtn.addEventListener('click', exportWatchlist);
    elImportBtn.addEventListener('click', () => elImportFile.click());
    elImportFile.addEventListener('change', (e) => {
      if (e.target.files && e.target.files[0]) {
        importWatchlist(e.target.files[0]);
        elImportFile.value = '';
      }
    });

    const elClearAllBtn = document.getElementById('wlClearAllBtn');
    if (elClearAllBtn) {
      elClearAllBtn.addEventListener('click', () => {
        const items = loadWatchlist();
        if (!items.length) {
          showToast('Danh mục theo dõi đang rỗng', 'error');
          return;
        }
        pendingDeleteSymbol = '__ALL__';
        document.getElementById('wlConfirmTitle').textContent = 'Xóa tất cả danh mục theo dõi?';
        document.getElementById('wlConfirmMsg').textContent = `Tất cả ${items.length} cổ phiếu đang theo dõi sẽ bị xóa khỏi trình duyệt. Thao tác này không thể hoàn tác.`;
        document.getElementById('wlConfirmOverlay').classList.add('open');
      });
    }

    document.getElementById('wlConfirmYes').addEventListener('click', () => {
      if (pendingDeleteSymbol === '__ALL__') {
        saveWatchlist([]);
        showToast('Đã xóa tất cả cổ phiếu khỏi danh mục theo dõi', 'success');
        renderList();
      } else if (pendingDeleteSymbol) {
        deleteStock(pendingDeleteSymbol);
      }
      pendingDeleteSymbol = null;
      document.getElementById('wlConfirmOverlay').classList.remove('open');
    });

    document.getElementById('wlConfirmNo').addEventListener('click', () => {
      pendingDeleteSymbol = null;
      document.getElementById('wlConfirmOverlay').classList.remove('open');
    });

    document.getElementById('wlConfirmOverlay').addEventListener('click', e => {
      if (e.target === document.getElementById('wlConfirmOverlay')) {
        pendingDeleteSymbol = null;
        document.getElementById('wlConfirmOverlay').classList.remove('open');
      }
    });
  }

  /* ---------- Initialization ---------- */
  function init() {
    initEvents();
    renderList();
    fetchPricesBatch();
    document.addEventListener('lp:watchlist-synced', () => {
      renderList();
      fetchPricesBatch();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
