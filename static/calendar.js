(() => {
  'use strict';

  const WATCHLIST_KEY = 'lps_personal_watchlist_v1';
  const PREF_KEY = 'lps_calendar_filters_v2';
  const GROUP_PAGE_SIZE = 8;
  const $ = id => document.getElementById(id);
  const list = $('calendarList');
  const grid = $('calendarGrid');
  const coverage = $('coverageText');
  const lineage = $('dataLineage');
  const symbolInput = $('calendarSymbol');
  const refreshButton = $('refreshCalendar');
  const dialog = $('eventDialog');
  const dialogPanel = dialog.querySelector('.event-dialog-panel');

  const state = {
    data: [], nearby: [], meta: {}, type: 'all', range: 'forward', view: 'list',
    exchange: 'all', verification: 'all', watchlistOnly: false,
    customStart: '', customEnd: '', visibleGroups: GROUP_PAGE_SIZE,
    activeDialogEvents: [], dialogTrigger: null,
  };

  const typeMeta = {
    financial_report: { label: 'BCTC', className: 'report' },
    earnings_release: { label: 'KQKD', className: 'earnings' },
    cash_dividend: { label: 'CỔ TỨC TM', className: 'cash-div' },
    stock_dividend: { label: 'CỔ TỨC CP', className: 'stock-div' },
    shareholder_meeting_annual: { label: 'ĐHĐCĐ', className: 'meeting' },
    shareholder_meeting_extraordinary: { label: 'ĐHĐCĐ BT', className: 'meeting-egm' },
    capital_action: { label: 'PHÁT HÀNH', className: 'capital' },
    listing_change: { label: 'NIÊM YẾT', className: 'listing' },
    trading_halt: { label: 'TẠM NGỪNG', className: 'halt' },
  };
  const verificationMeta = {
    official: { label: 'Nguồn chính thức', className: 'verified' },
    cross_checked: { label: 'Đã đối chiếu', className: 'verified' },
    provider_only: { label: 'Một nguồn', className: 'provider' },
    conflict: { label: 'Có xung đột', className: 'conflict' },
  };
  const relatedDateLabels = {
    publication_date: 'Công bố', ex_right_date: 'GDKHQ', record_date: 'ĐKCC',
    payment_date: 'Thanh toán', meeting_date: 'Ngày họp', issue_date: 'Phát hành',
    listing_date: 'Niêm yết/GD đầu tiên', delisting_date: 'Hủy niêm yết',
    provider_display_date: 'Ngày theo nguồn',
  };

  const pad = value => String(value).padStart(2, '0');
  const iso = value => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));
  const safeUrl = value => /^https?:\/\//i.test(String(value || '')) ? String(value) : '';
  const dateShort = value => value ? new Date(`${value}T00:00:00`).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit' }) : 'N/A';
  const dateLong = value => value ? new Date(`${value}T00:00:00`).toLocaleDateString('vi-VN', { weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric' }) : 'N/A';
  const todayIso = () => iso(new Date());

  const loadWatchlist = () => {
    try {
      const parsed = JSON.parse(localStorage.getItem(WATCHLIST_KEY) || '[]');
      return new Set(Array.isArray(parsed) ? parsed.map(item => String(item?.symbol || '').toUpperCase()).filter(Boolean) : []);
    } catch { return new Set(); }
  };

  const restorePreferences = () => {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(PREF_KEY) || '{}'); } catch {}
    const params = new URLSearchParams(location.search);
    state.type = params.get('type') || saved.type || state.type;
    state.range = params.get('range') || saved.range || state.range;
    state.exchange = params.get('exchange') || saved.exchange || state.exchange;
    state.verification = params.get('verification') || saved.verification || state.verification;
    state.view = params.get('view') || saved.view || state.view;
    state.watchlistOnly = params.get('watchlist') === '1' || (!params.has('watchlist') && Boolean(saved.watchlistOnly));
    state.customStart = params.get('start') || saved.customStart || '';
    state.customEnd = params.get('end') || saved.customEnd || '';
    symbolInput.value = (params.get('symbol') || saved.symbol || '').toUpperCase().slice(0, 6);
  };

  const savePreferences = () => {
    const payload = {
      type: state.type, range: state.range, exchange: state.exchange,
      verification: state.verification, view: state.view,
      watchlistOnly: state.watchlistOnly, customStart: state.customStart,
      customEnd: state.customEnd, symbol: symbolInput.value.trim().toUpperCase(),
    };
    try { localStorage.setItem(PREF_KEY, JSON.stringify(payload)); } catch {}
    const params = new URLSearchParams();
    Object.entries(payload).forEach(([key, value]) => {
      if (value === '' || value === false || value === 'all' || (key === 'range' && value === 'forward') || (key === 'view' && value === 'list')) return;
      params.set(key, value === true ? '1' : String(value));
    });
    history.replaceState(null, '', `${location.pathname}${params.size ? `?${params}` : ''}`);
  };

  const bounds = () => {
    const now = new Date(); now.setHours(0, 0, 0, 0);
    const weekday = (now.getDay() + 6) % 7;
    const monday = new Date(now); monday.setDate(now.getDate() - weekday);
    if (state.range === 'today') return [now, now];
    if (state.range === 'current') { const end = new Date(monday); end.setDate(end.getDate() + 6); return [monday, end]; }
    if (state.range === 'next') { const start = new Date(monday); start.setDate(start.getDate() + 7); const end = new Date(start); end.setDate(end.getDate() + 6); return [start, end]; }
    if (state.range === 'custom' && state.customStart && state.customEnd) return [new Date(`${state.customStart}T00:00:00`), new Date(`${state.customEnd}T00:00:00`)];
    const end = new Date(now); end.setDate(end.getDate() + 30); return [now, end];
  };

  const typeMatches = event => {
    if (state.type === 'all') return true;
    if (state.type === 'financial') return ['financial_report', 'earnings_release'].includes(event.type);
    if (state.type === 'dividend') return ['cash_dividend', 'stock_dividend'].includes(event.type);
    if (state.type === 'meeting') return String(event.type || '').startsWith('shareholder_meeting');
    return event.type === state.type;
  };

  const filteredRows = rows => {
    const symbol = symbolInput.value.trim().toUpperCase();
    const watchlist = loadWatchlist();
    return rows.filter(event => {
      const verify = event.verification?.status || 'provider_only';
      const verificationMatch = state.verification === 'all'
        || (state.verification === 'verified' && ['official', 'cross_checked'].includes(verify))
        || verify === state.verification;
      return typeMatches(event)
        && (!symbol || event.symbol === symbol)
        && (state.exchange === 'all' || event.exchange === state.exchange)
        && verificationMatch
        && (!state.watchlistOnly || watchlist.has(event.symbol));
    });
  };

  const renderStats = rows => {
    const watchlist = loadWatchlist();
    $('upcomingEvents').textContent = rows.filter(event => ['upcoming', 'today'].includes(event.status)).length.toLocaleString('vi-VN');
    $('watchlistEvents').textContent = rows.filter(event => watchlist.has(event.symbol)).length.toLocaleString('vi-VN');
    $('verifiedEvents').textContent = rows.filter(event => ['official', 'cross_checked'].includes(event.verification?.status)).length.toLocaleString('vi-VN');
    $('reviewEvents').textContent = rows.filter(event => !['official', 'cross_checked'].includes(event.verification?.status)).length.toLocaleString('vi-VN');
    const [start, end] = bounds();
    $('upcomingWindow').textContent = `${dateShort(iso(start))} – ${dateShort(iso(end))}`;
  };

  const detailsFor = event => {
    const values = [];
    const semanticDates = new Set(Object.entries(event.related_dates || {})
      .filter(([key, value]) => key !== 'provider_display_date' && value)
      .map(([, value]) => value));
    Object.entries(event.related_dates || {}).forEach(([key, value]) => {
      if (key === 'provider_display_date' && semanticDates.has(value)) return;
      if (value && value !== event.event_date) values.push(`${relatedDateLabels[key] || key}: ${dateShort(value)}`);
    });
    if (event.ratio_label) values.push(event.ratio_label);
    if (event.details?.issue_price != null) values.push(`Giá phát hành: ${Number(event.details.issue_price).toLocaleString('vi-VN')} VND`);
    if (event.details?.meeting_location) values.push(`Địa điểm: ${event.details.meeting_location}`);
    if (event.details?.report_period) values.push(`Kỳ: ${event.details.report_period}`);
    if (event.event_time) values.push(`Công bố lúc ${event.event_time}`);
    return values;
  };

  const eventCard = event => {
    const meta = typeMeta[event.type] || { label: 'SỰ KIỆN', className: 'other' };
    const verify = verificationMeta[event.verification?.status] || verificationMeta.provider_only;
    return `<article class="event-row ${meta.className}">
      <div class="event-date-role"><strong>${esc(event.date_role_label || event.date_role || 'Ngày sự kiện')}</strong><span>${dateShort(event.event_date)}</span></div>
      <a class="event-symbol" href="/stock/${encodeURIComponent(event.symbol)}">${esc(event.symbol)}</a>
      <span class="event-type ${meta.className}">${meta.label}</span>
      <div class="event-content"><strong>${esc(event.title)}</strong><div class="event-details">${detailsFor(event).slice(0, 4).map(item => `<span>${esc(item)}</span>`).join('')}</div></div>
      <div class="event-source"><span class="verification-badge ${verify.className}">${verify.label}</span><button class="event-detail-btn" type="button" data-event-id="${esc(event.id)}">Chi tiết & nguồn</button></div>
    </article>`;
  };

  const renderNextEvent = rows => {
    const today = new Date(`${todayIso()}T00:00:00`);
    const next = rows.filter(event => event.event_date >= todayIso()).sort((a, b) => a.event_date.localeCompare(b.event_date))[0];
    if (!next) { $('nextEventBanner').hidden = true; return; }
    const target = new Date(`${next.event_date}T00:00:00`);
    const days = Math.round((target - today) / 86400000);
    $('nextEventDistance').textContent = days === 0 ? 'Hôm nay' : `Còn ${days} ngày`;
    $('nextEventSummary').innerHTML = `<strong>${esc(next.symbol)}</strong><span>${esc(typeMeta[next.type]?.label || 'Sự kiện')} · ${esc(next.date_role_label || next.date_role)} · ${dateLong(next.event_date)}</span>`;
    $('nextEventBanner').hidden = false;
  };

  const renderList = rows => {
    const groups = rows.reduce((result, event) => { (result[event.event_date] ||= []).push(event); return result; }, {});
    const entries = Object.entries(groups);
    if (!entries.length) {
      const unavailable = state.meta.data_quality?.partial && !state.meta.fetched_at;
      list.innerHTML = `<div class="calendar-empty"><strong>${unavailable ? 'Nguồn dữ liệu đang chưa sẵn sàng.' : 'Chưa có sự kiện phù hợp bộ lọc.'}</strong><span>${unavailable ? 'Hệ thống không trả dữ liệu giả; vui lòng thử lại sau.' : 'Hãy đổi khoảng ngày hoặc xóa bớt bộ lọc.'}</span></div>`;
      $('loadMore').hidden = true;
      return;
    }
    const visible = entries.slice(0, state.visibleGroups);
    list.innerHTML = visible.map(([eventDate, events]) => `<section class="calendar-day-group"><header><div><span>NGÀY SỰ KIỆN</span><h2>${esc(dateLong(eventDate))}</h2></div><strong>${events.length} mốc</strong></header>${events.map(eventCard).join('')}</section>`).join('');
    $('loadMore').hidden = visible.length >= entries.length;
  };

  const renderGrid = rows => {
    const [start, end] = bounds();
    const byDate = rows.reduce((result, event) => { (result[event.event_date] ||= []).push(event); return result; }, {});
    const startLabel = start.toLocaleDateString('vi-VN', { month: 'long', year: 'numeric' });
    const endLabel = end.toLocaleDateString('vi-VN', { month: 'long', year: 'numeric' });
    $('calendarMonthHeader').textContent = startLabel === endLabel ? startLabel : `${startLabel} – ${endLabel}`;
    const offset = (start.getDay() + 6) % 7;
    let html = '<span class="calendar-day empty" role="presentation"></span>'.repeat(offset);
    const total = Math.round((end - start) / 86400000) + 1;
    for (let day = 0; day < total; day += 1) {
      const current = new Date(start); current.setDate(start.getDate() + day);
      const key = iso(current); const dayEvents = byDate[key] || [];
      const dots = dayEvents.slice(0, 4).map(event => `<i class="event-dot ${typeMeta[event.type]?.className || 'other'}"></i>`).join('');
      html += `<button class="calendar-day${key === todayIso() ? ' today' : ''}" type="button" role="gridcell" data-date="${key}" aria-label="${esc(dateLong(key))}, ${dayEvents.length} sự kiện"><span>${current.getDate()}</span><span class="day-dots">${dots}</span>${dayEvents.length > 4 ? `<small>+${dayEvents.length - 4}</small>` : ''}</button>`;
    }
    $('calendarDays').innerHTML = html;
  };

  const renderQuality = () => {
    const quality = state.meta.data_quality || {};
    const banner = $('dataQualityBanner');
    if (!quality.stale && !quality.partial) { banner.hidden = true; return; }
    banner.className = `data-quality-banner ${quality.stale ? 'stale' : 'partial'}`;
    banner.textContent = quality.stale
      ? 'Đang hiển thị dữ liệu hợp lệ gần nhất; nguồn đang được đồng bộ lại.'
      : 'Độ phủ nguồn chưa hoàn tất; số liệu bên dưới chỉ phản ánh phần đã quét.';
    banner.hidden = false;
  };

  const render = () => {
    savePreferences();
    const rows = filteredRows(state.data);
    renderStats(rows); renderNextEvent(rows); renderQuality();
    if (state.view === 'grid') renderGrid(rows); else renderList(rows);
  };

  const verificationDetails = event => {
    const sources = event.verification?.sources || [];
    return sources.length ? sources.map(source => {
      const url = safeUrl(source.source_url);
      return `<li><strong>${esc(source.source_name || 'Nguồn dữ liệu')}</strong><span>${esc(source.source_tier === 'official' ? 'Nguồn chính thức' : 'Nguồn tổng hợp')}</span>${source.published_at ? `<small>Công bố: ${esc(source.published_at)}</small>` : ''}${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Mở chứng từ</a>` : '<small>Chưa có URL chứng từ gốc</small>'}</li>`;
    }).join('') : '<li>Chưa có bằng chứng nguồn chi tiết.</li>';
  };

  const openEventDialog = (events, trigger, dayLabel = '') => {
    state.activeDialogEvents = events;
    state.dialogTrigger = trigger || document.activeElement;
    const single = events.length === 1 ? events[0] : null;
    $('eventDialogEyebrow').textContent = single ? `${single.symbol} · ${single.exchange || 'Chưa rõ sàn'}` : dayLabel;
    $('eventDialogTitle').textContent = single ? single.title : `${events.length} sự kiện trong ngày`;
    $('exportSingleEvent').textContent = single ? 'Thêm vào lịch' : 'Xuất ngày này';
    if (single) {
      const verify = verificationMeta[single.verification?.status] || verificationMeta.provider_only;
      $('eventDialogContent').innerHTML = `<div class="dialog-summary"><span class="verification-badge ${verify.className}">${verify.label}</span><strong>${esc(single.date_role_label || single.date_role)} · ${dateLong(single.event_date)}${single.event_time ? ` · ${esc(single.event_time)}` : ''}</strong></div>
        <dl>${detailsFor(single).map(value => `<div><dt>Thông tin</dt><dd>${esc(value)}</dd></div>`).join('') || '<div><dt>Dữ liệu bổ sung</dt><dd>N/A</dd></div>'}</dl>
        <h3>Nguồn & bằng chứng</h3><ul class="evidence-list">${verificationDetails(single)}</ul>
        ${single.verification?.conflict_fields?.length ? `<div class="conflict-note">Xung đột: ${esc(single.verification.conflict_fields.join(', '))}</div>` : ''}`;
    } else {
      $('eventDialogContent').innerHTML = `<div class="dialog-event-list">${events.map(event => `<button type="button" data-dialog-event-id="${esc(event.id)}"><strong>${esc(event.symbol)}</strong><span>${esc(typeMeta[event.type]?.label || 'Sự kiện')} · ${esc(event.title)}</span></button>`).join('')}</div>`;
    }
    dialog.hidden = false;
    dialog.setAttribute('aria-hidden', 'false');
    document.body.classList.add('calendar-dialog-open');
    $('eventDialogClose').focus({ preventScroll: true });
  };

  const closeDialog = () => {
    if (dialog.hidden) return;
    dialog.hidden = true; dialog.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('calendar-dialog-open');
    const focusTarget = state.dialogTrigger;
    if (focusTarget?.focus) focusTarget.focus({ preventScroll: true });
    state.activeDialogEvents = [];
  };

  const icsEscape = value => String(value || '').replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/,/g, '\\,').replace(/;/g, '\\;');
  const icsDate = value => String(value || '').replaceAll('-', '');
  const nextDate = value => { const dateValue = new Date(`${value}T00:00:00`); dateValue.setDate(dateValue.getDate() + 1); return iso(dateValue); };
  const buildIcs = events => {
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
    const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Loc Phat Securities//Corporate Calendar v2//VI', 'CALSCALE:GREGORIAN'];
    events.forEach(event => {
      lines.push('BEGIN:VEVENT', `UID:${icsEscape(event.id)}@locphat-securities`, `DTSTAMP:${stamp}`);
      if (event.event_time) lines.push(`DTSTART;TZID=Asia/Ho_Chi_Minh:${icsDate(event.event_date)}T${event.event_time.replace(':', '')}00`);
      else lines.push(`DTSTART;VALUE=DATE:${icsDate(event.event_date)}`, `DTEND;VALUE=DATE:${icsDate(nextDate(event.event_date))}`);
      lines.push(`SUMMARY:${icsEscape(`${event.symbol} · ${event.date_role_label || event.date_role} · ${event.title}`)}`);
      lines.push(`DESCRIPTION:${icsEscape(`Loại: ${typeMeta[event.type]?.label || event.type}. Xác minh: ${verificationMeta[event.verification?.status]?.label || 'Một nguồn'}.`)}`);
      const sourceUrl = (event.verification?.sources || []).map(item => safeUrl(item.source_url)).find(Boolean);
      if (sourceUrl) lines.push(`URL:${sourceUrl}`);
      lines.push('END:VEVENT');
    });
    lines.push('END:VCALENDAR');
    return lines.join('\r\n');
  };

  const downloadIcs = events => {
    if (!events.length) return;
    const blob = new Blob([buildIcs(events)], { type: 'text/calendar;charset=utf-8' });
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
    anchor.href = url; anchor.download = `locphat-calendar-${todayIso()}.ics`;
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const updateControls = () => {
    document.querySelectorAll('#dateTabs button').forEach(button => button.classList.toggle('active', button.dataset.range === state.range));
    $('customRange').hidden = state.range !== 'custom';
    document.querySelectorAll('#typeFilters button').forEach(button => { const active = button.dataset.type === state.type; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
    document.querySelectorAll('#viewSegmented button').forEach(button => { const active = button.dataset.view === state.view; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
    $('exchangeFilter').value = state.exchange; $('verificationFilter').value = state.verification;
    $('watchlistFilter').setAttribute('aria-pressed', String(state.watchlistOnly)); $('watchlistFilter').classList.toggle('active', state.watchlistOnly);
    $('customStart').value = state.customStart; $('customEnd').value = state.customEnd;
    list.hidden = state.view !== 'list'; $('loadMore').hidden = state.view !== 'list'; grid.hidden = state.view !== 'grid';
  };

  const load = async refresh => {
    const [start, end] = bounds();
    refreshButton.disabled = true; refreshButton.classList.add('loading');
    if (!state.data.length) list.innerHTML = '<div class="calendar-loading"><span></span><strong>Đang đọc lịch doanh nghiệp...</strong></div>';
    try {
      const response = await fetch(`/api/corporate-calendar?start=${iso(start)}&end=${iso(end)}${refresh ? '&refresh=true' : ''}`, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : payload.detail?.message || `Lỗi HTTP ${response.status}`);
      state.meta = payload; state.data = payload.events || []; state.nearby = payload.nearby_events || []; state.visibleGroups = GROUP_PAGE_SIZE;
      const cov = payload.coverage || {};
      coverage.textContent = `${cov.returned_events ?? state.data.length} mốc · ${cov.returned_symbols ?? 0} mã · quét ${cov.universe_scanned ?? 0}/${cov.universe_total ?? '--'} mã · loại ${cov.rejected_items ?? 0} mục không hợp lệ`;
      const asOf = payload.fetched_at ? new Date(payload.fetched_at).toLocaleString('vi-VN') : 'chưa có';
      lineage.innerHTML = `<span>${esc(payload.source || 'Calendar v2')}</span><span>Cập nhật ${esc(asOf)} · Không dữ liệu tổng hợp: ${payload.no_synthetic_data ? 'Có' : 'Không rõ'}</span>`;
      render();
      if (refresh && payload.refresh?.state === 'running') setTimeout(() => load(false), 3000);
    } catch (error) {
      state.data = []; renderStats([]);
      list.innerHTML = `<div class="calendar-empty error"><strong>Không thể tải lịch doanh nghiệp</strong><span>${esc(error.message)}</span><button type="button" id="retryCalendar">Thử lại</button></div>`;
      $('retryCalendar')?.addEventListener('click', () => load(false));
    } finally { refreshButton.disabled = false; refreshButton.classList.remove('loading'); }
  };

  $('dateTabs').addEventListener('click', event => {
    const button = event.target.closest('button[data-range]'); if (!button) return;
    state.range = button.dataset.range; state.visibleGroups = GROUP_PAGE_SIZE; updateControls();
    if (state.range !== 'custom') load(false);
  });
  $('applyCustomRange').addEventListener('click', () => {
    const start = $('customStart').value; const end = $('customEnd').value;
    if (!start || !end || end < start) { $('customEnd').setCustomValidity('Ngày kết thúc phải từ ngày bắt đầu trở đi.'); $('customEnd').reportValidity(); return; }
    $('customEnd').setCustomValidity(''); state.customStart = start; state.customEnd = end; load(false);
  });
  $('typeFilters').addEventListener('click', event => { const button = event.target.closest('button[data-type]'); if (!button) return; state.type = button.dataset.type; state.visibleGroups = GROUP_PAGE_SIZE; updateControls(); render(); });
  $('viewSegmented').addEventListener('click', event => { const button = event.target.closest('button[data-view]'); if (!button) return; state.view = button.dataset.view; updateControls(); render(); });
  symbolInput.addEventListener('input', () => { symbolInput.value = symbolInput.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6); state.visibleGroups = GROUP_PAGE_SIZE; render(); });
  $('exchangeFilter').addEventListener('change', event => { state.exchange = event.target.value; render(); });
  $('verificationFilter').addEventListener('change', event => { state.verification = event.target.value; render(); });
  $('watchlistFilter').addEventListener('click', () => { state.watchlistOnly = !state.watchlistOnly; updateControls(); render(); });
  $('clearFilters').addEventListener('click', () => { state.type = 'all'; state.exchange = 'all'; state.verification = 'all'; state.watchlistOnly = false; symbolInput.value = ''; state.visibleGroups = GROUP_PAGE_SIZE; updateControls(); render(); });
  refreshButton.addEventListener('click', () => load(true));
  $('exportCalendar').addEventListener('click', () => downloadIcs(filteredRows(state.data)));
  $('exportSingleEvent').addEventListener('click', () => downloadIcs(state.activeDialogEvents));
  $('loadMore').addEventListener('click', () => { state.visibleGroups += GROUP_PAGE_SIZE; render(); });

  list.addEventListener('click', event => { const button = event.target.closest('[data-event-id]'); if (!button) return; const item = state.data.find(row => row.id === button.dataset.eventId); if (item) openEventDialog([item], button); });
  $('calendarDays').addEventListener('click', event => { const button = event.target.closest('button[data-date]'); if (!button) return; const rows = filteredRows(state.data).filter(item => item.event_date === button.dataset.date); if (rows.length) openEventDialog(rows, button, dateLong(button.dataset.date)); });
  $('eventDialogContent').addEventListener('click', event => { const button = event.target.closest('[data-dialog-event-id]'); if (!button) return; const item = state.activeDialogEvents.find(row => row.id === button.dataset.dialogEventId); if (item) openEventDialog([item], state.dialogTrigger); });
  dialog.addEventListener('click', event => { if (event.target.closest('[data-close-dialog]')) closeDialog(); });
  $('eventDialogClose').addEventListener('click', closeDialog);
  document.addEventListener('keydown', event => {
    if (dialog.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeDialog(); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...dialogPanel.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled])')].filter(item => !item.hidden);
    if (!focusable.length) return;
    const first = focusable[0]; const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });

  const scrollToTopBtn = $('scrollToTopBtn');
  scrollToTopBtn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
  window.addEventListener('scroll', () => scrollToTopBtn.classList.toggle('visible', window.scrollY > 240), { passive: true });

  restorePreferences(); updateControls(); load(false);
})();
