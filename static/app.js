// Công ty Chứng Khoán Lộc Phát Securities - Client Script

/* ==========================================================================
   GLOBAL STATE - All state variables declared once here
   ========================================================================== */
let donutChart = null;
let candleChart = null;
let radarChart = null;
let trendComboChart = null;
let revenueTrendChart = null;
let segmentStackedChart = null;

let currentStockSymbol = '';
let currentAiAdvisor = null;
let currentDecisionFramework = null;
let currentRevenueStructure = null;
let currentTrendData = null;
let currentTrendViewMode = 'chart';
let currentTrendPeriodMode = 'quarter';
let currentPriceTimeframe = 'ALL';
let currentTargetSymbol = '';
let currentPeerList = [];
let currentPeerData = null;
let rawPriceHistory = [];
let ALL_TRACK_RECORDS = [];
let dnseRealtimeAbortController = null;
let dnseRealtimeTimer = null;
let currentDashboardData = null;
let currentChartEngine = 'apex';
let tvWidgetInstance = null;

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

/* ==========================================================================
   WATCHLIST - localStorage utilities (shared with watchlist.js)
   These are lightweight stubs so app.js can check/update watchlist
   without depending on watchlist.js being loaded.
   ========================================================================== */
const WL_STORAGE_KEY = 'lps_personal_watchlist_v1';

function _wlDefaultAi() {
  return { available: false, analyzed_at: null, quant_status: null, score: null,
    buy_zone_low: null, buy_zone_high: null, target_price: null, stop_loss: null };
}

function _wlLoad() {
  try {
    const raw = localStorage.getItem(WL_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}

function _wlSave(items) {
  try { localStorage.setItem(WL_STORAGE_KEY, JSON.stringify(items)); } catch {}
}

function _wlIsIn(symbol) {
  const sym = String(symbol || '').toUpperCase().trim();
  return _wlLoad().some(i => (i.symbol || '').toUpperCase() === sym);
}

function _wlAdd(stock) {
  const items = _wlLoad();
  const sym = String(stock.symbol || '').toUpperCase().trim();
  if (!sym || items.some(i => (i.symbol || '').toUpperCase() === sym)) return false;
  items.unshift({
    symbol: sym,
    company_name: stock.company_name || '',
    latest_price: stock.latest_price || null,
    price_change: null,
    price_change_pct: null,
    price_updated_at: stock.price_updated_at || null,
    note: '',
    added_at: new Date().toISOString(),
    ai_analysis: _wlDefaultAi()
  });
  _wlSave(items);
  return true;
}

function _wlRemove(symbol) {
  const sym = String(symbol || '').toUpperCase().trim();
  const items = _wlLoad();
  const filtered = items.filter(i => (i.symbol || '').toUpperCase() !== sym);
  if (filtered.length < items.length) { _wlSave(filtered); return true; }
  return false;
}

function _wlNormalizeAiVal(val) {
  if (val === null || val === undefined) return null;
  const s = String(val).trim();
  if (!s) return null;
  const upper = s.toUpperCase();
  if (['N/A', 'NA', 'NULL', 'UNDEFINED', '--', 'CHƯA CÓ'].includes(upper)) return null;
  return s;
}

function _wlUpdateAi(symbol, aiData) {
  const sym = String(symbol || '').toUpperCase().trim();
  const items = _wlLoad();
  const item = items.find(i => (i.symbol || '').toUpperCase() === sym);
  if (!item) return;

  const rec = aiData.recommendation || {};
  const trade = aiData.premium_analysis?.trade_setup || aiData.trade_setup || {};
  const scorecard = aiData.premium_analysis?.scorecard || {};

  const entryZone = _wlNormalizeAiVal(trade.entry_zone);
  const targetPrice = _wlNormalizeAiVal(trade.target_price);
  const stopLoss = _wlNormalizeAiVal(trade.stop_loss_price || trade.stop_loss);

  item.ai_analysis = {
    available: true,
    analyzed_at: new Date().toISOString(),
    quant_status: _wlNormalizeAiVal(rec.action),
    score: scorecard.total != null && Number.isFinite(Number(scorecard.total)) ? Number(scorecard.total) : null,
    trade_setup_enabled: !!(entryZone || targetPrice || stopLoss),
    entry_zone: entryZone,
    target_price: targetPrice,
    stop_loss: stopLoss
  };
  _wlSave(items);
}

/* -- Watchlist toggle button on stock page -- */
function initWatchlistButton() {
  const btn = document.getElementById('watchlistToggleBtn');
  if (!btn || !currentStockSymbol) return;
  btn.style.display = 'inline-flex';
  updateWatchlistButtonState();

  btn.onclick = function() {
    const sym = currentStockSymbol;
    if (!sym) return;
    if (_wlIsIn(sym)) {
      // Show inline confirm
      const confirm = document.getElementById('watchlistInlineConfirm');
      if (confirm) confirm.classList.add('open');
    } else {
      // Add to watchlist
      const price = currentDashboardData?.current_price || null;
      const companyName = currentDashboardData?.organ_name || '';
      _wlAdd({
        symbol: sym,
        company_name: companyName,
        latest_price: price,
        price_updated_at: price ? new Date().toISOString() : null
      });
      updateWatchlistButtonState();
      _wlShowPageToast('Đã thêm ' + sym + ' vào danh mục theo dõi', 'success');
    }
  };

  // Confirm actions
  const confirmYes = document.getElementById('watchlistConfirmYes');
  const confirmNo = document.getElementById('watchlistConfirmNo');
  const confirmBox = document.getElementById('watchlistInlineConfirm');
  if (confirmYes) {
    confirmYes.onclick = function() {
      _wlRemove(currentStockSymbol);
      updateWatchlistButtonState();
      if (confirmBox) confirmBox.classList.remove('open');
      _wlShowPageToast('Đã xóa ' + currentStockSymbol + ' khỏi danh mục', 'success');
    };
  }
  if (confirmNo) {
    confirmNo.onclick = function() {
      if (confirmBox) confirmBox.classList.remove('open');
    };
  }
}

function updateWatchlistButtonState() {
  const btn = document.getElementById('watchlistToggleBtn');
  if (!btn || !currentStockSymbol) return;
  const confirmBox = document.getElementById('watchlistInlineConfirm');
  if (confirmBox) confirmBox.classList.remove('open');
  if (_wlIsIn(currentStockSymbol)) {
    btn.textContent = '★ Đang theo dõi';
    btn.classList.add('watching');
  } else {
    btn.textContent = '☆ Thêm vào theo dõi';
    btn.classList.remove('watching');
  }
}

function _wlShowPageToast(msg, type) {
  const existing = document.querySelector('.wl-page-toast');
  if (existing) existing.remove();
  const el = document.createElement('div');
  el.className = 'wl-page-toast ' + (type || 'success');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => {
    el.classList.add('fadeout');
    setTimeout(() => el.remove(), 300);
  }, 2500);
}

/* ==========================================================================
   COMPANY LOGO SYSTEM (Simplize High-Res CDN + Multi-Format Fallback)
   ========================================================================== */
function getLogoUrl(symbol, ext = 'jpeg') {
  return `https://cdn.simplize.vn/simplizevn/logo/${symbol.toUpperCase()}.${ext}`;
}

function handleLogoError(imgEl, symbol, fallbackElId) {
  if (window.handleLogoFallback) {
    window.handleLogoFallback(imgEl, symbol);
    return;
  }
  const sym = symbol.toUpperCase();
  const currentSrc = imgEl.src || '';

  if (currentSrc.includes('.jpeg')) {
    imgEl.src = getLogoUrl(sym, 'png');
  } else if (currentSrc.includes('.png')) {
    imgEl.src = getLogoUrl(sym, 'jpg');
  } else if (currentSrc.includes('.jpg')) {
    imgEl.src = getLogoUrl(sym, 'webp');
  } else {
    imgEl.style.display = 'none';
    if (fallbackElId) {
      const fb = document.getElementById(fallbackElId);
      if (fb) fb.style.display = 'flex';
    } else if (imgEl.nextElementSibling) {
      imgEl.nextElementSibling.style.display = 'flex';
    }
  }
}

function setCompanyLogo(symbol) {
  const img = document.getElementById('companyLogo');
  const fallback = document.getElementById('companyLogoFallback');
  const initials = document.getElementById('companyLogoInitials');
  if (!img || !fallback) return;

  const sym = symbol.toUpperCase();
  initials.textContent = sym.substring(0, 3);

  // Show logo, hide fallback initially
  img.style.display = 'block';
  fallback.style.display = 'none';
  img.src = getLogoUrl(sym, 'jpeg');
  img.alt = sym;

  img.onerror = function() {
    handleLogoError(img, sym, 'companyLogoFallback');
  };
}

/* ==========================================================================
   SEARCH HISTORY SYSTEM (localStorage - persisted across searches)
   ========================================================================== */
const HISTORY_KEY = 'lps_search_history';
const MAX_HISTORY = 10;

function saveSearchHistory(symbol, organName) {
  if (!symbol) return;
  let history = loadSearchHistory();
  // Remove duplicate (so it moves to front)
  history = history.filter(h => h.symbol !== symbol.toUpperCase());
  // Add to front
  history.unshift({
    symbol: symbol.toUpperCase(),
    organ_name: organName || '',
    timestamp: Date.now()
  });
  // Limit to MAX
  if (history.length > MAX_HISTORY) history = history.slice(0, MAX_HISTORY);
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  renderSearchHistory();
}

function loadSearchHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (raw !== null) {
      return JSON.parse(raw) || [];
    }
  } catch (e) {}

  return [];
}

function renderSearchHistory() {
  const history = loadSearchHistory();
  const modalList = document.getElementById('modalHistoryList');

  if (!modalList) return;

  if (history.length === 0) {
    modalList.innerHTML = `
      <div class="modal-empty-state">
        <i class="fa-solid fa-clock-rotate-left" style="margin-right: 6px;"></i>Chưa có lịch sử tìm kiếm gần đây
      </div>
    `;
    return;
  }

  modalList.innerHTML = history.map(h => {
    const logoUrl = getLogoUrl(h.symbol, 'jpeg');
    const timeAgo = h.timestamp ? getTimeAgo(h.timestamp) : '';
    return `
      <div class="modal-history-item" onclick="selectStock('${h.symbol}'); closeSearchModal();">
        <div class="modal-item-left">
          <i class="fa-regular fa-clock modal-item-clock"></i>
          <div class="modal-item-logo-wrapper">
            <img class="modal-item-logo" src="${logoUrl}" alt="${h.symbol}"
                 onerror="handleLogoError(this, '${h.symbol}')">
            <div class="modal-item-logo-fallback" style="display:none;">
              <span>${h.symbol.substring(0, 2)}</span>
            </div>
          </div>
          <div class="modal-item-text">
            <span class="modal-item-symbol">${h.symbol}</span>
            <span class="modal-item-name">${truncateText(h.organ_name, 35)}</span>
          </div>
        </div>
        <div class="modal-item-right">
          <span class="modal-item-tag">${h.symbol}</span>
          ${timeAgo ? `<span class="modal-item-time text-xs text-slate-500 font-medium ml-2">${timeAgo}</span>` : ''}
          <button class="modal-item-remove" title="Xóa khỏi lịch sử" onclick="removeSearchHistoryItem(event, '${h.symbol}')">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
      </div>
    `;
  }).join('');
}

let ALL_STOCKS_INDEX = [];

async function loadAllStocksIndex() {
  if (ALL_STOCKS_INDEX.length > 0) return;
  try {
    const res = await fetch('/api/all_stocks');
    if (res.ok) {
      const data = await res.json();
      ALL_STOCKS_INDEX = data || [];
    }
  } catch (e) {
    console.warn("Could not load all stocks index:", e);
  }
}

function removeAccentsJS(str) {
  if (!str) return '';
  return str.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D");
}

function handleSearchModalInput(query) {
  const modalList = document.getElementById('modalHistoryList');
  const modalHeaderTitle = document.getElementById('modalHeaderTitle');
  const modalHeaderAction = document.getElementById('modalHeaderAction');
  if (!modalList) return;

  const qRaw = (query || '').trim().toUpperCase();
  const qNorm = removeAccentsJS(query || '').toLowerCase().trim();

  if (!qRaw) {
    if (modalHeaderTitle) modalHeaderTitle.innerHTML = `<span class="header-indicator"></span> TÌM KIẾM GẦN ĐÂY`;
    if (modalHeaderAction) modalHeaderAction.innerHTML = `<button class="modal-clear-btn" onclick="clearSearchHistory()">Xóa Tất Cả</button>`;
    renderSearchHistory();
    return;
  }

  // Header update for Suggestions mode
  if (modalHeaderTitle) modalHeaderTitle.innerHTML = `<span class="header-indicator"></span> GỢI Ý TÌM KIẾM`;

  // Filter matching stocks
  let matches = [];
  if (ALL_STOCKS_INDEX && ALL_STOCKS_INDEX.length > 0) {
    // 1. Symbol starts with query
    for (let s of ALL_STOCKS_INDEX) {
      if (s.symbol.startsWith(qRaw)) matches.push(s);
    }
    // 2. Symbol contains query
    for (let s of ALL_STOCKS_INDEX) {
      if (s.symbol.includes(qRaw) && !matches.includes(s)) matches.push(s);
    }
    // 3. Name contains query (accent-less or original)
    for (let s of ALL_STOCKS_INDEX) {
      if ((s.name_norm.includes(qNorm) || s.name.toLowerCase().includes(query.toLowerCase())) && !matches.includes(s)) {
        matches.push(s);
      }
    }
  }

  const results = matches.slice(0, 10);
  if (modalHeaderAction) {
    modalHeaderAction.innerHTML = `<span class="text-xs text-emerald-400 font-bold">${results.length} gợi ý</span>`;
  }

  if (results.length === 0) {
    modalList.innerHTML = `
      <div class="modal-empty-state" style="padding: 24px 16px;">
        <i class="fa-solid fa-magnifying-glass" style="margin-right: 8px; color: #10b981;"></i>
        Nhấn <strong>ENTER</strong> hoặc bấm nút tìm kiếm để phân tích mã <span class="text-emerald-400 font-bold">${qRaw}</span>
      </div>
    `;
    return;
  }

  modalList.innerHTML = results.map(s => {
    const logoUrl = getLogoUrl(s.symbol, 'jpeg');
    return `
      <div class="modal-history-item" onclick="selectStock('${s.symbol}'); closeSearchModal();">
        <div class="modal-item-left">
          <i class="fa-solid fa-magnifying-glass modal-item-clock" style="color: #38bdf8;"></i>
          <div class="modal-item-logo-wrapper">
            <img class="modal-item-logo" src="${logoUrl}" alt="${s.symbol}"
                 onerror="handleLogoError(this, '${s.symbol}')">
            <div class="modal-item-logo-fallback" style="display:none;">
              <span>${s.symbol.substring(0, 2)}</span>
            </div>
          </div>
          <div class="modal-item-text">
            <span class="modal-item-symbol">${s.symbol}</span>
            <span class="modal-item-name">${truncateText(s.name, 42)}</span>
          </div>
        </div>
        <div class="modal-item-right">
          <span class="modal-item-tag" style="background: rgba(16, 185, 129, 0.15); color: #08713c;">${s.symbol}</span>
        </div>
      </div>
    `;
  }).join('');
}

function openSearchModal(initialValue = '') {
  const overlay = document.getElementById('searchModalOverlay');
  const modalInput = document.getElementById('modalSymbolInput');
  if (!overlay) {
    document.getElementById('lpGlobalSearch')?.click();
    if (initialValue) {
      setTimeout(() => {
        const sharedInput = document.getElementById('lpSearchInput');
        if (!sharedInput) return;
        sharedInput.value = initialValue;
        sharedInput.dispatchEvent(new Event('input', { bubbles: true }));
      }, 30);
    }
    return;
  }
  
  loadAllStocksIndex();
  renderSearchHistory();
  overlay.classList.add('active');
  if (modalInput) {
    modalInput.value = initialValue;
    handleSearchModalInput(initialValue);
    setTimeout(() => modalInput.focus(), 50);
  }
}

function closeSearchModal() {
  const overlay = document.getElementById('searchModalOverlay');
  if (overlay) {
    overlay.classList.remove('active');
  } else {
    document.getElementById('lpSearchOverlay')?.classList.remove('open');
  }
}

function handleOverlayClick(e) {
  if (e.target.id === 'searchModalOverlay') {
    closeSearchModal();
  }
}

function clearSearchHistory() {
  localStorage.setItem(HISTORY_KEY, JSON.stringify([]));
  renderSearchHistory();
}

function removeSearchHistoryItem(event, symbol) {
  if (event) {
    event.stopPropagation();
    event.preventDefault();
  }
  let history = loadSearchHistory();
  history = history.filter(h => h.symbol !== symbol.toUpperCase());
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  renderSearchHistory();
}

function getTimeAgo(timestamp) {
  const diff = Date.now() - timestamp;
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (seconds < 30) return 'Vừa xong';
  if (minutes < 1) return `${seconds}s`;
  if (minutes < 60) return `${minutes} phút`;
  if (hours < 24) return `${hours} giờ`;
  if (days < 30) return `${days} ngày`;
  return `${Math.floor(days / 30)} tháng`;
}

function truncateText(text, maxLen) {
  if (!text) return '';
  return text.length > maxLen ? text.substring(0, maxLen) + '...' : text;
}

document.addEventListener('DOMContentLoaded', () => {
  // Pre-load all stocks index for autocomplete
  loadAllStocksIndex();

  // Check URL routing to open appropriate view
  const path = window.location.pathname.toLowerCase();
  const urlParams = new URLSearchParams(window.location.search);
  const pageParam = urlParams.get('page') || urlParams.get('view');

  const stockPathMatch = path.match(/^\/stock\/([a-z0-9]{2,6})$/);
  if (stockPathMatch) {
    selectStock(stockPathMatch[1]);
  } else if (urlParams.get('symbol')) {
    selectStock(urlParams.get('symbol'));
  } else {
    showWelcomeHome();
  }

  // Handle browser back/forward buttons
  window.addEventListener('popstate', () => {
    const p = window.location.pathname.toLowerCase();
    if (p === '/') {
      showWelcomeHome();
    }
  });

  // Render search history on page load
  renderSearchHistory();

  const symbolInput = document.getElementById('symbolInput');
  const welcomeInput = document.getElementById('welcomeSymbolInput');
  const modalInput = document.getElementById('modalSymbolInput');

  if (modalInput) {
    modalInput.addEventListener('input', (e) => {
      handleSearchModalInput(e.target.value);
    });

    modalInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const query = modalInput.value.trim().toUpperCase();
        if (query) {
          // If suggestions exist, pick first match or query directly
          const qNorm = removeAccentsJS(query).toLowerCase();
          const firstMatch = ALL_STOCKS_INDEX.find(s => s.symbol === query || s.symbol.startsWith(query));
          const targetSym = firstMatch ? firstMatch.symbol : query;
          selectStock(targetSym);
          closeSearchModal();
        }
      }
    });
  }

  // Click or focus on search bar opens the Floating Search Modal (TapChiphoWall style)
  [symbolInput, welcomeInput].forEach(input => {
    if (!input) return;

    input.addEventListener('click', () => {
      openSearchModal(input.value.trim());
    });
    input.addEventListener('focus', () => {
      openSearchModal(input.value.trim());
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const val = input.value.trim();
        if (val) {
          closeSearchModal();
          fetchStockData(val);
        }
      }
    });

    input.addEventListener('input', (e) => {
      e.target.value = e.target.value.toUpperCase().replace(/\s+/g, '');
    });
  });

  if (modalInput) {
    modalInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const symbol = modalInput.value.trim();
        if (symbol) {
          closeSearchModal();
          fetchStockData(symbol);
        }
      } else if (e.key === 'Escape') {
        closeSearchModal();
      }
    });

    modalInput.addEventListener('input', (e) => {
      e.target.value = e.target.value.toUpperCase().replace(/\s+/g, '');
    });
  }

  // Global ESC key listener to close modal
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeSearchModal();
    }
  });

  const searchBtn = document.getElementById('searchBtn');
  if (searchBtn) {
    searchBtn.addEventListener('click', () => {
      const symbol = symbolInput ? symbolInput.value.trim() : '';
      if (symbol) {
        fetchStockData(symbol);
      } else {
        openSearchModal('');
      }
    });
  }

  // Setup scroll listener for scroll-to-top (> 30% trigger) and sticky navbar
  window.addEventListener('scroll', handleScrollEvents, { passive: true });
});

/* ==========================================================================
   SCROLL TO TOP & STICKY HEADER SCROLL CONTROLLER (> 30% Trigger)
   ========================================================================== */
function scrollToTop() {
  window.scrollTo({
    top: 0,
    behavior: 'smooth'
  });
}

function handleScrollEvents() {
  const scrollTop = window.scrollY || document.documentElement.scrollTop;
  const scrollHeight = document.documentElement.scrollHeight - document.documentElement.clientHeight;
  const scrollPercent = scrollHeight > 0 ? (scrollTop / scrollHeight) * 100 : 0;
  
  const scrollToTopBtn = document.getElementById('scrollToTopBtn');
  if (scrollToTopBtn) {
    if (scrollPercent >= 30) {
      scrollToTopBtn.classList.add('visible');
    } else {
      scrollToTopBtn.classList.remove('visible');
    }
  }

  // Navbar shadow enhancement on scroll
  const navbar = document.querySelector('.navbar');
  if (navbar) {
    if (scrollTop > 15) {
      navbar.classList.add('is-scrolled');
    } else {
      navbar.classList.remove('is-scrolled');
    }
  }
}

function showWelcomeHome() {
  document.body.classList.add('is-welcome-page');
  const welcomeState = document.getElementById('welcomeState');
  const dashboardState = document.getElementById('dashboardState');
  const trackRecordState = document.getElementById('trackRecordState');
  const navbarSearchBox = document.getElementById('navbarSearchBox');
  const symbolInput = document.getElementById('symbolInput');
  const welcomeInput = document.getElementById('welcomeSymbolInput');

  if (welcomeState) welcomeState.style.display = 'flex';
  if (dashboardState) dashboardState.style.display = 'none';
  if (trackRecordState) trackRecordState.style.display = 'none';
  if (navbarSearchBox) navbarSearchBox.style.display = 'flex';

  if (symbolInput) symbolInput.value = '';
  if (welcomeInput) welcomeInput.value = '';
  hideError();

  if (window.location.pathname !== '/' && window.history.pushState) {
    history.pushState({ page: 'home' }, '', '/');
  }

  // Scroll to top when returning home
  scrollToTop();

  // Re-render search history with updated timestamps
  renderSearchHistory();
}

function submitWelcomeSearch() {
  const welcomeInput = document.getElementById('welcomeSymbolInput');
  const navbarInput = document.getElementById('symbolInput');
  const symbol = (welcomeInput && welcomeInput.value.trim()) || (navbarInput && navbarInput.value.trim()) || '';
  if (symbol) {
    fetchStockData(symbol);
  } else {
    document.querySelector('[data-lp-open-search]')?.click();
  }
}

function clearAllSearchInputs() {
  const symbolInput = document.getElementById('symbolInput');
  const welcomeInput = document.getElementById('welcomeSymbolInput');
  const modalInput = document.getElementById('modalSymbolInput');
  if (symbolInput) symbolInput.value = '';
  if (welcomeInput) welcomeInput.value = '';
  if (modalInput) modalInput.value = '';
}

function selectStock(symbol) {
  hideError();
  fetchStockData(symbol);
}

function showError(msg) {
  const banner = document.getElementById('errorBanner');
  document.getElementById('errorMessage').textContent = msg;
  banner.style.display = 'flex';
}

function hideError() {
  const banner = document.getElementById('errorBanner');
  banner.style.display = 'none';
}

async function fetchStockData(symbol) {
  if (!symbol) return;
  const sym = symbol.trim().toUpperCase();
  const targetPath = `/stock/${encodeURIComponent(sym)}`;
  if (window.location.pathname.toUpperCase() !== targetPath.toUpperCase()) {
    window.location.href = targetPath;
    return;
  }
  hideError();

  const welcomeState = document.getElementById('welcomeState');
  if (welcomeState) welcomeState.style.display = 'none';
  document.body.classList.remove('is-welcome-page');

  showLoading(true, `Đang tải dữ liệu cho mã ${sym}...`);

  try {
    const res = await fetch(`/api/analyze/${sym}`);
    const data = await res.json();
    
    if (!res.ok) {
      const errorMsg = data.detail || `Bạn đã nhập sai mã cổ phiếu! Mã '${sym}' không tồn tại trên thị trường chứng khoán.`;
      showError(errorMsg);
      if (welcomeState) welcomeState.style.display = 'flex';
      document.body.classList.add('is-welcome-page');
      return;
    }
    
    renderDashboard(data);
  } catch (err) {
    showError(`Bạn đã nhập sai mã cổ phiếu! Mã '${sym}' không tồn tại trên thị trường chứng khoán Việt Nam.`);
    if (welcomeState) welcomeState.style.display = 'flex';
    document.body.classList.add('is-welcome-page');
  } finally {
    showLoading(false);
  }
}

function showLoading(active, text = '') {
  const overlay = document.getElementById('loading');
  if (text) document.getElementById('loadingText').textContent = text;
  overlay.classList.toggle('active', active);
}

function safeFmt(val, fallback = 'N/A') {
  if (val === null || val === undefined) return fallback;
  if (typeof val === 'number') return val.toLocaleString();
  return String(val);
}

function formatLocalDateTime(value) {
  if (!value) return 'chưa có thời điểm';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString('vi-VN', {
    timeZone: 'Asia/Ho_Chi_Minh',
    hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric'
  });
}

function renderAsOfContract(asOf = {}) {
  const market = asOf.market || {};
  const financials = asOf.financials || {};
  const ttm = asOf.ttm || {};
  const marketEl = document.getElementById('asOfMarket');
  const financialsEl = document.getElementById('asOfFinancials');
  const ttmEl = document.getElementById('asOfTtm');
  const generatedEl = document.getElementById('asOfGenerated');
  if (marketEl) marketEl.textContent = `${market.source || 'DNSE'} · ${formatLocalDateTime(market.as_of)}`;
  if (financialsEl) financialsEl.textContent = `${financials.period || 'N/A'}${financials.reported_at ? ` · công bố ${formatLocalDateTime(financials.reported_at)}` : ''}`;
  if (ttmEl) ttmEl.textContent = Array.isArray(ttm.quarters) && ttm.quarters.length
    ? `${ttm.quarters.join(' + ')}${ttm.complete ? '' : ' · chưa đủ 4 quý'}`
    : 'chưa đủ dữ liệu';
  if (generatedEl) generatedEl.textContent = `Tải lúc ${formatLocalDateTime(asOf.generated_at)}`;
}

function updateLiveValuation(price, priceAsOf, source) {
  if (!currentDashboardData) return;
  const numericPrice = Number(price);
  if (!Number.isFinite(numericPrice) || numericPrice <= 0) return;
  const val = currentDashboardData.valuation || {};
  const sharesMn = Number(val.issue_share_million || currentDashboardData.issue_share_million);
  const bvps = Number(val.bvps);
  const eps = Number(val.eps_ttm);

  currentDashboardData.current_price = numericPrice;
  if (Number.isFinite(sharesMn) && sharesMn > 0) {
    const marketCap = numericPrice * sharesMn / 1000;
    currentDashboardData.market_cap_billion = marketCap;
    document.getElementById('displayMarketCap').textContent = marketCap.toLocaleString('vi-VN', { maximumFractionDigits: 1 });
  }
  if (Number.isFinite(bvps) && bvps > 0) {
    val.pb_ratio = Number((numericPrice / bvps).toFixed(2));
    document.getElementById('valPB').textContent = `${val.pb_ratio}x`;
    const min = Number(val.pb_reference_min);
    const max = Number(val.pb_reference_max);
    if (Number.isFinite(min) && Number.isFinite(max)) {
      if (val.pb_ratio < min) {
        val.pb_status = `Hấp dẫn (Dưới tham chiếu ${min}x)`;
        val.pb_badge = 'success';
      } else if (val.pb_ratio <= max) {
        val.pb_status = `Hợp lý (Trong khoảng ${min}x - ${max}x)`;
        val.pb_badge = 'primary';
      } else {
        val.pb_status = `Cao (Vượt mốc ${max}x - Cần cẩn trọng)`;
        val.pb_badge = 'warning';
      }
      document.getElementById('tagPB').textContent = val.pb_status;
      document.getElementById('tagPB').className = `status-tag ${val.pb_badge}`;
    }
  }
  if (Number.isFinite(eps) && eps > 0) {
    val.pe_ratio = Number((numericPrice / eps).toFixed(1));
    document.getElementById('valPE').textContent = `${val.pe_ratio}x`;
  } else {
    val.pe_ratio = null;
    document.getElementById('valPE').textContent = 'N/A';
  }
  const pbNote = document.getElementById('pbNote');
  if (pbNote) pbNote.innerHTML = `<i class="fa-solid fa-circle-info"></i> <strong>Đánh giá P/B theo giá mới nhất:</strong> ${currentDashboardData.symbol} <strong>${val.pb_ratio ?? 'N/A'}x</strong> (${escapeHtml(val.pb_status || 'Chưa đủ dữ liệu')}). ${escapeHtml(val.pb_ref_text || '')}`;
  currentDashboardData.as_of = currentDashboardData.as_of || {};
  currentDashboardData.as_of.market = { ...(currentDashboardData.as_of.market || {}), source: source || 'DNSE', as_of: priceAsOf };
  renderAsOfContract(currentDashboardData.as_of);
}

function setDnseRealtimeStatus(text, tone = 'neutral') {
  const el = document.getElementById('dnseRealtimeStatus');
  if (!el) return;
  const toneClasses = {
    live: 'text-emerald-400',
    fallback: 'text-amber-400',
    error: 'text-rose-400',
    neutral: 'text-slate-500'
  };
  el.className = `price-label text-[11px] font-semibold mt-1 ${toneClasses[tone] || toneClasses.neutral}`;
  el.textContent = text;
}

function formatDnsePrice(price) {
  const numeric = Number(price);
  if (!Number.isFinite(numeric) || numeric <= 0) return null;
  return Math.round(numeric).toLocaleString();
}

async function refreshDnseRealtimePrice(symbol) {
  if (!symbol) return;
  const sym = symbol.trim().toUpperCase();
  if (dnseRealtimeAbortController) {
    dnseRealtimeAbortController.abort();
  }
  dnseRealtimeAbortController = new AbortController();
  setDnseRealtimeStatus('DNSE WebSocket: đang kết nối...', 'neutral');

  try {
    const res = await fetch(`/api/dnse/realtime/${sym}?timeout=6`, {
      signal: dnseRealtimeAbortController.signal
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Không gọi được DNSE realtime');
    }

    const formattedPrice = formatDnsePrice(data.price_vnd);
    if (formattedPrice) {
      document.getElementById('displayPrice').textContent = formattedPrice;
      updateLiveValuation(data.price_vnd, data.exchange_time || data.fetched_at, data.source);
    }

    if (data.status === 'live') {
      setDnseRealtimeStatus('Nguồn: Dữ liệu giao dịch khớp lệnh trực tiếp (DNSE)', 'live');
    } else if (data.status === 'rest_fallback') {
      setDnseRealtimeStatus('Nguồn: Giá khớp lệnh mới nhất (DNSE)', 'fallback');
    } else if (data.authenticated && data.subscribed) {
      setDnseRealtimeStatus('Nguồn: Đang chờ phiên giao dịch tiếp theo', 'fallback');
    } else if (data.status === 'config_error' || data.status === 'auth_error') {
      setDnseRealtimeStatus('Nguồn: Dữ liệu giá thị trường mới nhất', 'fallback');
    } else {
      setDnseRealtimeStatus('Nguồn: Dữ liệu giao dịch DNSE', 'neutral');
    }
  } catch (err) {
    if (err.name === 'AbortError') return;
    console.warn('DNSE realtime error:', err);
    setDnseRealtimeStatus('Nguồn: Dữ liệu giá thị trường mới nhất', 'neutral');
  }
}

function startDnseRealtimePolling(symbol) {
  if (dnseRealtimeTimer) clearInterval(dnseRealtimeTimer);
  refreshDnseRealtimePrice(symbol);
  dnseRealtimeTimer = setInterval(() => {
    if (currentStockSymbol === symbol) refreshDnseRealtimePrice(symbol);
  }, 15000);
}

function renderDashboard(data) {
  currentDashboardData = data;
  currentStockSymbol = data.symbol || '';
  currentTargetSymbol = data.symbol || '';

  document.body.classList.remove('is-welcome-page');
  const welcomeState = document.getElementById('welcomeState');
  const dashboardState = document.getElementById('dashboardState');
  const trackRecordState = document.getElementById('trackRecordState');
  const navbarSearchBox = document.getElementById('navbarSearchBox');

  if (welcomeState) welcomeState.style.display = 'none';
  if (dashboardState) dashboardState.style.display = 'block';
  if (trackRecordState) trackRecordState.style.display = 'none';
  if (navbarSearchBox) navbarSearchBox.style.display = 'flex';

  // Close any open search modal automatically
  closeSearchModal();

  // Clear all search input boxes so search bar is clean after fetching data
  clearAllSearchInputs();

  // Stock Header
  document.getElementById('displaySymbol').textContent = data.symbol || '';
  if (document.getElementById('displayExchange')) document.getElementById('displayExchange').textContent = data.exchange || 'HOSE';
  document.getElementById('displayOrganName').textContent = data.organ_name || '';
  document.getElementById('displayQuarter').textContent = data.latest_quarter || '';
  document.getElementById('displayMarketCap').textContent = safeFmt(data.market_cap_billion);
  document.getElementById('displayPrice').textContent = safeFmt(data.current_price);
  renderAsOfContract(data.as_of || {});
  setDnseRealtimeStatus('Nguồn: DNSE, đang kiểm tra realtime...', 'neutral');
  startDnseRealtimePolling(data.symbol);

  // Update Teaser Titles
  if (document.getElementById('teaserSymbolName')) document.getElementById('teaserSymbolName').textContent = data.symbol;
  if (document.getElementById('revTeaserSymbolName')) document.getElementById('revTeaserSymbolName').textContent = data.symbol;
  if (document.getElementById('trendTeaserSymbolName')) document.getElementById('trendTeaserSymbolName').textContent = data.symbol;
  if (document.getElementById('peerTeaserSymbolName')) document.getElementById('peerTeaserSymbolName').textContent = data.symbol;
  if (document.getElementById('aiSymbolPlaceholder')) document.getElementById('aiSymbolPlaceholder').textContent = data.symbol;
  if (document.getElementById('newsSymbolPlaceholder')) document.getElementById('newsSymbolPlaceholder').textContent = data.symbol;

  // Set company logo
  setCompanyLogo(data.symbol);

  // Save to search history
  saveSearchHistory(data.symbol, data.organ_name);

  // Init watchlist toggle button
  initWatchlistButton();

  // Call Peer Comparison Fetch
  fetchPeerComparison(data.symbol);



  // 1. Valuation & Profitability
  const val = data.valuation || {};
  document.getElementById('valPB').textContent = val.pb_ratio != null ? `${val.pb_ratio}x` : 'N/A';
  document.getElementById('tagPB').textContent = val.pb_status || '';
  document.getElementById('tagPB').className = `status-tag ${val.pb_badge || 'primary'}`;

  document.getElementById('valROE').textContent = val.roe_ratio != null ? `${val.roe_ratio}%` : 'N/A';
  document.getElementById('tagROE').textContent = val.roe_status || '';
  document.getElementById('tagROE').className = `status-tag ${val.roe_badge || 'primary'}`;

  document.getElementById('valPE').textContent = val.pe_ratio != null ? `${val.pe_ratio}x` : 'N/A';
  document.getElementById('valBVPS').textContent = `${safeFmt(val.bvps)} đ`;
  document.getElementById('valEPS').textContent = `${safeFmt(val.eps_ttm)} đ`;
  document.getElementById('valNPAT').textContent = `${safeFmt(val.npat_ttm_billion)} tỷ`;

  // Render Beta & Issue Shares
  const betaVal = Number(val.beta);
  const valBetaEl = document.getElementById('valBeta');
  if (valBetaEl) valBetaEl.textContent = Number.isFinite(betaVal) && betaVal > 0 ? `${betaVal}x` : 'N/A';

  const tagBetaEl = document.getElementById('tagBeta');
  if (tagBetaEl) {
    if (!Number.isFinite(betaVal) || betaVal <= 0) {
      tagBetaEl.textContent = 'Chưa đủ dữ liệu';
      tagBetaEl.className = 'status-tag primary';
    } else if (betaVal > 1.2) {
      tagBetaEl.textContent = "Nhạy sóng cao (>1.2)";
      tagBetaEl.className = "status-tag warning";
    } else if (betaVal >= 0.8) {
      tagBetaEl.textContent = "Nhạy sóng VNI";
      tagBetaEl.className = "status-tag primary";
    } else {
      tagBetaEl.textContent = "Biến động thấp (<0.8)";
      tagBetaEl.className = "status-tag success";
    }
  }

  const issueShareMn = Number(val.issue_share_million || data.issue_share_million);
  const issueShareText = !Number.isFinite(issueShareMn) || issueShareMn <= 0 ? 'N/A' : issueShareMn >= 1000
    ? `${(issueShareMn / 1000).toFixed(2)} Tỷ CP` 
    : `${issueShareMn.toLocaleString()} Tr CP`;

  const valIssueShareEl = document.getElementById('valIssueShare');
  if (valIssueShareEl) valIssueShareEl.textContent = issueShareText;

  const headerIssueShareEl = document.getElementById('displayIssueShareHeader');
  if (headerIssueShareEl) headerIssueShareEl.textContent = issueShareText;

  // Render Dividend Policy & IPO Metadata
  const exRightDateEl = document.getElementById('valExRightDate');
  if (exRightDateEl) exRightDateEl.textContent = val.ex_date || 'N/A';

  const divGrowthEl = document.getElementById('valDivGrowth');
  if (divGrowthEl) divGrowthEl.textContent = val.div_growth || 'N/A';

  const payoutRatioEl = document.getElementById('valPayoutRatio');
  if (payoutRatioEl) payoutRatioEl.textContent = val.payout_ratio || 'N/A';

  const ipoDateEl = document.getElementById('valIPODate');
  if (ipoDateEl) ipoDateEl.textContent = val.listing_date || 'N/A';

  const pbRefText = val.pb_ref_text || `Mức tham chiếu của ngành ${val.sector_name || data.sector_name || 'tương ứng'} thường phù hợp với đặc thù kinh doanh.`;
  document.getElementById('pbNote').innerHTML = `
    <i class="fa-solid fa-circle-info"></i> <strong>Đánh giá P/B:</strong> 
    Chỉ số P/B hiện tại của ${data.symbol} là <strong>${val.pb_ratio}x</strong> (${val.pb_status}). 
    ${pbRefText}
  `;

  // 2. Dynamic Sector Financial Health & Asset Quality Engine
  const sh = data.sector_financial_health;
  if (sh) {
    const quality = data.data_quality || {};
    const qualityText = [
      quality.latest_reported_period && `BCTC: ${quality.latest_reported_period}`,
      quality.ttm_quarters_used && `TTM: ${quality.ttm_quarters_used} quý`,
      quality.price_source && `Giá: ${quality.price_source}`
    ].filter(Boolean).join(' | ');
    const qualityNote = document.getElementById('dataQualityNote');
    if (qualityNote) {
      const warnings = Array.isArray(quality.warnings) ? quality.warnings : [];
      qualityNote.textContent = [qualityText, ...warnings].filter(Boolean).join(' | ');
      qualityNote.className = warnings.length
        ? 'text-[11px] text-amber-300 mb-3'
        : 'text-[11px] text-slate-400 mb-3';
    }
    const sectorBadge = document.getElementById('sectorArchetypeBadge');
    sectorBadge.textContent = `${sh.badge_label}`;
    if (qualityText) sectorBadge.title = qualityText;
    
    // Render 4 Main Sector Metrics
    const gridEl = document.getElementById('sectorMainMetricsGrid');
    gridEl.innerHTML = sh.metrics.map(m => `
      <div class="metric-box">
        <div class="metric-name">${m.label}</div>
        <div class="metric-num" style="font-size: 20px;">${m.value}</div>
        <span class="status-tag ${m.badge}">${m.subtext}</span>
      </div>
    `).join('');

    // Render 2 Detail Metrics
    const detailEl = document.getElementById('sectorDetailMetricsRow');
    detailEl.innerHTML = sh.detail_metrics.map(d => `
      <div style="background: var(--lp-paper); border: 1px solid var(--border-color); border-radius: var(--radius-sm); padding: 12px;">
        <div style="font-size: 11px; color: var(--text-muted); font-weight: 600;">${d.label}</div>
        <div style="font-size: 16px; font-weight: 800; color: var(--text-main); margin: 2px 0;">${d.value}</div>
        <div style="font-size: 11px; color: var(--text-sub);">${d.desc}</div>
      </div>
    `).join('');

    // Render Risk Warning
    document.getElementById('sectorRiskText').textContent = sh.risk_warning;
  }

  // 3. Dynamic Revenue Structure (Lazy-Built on User Click)
  currentRevenueStructure = data.revenue_structure;
  const revTeaserSymbolEl = document.getElementById('revTeaserSymbolName');
  if (revTeaserSymbolEl) revTeaserSymbolEl.textContent = data.symbol;

  // Reset Section 3 to Pre-Analysis Teaser state when loading new stock
  const revPreState = document.getElementById('revPreAnalysisState');
  const revScanState = document.getElementById('revScanningState');
  const revResState = document.getElementById('revResultsState');
  const revBadgeTop = document.getElementById('revBadgeTop');

  if (revPreState) revPreState.style.display = 'flex';
  if (revScanState) revScanState.style.display = 'none';
  if (revResState) revResState.style.display = 'none';
  if (revBadgeTop) revBadgeTop.innerHTML = `<i class="fa-solid fa-chart-pie" style="color: var(--primary-emerald);"></i> TTM Engine Ready`;

  if (donutChart) {
    donutChart.destroy();
    donutChart = null;
  }

  // Reset Tab 2 AI Advisor to Pre-Analysis Teaser state when loading new stock (Do NOT consume DeepSeek API automatically)
  resetAiAdvisorTeaserState(data.symbol);

  // 4. Decision & Recommendation Framework (80% / 20% Model & DeepSeek AI Advisor)
  currentDecisionFramework = data.decision_framework;
  currentAiAdvisor = data.ai_advisor;
  const teaserSymbolEl = document.getElementById('teaserSymbolName');
  if (teaserSymbolEl) teaserSymbolEl.textContent = data.symbol;

  // Reset Section 4 to Pre-Analysis Teaser state when loading new stock
  const preState = document.getElementById('aiPreAnalysisState');
  const scanState = document.getElementById('aiScanningState');
  const resState = document.getElementById('aiResultsState');
  const topBadge = document.getElementById('scoreBadgeTop');

  if (preState) preState.style.display = 'flex';
  if (scanState) scanState.style.display = 'none';
  if (resState) resState.style.display = 'none';
  // Render Candlestick Chart with Engine Switcher (TradingView Lightweight / Apex)
  initTradingViewLightweightChart(data.price_history, data.symbol);
  initPriceCandleChart(data.price_history);

  // Auto-fetch & render the search-grounded hot-news widget
  if (data.widget_hot_news) {
    renderHotNewsWidget(data);
  } else {
    fetchGroundedNewsFeed(data.symbol);
  }

  // Render Section 6: Forensic Red-Flag Analysis
  renderForensicAnalysis(data.forensic_analysis);


  // Reset Section 5 to Pre-Analysis Teaser state when loading new stock
  currentTargetSymbol = data.symbol;
  currentPeerList = [];   // Reset rỗng để backend tự chọn đúng ngành mới
  currentPeerData = null;

  const peerTeaserSymbolEl = document.getElementById('peerTeaserSymbolName');
  if (peerTeaserSymbolEl) peerTeaserSymbolEl.textContent = data.symbol;

  const peerPreState = document.getElementById('peerPreAnalysisState');
  const peerScanState = document.getElementById('peerScanningState');
  const peerResState = document.getElementById('peerResultsState');
  const peerBadgeTop = document.getElementById('peerBadgeTop');

  if (peerPreState) peerPreState.style.display = 'flex';
  if (peerScanState) peerScanState.style.display = 'none';
  if (peerResState) peerResState.style.display = 'none';
  if (peerBadgeTop) peerBadgeTop.innerHTML = `<i class="fa-solid fa-bolt" style="color: #a855f7;"></i> Matrix Engine Ready`;

  // Trend Component Reset State (Lazy Load)
  currentTrendData = data.trend_table || data.quarterly_trends;

  const trendTeaserSymbolEl = document.getElementById('trendTeaserSymbolName');
  if (trendTeaserSymbolEl) trendTeaserSymbolEl.textContent = data.symbol;

  const trendPreState = document.getElementById('trendPreAnalysisState');
  const trendScanState = document.getElementById('trendScanningState');
  const trendResState = document.getElementById('trendResultsState');
  const trendBadgeTop = document.getElementById('trendBadgeTop');

  if (trendPreState) trendPreState.style.display = 'flex';
  if (trendScanState) trendScanState.style.display = 'none';
  if (trendResState) trendResState.style.display = 'none';
  if (trendBadgeTop) trendBadgeTop.innerHTML = `<i class="fa-solid fa-chart-column" style="color: #38bdf8;"></i> Trend Engine Ready`;

  if (trendComboChart) {
    trendComboChart.destroy();
    trendComboChart = null;
  }
}

// NOTE: All global state variables are declared at the top of this file

function triggerTrendAnalysis() {
  if (!currentStockSymbol) {
    openSearchModal('');
    return;
  }
  if (!currentTrendData) {
    fetchStockData(currentStockSymbol);
    return;
  }
  const preState = document.getElementById('trendPreAnalysisState');
  const scanState = document.getElementById('trendScanningState');
  const resState = document.getElementById('trendResultsState');
  const scanText = document.getElementById('trendScanText');
  const scanProgress = document.getElementById('trendScanProgress');
  const trendBadgeTop = document.getElementById('trendBadgeTop');

  if (preState) preState.style.display = 'none';
  if (resState) resState.style.display = 'none';
  if (scanState) scanState.style.display = 'block';

  if (scanProgress) scanProgress.style.width = '0%';
  if (scanText) scanText.textContent = '⚡ Đang kết nối nguồn BCTC & Trích xuất các kỳ báo cáo...';

  setTimeout(() => {
    if (scanProgress) scanProgress.style.width = '55%';
  }, 200);

  setTimeout(() => {
    if (scanProgress) scanProgress.style.width = '100%';
    
    renderTrendMetadata(currentTrendData);
    renderTrendComponent(currentTrendData);

    if (scanState) scanState.style.display = 'none';
    if (resState) resState.style.display = 'block';
    if (trendBadgeTop) trendBadgeTop.innerHTML = `<i class="fa-solid fa-shield-check" style="color: #10b981;"></i> Đã đối chiếu kỳ dữ liệu`;
  }, 400);
}

function renderTrendMetadata(trend) {
  const element = document.getElementById('trendDataMeta');
  if (!element) return;
  const meta = trend?.metadata || {};
  const periods = currentTrendPeriodMode === 'year' ? meta.annual_periods : meta.quarterly_periods;
  const selected = (meta.selected_indicators || []).map(item => `<span class="industry-chip">${escapeHtml(item)}</span>`).join('');
  const indicators = (meta.industry_key_indicators || []).map(item => `<span class="industry-chip muted">${escapeHtml(item)}</span>`).join('');
  element.innerHTML = `
    <div class="trend-source-panel">
      <div style="display:flex; gap:12px; flex-wrap:wrap; justify-content:space-between;">
        <strong style="color:#07577a;"><i class="fa-solid fa-database"></i> ${escapeHtml(meta.source || 'Nguồn BCTC chuẩn hóa')}</strong>
        <span class="industry-sector-label">${escapeHtml(meta.sector_name || '')}</span>
        <span>Kỳ có dữ liệu: ${escapeHtml((periods || []).join(', ') || 'N/A')}</span>
      </div>
      <div>${escapeHtml(meta.flow_definition || '')} ${escapeHtml(meta.stock_definition || '')}</div>
      <div style="color:#59656b;">${escapeHtml(meta.comparison || '')}</div>
      ${selected ? `<div class="industry-chip-row"><strong>4 chỉ tiêu đang hiển thị:</strong>${selected}</div>` : ''}
      ${indicators ? `<details class="industry-details"><summary>Chỉ tiêu ngành nên theo dõi thêm</summary><div class="industry-chip-row">${indicators}</div></details>` : ''}
    </div>`;
}

function triggerRevenueAnalysis() {
  if (!currentStockSymbol) {
    openSearchModal('');
    return;
  }
  if (!currentRevenueStructure) {
    fetchStockData(currentStockSymbol);
    return;
  }
  const preState = document.getElementById('revPreAnalysisState');
  const scanState = document.getElementById('revScanningState');
  const resState = document.getElementById('revResultsState');
  const scanText = document.getElementById('revScanText');
  const scanProgress = document.getElementById('revScanProgress');
  const revBadgeTop = document.getElementById('revBadgeTop');

  if (preState) preState.style.display = 'none';
  if (resState) resState.style.display = 'none';
  if (scanState) scanState.style.display = 'block';

  if (scanProgress) scanProgress.style.width = '0%';
  if (scanText) scanText.textContent = 'Đang đối chiếu kỳ báo cáo và nguồn công bố doanh nghiệp...';

  setTimeout(() => {
    if (scanProgress) scanProgress.style.width = '40%';
    if (scanText) scanText.textContent = 'Đang kiểm tra tính đầy đủ và khả năng cộng khớp cơ cấu...';
  }, 150);

  setTimeout(() => {
    try {
      const rev = currentRevenueStructure;

      // Render Disclosure Metadata first
      renderRevenueMetadata(rev);

      // Render KPI Grid
      renderRevenueKpis(rev);

      // Render Main Visualization (Donut + Legend)
      const visualizationGrid = document.getElementById('revenueVisualizationGrid');
      if (rev && rev.status === 'available' && rev.segments && rev.segments.length > 0) {
        if (visualizationGrid) visualizationGrid.style.display = 'grid';
        const totalBillion = rev.total_revenue_billion || rev.total_revenue_ttm_billion || rev.total_revenue_ttm || rev.total_income_billion || rev.total_rev_billion || (rev.segments ? rev.segments.reduce((acc, s) => acc + (s.amount_billion || 0), 0) : 0);
        const totalEl = document.getElementById('chartCenterTotal');
        if (totalEl) totalEl.textContent = `${totalBillion.toLocaleString()} Tỷ`;
        const periodLabel = document.getElementById('chartCenterPeriodLabel');
        if (periodLabel) {
          periodLabel.textContent = rev.classification === 'accounting_income_sources' ? 'Nguồn thu dương' : 'Tổng doanh thu';
        }

        const series = rev.segments.map(s => Math.max(0, s.amount_billion));
        const labels = rev.segments.map(s => s.name);
        const colors = rev.segments.map(s => s.color || '#10b981');

        renderDonutChart(series, labels, colors);

        // Render Legend
        const legendEl = document.getElementById('revenueLegendList');
        if (legendEl) {
          legendEl.innerHTML = rev.segments.map((s, idx) => {
            const hasChildren = s.children && s.children.length > 0;
            const iconHtml = hasChildren ? `<i class="fa-solid fa-chevron-right accordion-icon open" id="accIcon_${idx}"></i>` : `<i class="fa-solid fa-circle" style="font-size: 6px; vertical-align: middle; margin-right: 6px; color: ${s.color};"></i>`;

            const childrenHtml = hasChildren ? s.children.map(child => `
              <div class="child-tree-item">
                <span>↳ ${child.name}:</span>
                <strong style="color: var(--text-main);">${child.amount_billion.toLocaleString()} tỷ (${child.percentage}%)</strong>
              </div>
            `).join('') : '';

            return `
              <div style="background: var(--lp-paper); border: 1px solid var(--border-color); border-radius: var(--radius-sm); padding: 4px;">
                <div class="accordion-header ${hasChildren ? 'open' : ''}" onclick="toggleAccordion(${idx})">
                <span style="color: ${s.color};">${iconHtml} ${escapeHtml(s.name)}</span>
                  <span style="color: var(--text-muted);">${s.percentage}% <strong style="color: var(--text-main);">(${s.amount_billion.toLocaleString()} tỷ)</strong></span>
                </div>
                ${hasChildren ? `
                  <div class="accordion-body open" id="accBody_${idx}" style="border-left: 2px solid ${s.color};">
                    ${childrenHtml}
                  </div>
                ` : ''}
              </div>
            `;
          }).join('');
        }
      } else {
        if (visualizationGrid) visualizationGrid.style.display = 'grid';
        if (donutChart) {
          donutChart.destroy();
          donutChart = null;
        }
        const legendEl = document.getElementById('revenueLegendList');
        if (legendEl) {
          legendEl.innerHTML = `<div style="color: var(--text-muted); text-align: center; padding: 40px;">Chưa có dữ liệu cơ cấu</div>`;
        }
      }

      // Render Historical Trends
      renderRevenueTrends(rev);

      // Render Sector Context
      renderSectorContext(rev);

      // Render Disclosure Metadata
      renderRevenueMetadata(rev);

      // Update Badge
      if (revBadgeTop) {
        revBadgeTop.innerHTML = rev?.status === 'available'
          ? (rev.fallback_used
            ? `<i class="fa-solid fa-clock-rotate-left" style="color: #f59e0b;"></i> Kỳ gần nhất: ${escapeHtml(rev.period || 'N/A')}`
            : `<i class="fa-solid fa-shield-check" style="color: #10b981;"></i> Khớp kỳ ${escapeHtml(rev.period || '')}`)
          : `<i class="fa-solid fa-circle-info" style="color: #f59e0b;"></i> Chưa có công bố chi tiết`;
      }

    } catch(e) {
      console.error('Revenue analysis render error:', e);
    } finally {
      if (scanState) scanState.style.display = 'none';
      if (resState) resState.style.display = 'block';
    }
  }, 500);
}

function renderRevenueKpis(rev) {
  const container = document.getElementById('revKpiGrid');
  if (!container) return;

  const segments = rev?.segments || [];
  const total = rev?.total_revenue_billion || rev?.total_revenue_ttm_billion || rev?.total_income_billion || 0;
  const quality = rev?.quality_assessment || {};
  const historical = rev?.historical_trends?.summary || {};

  // Calculate KPIs
  const kpis = [];

  // Total Revenue
  kpis.push({
    label: 'Tổng Doanh Thu',
    value: total > 0 ? `${total.toLocaleString()} Tỷ` : 'N/A',
    trend: historical.trend_direction,
    color: '#10b981',
    icon: 'fa-sack-dollar',
  });

  // Number of Segments
  kpis.push({
    label: 'Số Nguồn Thu',
    value: segments.length > 0 ? `${segments.length} nhóm` : 'N/A',
    trend: null,
    color: '#38bdf8',
    icon: 'fa-layer-group',
  });

  // YoY Growth (if available)
  if (historical.yoy_growth_pct !== undefined && historical.yoy_growth_pct !== null) {
    kpis.push({
      label: 'Tăng Trưởng Doanh Thu',
      value: `${historical.yoy_growth_pct > 0 ? '+' : ''}${historical.yoy_growth_pct}% YoY`,
      trend: historical.yoy_growth_pct > 0 ? 'up' : 'down',
      color: historical.yoy_growth_pct > 0 ? '#10b981' : '#ef4444',
      icon: 'fa-chart-line',
    });
  } else {
    kpis.push({
      label: 'Tăng Trưởng Doanh Thu',
      value: 'N/A',
      trend: null,
      color: '#59656b',
      icon: 'fa-chart-line',
    });
  }

  // Quality Score
  if (quality.score !== undefined) {
    kpis.push({
      label: 'Chất Lượng Thu Nhập',
      value: `${quality.score}/100`,
      trend: quality.score >= 80 ? 'up' : quality.score >= 60 ? 'flat' : 'down',
      color: quality.score >= 80 ? '#10b981' : quality.score >= 60 ? '#f59e0b' : '#ef4444',
      icon: 'fa-shield-check',
    });
  } else {
    kpis.push({
      label: 'Chất Lượng Thu Nhập',
      value: 'N/A',
      trend: null,
      color: '#59656b',
      icon: 'fa-shield-check',
    });
  }

  container.innerHTML = kpis.map(kpi => `
    <div class="rev-kpi-card" style="background: var(--lp-paper); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: 12px;">
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
        <i class="fa-solid ${kpi.icon}" style="color: ${kpi.color}; font-size: 14px;"></i>
        <span style="color: var(--text-muted); font-size: 10px; text-transform: uppercase;">${kpi.label}</span>
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <span style="font-size: 18px; font-weight: 700; color: var(--text-main);">${kpi.value}</span>
        ${kpi.trend === 'up' ? '<i class="fa-solid fa-arrow-up" style="color: #10b981; font-size: 12px;"></i>' :
          kpi.trend === 'down' ? '<i class="fa-solid fa-arrow-down" style="color: #ef4444; font-size: 12px;"></i>' : ''}
      </div>
    </div>
  `).join('');
}

function renderRevenueTrends(rev) {
  const trendsSection = document.getElementById('revTrendsSection');
  const trendKpis = document.getElementById('revTrendKpis');
  const trendChart = document.getElementById('revenueTrendChart');
  const segmentSection = document.getElementById('revSegmentTrendsSection');
  const segmentChart = document.getElementById('revenueSegmentStackedChart');
  const periodsBadge = document.getElementById('revTrendsPeriods');

  if (!trendsSection) return;

  const historical = rev?.historical_trends;
  const segmentTrends = rev?.segment_trends;

  if (!historical || historical.status !== 'available' || !historical.data_points || historical.data_points.length < 2) {
    trendsSection.style.display = 'none';
    return;
  }

  trendsSection.style.display = 'block';

  // Update periods badge
  if (periodsBadge) {
    periodsBadge.textContent = `${historical.summary?.periods_count || 0} kỳ`;
  }

  // Render Trend KPIs
  if (trendKpis) {
    const summary = historical.summary || {};
    const trendKpisData = [
      { label: 'Doanh Thu Kỳ Trước', value: summary.previous_revenue ? `${summary.previous_revenue.toLocaleString('vi-VN')} Tỷ` : 'N/A', color: 'var(--lp-ink)' },
      { label: 'YoY', value: summary.yoy_growth_pct !== undefined ? `${summary.yoy_growth_pct > 0 ? '+' : ''}${summary.yoy_growth_pct}%` : 'N/A', color: summary.yoy_growth_pct > 0 ? '#08713c' : '#9f2725' },
      { label: 'CAGR', value: summary.cagr_pct !== undefined ? `${summary.cagr_pct > 0 ? '+' : ''}${summary.cagr_pct}%` : 'N/A', color: '#07577a' },
      { label: 'Biên Lợi Nhuận Gộp TB', value: summary.avg_gross_margin_pct !== undefined ? `${summary.avg_gross_margin_pct}%` : 'N/A', color: '#805000' },
    ];
    trendKpis.innerHTML = trendKpisData.map(k => `
      <div style="background: var(--lp-paper); border: 1px solid var(--border-color); border-radius: var(--radius-sm); padding: 10px; text-align: center;">
        <div style="color: var(--text-muted); font-size: 10px; font-weight: 600; text-transform: uppercase; margin-bottom: 4px;">${k.label}</div>
        <div style="font-size: 17px; font-weight: 800; color: ${k.color};">${k.value}</div>
      </div>
    `).join('');
  }

  // Render Area/Line Chart
  if (trendChart) {
    renderRevenueAreaChart(historical);
  }

  // Render Segment Stacked Chart (if available)
  if (segmentTrends && segmentTrends.status === 'available' && segmentTrends.segments) {
    segmentSection.style.display = 'block';
    if (segmentChart) {
      renderSegmentStackedChart(segmentTrends);
    }
  } else {
    segmentSection.style.display = 'none';
  }
}

function renderRevenueAreaChart(historical) {
  const container = document.getElementById('revenueTrendChart');
  if (!container) return;

  const dataPoints = historical.data_points || [];

  // Prepare data for ApexCharts - filter out null/undefined
  const categories = dataPoints.map(p => p.period);
  const revenueSeries = dataPoints.map(p => p.revenue_billion ?? null);
  const grossProfitData = dataPoints.map(p => p.gross_profit_billion ?? null);
  const npatData = dataPoints.map(p => p.npat_billion ?? null);

  // Destroy existing chart
  if (revenueTrendChart) {
    try { revenueTrendChart.destroy(); } catch(e) { /* ignore */ }
    revenueTrendChart = null;
  }
  container.innerHTML = '';

  const options = {
    series: [
      { name: 'Doanh Thu', type: 'area', data: revenueSeries },
      { name: 'Lợi Nhuận Gộp', type: 'line', data: grossProfitData },
      { name: 'LNST', type: 'line', data: npatData },
    ],
    chart: {
      height: 300,
      type: 'line',
      toolbar: { show: false },
      zoom: { enabled: false },
      fontFamily: 'Inter, sans-serif',
      background: 'transparent',
      parentHeightOffset: 0,
    },
    colors: ['#08713c', '#07577a', '#b45309'],
    stroke: {
      curve: 'smooth',
      width: [3, 2.5, 2.5],
      dashArray: [0, 5, 5],
    },
    markers: {
      size: [5, 4, 4],
      strokeColors: '#ffffff',
      strokeWidth: 2,
      hover: { size: 7 }
    },
    fill: {
      type: ['gradient', 'solid', 'solid'],
      gradient: {
        shadeIntensity: 1,
        opacityFrom: 0.25,
        opacityTo: 0.03,
        stops: [0, 90, 100],
      },
    },
    labels: categories,
    xaxis: {
      type: 'category',
      labels: { style: { colors: '#374151', fontSize: '11px', fontWeight: '600' } },
      axisBorder: { show: true, color: 'rgba(0, 0, 0, 0.1)' },
      axisTicks: { show: true, color: 'rgba(0, 0, 0, 0.1)' },
    },
    yaxis: {
      labels: {
        style: { colors: '#374151', fontSize: '11px', fontWeight: '600' },
        formatter: v => v >= 1000 ? `${(v/1000).toFixed(1).replace(/\.0$/, '')}k Tỷ` : `${v.toLocaleString('vi-VN')} Tỷ`,
      },
    },
    legend: {
      position: 'top',
      horizontalAlign: 'right',
      labels: { colors: '#1e293b' },
      fontSize: '12px',
      fontWeight: 600,
      itemMargin: { horizontal: 10, vertical: 5 }
    },
    tooltip: {
      theme: 'light',
      style: { fontSize: '12px', fontFamily: 'Inter, sans-serif' },
      y: { formatter: v => v !== null && v !== undefined ? `${v.toLocaleString('vi-VN')} Tỷ` : 'N/A' },
    },
    grid: {
      borderColor: 'rgba(0, 0, 0, 0.08)',
      strokeDashArray: 3,
    },
    dataLabels: {
      enabled: true,
      offsetY: -6,
      style: {
        fontSize: '10px',
        fontFamily: 'Inter, sans-serif',
        fontWeight: '700',
        colors: ['#08713c', '#07577a', '#b45309']
      },
      background: {
        enabled: true,
        foreColor: '#ffffff',
        padding: 3,
        borderRadius: 3,
        borderWidth: 1,
        borderColor: 'rgba(0, 0, 0, 0.1)',
        opacity: 0.95,
        dropShadow: { enabled: false }
      },
      formatter: function(val) {
        if (val === null || val === undefined) return '';
        if (Math.abs(val) >= 1000) {
          return (val / 1000).toFixed(1).replace(/\.0$/, '') + 'k Tỷ';
        }
        return val.toLocaleString('vi-VN') + ' Tỷ';
      }
    },
  };

  revenueTrendChart = new ApexCharts(container, options);
  revenueTrendChart.render();
}

function renderSegmentStackedChart(segmentTrends) {
  const container = document.getElementById('revenueSegmentStackedChart');
  if (!container) return;

  const segments = segmentTrends.segments || {};

  // Get all unique periods
  const allPeriods = new Set();
  Object.values(segments).forEach(segData => {
    segData.forEach(p => allPeriods.add(p.period));
  });
  const categories = Array.from(allPeriods).sort();

  if (categories.length === 0) return;

  // Prepare series
  const series = Object.entries(segments).map(([name, data]) => {
    const dataByPeriod = {};
    data.forEach(p => { dataByPeriod[p.period] = p.value_billion; });
    return {
      name: name,
      data: categories.map(p => dataByPeriod[p] || 0),
    };
  });

  // Color palette
  const chartColors = ['#08713c', '#07577a', '#b45309', '#6366f1', '#475569'];

  // Destroy existing chart
  if (segmentStackedChart) {
    try { segmentStackedChart.destroy(); } catch(e) { /* ignore */ }
    segmentStackedChart = null;
  }
  container.innerHTML = '';

  const options = {
    series: series,
    chart: {
      type: 'bar',
      height: 250,
      stacked: true,
      toolbar: { show: false },
      zoom: { enabled: false },
      fontFamily: 'Inter, sans-serif',
      background: 'transparent',
      parentHeightOffset: 0,
    },
    plotOptions: {
      bar: {
        horizontal: false,
        columnWidth: '55%',
        borderRadius: 2,
      },
    },
    colors: chartColors,
    labels: categories,
    xaxis: {
      type: 'category',
      labels: { style: { colors: '#374151', fontSize: '11px', fontWeight: '600' } },
      axisBorder: { show: false },
      axisTicks: { show: false },
    },
    yaxis: {
      labels: {
        style: { colors: '#374151', fontSize: '11px', fontWeight: '600' },
        formatter: v => v >= 1000 ? `${(v/1000).toFixed(1).replace(/\.0$/, '')}k Tỷ` : `${v.toLocaleString('vi-VN')} Tỷ`,
      },
    },
    legend: {
      position: 'top',
      horizontalAlign: 'right',
      labels: { colors: '#1e293b' },
      fontSize: '11px',
      fontWeight: 600,
    },
    tooltip: {
      theme: 'light',
      style: { fontSize: '12px', fontFamily: 'Inter, sans-serif' },
      y: { formatter: v => v !== null && v !== undefined ? `${v.toLocaleString('vi-VN')} Tỷ` : 'N/A' },
    },
    grid: {
      borderColor: 'rgba(0, 0, 0, 0.08)',
      strokeDashArray: 3,
    },
    dataLabels: {
      enabled: true,
      formatter: v => v > 0 ? (v >= 1000 ? `${(v/1000).toFixed(1).replace(/\.0$/, '')}k` : `${v.toLocaleString('vi-VN')}`) : '',
      style: {
        fontSize: '10px',
        fontWeight: '700',
        colors: ['#ffffff']
      }
    }
  };

  segmentStackedChart = new ApexCharts(container, options);
  segmentStackedChart.render();
}

function renderSectorContext(rev) {
  const container = document.getElementById('revSectorContext');
  if (!container) return;

  const sectorInfo = rev?.historical_trends?.sector_context;
  // Hide if no sector info or if it's the generic "Đa ngành" without useful data
  if (!sectorInfo || sectorInfo.name === 'Đa ngành') {
    container.style.display = 'none';
    return;
  }

  container.style.display = 'block';
  container.innerHTML = `
    <div style="background: var(--lp-paper); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: 16px; margin-top: 16px;">
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px;">
        <i class="fa-solid fa-industry" style="color: #38bdf8;"></i>
        <strong style="color: var(--text-main);">Ngành: ${escapeHtml(sectorInfo.name || 'N/A')}</strong>
      </div>
      ${sectorInfo.typical_sources ? `
        <div style="margin-bottom: 12px;">
          <div style="color: var(--text-muted); font-size: 11px; margin-bottom: 6px;">Cơ cấu nguồn thu điển hình ngành:</div>
          <div style="display: flex; flex-wrap: wrap; gap: 6px;">
            ${sectorInfo.typical_sources.map(s => `
              <div style="background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 4px; padding: 4px 8px; font-size: 10px;">
                <span style="color: #38bdf8;">${escapeHtml(s.name)}</span>
                <span style="color: var(--text-muted);">${Math.round(s.weight * 100)}%</span>
              </div>
            `).join('')}
          </div>
        </div>
      ` : ''}
      ${sectorInfo.key_metrics && sectorInfo.key_metrics.length > 0 ? `
        <div style="margin-bottom: 12px;">
          <div style="color: var(--text-muted); font-size: 11px; margin-bottom: 6px;">Chỉ tiêu đánh giá đặc thù:</div>
          <div style="display: flex; flex-wrap: wrap; gap: 6px;">
            ${sectorInfo.key_metrics.map(m => `
              <span style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 4px; padding: 4px 8px; font-size: 10px; color: #10b981;">${escapeHtml(m)}</span>
            `).join('')}
          </div>
        </div>
      ` : ''}
      ${sectorInfo.red_flags && sectorInfo.red_flags.length > 0 ? `
        <div style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.2); border-radius: 4px; padding: 8px 12px;">
          <div style="color: #ef4444; font-size: 10px; font-weight: 700; margin-bottom: 4px;">
            <i class="fa-solid fa-exclamation-triangle"></i> Cảnh báo đặc thù ngành:
          </div>
          <ul style="margin: 0; padding-left: 16px; color: #9f2725; font-size: 10px;">
            ${sectorInfo.red_flags.map(f => `<li>${escapeHtml(f)}</li>`).join('')}
          </ul>
        </div>
      ` : ''}
    </div>
  `;
}

function renderIndustryProfile(profile) {
  if (!profile) return '';
  const sources = (profile.expected_revenue_sources || []).map(item => `<span class="industry-chip">${escapeHtml(item)}</span>`).join('');
  const indicators = (profile.key_indicators || []).map(item => `<span class="industry-chip muted">${escapeHtml(item)}</span>`).join('');
  const cautions = (profile.cautions || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  return `
    <div class="industry-profile-panel">
      <div class="industry-profile-heading">
        <strong>${escapeHtml(profile.sector_name || 'Ngành')}</strong>
        <span>Khung phân tích cùng taxonomy Bản đồ nhiệt</span>
      </div>
      ${sources ? `<div class="industry-profile-block"><b>Nguồn doanh thu cần doanh nghiệp thuyết minh:</b><div class="industry-chip-row">${sources}</div></div>` : ''}
      ${indicators ? `<div class="industry-profile-block"><b>Chỉ tiêu đánh giá đặc thù:</b><div class="industry-chip-row">${indicators}</div></div>` : ''}
      ${cautions ? `<ul class="industry-caution-list">${cautions}</ul>` : ''}
      <div class="industry-context-note">Các nhãn trên là khung kiểm tra ngành, không phải tỷ trọng do ứng dụng tự ước lượng.</div>
    </div>`;
}

function renderRevenueQuality(quality) {
  if (!quality) return '';
  const metrics = (quality.metrics || []).map(metric => {
    const hasValue = metric.value !== null && metric.value !== undefined;
    const value = hasValue ? `${Number(metric.value).toLocaleString('vi-VN')} ${escapeHtml(metric.unit || '')}` : 'N/A';
    return `<div class="revenue-quality-metric">
      <span>${escapeHtml(metric.label)}</span>
      <strong class="${hasValue ? '' : 'unavailable'}">${value}</strong>
      <small>${escapeHtml(metric.meaning || '')}</small>
    </div>`;
  }).join('');
  const warnings = (quality.warnings || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  return `<div class="revenue-quality-panel">
    <div class="revenue-quality-heading"><strong>Chất lượng nguồn thu</strong><span>${quality.score}/100 · ${escapeHtml(quality.label || '')}</span></div>
    <div class="revenue-quality-grid">${metrics}</div>
    ${warnings ? `<ul class="industry-caution-list">${warnings}</ul>` : ''}
    <div class="industry-context-note">${escapeHtml(quality.methodology || '')}</div>
  </div>`;
}

function renderRevenueMetadata(revenue) {
  const element = document.getElementById('revenueDisclosureMeta');
  if (!element || !revenue) return;
  const source = revenue.source || {};
  const limitations = (revenue.limitations || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  const profileHtml = renderIndustryProfile(revenue.industry_profile);
  if (revenue.status !== 'available') {
    const historical = revenue.historical_reference || {};
    element.innerHTML = `
      <div style="background:rgba(245,158,11,.08); border:1px solid rgba(245,158,11,.3); padding:16px; border-radius:6px;">
        <div style="font-weight:800; color:#805000; margin-bottom:6px;"><i class="fa-solid fa-triangle-exclamation"></i> Không dựng cơ cấu khi thiếu bằng chứng</div>
        <div style="color:#151817;">${escapeHtml(revenue.message || 'Chưa có dữ liệu phân khúc được kiểm chứng.')}</div>
        ${revenue.target_period ? `<div style="margin-top:6px; color:#805000;">Kỳ yêu cầu: <strong>${escapeHtml(revenue.target_period)}</strong>. Dữ liệu cũ không được dùng làm số hiện tại.</div>` : ''}
        ${historical.period ? `<div style="margin-top:4px; color:#59656b;">Tham chiếu lịch sử gần nhất: ${escapeHtml(historical.period)} (chỉ ghi nguồn, không dựng biểu đồ).</div>` : ''}
        ${limitations ? `<ul style="margin:8px 0 0 18px; color:#59656b; font-size:12px;">${limitations}</ul>` : ''}
      </div>
      ${profileHtml}`;
    return;
  }
  const sourceLink = source.url
    ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer" style="color:#07577a; text-decoration:underline;">${escapeHtml(source.document || 'Mở nguồn')}</a>`
    : escapeHtml(source.document || source.publisher || 'BCTC chuẩn hóa');
  const negative = (revenue.negative_components || []).map(item => `${escapeHtml(item.name)}: ${Number(item.amount_billion).toLocaleString()} tỷ`).join('; ');
  const fallbackNotice = revenue.fallback_used
    ? `<div style="color:#805000; font-weight:700;">Đang dùng kỳ gần nhất ${escapeHtml(revenue.period || 'N/A')} vì chưa có cơ cấu phù hợp cho ${escapeHtml(revenue.target_period || 'kỳ yêu cầu')}.</div>`
    : '';
  const qualityHtml = renderRevenueQuality(revenue.quality_assessment);
  element.innerHTML = `
    <div style="background:${revenue.fallback_used ? 'rgba(245,158,11,.07)' : 'rgba(16,185,129,.07)'}; border:1px solid ${revenue.fallback_used ? 'rgba(245,158,11,.3)' : 'rgba(16,185,129,.26)'}; padding:12px; border-radius:6px; font-size:12px; line-height:1.6; color:#374047;">
      <div style="display:flex; flex-wrap:wrap; gap:10px; justify-content:space-between;">
        <strong style="color:#6ee7b7;">${escapeHtml(revenue.title || 'Cơ cấu doanh thu')}</strong>
        <span>Kỳ: <strong style="color:var(--text-main);">${escapeHtml(revenue.period || 'N/A')}</strong> | Tin cậy: ${escapeHtml(revenue.confidence || 'N/A')}</span>
      </div>
      ${fallbackNotice}
      <div>Nguồn: ${sourceLink}${source.publisher ? `, ${escapeHtml(source.publisher)}` : ''}</div>
      ${source.evidence ? `<div style="color:#59656b;">Căn cứ: ${escapeHtml(source.evidence)}</div>` : ''}
      ${negative ? `<div style="color:#9f2725;">Khoản âm giữ nguyên dấu, không đưa vào donut: ${negative}</div>` : ''}
      ${limitations ? `<ul style="margin:6px 0 0 18px; color:#59656b;">${limitations}</ul>` : ''}
    </div>
    ${qualityHtml}
    ${profileHtml}`;
}

async function triggerQuantAiAnalysis() {
  if (!currentStockSymbol) {
    openSearchModal('');
    return;
  }
  const preState = document.getElementById('aiPreAnalysisState');
  const scanState = document.getElementById('aiScanningState');
  const resState = document.getElementById('aiResultsState');
  const scanText = document.getElementById('aiScanText');
  const scanProgress = document.getElementById('aiScanProgress');

  if (preState) preState.style.display = 'none';
  if (resState) resState.style.display = 'none';
  if (scanState) scanState.style.display = 'block';

  if (scanProgress) scanProgress.style.width = '15%';
  if (scanText) scanText.textContent = 'Dang doi chieu BCTC, peer va suc manh tuong doi VN-Index...';

  try {
    const response = await fetch(`/api/quant/${currentStockSymbol}`);
    if (!response.ok) throw new Error('Khong the tinh Quant framework');
    const payload = await response.json();
    currentDecisionFramework = payload.decision_framework;
    if (scanProgress) scanProgress.style.width = '100%';
    renderQuantFramework(currentDecisionFramework);
    if (scanState) scanState.style.display = 'none';
    if (resState) resState.style.display = 'block';
  } catch (error) {
    console.error('Quant framework error:', error);
    if (scanText) scanText.textContent = 'Khong the hoan tat doi chieu Quant. Vui long thu lai.';
    if (scanProgress) scanProgress.style.width = '0%';
  }
}

function renderQuantFramework(df) {
  if (!df) return;
  const scoreText = (group) => `${Number(group?.score || 0).toFixed(1)} / ${group?.max_score || 0} D`;
  const topBadge = document.getElementById('scoreBadgeTop');
  if (topBadge) topBadge.textContent = `Tong diem: ${Number(df.total_score || 0).toFixed(1)}/100`;
  const total = document.getElementById('displayTotalScore');
  if (total) {
    total.textContent = Number(df.total_score || 0).toFixed(1);
    total.className = `score-circle-lg ${df.recommendation_badge || 'warning'}`;
  }
  const action = document.getElementById('recActionText');
  if (action) action.textContent = df.recommendation_action || 'THEO DOI / CHO XAC NHAN';
  const mapping = [
    ['score80Val', `${Number(df.weight_80_score || 0).toFixed(1)} / 80 D`],
    ['score20Val', `${Number(df.weight_20_score || 0).toFixed(1)} / 20 D`],
    ['macroScoreVal', scoreText(df.macro_sector)],
    ['macroDesc', df.macro_sector?.detail || 'N/A'],
    ['sectorPeersVal', scoreText(df.sector_peers)],
    ['sectorPeersDesc', df.sector_peers?.detail || 'N/A'],
    ['fundamentalScoreVal', scoreText(df.fundamental)],
    ['fundamentalDesc', df.fundamental?.detail || 'N/A'],
    ['taProbVal', scoreText(df.ta_probability)],
    ['taProbDesc', df.ta_probability?.detail || 'N/A'],
    ['speedScoreVal', `${Number(df.speed_accuracy?.data_score || 0).toFixed(1)} / ${df.speed_accuracy?.data_max_score || 12} D`],
    ['latencyVal', df.speed_accuracy?.detail || 'N/A'],
    ['accuracyScoreVal', `${Number(df.speed_accuracy?.calibration_score || 0).toFixed(1)} / ${df.speed_accuracy?.calibration_max_score || 8} D`],
    ['overallActionRecommendation', `${df.recommendation_summary || ''} ${df.trade_plan?.reason || ''}`],
  ];
  mapping.forEach(([id, value]) => {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
  });
}

function resetAiAdvisorTeaserState(symbol) {
  const sym = symbol || currentStockSymbol || '---';
  currentAiAdvisor = null;

  const placeholder = document.getElementById('aiThesisPlaceholder');
  const scanState = document.getElementById('aiThesisScanningState');
  const content = document.getElementById('aiThesisContent');
  const reanalyzeBtn = document.getElementById('btnReanalyzeAi');

  if (placeholder) {
    placeholder.innerHTML = `
      <div class="ai-core-glow-card">
        <div class="ai-core-ring-bg" style="background: radial-gradient(circle, rgba(56, 189, 248, 0.15) 0%, transparent 70%);"></div>
        <div class="ai-core-header">
          <span class="ai-chip-pill" style="color: #38bdf8; border-color: rgba(56, 189, 248, 0.3); background: rgba(56, 189, 248, 0.1);"><i class="fa-solid fa-layer-group"></i> QUANT TRƯỚC · AI SAU</span>
          <h3 class="ai-hero-heading">Phân Tích <span id="aiSymbolPlaceholder" class="glow-symbol-text" style="background: linear-gradient(90deg, #ffffff, #38bdf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">${sym}</span></h3>
        </div>
        <div class="ai-button-wrapper">
          <div class="ai-button-aura" style="background: radial-gradient(circle, rgba(56, 189, 248, 0.4) 0%, transparent 70%);"></div>
          <button class="btn-ai-cyber-hero" id="btnGenerateAiReport" style="background: linear-gradient(135deg, #0284c7, #0369a1); border-color: rgba(56, 189, 248, 0.5); box-shadow: 0 0 25px rgba(56, 189, 248, 0.4);" onclick="triggerAiAdvisorReport()">
            <div class="btn-shine"></div>
            <i class="fa-solid fa-wand-magic-sparkles"></i>
            <span class="btn-text">Chạy Phân Tích Hợp Nhất</span>
            <i class="fa-solid fa-bolt-lightning"></i>
          </button>
        </div>
      </div>
    `;
    placeholder.style.display = 'flex';
  }
  if (scanState) scanState.style.display = 'none';
  if (content) content.style.display = 'none';
  if (reanalyzeBtn) reanalyzeBtn.style.display = 'none';
}

function renderAiAdvisorReport(ai) {
  const placeholder = document.getElementById('aiThesisPlaceholder');
  const scanState = document.getElementById('aiThesisScanningState');
  const content = document.getElementById('aiThesisContent');
  const reanalyzeBtn = document.getElementById('btnReanalyzeAi');
  const symbolPlaceholder = document.getElementById('aiSymbolPlaceholder');

  if (symbolPlaceholder && currentStockSymbol) {
    symbolPlaceholder.textContent = currentStockSymbol;
  }

  if (!ai) {
    if (placeholder) placeholder.style.display = 'flex';
    if (scanState) scanState.style.display = 'none';
    if (content) content.style.display = 'none';
    if (reanalyzeBtn) reanalyzeBtn.style.display = 'none';
    return;
  }

  if (placeholder) placeholder.style.display = 'none';
  if (scanState) scanState.style.display = 'none';
  if (content) content.style.display = 'block';
  if (reanalyzeBtn) reanalyzeBtn.style.display = 'inline-flex';

  const rec = ai.recommendation || {};
  const trade = ai.trade_setup || {};
  const risksObj = ai.risks_and_invalidations || {};
  const premium = ai.premium_analysis || {};
  const confidence = premium.confidence || ai.confidence || {};
  const valuation = premium.valuation || {};
  const scorecard = premium.scorecard || {};
  const technical = ai.technical_analysis || premium.technical_analysis || {};

  const recAction = rec.action || ai.recommendation_text || "N/A";
  const recEl = document.getElementById('aiRecText');
  if (recEl) {
    recEl.textContent = recAction;
    recEl.style.color = (recAction.includes('MUA') || recAction.includes('BUY')) ? '#10b981' : (recAction.includes('NẮM') ? '#f59e0b' : '#f43f5e');
  }

  const weightEl = document.getElementById('aiPortfolioWeight');
  if (weightEl) weightEl.textContent = `Tỷ trọng: ${rec.portfolio_weight || 'N/A'}`;

  const targetEl = document.getElementById('aiTargetRange');
  if (targetEl) targetEl.textContent = `Mục tiêu: ${trade.target_price || 'N/A'} (${trade.upside_percent || 'N/A'})`;

  const entryEl = document.getElementById('aiEntryZone');
  if (entryEl) entryEl.textContent = `Vùng mua: ${trade.entry_zone || 'N/A'}`;

  const stopLossEl = document.getElementById('aiStopLossText');
  if (stopLossEl) stopLossEl.textContent = `Cắt lỗ: ${trade.stop_loss_price || 'N/A'} (${trade.downside_risk_percent || 'N/A'})`;

  const riskLevelEl = document.getElementById('aiRiskLevelText');
  if (riskLevelEl) riskLevelEl.textContent = `Rủi ro: ${rec.risk_level || 'N/A'} | Nắm giữ: ${trade.holding_horizon || 'N/A'}`;

  const confidenceEl = document.getElementById('aiConfidenceGrade');
  if (confidenceEl) confidenceEl.textContent = `${confidence.grade || '--'} · ${confidence.score ?? '--'}/100`;
  const gatesEl = document.getElementById('aiConfidenceGates');
  if (gatesEl) gatesEl.textContent = `${confidence.passed ?? '--'}/${confidence.total ?? '--'} gate đạt`;
  const quantEl = document.getElementById('aiQuantScore');
  if (quantEl) quantEl.textContent = `${scorecard.total ?? '--'}/100`;
  const fairValueEl = document.getElementById('aiFairValue');
  if (fairValueEl) fairValueEl.textContent = valuation.fair_value ? `${Number(valuation.fair_value).toLocaleString('vi-VN')} đ` : 'N/A';
  const methodEl = document.getElementById('aiValuationMethod');
  if (methodEl) methodEl.textContent = valuation.methodology || 'Chưa đủ dữ liệu';
  const mosEl = document.getElementById('aiMarginSafety');
  if (mosEl) mosEl.textContent = valuation.margin_of_safety_pct != null ? `${valuation.margin_of_safety_pct > 0 ? '+' : ''}${valuation.margin_of_safety_pct}%` : 'N/A';
  const rrEl = document.getElementById('aiRewardRisk');
  if (rrEl) rrEl.textContent = `R:R ${trade.reward_risk || 'N/A'}`;

  const weekly = technical.weekly || {};
  const calibration = technical.calibration || {};
  const technicalMap = [
    ['aiTechnicalRegime', technical.regime || 'N/A'], ['aiTechnicalAsOf', `Dữ liệu đến ${technical.as_of || 'N/A'}`],
    ['aiDailyTrend', technical.trend || 'N/A'], ['aiIndicatorLine', `RSI ${technical.rsi ?? '--'} · ADX ${technical.adx14 ?? '--'} · ATR ${technical.atr_pct ?? '--'}%`],
    ['aiWeeklyTrend', weekly.regime || 'N/A'], ['aiWeeklyLine', weekly.available ? `EMA20 ${Number(weekly.ema20).toLocaleString('vi-VN')} · RSI ${weekly.rsi14}` : 'Chưa đủ dữ liệu tuần'],
    ['aiCalibration', calibration.hit_rate_pct != null ? `${calibration.hit_rate_pct}% · ${calibration.sample_size} mẫu` : 'Chưa đủ mẫu'],
    ['aiCalibrationDetail', calibration.reliable ? `Trung vị ${calibration.median_return_pct}% / 20 phiên` : 'Không dùng xác suất khi dưới 20 mẫu'],
  ];
  technicalMap.forEach(([id,value])=>{const el=document.getElementById(id);if(el)el.textContent=value});
  const signalEl = document.getElementById('aiTechnicalSignals');
  if (signalEl) signalEl.innerHTML = (technical.signals || []).map(signal => `<div class="decision-signal ${escapeHtml(signal.state)}"><strong>${escapeHtml(signal.name)}</strong><small>${escapeHtml(signal.detail)}</small></div>`).join('') || '<span class="text-slate-400 text-xs">Không đủ 200 phiên để tính ma trận kỹ thuật.</span>';

  const thesisList = document.getElementById('aiThesisList');
  const thesisArray = ai.quantified_investment_thesis || ai.investment_thesis || [];
  if (thesisList && Array.isArray(thesisArray)) {
    thesisList.innerHTML = thesisArray.map(t => `
      <li style="display: flex; gap: 8px; align-items: flex-start;">
        <i class="fa-solid fa-angle-right" style="color: #38bdf8; margin-top: 3px;"></i>
        <span>${escapeHtml(t)}</span>
      </li>
    `).join('');
  }

  const catalystsList = document.getElementById('aiCatalystsList');
  const catArray = ai.catalysts || [];
  if (catalystsList && Array.isArray(catArray)) {
    catalystsList.innerHTML = catArray.map(c => `<li>${escapeHtml(c)}</li>`).join('');
  }

  const risksList = document.getElementById('aiRisksList');
  const riskArray = risksObj.key_risks || ai.key_risks || [];
  if (risksList && Array.isArray(riskArray)) {
    risksList.innerHTML = riskArray.map(r => `<li>${escapeHtml(r)}</li>`).join('');
  }

  const invEl = document.getElementById('aiInvalidationText');
  if (invEl) invEl.textContent = risksObj.invalidation_trigger || 'Nếu thủng mốc cắt lỗ hoặc chỉ số gãy ngưỡng, luận điểm mua bị hủy bỏ.';

  const stratEl = document.getElementById('aiCapitalStrategyText');
  if (stratEl) stratEl.textContent = ai.capital_allocation_strategy || 'Giải ngân theo tỷ trọng tích lũy từng phần quanh nền giá.';
}

function showAiOverloadError(type, symbol) {
  const sym = symbol || currentStockSymbol || '';
  const quotaMsg = "Hiện nay hết lượt dùng miễn phí AI, quay trở lại sau";

  if (type === 'thesis') {
    const placeholder = document.getElementById('aiThesisPlaceholder');
    const scanState = document.getElementById('aiThesisScanningState');
    if (scanState) scanState.style.display = 'none';

    if (placeholder) {
      placeholder.innerHTML = `
        <div class="w-14 h-14 rounded-full bg-amber-500/10 border border-amber-500/30 flex items-center justify-center mb-3">
          <i class="fa-solid fa-triangle-exclamation text-amber-400 text-2xl"></i>
        </div>
        <h4 class="text-base font-bold text-amber-300 mb-1">Thông Báo AI</h4>
        <p class="text-xs text-slate-200 max-w-md mb-4 leading-relaxed font-semibold">
          ${quotaMsg}
        </p>
        <button onclick="triggerAiAdvisorReport('${sym}')" class="px-5 py-2.5 rounded-lg font-bold text-xs bg-amber-500 hover:bg-amber-400 text-slate-950 transition-all shadow-lg flex items-center gap-2 cursor-pointer">
          <i class="fa-solid fa-arrows-rotate"></i> THỬ LẠI KÍCH HOẠT AI
        </button>
      `;
      placeholder.style.display = 'flex';
      const content = document.getElementById('aiThesisContent');
      if (content) content.style.display = 'none';
      const reanalyzeBtn = document.getElementById('btnReanalyzeAi');
      if (reanalyzeBtn) reanalyzeBtn.style.display = 'none';
    }
  } else if (type === 'news') {
    const newsPlaceholder = document.getElementById('newsPlaceholder');
    if (newsPlaceholder) {
      newsPlaceholder.innerHTML = `
        <div class="w-12 h-12 rounded-full bg-amber-500/10 border border-amber-500/30 flex items-center justify-center mb-3">
          <i class="fa-solid fa-triangle-exclamation text-amber-400 text-xl"></i>
        </div>
        <h5 class="text-xs font-bold text-amber-300 mb-1">Thông Báo AI</h5>
        <p class="text-[11px] text-slate-200 max-w-xs mb-4 leading-relaxed font-semibold">
          ${quotaMsg}
        </p>
        <button onclick="triggerAiNewsFeed('${sym}')" class="px-4 py-2 rounded-lg font-bold text-xs bg-amber-500 hover:bg-amber-400 text-slate-950 transition-all shadow-lg flex items-center gap-2 cursor-pointer">
          <i class="fa-solid fa-arrows-rotate"></i> THỬ LẠI TỔNG HỢP TIN
        </button>
      `;
      newsPlaceholder.style.display = 'flex';
      const newsContent = document.getElementById('newsContent');
      if (newsContent) newsContent.style.display = 'none';
      const newsRisksBox = document.getElementById('newsRisksBox');
      if (newsRisksBox) newsRisksBox.style.display = 'none';
    }
  }
}

async function fetchAiReport(symbol) {
  const sym = symbol || currentStockSymbol;
  if (!sym) {
    openSearchModal('');
    return;
  }
  const placeholder = document.getElementById('aiThesisPlaceholder');
  const scanState = document.getElementById('aiThesisScanningState');
  const scanProgress = document.getElementById('aiThesisScanProgress');
  const scanText = document.getElementById('aiThesisScanText');
  const content = document.getElementById('aiThesisContent');
  const reanalyzeBtn = document.getElementById('btnReanalyzeAi');

  if (placeholder) placeholder.style.display = 'none';
  if (content) content.style.display = 'none';
  if (reanalyzeBtn) reanalyzeBtn.style.display = 'none';
  if (scanState) scanState.style.display = 'block';

  if (scanProgress) scanProgress.style.width = '10%';
  if (scanText) scanText.textContent = `⚡ Đang kết nối Lộc Phát AI Engine cho mã ${sym}...`;

  let curWidth = 10;
  const progressInterval = setInterval(() => {
    if (curWidth < 85) {
      curWidth += 15;
      if (scanProgress) scanProgress.style.width = curWidth + '%';
    }
  }, 350);

  try {
    const res = await fetch(`/api/ai_analysis/${sym}`, {
      method: 'POST',
      headers: { 'X-LP-User-Action': 'deepseek' }
    }).then(r => r.json());
    clearInterval(progressInterval);
    if (scanProgress) scanProgress.style.width = '100%';

    if (res && res.ai_advisor && !res.error && !res.overloaded && !res.quota_exceeded) {
      currentAiAdvisor = res.ai_advisor;
      if (scanState) scanState.style.display = 'none';
      renderAiAdvisorReport(currentAiAdvisor);
      // Sync AI result to watchlist if stock is being watched
      if (_wlIsIn(sym)) {
        _wlUpdateAi(sym, res.ai_advisor);
      }
      if (res.ai_advisor.widget_hot_news) {
        renderHotNewsWidget(res);
      }
    } else {
      if (scanState) scanState.style.display = 'none';
      showAiOverloadError('thesis', sym);
    }
  } catch (err) {
    clearInterval(progressInterval);
    if (scanState) scanState.style.display = 'none';
    console.error('Error fetching AI analysis:', err);
    showAiOverloadError('thesis', sym);
  } finally {
    if (reanalyzeBtn) {
      reanalyzeBtn.disabled = false;
      reanalyzeBtn.innerHTML = `<i class="fa-solid fa-arrows-rotate"></i> Phân Tích Lại AI`;
    }
  }
}

function triggerAiAdvisorReport(symbol) {
  const sym = symbol || currentStockSymbol;
  if (!sym) {
    openSearchModal('');
    return;
  }
  fetchAiReport(sym);
}

// Global handler fallback for compatibility
function triggerAiAnalysis(symbol) {
  triggerAiAdvisorReport(symbol);
}

// Main Dashboard Horizontal Tab Switcher
function switchDashboardTab(tabName) {
  const btnChart = document.getElementById('dashTabBtnChart');
  const btnAi = document.getElementById('dashTabBtnAi');
  const contentChart = document.getElementById('dashTabContentChart');
  const contentAi = document.getElementById('dashTabContentAi');
  const toolbar = document.getElementById('chartTimeframeToolbar');

  if (tabName === 'chart') {
    if (btnChart) btnChart.classList.add('active');
    if (btnAi) btnAi.classList.remove('active');

    if (contentChart) contentChart.style.display = 'block';
    if (contentAi) contentAi.style.display = 'none';
    if (toolbar) toolbar.style.display = (currentChartEngine === 'apex') ? 'flex' : 'none';

    // Auto-resize chart on tab reveal
    setTimeout(() => {
      if (candleChart && typeof candleChart.render === 'function') {
        candleChart.windowResize();
      }
    }, 50);
  } else if (tabName === 'ai') {
    if (btnAi) btnAi.classList.add('active');
    if (btnChart) btnChart.classList.remove('active');

    if (contentChart) contentChart.style.display = 'none';
    if (contentAi) contentAi.style.display = 'block';
    if (toolbar) toolbar.style.display = 'none';
  }
}

async function fetchGroundedNewsFeed(symbol) {
  const sym = symbol || currentStockSymbol;
  if (!sym) return;

  const feedEl = document.getElementById('hotNewsFeed');
  if (feedEl && (!feedEl.children.length || feedEl.querySelector('.fa-spinner'))) {
    feedEl.innerHTML = `
      <div class="py-6 text-center text-slate-400 text-xs">
        <i class="fa-solid fa-spinner fa-spin text-sky-400 text-lg mb-2"></i>
        <div>Đang tải tin tức có grounding...</div>
      </div>
    `;
  }

  try {
    const res = await fetch(`/api/ai_news/${sym}`).then(r => r.json());
    if (res && res.widget_hot_news) {
      renderHotNewsWidget({ symbol: sym, widget_hot_news: res.widget_hot_news });
    }
  } catch (err) {
    console.error("Lỗi tải tin tức có grounding:", err);
    if (feedEl && !feedEl.querySelector('.news-card-item')) {
      feedEl.innerHTML = `<div class="py-4 text-center text-slate-400 text-xs">Không thể tải tin tức có grounding cho mã ${sym}.</div>`;
    }
  }
}

async function triggerAiNewsFeed(symbol) {
  return fetchGroundedNewsFeed(symbol);
}


function renderHotNewsWidget(data) {
  if (!data) return;
  const ai = data.ai_advisor || {};
  const newsWidget = ai.widget_hot_news || data.widget_hot_news || {};
  const symbol = data.symbol || currentStockSymbol || 'CP';

  // Always ensure containers are displayed and error placeholder is hidden
  const newsContent = document.getElementById('newsContent');
  if (newsContent) newsContent.style.display = 'block';

  const newsRisksBox = document.getElementById('newsRisksBox');
  if (newsRisksBox) newsRisksBox.style.display = 'block';

  const newsPlaceholder = document.getElementById('newsPlaceholder');
  if (newsPlaceholder) newsPlaceholder.style.display = 'none';

  // Render Catalyst Hashtags
  const tagsEl = document.getElementById('newsCatalystTags');
  const tags = (Array.isArray(newsWidget.catalyst_tags) && newsWidget.catalyst_tags.length > 0)
    ? newsWidget.catalyst_tags
    : ['#KinhDoanh', '#MởRộng', '#CổTức'];
  if (tagsEl) {
    tagsEl.innerHTML = tags.map(t => `<span class="badge-tag">${t}</span>`).join('');
  }

  // Render News Feed Items
  const feedEl = document.getElementById('hotNewsFeed');
  const rawList = newsWidget.news_list;
  const newsList = (Array.isArray(rawList) && rawList.length > 0)
    ? rawList
    : [];

  if (feedEl) {
    if (!newsList.length) {
      feedEl.innerHTML = `<div class="py-5 text-center text-slate-400 text-xs">Chưa tìm thấy tin mới có nguồn kiểm chứng cho ${escapeHtml(symbol)}.</div>`;
      return;
    }
    feedEl.innerHTML = newsList.map(n => {
      const titleText = n.title || symbol;
      const link = n.article_url || '';
      const safeLink = escapeHtml(link);
      const imgUrl = n.image_proxy_url || ((n.image_url && n.image_url !== 'None') ? n.image_url : '');
      const summaryContent = n.summary_html || escapeHtml(n.title || `${symbol} tin tức cập nhật`);
      const sourceText = escapeHtml(n.source || 'Nguồn bài báo');
      const timestamp = formatLocalDateTime(n.published_at || n.timestamp);
      const interaction = link ? `data-link="${safeLink}" onclick="window.open(this.dataset.link, '_blank', 'noopener')"` : '';

      return `
        <div class="news-card-item ${link ? 'cursor-pointer' : ''}" ${interaction} title="${link ? 'Bấm để đọc toàn văn bài báo' : 'Tin công bố chưa có URL bài gốc'}">
          <div class="news-summary-text">
            ${summaryContent}
            <i class="fa-solid fa-arrow-up-right-from-square text-sky-400 text-[10px] ml-1"></i>
          </div>
          ${imgUrl ? `<div class="news-media-preview">
            <img src="${escapeHtml(imgUrl)}" alt="Ảnh bài viết ${escapeHtml(titleText)}" class="news-thumb-img" onerror="this.closest('.news-media-preview').remove()" />
            <div class="news-preview-badge"><i class="fa-solid fa-newspaper mr-1"></i>Đọc bài báo</div>
          </div>` : ''}
          <div class="news-timestamp flex items-center justify-between">
            <span>${sourceText} · ${escapeHtml(timestamp)}</span>
            ${link ? `<span class="text-sky-400 font-semibold text-[9px] hover:underline">Xem chi tiết <i class="fa-solid fa-angle-right"></i></span>` : ''}
          </div>
        </div>
      `;
    }).join('');
  }

  // Render Non-financial Risks
  const risksEl = document.getElementById('newsRisksContent');
  const risks = newsWidget.non_financial_risks || [`Rủi ro chi phí nguyên vật liệu & biến động thị trường ngành mã ${symbol}.`];
  if (risksEl) {
    risksEl.textContent = Array.isArray(risks) ? risks.join(' | ') : (typeof risks === 'string' ? risks : JSON.stringify(risks));
  }
}

function toggleAccordion(idx) {
  const body = document.getElementById(`accBody_${idx}`);
  const icon = document.getElementById(`accIcon_${idx}`);
  const header = icon ? icon.closest('.accordion-header') : null;
  if (body) {
    body.classList.toggle('open');
  }
  if (icon) {
    icon.classList.toggle('open');
  }
  if (header) {
    header.classList.toggle('open');
  }
}



function setTrendViewMode(viewMode) {
  currentTrendViewMode = viewMode;
  const btnChart = document.getElementById('btnChartViewMode');
  const btnLine = document.getElementById('btnLineViewMode');
  const btnTable = document.getElementById('btnTableViewMode');

  if (btnChart) btnChart.classList.toggle('active', viewMode === 'chart' || viewMode === 'bar');
  if (btnLine) btnLine.classList.toggle('active', viewMode === 'line');
  if (btnTable) btnTable.classList.toggle('active', viewMode === 'table');

  const chartContainer = document.getElementById('trendComboChartContainer');
  const tableContainer = document.getElementById('trendTableContainer');

  if (chartContainer) chartContainer.style.display = (viewMode === 'table') ? 'none' : 'block';
  if (tableContainer) tableContainer.style.display = (viewMode === 'table') ? 'block' : 'none';

  if (currentTrendData) {
    renderTrendMetadata(currentTrendData);
    renderTrendComponent(currentTrendData);
  }
}

function setTrendPeriodMode(periodMode) {
  currentTrendPeriodMode = periodMode;
  const btnQ = document.getElementById('btnQuarterMode');
  const btnY = document.getElementById('btnYearMode');
  if (btnQ) btnQ.classList.toggle('active', periodMode === 'quarter');
  if (btnY) btnY.classList.toggle('active', periodMode === 'year');

  if (currentTrendData) {
    renderTrendMetadata(currentTrendData);
    renderTrendComponent(currentTrendData);
  }
}

function setTrendTableMode(mode) {
  setTrendPeriodMode(mode);
}

function renderTrendComponent(trendTableData) {
  currentTrendData = trendTableData;
  if (!trendTableData) return;

  if (currentTrendViewMode === 'table') {
    renderTrendTable(trendTableData);
  } else {
    renderTrendComboChart(trendTableData, currentTrendViewMode);
  }
}

function renderTrendComboChart(trendTableData, viewMode = 'chart') {
  const chartEl = document.querySelector("#trendComboChart");
  if (!chartEl) return;

  const cols = trendTableData.columns;
  const rows = (currentTrendPeriodMode === 'quarter') ? trendTableData.quarterly_data : trendTableData.yearly_data;

  if (!rows || rows.length === 0) {
    chartEl.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--text-muted);">Chưa có dữ liệu biểu đồ kỳ báo cáo</div>`;
    return;
  }

  const categories = rows.map(r => r.period);
  const isLineView = (viewMode === 'line');
  const series = [];
  const palette = ['#38bdf8', '#8b5cf6', '#f59e0b', '#ec4899', '#06b6d4', '#a855f7'];

  const metricCols = cols.filter(c => c.key !== 'period');
  const compactChart = window.innerWidth < 700;

  if (isLineView) {
    // Pure Multi-Line Chart mode with smooth curves & markers
    metricCols.forEach((col) => {
      series.push({
        name: col.label,
        type: 'line',
        data: rows.map(r => {
          const raw = String(r[col.key] || '').replace(/,/g, '');
          const val = parseFloat(raw);
          return isNaN(val) ? null : val;
        })
      });
    });
  } else {
    // Column / Combo Chart mode
    const barCols = metricCols.filter(c => c.key !== 'npat').slice(0, 3);
    barCols.forEach((col, idx) => {
      series.push({
        name: col.label,
        type: 'column',
        data: rows.map(r => {
          const raw = String(r[col.key] || '').replace(/,/g, '');
          const val = parseFloat(raw);
          return isNaN(val) ? null : val;
        })
      });
    });

    const npatCol = metricCols.find(c => c.key === 'npat');
    if (npatCol) {
      series.push({
        name: npatCol.label,
        type: 'line',
        data: rows.map(r => {
          const raw = String(r.npat || '').replace(/,/g, '');
          const val = parseFloat(raw);
          return isNaN(val) ? null : val;
        })
      });
    }
  }

  const colors = isLineView 
    ? metricCols.map((c, idx) => c.key === 'npat' ? '#10b981' : palette[idx % palette.length])
    : [...metricCols.filter(c => c.key !== 'npat').slice(0, 3).map((_, idx) => palette[idx % palette.length]), '#10b981'];

  const strokeWidths = isLineView ? series.map(() => 3) : series.map(s => s.type === 'line' ? 3 : 0);

  const options = {
    series: series,
    chart: {
      height: compactChart ? 320 : 360,
      type: 'line',
      background: 'transparent',
      toolbar: { show: true },
      fontFamily: 'Inter, sans-serif'
    },
    colors: colors,
    stroke: {
      width: strokeWidths,
      curve: 'smooth'
    },
    markers: {
      size: isLineView ? 5 : series.map(s => s.type === 'line' ? 5 : 0),
      strokeWidth: 2,
      strokeColors: '#fffdf7',
      hover: { size: 8 }
    },
    plotOptions: {
      bar: {
        columnWidth: '45%',
        borderRadius: 4,
        dataLabels: { position: 'top' }
      }
    },
    dataLabels: {
      enabled: false
    },
    theme: { mode: 'light' },
    xaxis: {
      categories: categories,
      labels: {
        style: { colors: '#59656b', fontWeight: 600, fontSize: '12px' }
      },
      axisBorder: { color: 'rgba(255, 255, 255, 0.1)' }
    },
    yaxis: series.map((s, idx) => {
      const isLNST = s.name.includes('LNST') || s.name.includes('Lợi Nhuận Sau Thuế');
      if (isLNST) {
        return {
          seriesName: s.name,
          opposite: true,
          axisTicks: { show: true },
          axisBorder: { show: true, color: '#10b981' },
          labels: {
            style: { colors: '#10b981' },
            formatter: (val) => (val >= 1000 || val <= -1000) ? `${(val/1000).toFixed(1)}k Tỷ` : `${val.toLocaleString()} Tỷ`
          },
          title: {
            text: compactChart ? undefined : "LNST (Tỷ VNĐ)",
            style: { color: '#10b981', fontSize: '11px', fontWeight: 600 }
          }
        };
      } else {
        return {
          seriesName: series[0].name,
          axisTicks: { show: idx === 0 },
          axisBorder: { show: idx === 0, color: '#3b82f6' },
          labels: {
            show: idx === 0,
            style: { colors: '#59656b' },
            formatter: (val) => (val >= 1000 || val <= -1000) ? `${(val/1000).toFixed(1)}k Tỷ` : `${val.toLocaleString()} Tỷ`
          },
          title: {
            text: idx === 0 && !compactChart ? "Giá trị (Tỷ VNĐ)" : undefined,
            style: { color: '#59656b', fontSize: '11px', fontWeight: 600 }
          }
        };
      }
    }),
    legend: {
      position: 'top',
      horizontalAlign: 'left',
      fontSize: compactChart ? '10px' : '12px',
      itemMargin: { horizontal: compactChart ? 5 : 10, vertical: 3 },
      labels: { colors: '#59656b' }
    },
    grid: {
      borderColor: 'rgba(55, 64, 71, 0.14)'
    },
    tooltip: {
      theme: 'light',
      shared: true,
      intersect: false,
      custom: function({ series, seriesIndex, dataPointIndex, w }) {
        const row = rows[dataPointIndex];
        if (!row) return '';
        const period = row.period;
        
        let badgeHtml = '';
        if (row.yoy_badge) {
          const b = row.yoy_badge;
          const isPos = b.pct >= 0;
          const sign = isPos ? '+' : '';
          const bg = isPos ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)';
          const color = isPos ? '#08713c' : '#f87171';
          const border = isPos ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)';
          badgeHtml = `<span style="background: ${bg}; color: ${color}; border: 1px solid ${border}; font-weight: 700; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-left: 6px;">${sign}${b.pct}% ${escapeHtml(b.basis || 'YoY')}</span>`;
        }

        let itemsHtml = cols.map(c => {
          if (c.key === 'period') return '';
          let val = row[c.key];
          if (val === undefined || val === null || val === '0.0') val = '-';
          const isLNST = (c.key === 'npat');
          const color = isLNST ? '#10b981' : '#38bdf8';
          return `
            <div style="display: flex; justify-content: space-between; gap: 16px; margin-top: 5px; font-size: 12px;">
              <span style="color: #59656b;"><span style="color:${color}">■</span> ${c.label} <small>(${c.nature === 'stock' ? 'cuối kỳ' : 'trong kỳ'})</small>:</span>
              <strong style="color: ${isLNST ? '#08713c' : '#151817'}; font-weight: 700;">${val} Tỷ ${isLNST ? badgeHtml : ''}</strong>
            </div>
          `;
        }).join('');

        return `
          <div style="background: #fffdf7; border: 1px solid #c9c5ba; border-radius: 6px; padding: 12px; box-shadow: 0 12px 28px rgba(35,31,24,0.16); color:#151817;">
            <div style="font-weight: 800; color: #151817; border-bottom: 1px solid #ded9cc; padding-bottom: 6px; margin-bottom: 6px; font-size: 13px;">
              <i class="fa-solid fa-calendar-days" style="color: #087d9c;"></i> Kỳ báo cáo: ${period}
            </div>
            ${itemsHtml}
          </div>
        `;
      }
    }
  };

  if (trendComboChart) trendComboChart.destroy();
  trendComboChart = new ApexCharts(chartEl, options);
  trendComboChart.render();
}

function renderTrendTable(trendTableData) {
  const cols = trendTableData.columns;
  const rows = (currentTrendPeriodMode === 'quarter') ? trendTableData.quarterly_data : trendTableData.yearly_data;

  // Render Head
  const headEl = document.getElementById('trendTableHead');
  if (headEl) {
    headEl.innerHTML = cols.map((c, idx) => {
      const alignClass = idx === 0 ? 'text-left' : 'text-right';
      return `<th class="${alignClass}">${c.label}</th>`;
    }).join('');
  }

  // Render Body
  const bodyEl = document.getElementById('trendTableBody');
  if (!bodyEl) return;
  if (!rows || rows.length === 0) {
    bodyEl.innerHTML = `<tr><td colspan="${cols.length}" style="text-align: center; color: var(--text-muted);">Chưa có dữ liệu kỳ báo cáo</td></tr>`;
    return;
  }

  bodyEl.innerHTML = rows.map(row => {
    return '<tr>' + cols.map((col, idx) => {
      const key = col.key;
      let val = row[key];
      if (val === undefined || val === null || val === '0.0') val = '-';
      
      if (key === 'period') {
        return `<td class="trend-col-period"><strong>${val}</strong></td>`;
      } else if (key === 'npat') {
        let badgeHtml = '';
        if (row.yoy_badge) {
          const b = row.yoy_badge;
          const isPos = b.pct >= 0;
          const sign = isPos ? '+' : '';
          const badgeClass = (b.class === 'red' || b.class === 'danger' || !isPos) ? 'red' : 'green';
          badgeHtml = `<span class="yoy-badge ${badgeClass}">${sign}${b.pct}%</span>`;
        }
        return `<td class="trend-col-num trend-col-npat"><div class="npat-cell-wrap"><strong class="num-val">${val}</strong>${badgeHtml}</div></td>`;
      } else {
        return `<td class="trend-col-num"><span class="num-val">${val}</span></td>`;
      }
    }).join('') + '</tr>';
  }).join('');
}

function renderDonutChart(seriesData, labelsData, colorsData) {
  const chartEl = document.querySelector("#revenueDonutChart");
  if (!chartEl) return;

  const validSeries = (seriesData && seriesData.length > 0) ? seriesData : [100];
  const validLabels = (labelsData && labelsData.length > 0) ? labelsData : ['Doanh Thu'];
  const validColors = (colorsData && colorsData.length > 0) ? colorsData : ['#10b981'];

  const options = {
    series: validSeries,
    labels: validLabels,
    colors: validColors,
    chart: {
      type: 'donut',
      height: 230,
      background: 'transparent'
    },
    stroke: { width: 2, colors: ['#fffdf7'] },
    legend: { show: false },
    dataLabels: {
      enabled: true,
      formatter: (val) => `${val.toFixed(1)}%`,
      style: {
        fontSize: '11px',
        fontFamily: 'Inter, sans-serif',
        fontWeight: '700',
        colors: ['#ffffff']
      },
      dropShadow: {
        enabled: true,
        top: 1,
        left: 1,
        blur: 2,
        color: '#000000',
        opacity: 0.8
      }
    },
    theme: { mode: 'light' },
    plotOptions: {
      pie: {
        donut: {
          size: '72%'
        }
      }
    },
    tooltip: {
      theme: 'light',
      y: {
        formatter: (val) => `${val.toLocaleString()} Tỷ VNĐ`
      }
    }
  };

  if (donutChart) donutChart.destroy();
  donutChart = new ApexCharts(chartEl, options);
  donutChart.render();
}



/* ==========================================================================
   TRADINGVIEW & PRICE CANDLE CHART ENGINE
   ========================================================================== */
function switchChartEngine(engine) {
  currentChartEngine = engine;
  const tvBtn = document.getElementById('btnChartEngineTv');
  const apexBtn = document.getElementById('btnChartEngineApex');
  const tvContainer = document.getElementById('tradingviewChartContainer');
  const apexContainer = document.getElementById('priceCandleChart');

  if (engine === 'tradingview') {
    if (tvBtn) {
      tvBtn.className = 'px-3 py-1 text-xs font-semibold rounded-lg text-emerald-400 bg-emerald-500/15 border border-emerald-500/30 flex items-center gap-1.5 transition-all shadow-sm';
    }
    if (apexBtn) {
      apexBtn.className = 'px-3 py-1 text-xs font-semibold rounded-lg text-slate-400 hover:text-slate-200 transition-all flex items-center gap-1.5';
    }
    if (tvContainer) tvContainer.style.display = 'block';
    if (apexContainer) apexContainer.style.display = 'none';

    initTradingViewLightweightChart(rawPriceHistory, currentStockSymbol);
  } else {
    if (apexBtn) {
      apexBtn.className = 'px-3 py-1 text-xs font-semibold rounded-lg text-emerald-400 bg-emerald-500/15 border border-emerald-500/30 flex items-center gap-1.5 transition-all shadow-sm';
    }
    if (tvBtn) {
      tvBtn.className = 'px-3 py-1 text-xs font-semibold rounded-lg text-slate-400 hover:text-slate-200 transition-all flex items-center gap-1.5';
    }
    if (tvContainer) tvContainer.style.display = 'none';
    if (apexContainer) apexContainer.style.display = 'block';

    if (rawPriceHistory && rawPriceHistory.length > 0) {
      renderCandleChart(rawPriceHistory);
    }
  }
}

function initTradingViewLightweightChart(history, symbol) {
  const container = document.getElementById('tradingviewChartContainer');
  if (!container) return;

  container.innerHTML = '';

  const dataList = (history && history.length > 0) ? history : (rawPriceHistory || []);
  const sym = (symbol || currentStockSymbol || 'TCB').toUpperCase().trim();
  let exch = (currentDashboardData && currentDashboardData.exchange) ? currentDashboardData.exchange.toUpperCase().trim() : 'HOSE';
  if (exch === 'HSX') exch = 'HOSE';
  if (!['HOSE', 'HNX', 'UPCOM'].includes(exch)) exch = 'HOSE';

  // Direct link to TradingView.com for full drawing features
  const tvLinkEl = document.getElementById('tvExternalLink');
  if (tvLinkEl) {
    tvLinkEl.href = `https://www.tradingview.com/chart/?symbol=${exch}:${sym}`;
  }

  if (!dataList || dataList.length === 0) {
    container.innerHTML = `
      <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%; color:#59656b; text-align:center; padding: 20px;">
        <i class="fa-solid fa-chart-candlestick" style="font-size: 44px; color: rgba(16, 185, 129, 0.4); margin-bottom: 14px;"></i>
        <div style="font-weight: 700; color: #374047; font-size: 15px;">Đang nạp dữ liệu nến TradingView cho ${sym}...</div>
      </div>`;
    return;
  }

  if (typeof LightweightCharts === 'undefined') {
    container.innerHTML = `<div style="padding:20px; color:#ef4444;">Đang kết nối thư viện TradingView Lightweight Charts...</div>`;
    return;
  }

  // Floating Legend Info
  const legendEl = document.createElement('div');
  legendEl.className = 'tv-chart-legend';
  legendEl.style.cssText = 'position: absolute; top: 12px; left: 16px; z-index: 10; font-family: Inter, sans-serif; font-size: 12px; color: #374047; pointer-events: none; background: rgba(255,253,247,.92); backdrop-filter: blur(8px); padding: 6px 14px; border-radius: 2px; border: 1px solid #c9c5ba; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; box-shadow: 0 8px 24px rgba(35,31,24,.12);';
  container.appendChild(legendEl);

  const chartWidth = container.clientWidth || 800;
  const chartHeight = container.clientHeight || 520;

  tvLightweightChartInstance = LightweightCharts.createChart(container, {
    width: chartWidth,
    height: chartHeight,
    layout: {
      background: { type: 'solid', color: '#fffdf7' },
      textColor: '#68727a',
    },
    grid: {
      vertLines: { color: 'rgba(55, 64, 71, 0.12)' },
      horzLines: { color: 'rgba(55, 64, 71, 0.12)' },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: '#38bdf8', width: 1, style: LightweightCharts.LineStyle.Dashed },
      horzLine: { color: '#38bdf8', width: 1, style: LightweightCharts.LineStyle.Dashed },
    },
    rightPriceScale: {
      borderColor: '#c9c5ba',
      scaleMargins: { top: 0.1, bottom: 0.25 },
    },
    timeScale: {
      borderColor: '#c9c5ba',
      timeVisible: true,
      secondsVisible: false,
    },
  });

  const tvCandleSeries = tvLightweightChartInstance.addCandlestickSeries({
    upColor: '#10b981',
    downColor: '#ef4444',
    borderUpColor: '#10b981',
    borderDownColor: '#ef4444',
    wickUpColor: '#10b981',
    wickDownColor: '#ef4444',
  });

  const tvVolumeSeries = tvLightweightChartInstance.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: '',
    scaleMargins: { top: 0.78, bottom: 0 },
  });

  // Prepare & Sort Candles Data
  const sortedHistory = [...dataList].sort((a, b) => {
    const da = new Date(a.date || a.time || 0).getTime();
    const db = new Date(b.date || b.time || 0).getTime();
    return da - db;
  });

  const candles = [];
  const volumes = [];

  sortedHistory.forEach(item => {
    let rawDate = item.date || item.time;
    if (!rawDate) return;

    let timeStr = '';
    if (typeof rawDate === 'string') {
      timeStr = rawDate.split('T')[0];
    } else if (typeof rawDate === 'number') {
      let d = new Date(rawDate > 1e11 ? rawDate : rawDate * 1000);
      timeStr = d.toISOString().split('T')[0];
    }

    const open = Number(item.open) || 0;
    const high = Number(item.high) || 0;
    const low = Number(item.low) || 0;
    const close = Number(item.close) || 0;
    const volume = Number(item.volume) || 0;

    if (open > 0 && close > 0 && timeStr) {
      candles.push({ time: timeStr, open, high, low, close });
      volumes.push({
        time: timeStr,
        value: volume,
        color: close >= open ? 'rgba(16, 185, 129, 0.45)' : 'rgba(239, 68, 68, 0.45)'
      });
    }
  });

  tvCandleSeries.setData(candles);
  tvVolumeSeries.setData(volumes);

  const lastCandle = candles[candles.length - 1] || {};
  const lastVol = volumes[volumes.length - 1] || {};

  function updateLegend(c, v) {
    if (!c || !c.close) {
      legendEl.innerHTML = `<span style="font-weight:700; color:#38bdf8;">${exch}:${sym}</span>`;
      return;
    }
    const color = c.close >= c.open ? '#08713c' : '#f87171';
    const diff = c.close - c.open;
    const pct = c.open ? ((diff / c.open) * 100).toFixed(2) : '0.00';
    const sign = diff >= 0 ? '+' : '';
    legendEl.innerHTML = `
      <span style="font-weight:700; color:#38bdf8;">${exch}:${sym}</span>
      <span style="color:#59656b">${c.time}</span>
      <span>Mở: <b style="color:${color}">${c.open.toLocaleString()}</b></span>
      <span>Cao: <b style="color:${color}">${c.high.toLocaleString()}</b></span>
      <span>Thấp: <b style="color:${color}">${c.low.toLocaleString()}</b></span>
      <span>Đóng: <b style="color:${color}">${c.close.toLocaleString()} (${sign}${pct}%)</b></span>
      ${v && v.value ? `<span style="color:#59656b">KL: <b style="color:#151817">${v.value.toLocaleString()}</b></span>` : ''}
    `;
  }

  updateLegend(lastCandle, lastVol);

  tvLightweightChartInstance.subscribeCrosshairMove(param => {
    if (!param || !param.time || param.point === undefined || param.point.x < 0 || param.point.y < 0) {
      updateLegend(lastCandle, lastVol);
      return;
    }
    const candle = param.seriesData.get(tvCandleSeries);
    const vol = param.seriesData.get(tvVolumeSeries);
    if (candle) {
      updateLegend({ ...candle, time: param.time }, vol);
    }
  });

  tvLightweightChartInstance.timeScale().fitContent();

  const resizeObserver = new ResizeObserver(entries => {
    if (!entries || entries.length === 0) return;
    const entry = entries[0];
    if (tvLightweightChartInstance && entry.contentRect) {
      tvLightweightChartInstance.applyOptions({
        width: entry.contentRect.width,
        height: entry.contentRect.height
      });
    }
  });
  resizeObserver.observe(container);
}

function initPriceCandleChart(history) {
  rawPriceHistory = history || [];
  setTimeframeMode(currentPriceTimeframe);
}

function setTimeframeMode(tf) {
  currentPriceTimeframe = tf;

  ['1D', '3D', '1W', '1M', '3M', '1Y', 'ALL'].forEach(t => {
    const btn = document.getElementById(`btnTf${t}`);
    if (btn) btn.classList.toggle('active', t === tf);
  });

  if (tvLightweightChartInstance) {
    try {
      const timeScale = tvLightweightChartInstance.timeScale();
      if (tf === 'ALL') {
        timeScale.fitContent();
      } else {
        let days = 250;
        if (tf === '1D') days = 5;
        else if (tf === '3D') days = 10;
        else if (tf === '1W') days = 20;
        else if (tf === '1M') days = 30;
        else if (tf === '3M') days = 90;
        else if (tf === '1Y') days = 365;

        const totalCandles = rawPriceHistory ? rawPriceHistory.length : 100;
        timeScale.setVisibleLogicalRange({
          from: Math.max(0, totalCandles - days),
          to: Math.max(0, totalCandles - 1)
        });
      }
    } catch(e) {}
  }

  if (!rawPriceHistory || rawPriceHistory.length === 0) return;

  let sliced = rawPriceHistory;
  if (tf === '1D') sliced = rawPriceHistory.slice(-1);
  else if (tf === '3D') sliced = rawPriceHistory.slice(-3);
  else if (tf === '1W') sliced = rawPriceHistory.slice(-5);
  else if (tf === '1M') sliced = rawPriceHistory.slice(-22);
  else if (tf === '3M') sliced = rawPriceHistory.slice(-65);
  else if (tf === '1Y') sliced = rawPriceHistory.slice(-250);

  renderCandleChart(sliced);
}

function renderCandleChart(history) {
  const container = document.querySelector("#priceCandleChart");
  if (!container) return;

  if (!history || history.length === 0) {
    if (candleChart) {
      try { candleChart.destroy(); } catch(e) {}
      candleChart = null;
    }
    container.innerHTML = `
      <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:360px; color:#59656b; text-align:center; padding: 20px;">
        <i class="fa-solid fa-chart-candlestick" style="font-size: 44px; color: rgba(16, 185, 129, 0.4); margin-bottom: 14px;"></i>
        <div style="font-weight: 700; color: #374047; font-size: 15px; margin-bottom: 6px;">Đang cập nhật dữ liệu lịch sử giá</div>
        <div style="font-size: 12px; color: #64748b; max-width: 400px; margin-bottom: 16px;">Nguồn dữ liệu nến giá đang tự động kết nối lại (KBS / MSN / VCI). Bấm bên dưới để tải lại.</div>
        <button onclick="fetchStockData(currentStockSymbol)" style="background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: #08713c; font-weight: 700; padding: 8px 18px; border-radius: 9999px; font-size: 12px; cursor: pointer; transition: all 0.2s;">
          <i class="fa-solid fa-rotate-right"></i> Tải lại dữ liệu biểu đồ
        </button>
      </div>
    `;
    return;
  }

  // Clear any fallback HTML
  container.innerHTML = '';

  const candleSeries = history.map(item => {
    let rawDate = item.date || item.time || '';
    let t = Date.now();
    if (typeof rawDate === 'number') {
      t = rawDate > 1e11 ? rawDate : rawDate * 1000;
    } else if (rawDate) {
      let str = String(rawDate).trim().replace(' ', 'T');
      t = new Date(str).getTime();
      if (isNaN(t)) {
        const parts = str.split('T')[0].split('-');
        if (parts.length === 3) {
          t = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])).getTime();
        }
      }
    }

    const openVal = Number(item.open) || 0;
    const highVal = Number(item.high) || 0;
    const lowVal = Number(item.low) || 0;
    const closeVal = Number(item.close) || 0;

    return {
      x: isNaN(t) ? Date.now() : t,
      y: [openVal, highVal, lowVal, closeVal]
    };
  });

  const options = {
    series: [{
      name: 'Giá CP (VND)',
      data: candleSeries
    }],
    chart: {
      type: 'candlestick',
      height: 380,
      background: 'transparent',
      toolbar: { show: true }
    },
    theme: { mode: 'light' },
    xaxis: {
      type: 'datetime',
      labels: { style: { colors: '#59656b' } }
    },
    yaxis: {
      tooltip: { enabled: true },
      labels: {
        style: { colors: '#59656b' },
        formatter: (val) => val ? val.toLocaleString() : '0'
      }
    },
    plotOptions: {
      candlestick: {
        colors: {
          upward: '#10b981',
          downward: '#ef4444'
        }
      }
    },
    grid: { borderColor: 'rgba(55, 64, 71, 0.14)' }
  };

  if (candleChart) {
    try { candleChart.destroy(); } catch(e) {}
    candleChart = null;
  }
  candleChart = new ApexCharts(container, options);
  candleChart.render();
}



/* ==========================================================================
   SECTION 5: PEER COMPARISON (15 KEY METRICS) LOGIC
   ========================================================================== */



async function triggerPeerAnalysis() {
  const preState = document.getElementById('peerPreAnalysisState');
  const scanState = document.getElementById('peerScanningState');
  const resState = document.getElementById('peerResultsState');
  const scanText = document.getElementById('peerScanText');
  const scanProgress = document.getElementById('peerScanProgress');
  const peerBadgeTop = document.getElementById('peerBadgeTop');

  if (preState) preState.style.display = 'none';
  if (resState) resState.style.display = 'none';
  if (scanState) scanState.style.display = 'block';

  if (scanProgress) scanProgress.style.width = '0%';
  if (scanText) scanText.textContent = `⚡ Đang kết nối nguồn dữ liệu & Cào 15 chỉ số của các đối thủ cùng ngành ${currentTargetSymbol}...`;

  setTimeout(() => {
    if (scanProgress) scanProgress.style.width = '45%';
  }, 250);

  try {
    const peerParam = currentPeerList && currentPeerList.length > 0 ? `?peers=${currentPeerList.join(',')}&refresh=true` : '?refresh=true';
    const res = await fetch(`/api/peers/${currentTargetSymbol}${peerParam}`);
    if (!res.ok) throw new Error("Could not fetch peer comparison");
    const data = await res.json();
    currentPeerData = data;

    if (scanProgress) scanProgress.style.width = '85%';

    setTimeout(() => {
      if (scanProgress) scanProgress.style.width = '100%';

      currentPeerList = data.peer_symbols || currentPeerList;
      renderPeerPills(currentPeerList);
      renderPeerTable(data);

      if (scanState) scanState.style.display = 'none';
      if (resState) resState.style.display = 'block';
      if (peerBadgeTop) peerBadgeTop.innerHTML = `<i class="fa-solid fa-check" style="color: #a855f7;"></i> Trích Xuất Thành Công (15 Metrics)`;
    }, 550);
  } catch (err) {
    console.error("Error in peer comparison analysis:", err);
    if (scanState) scanState.style.display = 'none';
    if (preState) preState.style.display = 'flex';
    showError(`Hệ thống đang bận hoặc gián đoạn kết nối khi lấy dữ liệu so sánh cho ${currentTargetSymbol}. Vui lòng thử lại sau vài giây!`);
  }
}

async function fetchPeerComparison(symbol, customPeers = null, refresh = false) {
  currentTargetSymbol = symbol.toUpperCase();
  if (customPeers !== null) {
    currentPeerList = customPeers;
  }

  const pillsContainer = document.getElementById('peerPillsContainer');
  const tableBody = document.getElementById('peerTableBody');
  if (pillsContainer) pillsContainer.style.opacity = '0.5';
  if (tableBody) tableBody.style.opacity = '0.5';

  try {
    const peersPart = customPeers !== null
      ? `peers=${customPeers.join(',')}&`
      : (currentPeerList && currentPeerList.length > 0 ? `peers=${currentPeerList.join(',')}&` : '');
    const refreshPart = refresh ? 'refresh=true' : '';
    const peerParam = (peersPart || refreshPart) ? `?${peersPart}${refreshPart}` : '';
    const res = await fetch(`/api/peers/${currentTargetSymbol}${peerParam}`);
    if (!res.ok) return;
    const data = await res.json();
    currentPeerData = data;

    currentPeerList = Array.isArray(data.peer_symbols) ? data.peer_symbols : currentPeerList;
    renderPeerPills(currentPeerList);
    renderPeerTable(data);
  } catch (err) {
    console.error("Error fetching peer comparison:", err);
  } finally {
    if (pillsContainer) pillsContainer.style.opacity = '1';
    if (tableBody) tableBody.style.opacity = '1';
  }
}

function renderPeerPills(peers) {
  const container = document.getElementById('peerPillsContainer');
  if (!container) return;

  if (!peers || peers.length === 0) {
    container.innerHTML = `<span style="font-size: 11.5px; color: #59656b; font-style: italic; align-self: center;">(Chưa chọn mã đối thủ nào. Nhập mã và bấm + Thêm để so sánh)</span>`;
    return;
  }

  container.innerHTML = peers.map(p => `
    <span class="peer-pill">
      ${escapeHtml(p)}
      <span class="btn-remove-peer" onclick="handleRemovePeerSymbol('${escapeHtml(p)}')" title="Xóa mã này">&times;</span>
    </span>
  `).join('');
}

function handleAddPeerSymbol() {
  const input = document.getElementById('addPeerInput');
  if (!input) return;
  const peer = input.value.trim().toUpperCase();
  input.value = '';
  if (!peer) return;

  // Ignore silently without disruptive popup alerts
  if (peer === currentTargetSymbol) return;
  if (currentPeerList.map(p => p.toUpperCase()).includes(peer)) return;
  if (currentPeerList.length >= 10) return;

  currentPeerList.push(peer);
  fetchPeerComparison(currentTargetSymbol, currentPeerList);
}

function handleRemovePeerSymbol(peerToRemove) {
  const target = peerToRemove.toUpperCase();
  currentPeerList = currentPeerList.filter(p => p.toUpperCase() !== target);
  fetchPeerComparison(currentTargetSymbol, currentPeerList);
}

// Global listener for Enter key in addPeerInput
document.addEventListener('keydown', (e) => {
  if (e.target && e.target.id === 'addPeerInput' && e.key === 'Enter') {
    e.preventDefault();
    handleAddPeerSymbol();
  }
});


function renderPeerTable(data) {
  const headerRow = document.getElementById('peerTableHeaderRow');
  const tbody = document.getElementById('peerTableBody');
  if (!headerRow || !tbody) return;

  const companies = data.companies || [];
  const metricDefs = data.metric_definitions || [];
  const industryAvg = data.industry_average || {};
  const accuracy = data.data_accuracy || {};
  const provenanceByCompany = accuracy.provenance_by_company || {};
  const provenance = document.getElementById('peerDataProvenance');
  if (provenance) {
    const policyLine = escapeHtml(data.source_policy || 'Số liệu lấy từ BCTC chuẩn hóa và giá thị trường gần nhất.');
    const periodLine = escapeHtml(data.period_policy || '');
    const sourceMode = escapeHtml(accuracy.data_source_mode || 'pack');
    const summary = accuracy.store_summary || {};
    const storeCount = (summary.financial_snapshots || 0) + (summary.peer_metric_snapshots || 0);
    provenance.innerHTML = `
      <div style="background: var(--lp-paper-muted); border: 1px solid var(--lp-line); padding: 10px 14px; border-radius: 4px; color: #374047; font-size: 11.5px; line-height: 1.55; margin-bottom: 10px;">
        <strong style="color: var(--lp-navy); font-weight: 700;">Cách đọc dữ liệu:</strong>
        ${policyLine}
        ${periodLine}
        Cột cuối dùng trung vị của nhóm và ghi rõ số mã đủ dữ liệu; N/A nghĩa là chỉ số không phù hợp hoặc không đủ căn cứ, không phải bằng 0.
      </div>
      <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 8px 14px; background: var(--lp-paper-muted); border: 1px solid var(--lp-line); border-radius: 4px; color: var(--lp-ink-soft); font-size: 11.5px;">
        <span style="color: var(--lp-navy); font-weight: 700;"><i class="fa-solid fa-fingerprint"></i> Độ chính xác dữ liệu (Provenance 100%):</span>
        <span style="color: var(--lp-ink);">Chế độ: <code style="background: var(--lp-navy-soft); color: var(--lp-navy); padding: 2px 8px; border-radius: 4px; font-weight: 700;">${sourceMode}</code></span>
        <span style="color: var(--lp-ink);">BCTC snapshots: <strong>${summary.financial_snapshots || 0}</strong></span>
        <span style="color: var(--lp-ink);">Metric snapshots: <strong>${summary.peer_metric_snapshots || 0}</strong></span>
        <span style="color: var(--lp-muted);">— mỗi ô có thể truy ngược về URL nguồn & hash SHA-256.</span>
        <span style="margin-left: auto; display: flex; gap: 6px;">
          <button class="btn-force-refresh" type="button" onclick="forceRefreshPeerMatrix()">
            <i class="fa-solid fa-arrows-rotate"></i> <span>Force Refresh</span>
          </button>
        </span>
      </div>
    `;
  }

  // Build Header Columns
  let headerHtml = `
    <th style="width: 240px; text-align: left; color: var(--lp-ink); font-weight: 700;">Nhóm Chỉ Số & Tên Chỉ Số (15 chỉ số)</th>
    <th style="width: 80px; text-align: center; color: var(--lp-ink); font-weight: 700;">Đơn vị</th>
  `;

  companies.forEach(comp => {
    const isTarget = comp.symbol === data.target_symbol;
    const prov = provenanceByCompany[comp.symbol] || {};
    const hasProvenance = !!(prov && prov.snapshot_id);
    const provenanceBadge = hasProvenance
      ? `<div style="font-size: 9px; margin-top: 4px; display: flex; align-items: center; justify-content: center; gap: 4px;">
           <span title="Có provenance BCTC trong store" style="background: var(--lp-navy-soft); color: var(--lp-navy); border: 1px solid #a8c5d0; padding: 2px 8px; border-radius: 4px; cursor: pointer; font-weight: 700;" onclick="openPeerProvenance('${escapeHtml(comp.symbol)}')">
             <i class="fa-solid fa-fingerprint"></i> #${escapeHtml(String(prov.snapshot_id))}
           </span>
         </div>`
      : '';
    headerHtml += `
      <th style="text-align: center;" class="${isTarget ? 'target-col-header' : ''}">
        <div style="font-weight: 800; color: ${isTarget ? 'var(--lp-navy)' : 'var(--lp-ink)'};">${comp.symbol}</div>
        <div style="font-size: 10px; font-weight: 600; color: var(--lp-ink-soft);">${escapeHtml(comp.reported_period || 'Chưa có kỳ')}${isTarget ? ' · Mã phân tích' : ''}</div>
        ${provenanceBadge}
      </th>
    `;
  });

  headerHtml += `<th style="text-align: center; color: var(--lp-navy); font-weight: 800;" title="Ít bị sai lệch bởi ngoại lệ hơn trung bình cộng">${escapeHtml(data.aggregation_label || 'Trung vị nhóm')}</th>`;
  headerRow.innerHTML = headerHtml;

  // Group metrics by category
  const categories = {};
  metricDefs.forEach(mdef => {
    const cat = mdef.category;
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(mdef);
  });

  let tbodyHtml = '';

  Object.keys(categories).forEach(catName => {
    // Render Category Header Row
    const cleanCatName = catName.replace(/^\d+\.\s*/, '');
    tbodyHtml += `
      <tr class="category-row">
        <td colspan="${companies.length + 3}">
          <i class="fa-solid fa-layer-group"></i> ${cleanCatName}
        </td>
      </tr>
    `;

    categories[catName].forEach(mdef => {
      const k = mdef.key;
      const vals = companies.map(c => c.metrics[k]).filter(v => v !== null && v !== undefined && Number.isFinite(Number(v)));

      // Determine Best Metric value across all companies
      let bestVal = null;
      if (vals.length && mdef.better === "higher") {
        bestVal = Math.max(...vals);
      } else if (vals.length && mdef.better === "lower") {
        bestVal = Math.min(...vals);
      }

      tbodyHtml += `<tr>`;
      tbodyHtml += `
        <td style="font-weight: 600; color: #151817;" title="${escapeHtml(mdef.note || '')}">
          <i class="fa-solid ${mdef.icon}" style="color: #59656b; margin-right: 6px;"></i> ${mdef.name}
        </td>
        <td style="text-align: center; color: var(--text-muted); font-size: 11px;">${mdef.unit}</td>
      `;

      companies.forEach(comp => {
        const val = comp.metrics[k];
        const isTarget = comp.symbol === data.target_symbol;
        const isBest = (bestVal !== null && vals.length >= 2 && val === bestVal);
        const hasValue = val !== undefined && val !== null && Number.isFinite(Number(val));
        const sectorName = (comp.sector_name || comp.archetype || '').toString().toUpperCase();
        const notApplicableSectors = mdef.not_applicable_sectors || [];
        const naReason = notApplicableSectors.find(rule => sectorName.includes(rule));

        let fmtVal;
        if (hasValue) {
          fmtVal = Number(val).toLocaleString();
          if (mdef.unit === "%") fmtVal += "%";
          else if (mdef.unit === "Tỷ VNĐ") fmtVal += " tỷ";
        } else if (naReason) {
          fmtVal = `<span style="color:#64748b;" title="Chỉ số không áp dụng cho ngành ${escapeHtml(naReason)}">—</span>`;
        } else {
          fmtVal = `<span style="color:#64748b;" title="Chưa có BCTC hoặc dữ liệu chưa đủ">—</span>`;
        }

        tbodyHtml += `
          <td style="text-align: center;" class="${isTarget ? 'target-col-val' : ''} ${isBest ? 'metric-best' : ''}">
            ${fmtVal} ${isBest ? '👑' : ''}
          </td>
        `;
      });

      // Industry Average Column
      let avgVal;
      const avgHasValue = industryAvg[k] !== undefined && industryAvg[k] !== null && Number.isFinite(Number(industryAvg[k]));
      if (avgHasValue) {
        avgVal = Number(industryAvg[k]).toLocaleString();
        if (mdef.unit === "%") avgVal += "%";
        else if (mdef.unit === "Tỷ VNĐ") avgVal += " tỷ";
      } else {
        avgVal = `<span style="color:#64748b;" title="Không đủ mã có dữ liệu để tính trung vị">—</span>`;
      }

      tbodyHtml += `
        <td style="text-align: center; font-weight: 700; color: #38bdf8;">
          ${avgVal}<div style="font-size:9px; color:#64748b; font-weight:500;">${data.metric_coverage?.[k] || 0}/${companies.length} mã</div>
        </td>
      `;

      tbodyHtml += `</tr>`;
    });
  });

  tbody.innerHTML = tbodyHtml;
}

async function forceRefreshPeerMatrix() {
  if (!currentTargetSymbol) return;
  await fetchPeerComparison(currentTargetSymbol, currentPeerList, true);
}

async function openPeerProvenance(symbol) {
  const comp = (currentPeerData && currentPeerData.companies || []).find(c => c.symbol === symbol);
  const prov = (currentPeerData && currentPeerData.data_accuracy && currentPeerData.data_accuracy.provenance_by_company || {})[symbol];
  if (!prov || !prov.snapshot_id) {
    alert(`Chưa có provenance cho ${symbol}.`);
    return;
  }
  const url = `/api/peers/${symbol}/snapshot/${prov.snapshot_id}`;
  try {
    const res = await fetch(url);
    const data = await res.json();
    
    // Existing modal clean up
    let existingModal = document.getElementById('peerProvenanceModal');
    if (existingModal) existingModal.remove();

    const payload = data.payload || {};
    const metrics = payload.metrics || {};
    const priceSource = payload.price_source || 'DNSE REST live trade';
    const priceAsOf = payload.price_as_of || 'Vừa cập nhật';

    const modalHtml = `
      <div id="peerProvenanceModal" style="position: fixed; inset: 0; background: rgba(21, 24, 23, 0.58); backdrop-filter: blur(6px); z-index: 9999; display: flex; align-items: center; justify-content: center; padding: 20px;">
        <div style="background: #fffdf7; border: 1px solid #1b211f; border-radius: 6px; max-width: 680px; width: 100%; max-height: 85vh; overflow-y: auto; padding: 24px; color: #151817; font-family: sans-serif; box-shadow: 0 28px 80px rgba(35,31,24,.18);">
          <div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #c9c5ba; padding-bottom: 14px; margin-bottom: 16px;">
            <div style="display: flex; align-items: center; gap: 10px;">
              <i class="fa-solid fa-fingerprint" style="color: #a855f7; font-size: 20px;"></i>
              <h3 style="margin: 0; font-size: 16px; font-weight: 700; color: #151817;">Kiểm Toán Dữ Liệu Provenance — ${escapeHtml(data.symbol)}</h3>
            </div>
            <button onclick="document.getElementById('peerProvenanceModal').remove()" style="background: transparent; border: none; color: #59656b; font-size: 20px; cursor: pointer;">&times;</button>
          </div>

          <div style="background: rgba(168, 85, 247, 0.08); border: 1px solid rgba(168, 85, 247, 0.3); border-radius: 8px; padding: 12px; margin-bottom: 16px; font-size: 12px; line-height: 1.6;">
            <div style="color: #c4b5fd; font-weight: 700; margin-bottom: 4px;"><i class="fa-solid fa-shield-halved"></i> Xác thực Cryptographic SHA-256:</div>
            <code style="background: #f0ebdf; padding: 4px 8px; border-radius: 4px; color: #07577a; font-family: monospace; word-break: break-all; display: block; font-size: 11px;">${escapeHtml(data.payload_hash || '')}</code>
          </div>

          <table style="width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 16px;">
            <tr style="border-bottom: 1px solid #ded9cc;">
              <td style="padding: 8px 0; color: #59656b; width: 140px; font-weight: 600;">Snapshot ID:</td>
              <td style="padding: 8px 0; font-weight: 700; color: #a855f7;">#${data.id}</td>
            </tr>
            <tr style="border-bottom: 1px solid #ded9cc;">
              <td style="padding: 8px 0; color: #59656b; font-weight: 600;">Nguồn Giá Live:</td>
              <td style="padding: 8px 0; color: #08713c;">${escapeHtml(priceSource)} (${escapeHtml(priceAsOf)})</td>
            </tr>
            <tr style="border-bottom: 1px solid #ded9cc;">
              <td style="padding: 8px 0; color: #59656b; font-weight: 600;">Giá Khớp Lệnh:</td>
              <td style="padding: 8px 0; font-weight: 700; color: #805000;">${payload.current_price ? Number(payload.current_price).toLocaleString() + ' VNĐ' : 'N/A'}</td>
            </tr>
            <tr style="border-bottom: 1px solid #ded9cc;">
              <td style="padding: 8px 0; color: #59656b; font-weight: 600;">Kỳ BCTC Báo Cáo:</td>
              <td style="padding: 8px 0; color: #38bdf8; font-weight: 700;">${escapeHtml(data.period)}</td>
            </tr>
            <tr style="border-bottom: 1px solid #ded9cc;">
              <td style="padding: 8px 0; color: #59656b; font-weight: 600;">URL Nguồn BCTC:</td>
              <td style="padding: 8px 0; word-break: break-all; color: #59656b;"><a href="${escapeHtml(data.source_url)}" target="_blank" style="color: #60a5fa; text-decoration: underline;">${escapeHtml(data.source_url)}</a></td>
            </tr>
            <tr style="border-bottom: 1px solid #ded9cc;">
              <td style="padding: 8px 0; color: #59656b; font-weight: 600;">Thời Gian Thu Thập:</td>
              <td style="padding: 8px 0; color: #374047;">${escapeHtml(data.fetched_at)}</td>
            </tr>
          </table>

          <h4 style="margin: 16px 0 10px; font-size: 13px; color: #151817; border-bottom: 1px solid #c9c5ba; padding-bottom: 6px;">Chi Tiết 15 Chỉ Số Đã Tính</h4>
          <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; font-size: 11.5px;">
            ${Object.entries(metrics).map(([k, v]) => `
              <div style="background: #f0ebdf; padding: 8px 10px; border-radius: 6px; display: flex; justify-content: space-between;">
                <span style="color: #59656b; font-weight: 500;">${escapeHtml(k)}:</span>
                <span style="color: #151817; font-weight: 700;">${v !== null && v !== undefined ? escapeHtml(String(v)) : 'N/A'}</span>
              </div>
            `).join('')}
          </div>

          <div style="margin-top: 20px; text-align: right;">
            <button onclick="document.getElementById('peerProvenanceModal').remove()" style="background: #064a6b; color: #fff; border: none; padding: 8px 18px; border-radius: 4px; font-size: 12px; font-weight: 600; cursor: pointer;">Đóng</button>
          </div>
        </div>
      </div>
    `;
    document.body.insertAdjacentHTML('beforeend', modalHtml);
  } catch (err) {
    alert(`Không tải được snapshot ${prov.snapshot_id}: ${err}`);
  }
}


/* ==========================================================================
   TRACK RECORD PUBLIC AI ADVISOR PERFORMANCE HISTORY SYSTEM
   ========================================================================== */
async function fetchTrackRecordData() {
  const tbody = document.getElementById('trTableBody');
  if (tbody) {
    tbody.innerHTML = `
      <tr>
        <td colspan="8" class="text-center py-10 text-slate-400 font-medium">
          <i class="fa-solid fa-circle-notch fa-spin text-emerald-400 text-2xl mb-2 block"></i>
          Đang tải dữ liệu Track Record AI...
        </td>
      </tr>
    `;
  }

  try {
    const res = await fetch('/api/track_record');
    if (!res.ok) {
      throw new Error(`Mã lỗi HTTP ${res.status}`);
    }
    const data = await res.json();
    
    renderTrackRecordStats(data.stats);
    ALL_TRACK_RECORDS = data.records || [];
    applyTrackRecordFilters();
  } catch (err) {
    console.error("Error fetching track record data:", err);
    if (tbody) {
      tbody.innerHTML = `
        <tr>
          <td colspan="8" class="text-center py-8 text-rose-400 font-medium">
            <i class="fa-solid fa-triangle-exclamation text-2xl mb-2 block text-rose-500"></i>
            Lỗi khi tải dữ liệu Track Record (${err.message || 'Lỗi kết nối'}). 
            <button onclick="fetchTrackRecordData()" class="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-xs font-bold text-rose-300 transition-all cursor-pointer">
              <i class="fa-solid fa-arrows-rotate"></i> Thử lại
            </button>
          </td>
        </tr>
      `;
    }
  }
}

function renderTrackRecordStats(stats) {
  if (!stats) return;
  
  if (document.getElementById('trStatTotal')) {
    document.getElementById('trStatTotal').textContent = stats.tong_so_khuyen_nghi || 0;
  }
  if (document.getElementById('trStatSources')) {
    const aiTotal = (stats.deepseek_count || 0) + (stats.gemini_count || 0);
    document.getElementById('trStatSources').textContent = `${aiTotal} Lộc Phát AI / ${stats.fallback_count || 0} Fallback`;
  }
  if (document.getElementById('trStatWinRate')) {
    document.getElementById('trStatWinRate').textContent = `${stats.ty_le_thang || 0}%`;
  }
  if (document.getElementById('trStatFinished')) {
    document.getElementById('trStatFinished').textContent = `Trên ${stats.da_ket_thuc || 0} vị thế đã chốt (${stats.so_win || 0} Thắng)`;
  }
  if (document.getElementById('trStatAvgReturn')) {
    const avg = stats.loi_nhuan_trung_binh || 0;
    const sign = avg > 0 ? '+' : '';
    const el = document.getElementById('trStatAvgReturn');
    el.textContent = `${sign}${avg}%`;
    el.style.color = avg >= 0 ? '#10b981' : '#f43f5e';
  }
  
  // Top sector
  const sectors = stats.phan_bo_theo_nganh || [];
  if (sectors.length > 0 && document.getElementById('trStatTopSector')) {
    const topSec = [...sectors].sort((a, b) => b.ty_le_thang - a.ty_le_thang || b.loi_nhuan_trung_binh - a.loi_nhuan_trung_binh)[0];
    document.getElementById('trStatTopSector').textContent = topSec.sector_name;
    document.getElementById('trStatTopSectorSub').textContent = `Win-Rate: ${topSec.ty_le_thang}% (${topSec.tong_so} Khuyến nghị)`;
  }
}

function applyTrackRecordFilters() {
  const tickerInput = document.getElementById('trFilterTicker');
  const statusSelect = document.getElementById('trFilterStatus');
  
  const qTicker = tickerInput ? tickerInput.value.trim().toUpperCase() : '';
  const qStatus = statusSelect ? statusSelect.value.trim() : '';

  let filtered = ALL_TRACK_RECORDS;
  if (qTicker) {
    filtered = filtered.filter(r => r.ticker.includes(qTicker));
  }
  if (qStatus) {
    filtered = filtered.filter(r => r.status === qStatus);
  }

  renderTrackRecordTable(filtered);
}

function renderTrackRecordTable(records) {
  const tbody = document.getElementById('trTableBody');
  if (!tbody) return;

  if (!records || records.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="8" class="text-center py-8 text-slate-500 font-medium">
          <i class="fa-solid fa-clipboard-list text-2xl mb-2 block"></i>
          Chưa có nhật ký khuyến nghị khớp với bộ lọc
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = records.map(r => {
    const logoUrl = getLogoUrl(r.ticker, 'jpeg');
    const createdStr = r.timestamp_created ? new Date(r.timestamp_created).toLocaleString('vi-VN') : '';
    
    // Status Badge
    let statusBadge = '';
    if (r.status === 'DAT_MUC_TIEU') {
      statusBadge = `<span class="px-2.5 py-1 rounded-md text-[11px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">🎯 ĐẠT MỤC TIÊU</span>`;
    } else if (r.status === 'CHAM_CAT_LO') {
      statusBadge = `<span class="px-2.5 py-1 rounded-md text-[11px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/40">🛡️ CHẠM CẮT LỖ</span>`;
    } else if (r.status === 'HET_HAN_KHONG_DAT') {
      statusBadge = `<span class="px-2.5 py-1 rounded-md text-[11px] font-bold bg-amber-500/20 text-amber-400 border border-amber-500/40">⏳ HẾT HẠN</span>`;
    } else {
      statusBadge = `<span class="px-2.5 py-1 rounded-md text-[11px] font-bold bg-sky-500/20 text-sky-400 border border-sky-500/40">⚡ ĐANG THEO DÕI</span>`;
    }

    // Source Badge
    const sourceBadge = (r.source === 'deepseek' || r.source === 'deepseek-v4-flash' || r.source === 'gemini')
      ? `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30">Lộc Phát AI</span>`
      : `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700">Fallback</span>`;

    // Action color
    const act = r.action || 'MUA TÍCH LŨY';
    const actColor = (act.includes('MUA') || act.includes('BUY')) ? 'text-emerald-400' : (act.includes('NẮM') ? 'text-amber-400' : 'text-rose-400');

    // Return %
    const ret = r.actual_return_percent || 0;
    const retSign = ret > 0 ? '+' : '';
    const retClass = ret > 0 ? 'text-emerald-400' : (ret < 0 ? 'text-rose-400' : 'text-slate-400');

    return `
      <tr class="hover:bg-slate-800/40 transition-colors cursor-pointer" onclick="selectStock('${r.ticker}')">
        <td class="py-3 px-4">
          <div class="flex items-center gap-2.5">
            <div class="w-7 h-7 rounded-lg overflow-hidden bg-slate-950 flex items-center justify-center border border-slate-800 shrink-0">
              <img src="${logoUrl}" alt="${r.ticker}" class="w-full h-full object-contain" onerror="handleLogoError(this, '${r.ticker}')">
            </div>
            <div>
              <div class="font-extrabold text-white text-sm hover:text-emerald-400 transition-colors">${r.ticker}</div>
              <div class="text-[10px] text-slate-400 font-normal">${r.sector_name || 'Chứng khoán'}</div>
            </div>
          </div>
        </td>
        <td class="py-3 px-4 text-slate-400 text-[11px]">${createdStr}</td>
        <td class="py-3 px-4">${sourceBadge}</td>
        <td class="py-3 px-4 font-bold ${actColor}">${act}</td>
        <td class="py-3 px-4 text-right font-semibold text-slate-100">${(r.price_at_creation || 0).toLocaleString()} đ</td>
        <td class="py-3 px-4 text-center text-[11px] text-slate-300">
          <div class="truncate max-w-xs mx-auto">
            <span class="text-slate-400">Mua:</span> <strong class="text-slate-200">${r.entry_zone || '--'}</strong><br>
            <span class="text-emerald-400">Mục tiêu:</span> <strong>${r.target_price || '--'}</strong> | 
            <span class="text-rose-400">Cắt lỗ:</span> <strong>${r.stop_loss_price || '--'}</strong>
          </div>
        </td>
        <td class="py-3 px-4 text-center">${statusBadge}</td>
        <td class="py-3 px-4 text-right font-extrabold ${retClass} text-sm">${retSign}${ret}%</td>
      </tr>
    `;
  }).join('');
}

/**
 * 6. Render Forensic Red-Flag Analysis Engine Section
 */
function renderForensicAnalysis(forensicData) {
  const container = document.getElementById('forensicContent');
  const badgeContainer = document.getElementById('forensicBadgeTop');
  if (!container) return;

  if (!forensicData) {
    forensicData = {
      muc_do_rui_ro_tong_the: "Sạch",
      so_co_do_kich_hoat: 0,
      chi_tiet_co_do: []
    };
  }

  const riskLevel = forensicData.muc_do_rui_ro_tong_the || "Sạch";
  const flags = forensicData.chi_tiet_co_do || [];

  // Risk Level badge styling
  let badgeClass = "";
  let badgeIcon = "";

  if (riskLevel === "Sạch") {
    badgeClass = "bg-emerald-500/10 border-emerald-500/30 text-emerald-400";
    badgeIcon = "fa-shield-check";
  } else if (riskLevel === "Cần theo dõi") {
    badgeClass = "bg-sky-500/10 border-sky-500/30 text-sky-400";
    badgeIcon = "fa-circle-info";
  } else if (riskLevel === "Cảnh báo") {
    badgeClass = "bg-amber-500/10 border-amber-500/30 text-amber-400";
    badgeIcon = "fa-triangle-exclamation";
  } else {
    // Nghiêm trọng
    badgeClass = "bg-rose-500/10 border-rose-500/30 text-rose-400";
    badgeIcon = "fa-skull-crossbones";
  }

  if (badgeContainer) {
    badgeContainer.innerHTML = `
      <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-bold ${badgeClass}">
        <i class="fa-solid ${badgeIcon}"></i> Rủi Ro BCTC: ${riskLevel} (${flags.length} cờ đỏ)
      </span>
    `;
  }

  if (flags.length === 0) {
    container.innerHTML = `
      <div class="flex flex-col items-center justify-center py-8 text-center bg-slate-950/40 rounded-2xl border border-slate-800/60">
        <div class="w-14 h-14 rounded-full bg-emerald-500/10 text-emerald-400 flex items-center justify-center text-2xl mb-3 border border-emerald-500/30 shadow-lg shadow-emerald-500/10">
          <i class="fa-solid fa-shield-check"></i>
        </div>
        <h4 class="text-base font-extrabold text-white mb-1">Báo Cáo Tài Chính Sạch & An Toàn</h4>
        <p class="text-xs text-slate-400 max-w-md">Không phát hiện bất kỳ dấu hiệu bất thường nào trong 7 tiêu chí kiểm duyệt chất lượng BCTC 8 quý gần nhất.</p>
      </div>
    `;
    return;
  }

  let html = `
    <div class="mb-4 p-4 rounded-2xl border ${badgeClass} backdrop-blur-xl flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl flex items-center justify-center text-lg bg-slate-950/60">
          <i class="fa-solid ${badgeIcon}"></i>
        </div>
        <div>
          <div class="text-xs font-semibold opacity-80 uppercase tracking-wider">Đánh Giá Chất Lượng BCTC</div>
          <div class="text-base font-extrabold">Mức Độ Rủi Ro: ${riskLevel} (${flags.length} cờ đỏ kích hoạt)</div>
        </div>
      </div>
      <div class="text-xs opacity-90 italic">
        *Thuật toán Python định lượng tính toán trực tiếp từ dữ liệu BCTC đã chuẩn hóa.
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
  `;

  flags.forEach(flag => {
    let sevBadge = "";
    if (flag.severity === "cao") {
      sevBadge = `<span class="bg-rose-500/20 border border-rose-500/40 text-rose-400 text-[10px] font-extrabold px-2.5 py-0.5 rounded-full uppercase"><i class="fa-solid fa-fire"></i> CAO</span>`;
    } else if (flag.severity === "trung_binh_cao") {
      sevBadge = `<span class="bg-amber-500/20 border border-amber-500/40 text-amber-400 text-[10px] font-extrabold px-2.5 py-0.5 rounded-full uppercase"><i class="fa-solid fa-triangle-exclamation"></i> TRUNG BÌNH - CAO</span>`;
    } else if (flag.severity === "trung_binh") {
      sevBadge = `<span class="bg-yellow-500/20 border border-yellow-500/40 text-yellow-400 text-[10px] font-extrabold px-2.5 py-0.5 rounded-full uppercase"><i class="fa-solid fa-circle-exclamation"></i> TRUNG BÌNH</span>`;
    } else {
      sevBadge = `<span class="bg-sky-500/20 border border-sky-500/40 text-sky-400 text-[10px] font-extrabold px-2.5 py-0.5 rounded-full uppercase"><i class="fa-solid fa-eye"></i> THEO DÕI</span>`;
    }

    let causeBadge = "";
    if (flag.kha_nang_nguyen_nhan === "Có thể chính đáng") {
      causeBadge = `<span class="text-emerald-400 font-bold bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 rounded text-[11px]"><i class="fa-solid fa-circle-check"></i> Có thể chính đáng</span>`;
    } else {
      causeBadge = `<span class="text-rose-400 font-bold bg-rose-500/10 border border-rose-500/30 px-2 py-0.5 rounded text-[11px]"><i class="fa-solid fa-circle-exclamation"></i> Đáng lo ngại</span>`;
    }

    html += `
      <div class="bg-slate-950/60 border border-slate-800/80 hover:border-slate-700 p-4 rounded-2xl flex flex-col justify-between transition-all shadow-lg">
        <div>
          <div class="flex items-center justify-between gap-2 mb-2">
            <h5 class="text-sm font-bold text-white flex items-center gap-2">
              <i class="fa-solid fa-flag text-rose-500"></i> ${flag.ten_hien_thi || flag.flag}
            </h5>
            ${sevBadge}
          </div>

          <div class="text-xs font-semibold text-rose-400/90 bg-rose-500/10 border border-rose-500/20 px-3 py-1.5 rounded-xl mb-3">
            <i class="fa-solid fa-chart-line mr-1"></i> ${flag.so_lieu_cu_the || ''}
          </div>

          <p class="text-xs text-slate-300 mb-3 leading-relaxed">
            ${flag.giai_thich || ''}
          </p>
        </div>

        <div class="pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs gap-2">
          <div class="flex items-center gap-1">
            <span class="text-slate-400 font-medium">Đánh giá:</span>
            ${causeBadge}
          </div>
          <span class="text-[11px] text-slate-400 truncate max-w-[200px]" title="${flag.ly_do_nhan_dinh || ''}">${flag.ly_do_nhan_dinh || ''}</span>
        </div>
      </div>
    `;
  });

  html += `</div>`;
  container.innerHTML = html;
}
