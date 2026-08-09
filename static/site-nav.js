/* Global Logo Fallback Helper */
if (!window.handleLogoFallback) {
  window.TICKER_AVATAR_COLORS = [
    'linear-gradient(135deg, #1e3a8a, #3b82f6)',
    'linear-gradient(135deg, #065f46, #10b981)',
    'linear-gradient(135deg, #4c1d95, #8b5cf6)',
    'linear-gradient(135deg, #831843, #ec4899)',
    'linear-gradient(135deg, #7c2d12, #f97316)',
    'linear-gradient(135deg, #164e63, #06b6d4)',
    'linear-gradient(135deg, #1e293b, #64748b)',
    'linear-gradient(135deg, #701a75, #d946ef)'
  ];

  window.getTickerColor = function(symbol) {
    let hash = 0;
    const str = String(symbol || '').toUpperCase().trim();
    for (let i = 0; i < str.length; i++) {
      hash = str.charCodeAt(i) + ((hash << 5) - hash);
    }
    const idx = Math.abs(hash) % window.TICKER_AVATAR_COLORS.length;
    return window.TICKER_AVATAR_COLORS[idx];
  };

  window.handleLogoFallback = function(imgEl, symbol) {
    if (!imgEl) return;
    const sym = String(symbol || '').toUpperCase().trim();
    const tried = imgEl.getAttribute('data-logo-tried') || 'jpeg';

    if (tried === 'jpeg') {
      imgEl.setAttribute('data-logo-tried', 'png');
      imgEl.src = `https://cdn.simplize.vn/simplizevn/logo/${sym}.png`;
      return;
    } else if (tried === 'png') {
      imgEl.setAttribute('data-logo-tried', 'jpg');
      imgEl.src = `https://cdn.simplize.vn/simplizevn/logo/${sym}.jpg`;
      return;
    }

    imgEl.style.display = 'none';
    let parent = imgEl.parentElement;
    if (!parent) return;

    let fallback = parent.querySelector('.lp-logo-fallback, .wl-logo-fallback, .logo-avatar-fallback');
    if (!fallback) {
      fallback = document.createElement('div');
      const isWl = imgEl.classList.contains('wl-logo');
      const isModal = imgEl.classList.contains('modal-item-logo');

      fallback.className = 'lp-logo-fallback ' + (isWl ? 'wl-logo-fallback' : isModal ? 'modal-logo-fallback' : '');
      fallback.style.background = window.getTickerColor(sym);
      fallback.textContent = sym.length <= 4 ? sym : sym.substring(0, 3);
      parent.appendChild(fallback);
    }
    fallback.style.display = 'inline-flex';
  };
}

(() => {
  const mount = document.querySelector('[data-lp-site-nav]');
  if (!mount) return;
  mount.classList.add('lp-page-nav-wrap');

  try {
    const HISTORY_KEY = 'lps_search_history';

    // 1. Navigation items definitions (Single Source of Truth)
    const NAV_ITEMS = [
      {
        key: 'home',
        href: '/',
        label: 'Tổng quan',
        description: 'Phân tích và tra cứu cổ phiếu',
      },
      {
        key: 'visual-market',
        label: 'Trực quan thị trường',
        isDropdown: true,
        children: [
          {
            key: 'heatmap',
            href: '/heatmap',
            label: 'Bản đồ nhiệt',
            description: 'Theo dõi sức mạnh toàn thị trường',
          },
          {
            key: 'bubbles',
            href: '/bubbles',
            label: 'Bong bóng thị trường',
            description: 'Quan sát biến động cổ phiếu dạng bong bóng',
          },
        ],
      },
      {
        key: 'calendar',
        href: '/calendar',
        label: 'Lịch doanh nghiệp',
        description: 'Sự kiện và quyền cổ đông',
      },
      {
        key: 'tech',
        label: 'Kỹ thuật cổ phiếu',
        isDropdown: true,
        children: [
          {
            key: 'backtest',
            href: '/backtest',
            label: 'Kiểm định RSI',
            description: 'Kiểm định chiến lược phân kỳ RSI',
          },
          {
            key: 'rrg',
            href: '/rrg',
            label: 'Biểu đồ RRG',
            description: 'Biểu đồ sức mạnh giá RRG',
          },
        ],
      },
      {
        key: 'watchlist',
        href: '/watchlist',
        label: 'Theo Dõi Của Tôi',
        description: 'Danh mục cổ phiếu cá nhân',
      },
    ];

    const path = location.pathname.toLowerCase().replace(/\/$/, '') || '/';
    const active = path.startsWith('/stock') ? 'home'
      : path.startsWith('/heatmap') ? 'heatmap'
      : path.startsWith('/bubbles') ? 'bubbles'
      : path.startsWith('/backtest') ? 'backtest'
      : path.startsWith('/rrg') ? 'rrg'
      : path.startsWith('/calendar') ? 'calendar'
      : path.startsWith('/watchlist') ? 'watchlist'
      : 'home';

    const esc = (value) => String(value || '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));

    // Inject Search CSS if not present
    if (!document.querySelector('link[data-lp-search-style]')) {
      document.head.insertAdjacentHTML('beforeend', '<link data-lp-search-style rel="stylesheet" href="/static/site-nav-search.css?v=20260808_centered_responsive_v3">');
    }

    // 2. Render Header
    mount.innerHTML = `<header class="lp-global-nav">
      <a class="lp-nav-brand" href="/" aria-label="Lộc Phát Securities">
        <div class="lp-nav-brand-logo-wrap">
          <img src="/static/brand-logo.png?v=20260808_logo_fix" alt="Lộc Phát Securities">
        </div>
        <span class="lp-nav-brand-text">
          <strong>Lộc Phát Securities</strong>
          <small class="lp-brand-badge">Market Intelligence</small>
        </span>
      </a>

      <nav class="lp-nav-links" aria-label="Điều hướng chính">
        ${NAV_ITEMS.map(item => {
          if (item.isDropdown) {
            const hasActiveChild = item.children && item.children.some(c => c.key === active);
            return `
              <div class="lp-nav-dropdown ${hasActiveChild ? 'active' : ''}">
                <button class="lp-nav-link lp-dropdown-toggle" type="button" aria-haspopup="true">
                  <span>${esc(item.label)}</span>
                  <svg class="lp-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="6 9 12 15 18 9"></polyline>
                  </svg>
                </button>
                <div class="lp-dropdown-menu" role="menu">
                  ${item.children.map(child => `
                    <a class="lp-dropdown-item ${active === child.key ? 'active' : ''}" href="${child.href}" role="menuitem">
                      <div class="lp-dropdown-item-title">${esc(child.label)}</div>
                      ${child.description ? `<div class="lp-dropdown-item-sub">${esc(child.description)}</div>` : ''}
                    </a>
                  `).join('')}
                </div>
              </div>
            `;
          }
          return `
            <a class="lp-nav-link ${active === item.key ? 'active' : ''}" href="${item.href}">${esc(item.label)}</a>
          `;
        }).join('')}
      </nav>

      <div class="lp-nav-actions lp-nav-desktop-actions">
        <button class="lp-nav-search" data-lp-open-search type="button" aria-label="Tìm mã cổ phiếu">
          <svg class="lp-nav-search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <span>Tra cứu mã cổ phiếu...</span>
          <kbd>/</kbd>
        </button>
      </div>

      <div class="lp-nav-mobile-actions">
        <button class="lp-nav-icon-button" data-lp-open-search type="button" aria-label="Tìm kiếm mã cổ phiếu">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </button>
        <button class="lp-nav-menu-toggle" id="lpMobileNavToggle" type="button" aria-label="Mở menu điều hướng" aria-expanded="false" aria-controls="lpMobileNav">
          <span class="hamburger-bar"></span>
          <span class="hamburger-bar"></span>
          <span class="hamburger-bar"></span>
        </button>
      </div>
    </header>`;

    // 3. Render Search Overlay if missing
    if (!document.getElementById('lpSearchOverlay')) {
      document.body.insertAdjacentHTML('beforeend', `<div class="lp-search-overlay lp-search-overlay-legacy" id="lpSearchOverlay" role="dialog" aria-modal="true" aria-hidden="true" aria-label="Tìm cổ phiếu"><div class="lp-search-dialog" tabindex="-1">
        <div class="lp-search-head"><span class="lp-search-icon" aria-hidden="true">⌕</span><input id="lpSearchInput" autocomplete="off" enterkeyhint="search" aria-label="Nhập mã cổ phiếu cần tìm" placeholder="Tìm kiếm mã cổ phiếu (SSI, PNJ, BCM, FPT...)" maxlength="80"><div class="lp-search-controls"><kbd>ESC</kbd><button class="lp-search-close" id="lpSearchClose" type="button" title="Đóng tìm kiếm" aria-label="Đóng tìm kiếm">×</button></div></div>
        <div class="lp-search-body"><div class="lp-search-label"><span class="lp-search-label-title"><i></i><b id="lpSearchLabel">Tìm kiếm gần đây</b></span><button class="lp-search-clear" id="lpSearchClear" type="button">Xóa Tất Cả</button></div><div id="lpSearchResults"></div></div>
      </div></div>`);
    }

    // 4. Render Mobile Drawer Overlay if missing
    if (!document.getElementById('lpMobileNav')) {
      const mobileNavAiWrap = active === 'heatmap'
        ? `<div class="lp-mobile-nav-ai-wrap">
            <button class="lp-nav-ai lp-mobile-ai-btn" data-lp-heatmap-ai type="button" aria-label="Mở nhận định AI thị trường">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>
              </svg>
              <span>Nhận định AI thị trường</span>
            </button>
          </div>`
        : '';

      document.body.insertAdjacentHTML('beforeend', `<div class="lp-mobile-nav-overlay" id="lpMobileNav" aria-hidden="true">
        <div class="lp-mobile-nav-backdrop" data-lp-close-mobile-nav></div>

        <aside class="lp-mobile-nav-panel" role="dialog" aria-modal="true" aria-label="Điều hướng Lộc Phát Securities">
          <div class="lp-mobile-nav-head">
            <a class="lp-mobile-nav-brand" href="/" aria-label="Lộc Phát Securities">
              <img src="/static/brand-logo.png?v=20260808_logo_fix" alt="Lộc Phát Securities">
              <span><strong>Lộc Phát Securities</strong></span>
            </a>
            <div class="lp-mobile-nav-head-actions">
              <button class="lp-nav-icon-button" data-lp-open-search type="button" aria-label="Tìm kiếm mã cổ phiếu">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <circle cx="11" cy="11" r="8"></circle>
                  <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
              </button>
              <button class="lp-mobile-nav-close" data-lp-close-mobile-nav type="button" aria-label="Đóng menu điều hướng">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <line x1="18" y1="6" x2="6" y2="18"></line>
                  <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
              </button>
            </div>
          </div>

          ${mobileNavAiWrap}

          <nav class="lp-mobile-nav-links" aria-label="Điều hướng mobile">
            ${NAV_ITEMS.map(item => {
              if (item.isDropdown) {
                return `
                  <div class="lp-mobile-nav-group">
                    <div class="lp-mobile-nav-group-title">${esc(item.label)}</div>
                    ${item.children.map(child => {
                      const isActive = active === child.key;
                      return `
                        <a class="lp-mobile-nav-link ${isActive ? 'active' : ''}" href="${child.href}">
                          <div class="lp-mobile-nav-text">
                            <div class="lp-mobile-nav-title">
                              <strong>${esc(child.label)}</strong>
                              ${isActive ? `<span class="lp-mobile-nav-badge">Đang xem</span>` : ''}
                            </div>
                            <small>${esc(child.description)}</small>
                          </div>
                          <svg class="lp-mobile-nav-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <polyline points="9 18 15 12 9 6"></polyline>
                          </svg>
                        </a>
                      `;
                    }).join('')}
                  </div>
                `;
              }
              const isActive = active === item.key;
              return `
                <a class="lp-mobile-nav-link ${isActive ? 'active' : ''}" href="${item.href}">
                  <div class="lp-mobile-nav-text">
                    <div class="lp-mobile-nav-title">
                      <strong>${esc(item.label)}</strong>
                      ${isActive ? `<span class="lp-mobile-nav-badge">Đang xem</span>` : ''}
                    </div>
                    <small>${esc(item.description)}</small>
                  </div>
                  <svg class="lp-mobile-nav-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </a>
              `;
            }).join('')}
          </nav>

          <div class="lp-mobile-nav-footer">
            <small>Lộc Phát Securities © 2026</small>
          </div>
        </aside>
      </div>`);
    }

    // 5. Drawer State & Event Management
    let mobileNavOpen = false;
    let mobileNavTrigger = null;

    const openMobileNav = () => {
      const mobileNav = document.getElementById('lpMobileNav');
      const toggleBtn = document.getElementById('lpMobileNavToggle');
      if (!mobileNav) return;
      mobileNavOpen = true;
      mobileNavTrigger = document.activeElement;

      if (toggleBtn) {
        toggleBtn.setAttribute('aria-expanded', 'true');
        toggleBtn.classList.add('open');
      }
      mobileNav.setAttribute('aria-hidden', 'false');
      mobileNav.classList.add('open');
      document.body.classList.add('lp-mobile-nav-open');

      const closeBtn = mobileNav.querySelector('[data-lp-close-mobile-nav]');
      const firstLink = mobileNav.querySelector('.lp-mobile-nav-link');
      if (closeBtn) closeBtn.focus();
      else if (firstLink) firstLink.focus();
    };

    const closeMobileNav = () => {
      const mobileNav = document.getElementById('lpMobileNav');
      const toggleBtn = document.getElementById('lpMobileNavToggle');
      if (!mobileNav || !mobileNavOpen) return;
      mobileNavOpen = false;

      if (toggleBtn) {
        toggleBtn.setAttribute('aria-expanded', 'false');
        toggleBtn.classList.remove('open');
      }
      mobileNav.setAttribute('aria-hidden', 'true');
      mobileNav.classList.remove('open');
      document.body.classList.remove('lp-mobile-nav-open');

      if (mobileNavTrigger && typeof mobileNavTrigger.focus === 'function') {
        mobileNavTrigger.focus();
      }
    };

    const toggleMobileNav = () => {
      if (mobileNavOpen) closeMobileNav();
      else openMobileNav();
    };

    // Bind Mobile Toggle
    const toggleBtn = document.getElementById('lpMobileNavToggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', toggleMobileNav);
    }

    // Bind Close buttons
    document.querySelectorAll('[data-lp-close-mobile-nav]').forEach(button => {
      button.addEventListener('click', closeMobileNav);
    });

    // Close when clicking nav links inside drawer
    document.querySelectorAll('.lp-mobile-nav-link').forEach(link => {
      link.addEventListener('click', closeMobileNav);
    });

    // Handle Resize beyond the compact navigation breakpoint.
    window.addEventListener('resize', () => {
      if (window.innerWidth > 1180 && mobileNavOpen) {
        closeMobileNav();
      }
    });

    // Focus trap inside mobile nav
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && mobileNavOpen) {
        closeMobileNav();
        return;
      }
      if (e.key === 'Tab' && mobileNavOpen) {
        const panel = document.querySelector('.lp-mobile-nav-panel');
        if (!panel) return;
        const focusables = panel.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])');
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    });

    // 6. Search Overlay Integration
    const overlay = document.getElementById('lpSearchOverlay');
    const input = document.getElementById('lpSearchInput');
    const results = document.getElementById('lpSearchResults');
    const label = document.getElementById('lpSearchLabel');
    const clearButton = document.getElementById('lpSearchClear');
    let timer = 0;
    let rows = [];
    let selected = 0;
    let showingHistory = true;
    let lastSearchTrigger = null;

    const loadHistory = () => {
      try {
        const parsed = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
        return Array.isArray(parsed) ? parsed : [];
      } catch {
        return [];
      }
    };
    const saveHistory = (items) => localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 10)));
    const timeAgo = (timestamp) => {
      const elapsed = Math.max(0, Date.now() - Number(timestamp || 0));
      const minutes = Math.floor(elapsed / 60000);
      const hours = Math.floor(elapsed / 3600000);
      const days = Math.floor(elapsed / 86400000);
      if (!timestamp || elapsed < 30000) return 'Vừa xong';
      if (minutes < 1) return `${Math.floor(elapsed / 1000)}s`;
      if (minutes < 60) return `${minutes} phút`;
      if (hours < 24) return `${hours} giờ`;
      if (days < 30) return `${days} ngày`;
      return `${Math.floor(days / 30)} tháng`;
    };

    const openStock = (symbol, name = '') => {
      const sym = String(symbol || '').trim().toUpperCase();
      if (!sym) return;
      const history = loadHistory().filter((item) => item.symbol !== sym);
      history.unshift({ symbol: sym, organ_name: name, timestamp: Date.now() });
      saveHistory(history);
      location.href = `/stock/${encodeURIComponent(sym)}`;
    };

    const render = (list, historyMode = false) => {
      rows = list;
      selected = 0;
      showingHistory = historyMode;
      if (!list.length) {
        results.innerHTML = `<div class="lp-search-empty">${historyMode ? 'Chưa có lịch sử tìm kiếm gần đây.' : 'Không có kết quả phù hợp.'}</div>`;
        return;
      }
      results.innerHTML = list.map((row, index) => `<div class="lp-search-row ${index === 0 ? 'selected' : ''}" data-index="${index}" role="button" tabindex="0">
        <span class="lp-search-history-icon" aria-hidden="true">◷</span>
        <div class="lp-logo-wrap">
          <img class="lp-search-logo" src="https://cdn.simplize.vn/simplizevn/logo/${esc(row.symbol)}.jpeg" onerror="window.handleLogoFallback(this, '${esc(row.symbol)}')" alt="${esc(row.symbol)}">
        </div>
        <span class="lp-search-company"><strong>${esc(row.symbol)}</strong><small>${esc(row.name || row.organ_name || 'Doanh nghiệp niêm yết')}</small></span>
        <span class="lp-search-row-meta">${historyMode ? `<b>${esc(row.symbol)}</b><time>${esc(timeAgo(row.timestamp))}</time>` : '<b>MỞ PHÂN TÍCH</b>'}</span>
        ${historyMode ? `<button class="lp-search-remove" data-symbol="${esc(row.symbol)}" type="button" title="Xóa ${esc(row.symbol)} khỏi lịch sử" aria-label="Xóa ${esc(row.symbol)} khỏi lịch sử">×</button>` : ''}
      </div>`).join('');
      results.querySelectorAll('.lp-search-row').forEach((rowElement) => {
        rowElement.addEventListener('click', () => {
          const row = rows[Number(rowElement.dataset.index)];
          openStock(row.symbol, row.name || row.organ_name);
        });
        rowElement.addEventListener('keydown', (event) => {
          if (event.key === 'Enter') rowElement.click();
        });
      });
      results.querySelectorAll('.lp-search-remove').forEach((button) => {
        button.addEventListener('click', (event) => {
          event.stopPropagation();
          event.preventDefault();
          saveHistory(loadHistory().filter((item) => item.symbol !== button.dataset.symbol));
          showHistory();
        });
      });
    };

    const showHistory = () => {
      label.textContent = 'Tìm kiếm gần đây';
      clearButton.hidden = false;
      render(loadHistory(), true);
    };

    const openSearch = (event) => {
      lastSearchTrigger = event?.currentTarget instanceof HTMLElement ? event.currentTarget : document.activeElement;
      closeMobileNav();
      overlay.classList.add('open');
      overlay.setAttribute('aria-hidden', 'false');
      document.body.classList.add('lp-search-open');
      input.value = '';
      showHistory();
      setTimeout(() => input.focus(), 30);
    };

    const closeSearch = () => {
      if (!overlay.classList.contains('open')) return;
      overlay.classList.remove('open');
      overlay.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('lp-search-open');
      if (lastSearchTrigger instanceof HTMLElement && lastSearchTrigger.isConnected) {
        const focusTarget = lastSearchTrigger;
        window.setTimeout(() => focusTarget.focus({ preventScroll: true }), 0);
      }
    };

    // Bind all data-lp-open-search buttons (desktop bar, mobile bar, mobile drawer)
    document.querySelectorAll('[data-lp-open-search]').forEach(button => {
      button.addEventListener('click', openSearch);
    });

    document.getElementById('lpSearchClose')?.addEventListener('click', closeSearch);
    clearButton?.addEventListener('click', () => {
      saveHistory([]);
      showHistory();
    });
    overlay?.addEventListener('click', (event) => {
      if (event.target === overlay) closeSearch();
    });
    input?.addEventListener('input', () => {
      clearTimeout(timer);
      const query = input.value.trim();
      if (!query) return showHistory();
      timer = setTimeout(async () => {
        label.textContent = 'Gợi ý tìm kiếm';
        clearButton.hidden = true;
        try {
          const response = await fetch(`/api/search_suggest?q=${encodeURIComponent(query)}`);
          const data = await response.json();
          render(data.results || [], false);
        } catch {
          render([], false);
        }
      }, 120);
    });
    input?.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') return closeSearch();
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        selected = Math.max(0, Math.min(rows.length - 1, selected + (event.key === 'ArrowDown' ? 1 : -1)));
        results.querySelectorAll('.lp-search-row').forEach((element, index) => element.classList.toggle('selected', index === selected));
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        const row = rows[selected];
        openStock(row?.symbol || input.value, row?.name || row?.organ_name);
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && overlay?.classList.contains('open')) closeSearch();
      if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
        event.preventDefault();
        openSearch();
      }
    });

    window.LPGlobalSearch = { open: openSearch, close: closeSearch, openStock, showHistory, closeMobileNav };
  } catch (error) {
    console.error('Shared search and navigation initialization failed:', error);
  }
})();
