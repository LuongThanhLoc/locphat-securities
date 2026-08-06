(() => {
  'use strict';

  const list = document.getElementById('calendarList');
  const grid = document.getElementById('calendarGrid');
  const coverage = document.getElementById('coverageText');
  const lineage = document.getElementById('dataLineage');
  const symbolInput = document.getElementById('calendarSymbol');
  const refreshButton = document.getElementById('refreshCalendar');
  const viewToggle = document.getElementById('viewToggle');
  const viewLabel = document.getElementById('viewLabel');
  const countdownBanner = document.getElementById('countdownBanner');

  let data = [];
  let nearby = [];
  let payloadMeta = {};
  let type = 'all';
  let range = 'current';
  let viewMode = 'list'; // 'list' or 'grid'
  let countdownInterval = null;

  const typeMeta = {
    financial_report: { label: 'BCTC', className: 'report' },
    cash_dividend: { label: 'CỔ TỨC TM', className: 'cash-div' },
    stock_dividend: { label: 'CỔ TỨC CP', className: 'stock-div' },
    shareholder_meeting_annual: { label: 'ĐHĐCĐ', className: 'meeting' },
    shareholder_meeting_extraordinary: { label: 'ĐHĐCĐ BT', className: 'meeting-egm' },
    capital_action: { label: 'PHÁT HÀNH', className: 'capital' },
    listing_change: { label: 'NIÊM YẾT', className: 'listing' },
    trading_halt: { label: 'TẠM NGỪNG', className: 'halt' },
  };

  const typeColors = {
    cash_dividend: '#f0c249',
    stock_dividend: '#e8a832',
    shareholder_meeting_annual: '#55d4ad',
    shareholder_meeting_extraordinary: '#42b899',
    capital_action: '#c18bed',
    listing_change: '#8b9fed',
    trading_halt: '#f76b6b',
    financial_report: '#63c5f5',
  };

  const pad = value => String(value).padStart(2, '0');
  const iso = value => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));
  const safeUrl = value => /^https?:\/\//i.test(String(value || '')) ? String(value) : '';
  const dateShort = value => value ? new Date(`${value}T00:00:00`).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit' }) : '';
  const dateLong = value => new Date(`${value}T00:00:00`).toLocaleDateString('vi-VN', {
    weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric'
  });
  const dateGrid = value => new Date(`${value}T00:00:00`).toLocaleDateString('vi-VN', { day: 'numeric', month: 'short' });
  const timeValue = value => {
    const match = String(value || '').match(/T(\d{2}:\d{2})/);
    return match ? match[1] : '';
  };

  const bounds = () => {
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    const day = (now.getDay() + 6) % 7;
    const monday = new Date(now);
    monday.setDate(now.getDate() - day);
    if (range === 'prev') {
      const start = new Date(monday);
      start.setDate(start.getDate() - 7);
      const end = new Date(start);
      end.setDate(end.getDate() + 6);
      return [start, end];
    }
    if (range === 'next') {
      const start = new Date(monday);
      start.setDate(start.getDate() + 7);
      const end = new Date(start);
      end.setDate(end.getDate() + 6);
      return [start, end];
    }
    if (range === 'today') return [now, now];
    if (range === 'forward') {
      const end = new Date(now);
      end.setDate(end.getDate() + 30);
      return [now, end];
    }
    const end = new Date(monday);
    end.setDate(end.getDate() + 6);
    return [monday, end];
  };

  const filteredRows = rows => {
    const symbol = symbolInput.value.trim().toUpperCase();
    return rows.filter(event => (
      (type === 'all' || event.type === type || (type === 'dividend' && ['cash_dividend', 'stock_dividend'].includes(event.type))) &&
      (!symbol || event.symbol.includes(symbol))
    ));
  };

  const relatedDates = event => {
    const details = [];
    if (event.record_date && event.record_date !== event.event_date) details.push(`ĐKCC ${dateShort(event.record_date)}`);
    if (event.exright_date && event.exright_date !== event.event_date) details.push(`GDKHQ ${dateShort(event.exright_date)}`);
    if (event.payout_date) details.push(`Thanh toán ${dateShort(event.payout_date)}`);
    if (event.ratio_label) details.push(event.ratio_label);

    // Thêm thông tin enrichment
    if (event.meeting_info?.location) details.push(`Tại: ${event.meeting_info.location}`);
    if (event.capital_info?.issue_price) details.push(`Giá ${event.capital_info.issue_price.toLocaleString('vi-VN')} VND`);

    const publishedTime = event.type === 'financial_report' ? timeValue(event.published_at) : '';
    if (publishedTime) details.push(`Công bố ${publishedTime}`);
    return details;
  };

  const eventRows = rows => rows.map(event => {
    const meta = typeMeta[event.type] || { label: 'SỰ KIỆN', className: 'other' };
    const sourceUrl = safeUrl(event.source_url);
    const statusMap = {
      'upcoming': 'SẮP DIỄN RA',
      'today': 'HÔM NAY',
      'published': 'ĐÃ CÔNG BỐ',
      'occurred': 'ĐÃ DIỄN RA'
    };
    const status = statusMap[event.status] || event.status;
    const details = relatedDates(event);
    const impactBadge = event.impact === 'high'
      ? '<span class="impact-high">Tác động cao</span>'
      : event.impact === 'medium'
        ? '<span class="impact-medium">Theo dõi</span>'
        : '';

    return `
      <article class="event-row ${meta.className}">
        <div class="event-date-role">
          <strong>${esc(event.date_role || 'Ngày sự kiện')}</strong>
          <span>${event.event_date ? dateShort(event.event_date) : ''}</span>
        </div>
        <a class="event-symbol" href="/stock/${encodeURIComponent(event.symbol)}">${esc(event.symbol)}</a>
        <span class="event-type ${meta.className}">${meta.label}</span>
        <div class="event-content">
          <strong>${esc(event.title)}</strong>
          <div class="event-details">${details.map(detail => `<span>${esc(detail)}</span>`).join('')}</div>
          ${impactBadge}
        </div>
        <div class="event-source">
          <span class="event-status ${event.status || ''}">${status}</span>
          ${sourceUrl ? `<a href="${esc(sourceUrl)}" target="_blank" rel="noopener noreferrer">Mở nguồn</a>` : `<small>${esc(event.source || 'Nguồn xác minh')}</small>`}
          <a class="event-backtest-link" href="/backtest?symbol=${encodeURIComponent(event.symbol)}" style="font-size:11px;color:#35d4a4;font-weight:700;text-decoration:none;margin-top:4px;display:inline-flex;align-items:center;gap:4px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 3v18h18"></path>
              <path d="m19 9-5 5-4-4-3 3"></path>
            </svg>
            <span>RSI Backtest</span>
          </a>
        </div>
      </article>`;
  }).join('');

  const renderStats = rows => {
    document.getElementById('totalEvents').textContent = rows.length.toLocaleString('vi-VN');
    document.getElementById('totalSymbols').textContent = `${new Set(rows.map(event => event.symbol)).size} mã`;
    document.getElementById('reportEvents').textContent = rows.filter(event => event.type === 'financial_report').length.toLocaleString('vi-VN');
    document.getElementById('dividendEvents').textContent = rows.filter(event => ['cash_dividend', 'stock_dividend'].includes(event.type)).length.toLocaleString('vi-VN');
    document.getElementById('upcomingEvents').textContent = rows.filter(event => ['upcoming', 'today'].includes(event.status)).length.toLocaleString('vi-VN');
  };

  // Countdown to next event (live countdown ticking every second)
  const updateCountdown = () => {
    const now = new Date();
    const upcoming = data.filter(e => e.status === 'upcoming' || e.status === 'today')
      .sort((a, b) => a.event_date.localeCompare(b.event_date));

    if (!upcoming.length) {
      countdownBanner.style.display = 'none';
      if (countdownInterval) clearInterval(countdownInterval);
      return;
    }

    const nextEvent = upcoming[0];
    const eventDate = new Date(`${nextEvent.event_date}T09:30:00`);
    const diff = eventDate - now;

    if (diff <= 0) {
      countdownBanner.style.display = 'none';
      if (countdownInterval) clearInterval(countdownInterval);
      return;
    }

    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
    const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    const secs = Math.floor((diff % (1000 * 60)) / 1000);

    const daysEl = document.getElementById('countdownDays');
    const hoursEl = document.getElementById('countdownHours');
    const minsEl = document.getElementById('countdownMins');
    const secsEl = document.getElementById('countdownSecs');

    if (daysEl) daysEl.textContent = pad(days);
    if (hoursEl) hoursEl.textContent = pad(hours);
    if (minsEl) minsEl.textContent = pad(mins);
    if (secsEl) secsEl.textContent = pad(secs);

    const eventTypeLabel = typeMeta[nextEvent.type]?.label || 'SỰ KIỆN';
    document.getElementById('countdownEvent').innerHTML = `
      <span class="countdown-symbol">${esc(nextEvent.symbol)}</span>
      <span class="countdown-type ${nextEvent.type}">${eventTypeLabel}</span>
      <span class="countdown-date">${dateLong(nextEvent.event_date)}</span>
    `;

    countdownBanner.style.display = 'flex';
  };

  // Calendar Grid View
  const renderCalendarGrid = (rows) => {
    const [start, end] = bounds();
    const calendarDays = document.getElementById('calendarDays');
    const monthHeader = document.getElementById('calendarMonthHeader');

    // Header
    const startMonth = start.toLocaleDateString('vi-VN', { month: 'long', year: 'numeric' });
    monthHeader.textContent = startMonth;

    // Build events by date
    const eventsByDate = {};
    rows.forEach(event => {
      const date = event.event_date;
      if (!eventsByDate[date]) eventsByDate[date] = [];
      eventsByDate[date].push(event);
    });

    // Build calendar grid
    let html = '';
    const firstDay = new Date(start);
    const startDayOfWeek = (firstDay.getDay() + 6) % 7; // Monday = 0
    const totalDays = Math.ceil((end - start) / (1000 * 60 * 60 * 24)) + 1;

    // Padding before
    for (let i = 0; i < startDayOfWeek; i++) {
      html += '<div class="calendar-day empty"></div>';
    }

    // Days
    for (let d = 0; d < totalDays; d++) {
      const currentDate = new Date(start);
      currentDate.setDate(start.getDate() + d);
      const dateStr = iso(currentDate);
      const dayEvents = eventsByDate[dateStr] || [];
      const isToday = dateStr === iso(new Date());
      const isWeekend = currentDate.getDay() === 0 || currentDate.getDay() === 6;

      html += `
        <div class="calendar-day ${isToday ? 'today' : ''} ${isWeekend ? 'weekend' : ''}" data-date="${dateStr}">
          <div class="day-number">${currentDate.getDate()}</div>
          <div class="day-events">
            ${dayEvents.slice(0, 3).map(e => `
              <div class="day-event-dot ${typeMeta[e.type]?.className || ''}"
                   title="${esc(e.symbol)}: ${esc(e.title)}"
                   style="border-color: ${typeColors[e.type] || '#666'}">
                <span class="dot-symbol">${esc(e.symbol)}</span>
              </div>
            `).join('')}
            ${dayEvents.length > 3 ? `<div class="more-events">+${dayEvents.length - 3}</div>` : ''}
          </div>
        </div>`;
    }

    calendarDays.innerHTML = html;

    // Click handler for day
    calendarDays.querySelectorAll('.calendar-day:not(.empty)').forEach(dayEl => {
      dayEl.addEventListener('click', () => {
        const dateStr = dayEl.dataset.date;
        const dayEvents = eventsByDate[dateStr] || [];
        if (dayEvents.length) {
          symbolInput.value = '';
          type = 'all';
          document.querySelectorAll('.segmented button').forEach(b => b.classList.toggle('active', b.dataset.type === 'all'));
          // Filter to show only selected date
          viewMode = 'list';
          list.style.display = 'block';
          grid.style.display = 'none';
          viewLabel.textContent = 'Lịch';
          data = dayEvents;
          render();
        }
      });
    });
  };

  const render = () => {
    const filtered = filteredRows(data);
    renderStats(filtered);

    if (viewMode === 'grid') {
      renderCalendarGrid(filtered);
      return;
    }

    const groups = filtered.reduce((result, event) => {
      (result[event.event_date] ||= []).push(event);
      return result;
    }, {});

    if (!filtered.length) {
      const fallback = filteredRows(nearby).slice(0, 8);
      list.innerHTML = `
        <div class="calendar-empty">
          <strong>Chưa có sự kiện đã xác minh trong bộ lọc này.</strong>
          <span>Thử đổi khoảng thời gian hoặc bỏ lọc mã. Lịch không tự điền ngày dự kiến khi nguồn chưa xác nhận.</span>
        </div>
        ${fallback.length ? `<section class="calendar-day nearby"><header><div><span>THAM KHẢO</span><h2>Sự kiện xác minh gần nhất</h2></div><strong>${fallback.length} sự kiện</strong></header>${eventRows(fallback)}</section>` : ''}`;
      return;
    }

    list.innerHTML = Object.entries(groups).map(([eventDate, rows]) => `
      <section class="calendar-day-group">
        <header>
          <div><span>${esc(rows[0]?.date_role || 'NGÀY SỰ KIỆN')}</span><h2>${esc(dateLong(eventDate))}</h2></div>
          <strong>${rows.length} sự kiện</strong>
        </header>
        ${eventRows(rows)}
      </section>
    `).join('');
  };

  const load = async refresh => {
    const [start, end] = bounds();
    refreshButton.disabled = true;
    refreshButton.classList.add('loading');
    list.innerHTML = '<div class="calendar-loading"><span></span><strong>Đang đối chiếu lịch doanh nghiệp...</strong></div>';
    try {
      const url = `/api/corporate-calendar?start=${iso(start)}&end=${iso(end)}${refresh ? '&refresh=true' : ''}`;
      const response = await fetch(url, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Lỗi HTTP ${response.status}`);
      payloadMeta = payload;
      data = payload.events || [];
      nearby = payload.nearby_events || [];
      const resultCount = payload.coverage?.returned_events ?? data.length;
      const symbolCount = payload.coverage?.returned_symbols ?? new Set(data.map(event => event.symbol)).size;
      coverage.textContent = `${resultCount} sự kiện · ${symbolCount} mã · phủ nguồn ${payload.coverage?.source_coverage_pct ?? '--'}%`;
      const fetchedAt = payload.fetched_at ? new Date(payload.fetched_at).toLocaleString('vi-VN') : '--';
      lineage.innerHTML = `<span>${esc(payload.source || 'Nguồn dữ liệu xác minh')}</span><span>${payload.cache === 'fallback' ? 'Snapshot dự phòng' : `Cập nhật ${esc(fetchedAt)}`}</span>`;

      render();

      // Start countdown
      if (countdownInterval) clearInterval(countdownInterval);
      updateCountdown();
      countdownInterval = setInterval(updateCountdown, 1000); // Update every 1 second live

    } catch (error) {
      data = [];
      renderStats([]);
      list.innerHTML = `<div class="calendar-empty error"><strong>Không thể tải lịch doanh nghiệp</strong><span>${esc(error.message)}</span></div>`;
    } finally {
      refreshButton.disabled = false;
      refreshButton.classList.remove('loading');
    }
  };

  // View mode segmented toggle
  const viewSegmented = document.getElementById('viewSegmented');
  viewSegmented?.addEventListener('click', event => {
    const button = event.target.closest('button[data-view]');
    if (!button) return;
    viewMode = button.dataset.view;
    document.querySelectorAll('#viewSegmented button').forEach(b => b.classList.toggle('active', b === button));
    list.style.display = viewMode === 'list' ? 'block' : 'none';
    grid.style.display = viewMode === 'grid' ? 'block' : 'none';
    render();
  });

  document.getElementById('dateTabs').addEventListener('click', event => {
    const button = event.target.closest('button[data-range]');
    if (!button) return;
    range = button.dataset.range;
    document.querySelectorAll('#dateTabs button').forEach(item => item.classList.toggle('active', item === button));
    load(false);
  });

  document.querySelector('.segmented').addEventListener('click', event => {
    const button = event.target.closest('button[data-type]');
    if (!button) return;
    type = button.dataset.type;
    document.querySelectorAll('.segmented button').forEach(item => item.classList.toggle('active', item === button));
    render();
  });

  symbolInput.addEventListener('input', render);
  refreshButton.addEventListener('click', () => load(true));

  // Scroll to Top Floating Button Handler
  const scrollToTopBtn = document.getElementById('scrollToTopBtn');
  scrollToTopBtn?.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  window.addEventListener('scroll', () => {
    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    if (scrollTop > 180) {
      scrollToTopBtn?.classList.add('visible');
    } else {
      scrollToTopBtn?.classList.remove('visible');
    }
  }, { passive: true });

  // Initialize
  const initialSymbol = new URLSearchParams(location.search).get('symbol');
  if (initialSymbol) symbolInput.value = initialSymbol.toUpperCase().slice(0, 6);
  load(false);
})();
