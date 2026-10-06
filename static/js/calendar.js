/* DhanSutra - Financial Calendar & Bulk Ledger Module */
(function () {
  const $ = id => document.getElementById(id);
  const recs = () => (typeof recurringList !== 'undefined' ? recurringList : []);
  const compact = v => {
    const a = Math.abs(v);
    return a >= 1e5 ? (a / 1e5).toFixed(1) + 'L' : a >= 1e3 ? (a / 1e3).toFixed(1) + 'K' : a.toFixed(0);
  };

  document.head.insertAdjacentHTML('beforeend', `<style>
    .bulk-bar { display:none; position:fixed; left:50%; transform:translateX(-50%); bottom:20px; z-index:1050; flex-wrap:wrap; gap:0.5rem; align-items:center; justify-content:center;
                background:var(--surface); border:1px solid var(--border); box-shadow:var(--shadow-modal); padding:0.6rem 0.85rem; border-radius:14px; max-width:calc(100vw - 24px); }
    .bulk-bar .btn { padding:0.35rem 0.8rem; font-size:12px; }
    .bulk-cb { width:16px; height:16px; margin-right:8px; flex-shrink:0; cursor:pointer; accent-color:var(--palace-teal); }
    .cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; }
    .cal-dow { text-align:center; font-size:11px; font-weight:700; color:var(--text-muted); padding:4px 0; }
    .cal-cell { min-height:62px; padding:4px; text-align:left; background:var(--surface-subtle); border:1px solid var(--border-subtle); border-radius:8px; cursor:pointer; color:var(--text-main); font-family:inherit; display:flex; flex-direction:column; gap:1px; overflow:hidden; }
    .cal-cell.blank { visibility:hidden; cursor:default; }
    .cal-cell.today { border-color:var(--saffron); }
    .cal-cell.sel { border-color:var(--palace-teal); box-shadow:0 0 0 2px var(--palace-teal) inset; }
    .cal-cell .n { font-size:12px; font-weight:700; }
    .cal-cell .i, .cal-cell .e, .cal-cell .s { font-size:10px; line-height:1.2; white-space:nowrap; }
    .cal-cell .i { color:var(--emerald); } .cal-cell .e { color:var(--terracotta); } .cal-cell .s { color:var(--palace-teal); }
    @media (max-width:920px) { .bulk-bar { bottom:74px; } .cal-cell { min-height:52px; } }
  </style>`);

  const sel = new Set();
  const rowsContainer = $('ledgerRowsContainer');
  if (rowsContainer) {
    rowsContainer.insertAdjacentHTML(
      'beforebegin',
      `<label class="caption-text" style="display:flex; align-items:center; gap:6px; margin-bottom:0.6rem; cursor:pointer;"><input type="checkbox" id="bulkSelectAll" class="bulk-cb" style="margin:0;"> Select all in view</label>`
    );
  }

  document.body.insertAdjacentHTML('beforeend', `
    <div class="bulk-bar" id="bulkBar" role="toolbar" aria-label="Bulk actions">
      <strong id="bulkCount" class="body-text"></strong>
      <select id="bulkCat" class="form-control" aria-label="New category" style="width:auto; max-width:180px; padding:0.35rem 2rem 0.35rem 0.6rem;"></select>
      <button type="button" class="btn btn-primary btn-pill" id="bulkApply">Move</button>
      <button type="button" class="btn btn-secondary btn-pill" id="bulkExport">CSV</button>
      <button type="button" class="btn btn-destructive btn-pill" id="bulkDelete">Delete</button>
      <button type="button" class="btn btn-ghost btn-pill" id="bulkClear">Clear</button>
    </div>`);

  const visibleIds = () => Array.from(document.querySelectorAll('#ledgerRowsContainer .bulk-cb')).map(cb => Number(cb.dataset.id));

  function sync() {
    const ids = new Set(transactions.map(t => t.id));
    sel.forEach(id => { if (!ids.has(id)) sel.delete(id); });
    const vis = visibleIds(), chosen = vis.filter(id => sel.has(id)).length;
    const all = $('bulkSelectAll');
    if (all) {
      all.checked = vis.length > 0 && chosen === vis.length;
      all.indeterminate = chosen > 0 && chosen < vis.length;
    }
    const bar = $('bulkBar');
    if (bar) {
      bar.style.display = sel.size ? 'flex' : 'none';
      if (sel.size) {
        $('bulkCount').textContent = `${sel.size} selected`;
        const opt = type => categories.filter(c => (c.type || '').toUpperCase() === type)
          .map(c => `<option value="${c.id}">${sanitize(c.icon || '')} ${sanitize(c.name)}</option>`).join('');
        const keep = $('bulkCat').value;
        $('bulkCat').innerHTML = `<optgroup label="Expense">${opt('EXPENSE')}</optgroup><optgroup label="Income">${opt('INCOME')}</optgroup>`;
        if (keep) $('bulkCat').value = keep;
      }
    }
  }

  function decorateLedger() {
    document.querySelectorAll('#ledgerRowsContainer .ledger-row').forEach(row => {
      if (row.querySelector('.bulk-cb')) return;
      const m = (row.getAttribute('onclick') || '').match(/inspectTransaction\((\d+)\)/);
      if (!m) return;
      const id = Number(m[1]);
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'bulk-cb';
      cb.dataset.id = id;
      cb.checked = sel.has(id);
      cb.setAttribute('aria-label', 'Select transaction');
      cb.onclick = e => { e.stopPropagation(); cb.checked ? sel.add(id) : sel.delete(id); sync(); };
      const rowLeft = row.querySelector('.row-left');
      if (rowLeft) rowLeft.insertBefore(cb, rowLeft.firstChild);
    });
    sync();
  }

  if ($('bulkSelectAll')) {$('bulkSelectAll').onchange = e => {
      visibleIds().forEach(id => e.target.checked ? sel.add(id) : sel.delete(id));
      document.querySelectorAll('#ledgerRowsContainer .bulk-cb').forEach(cb => cb.checked = e.target.checked);
      sync();
    };
  }
  if ($('bulkClear')) {$('bulkClear').onclick = () => { sel.clear(); document.querySelectorAll('.bulk-cb').forEach(cb => cb.checked = false); sync(); };
  }

  const picked = () => Array.from(sel).map(id => transactions.find(t => t.id === id)).filter(Boolean);

  if ($('bulkApply')) {$('bulkApply').onclick = async () => {
      const cat = categories.find(c => c.id === parseInt($('bulkCat').value));
      if (!cat) { showToast('Choose a category first', 'error'); return; }
      const list = picked();
      const ok = list.filter(t => (t.type || '').toUpperCase() === (cat.type || '').toUpperCase());
      const skipped = list.length - ok.length;
      if (!ok.length) { showToast(`Nothing to move: "${cat.name}" is an ${String(cat.type).toLowerCase()} category`, 'error'); return; }
      const btn = $('bulkApply');
      btn.disabled = true;
      let done = 0;
      try {
        for (const t of ok) {
          const payload = { type: String(t.type).toUpperCase(), amount: Number(t.amount), date: t.date, description: t.description, account_id: t.account_id, category_id: cat.id };
          if (t.source_id) payload.source_id = t.source_id;
          const res = await API.updateTransaction(t.id, payload);
          if (!(await okOrToast(res, 'Could not update a transaction'))) break;
          done++;
        }
      } finally { btn.disabled = false; }
      sel.clear();
      await refreshData();
      showToast(`Moved ${done} of ${ok.length} to "${cat.name}"` + (skipped ? ` — ${skipped} skipped` : ''), done === ok.length ? 'success' : 'error');
    };
  }

  if ($('bulkDelete')) {$('bulkDelete').onclick = () => {
      const n = sel.size;
      confirmAction(`Delete ${n} transaction${n > 1 ? 's' : ''}`, 'This permanently deletes every selected transaction and cannot be undone.', async () => {
        let done = 0;
        for (const t of picked()) {
          const res = await API.deleteTransaction(t.id);
          if (res && res.ok) done++;
        }
        const total = n;
        sel.clear();
        await refreshData();
        showToast(`Deleted ${done} of ${total} transactions`, done === total ? 'success' : 'error');
      });
    };
  }

  if ($('bulkExport')) {$('bulkExport').onclick = () => {
      const q = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
      const lines = ['Date,Type,Description,Category,Account,Amount'].concat(picked().map(t => {
        const cat = categories.find(c => c.id === t.category_id);
        const acc = t.account_id ? accounts.find(a => a.id === t.account_id) : null;
        const route = (t.type || '').toUpperCase() === 'TRANSFER'
          ? `${(accounts.find(a => a.id === t.from_account_id) || {}).name || ''} -> ${(accounts.find(a => a.id === t.to_account_id) || {}).name || ''}`
          : (acc ? acc.name : '');
        return [t.date, t.type, t.description, cat ? cat.name : '', route, t.amount].map(q).join(',');
      }));
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv' }));
      a.download = 'dhansutra-selected.csv';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    };
  }

  // Calendar Navigation & DOM
  const dNav = document.querySelector('.desktop-nav');
  if (dNav) dNav.insertAdjacentHTML('beforeend', `<button class="nav-link" onclick="switchTab('calendar')">Calendar</button>`);
  const mobNav = document.querySelector('.mobile-bottom-nav');
  if (mobNav) {
    const mobItem = document.createElement('button');
    mobItem.className = 'mob-item';
    mobItem.innerHTML = '<span>📅</span><span>Calendar</span>';
    mobItem.onclick = () => switchTab('calendar', mobItem);
    mobNav.insertBefore(mobItem, mobNav.lastElementChild);
  }

  const mainContainer = document.querySelector('main.container');
  if (mainContainer) {
    mainContainer.insertAdjacentHTML('beforeend', `
      <section id="tab-calendar" style="display:none;">
        <div class="card">
          <div class="card-header">
            <h2 class="section-title" id="calTitle">Calendar</h2>
            <div style="display:flex; gap:4px;">
              <button type="button" class="btn btn-secondary btn-pill" id="calPrev" aria-label="Previous month" style="padding:0.25rem 0.75rem;">‹</button>
              <button type="button" class="btn btn-secondary btn-pill" id="calToday" style="padding:0.25rem 0.75rem;">Today</button>
              <button type="button" class="btn btn-secondary btn-pill" id="calNext" aria-label="Next month" style="padding:0.25rem 0.75rem;">›</button>
            </div>
          </div>
          <div class="caption-text" id="calSummary" style="margin-bottom:0.75rem;"></div>
          <div class="cal-grid" id="calGrid"></div>
          <div class="caption-text" style="margin-top:0.6rem;">⏱ = scheduled recurring item | green = money in | red = money out</div>
        </div>
        <div class="card" id="calDayCard"><div class="card-header"><h2 class="section-title" id="calDayTitle">Pick a day</h2></div><div id="calDayBody"></div></div>
      </section>`);
  }

  let calSel = null;
  const monthKey = () => formatMonthStr(selectedMonth);

  function scheduledByDay() {
    const key = monthKey(), out = {};
    recs().forEach(r => {
      let d = r.next_date, guard = 0;
      while (d && d.slice(0, 7) <= key && guard++ < 60) {
        if (d.startsWith(key)) (out[d] = out[d] || []).push(r);
        d = advanceDate(d, r.frequency);
      }
    });
    return out;
  }

  function dayData() {
    const key = monthKey(), out = {};
    transactions.forEach(t => {
      if (!String(t.date).startsWith(key)) return;
      const o = out[t.date] = out[t.date] || { inc: 0, exp: 0, list: [] };
      const ty = (t.type || '').toUpperCase(), a = Number(t.amount) || 0;
      if (ty === 'INCOME') o.inc += a; else if (ty === 'EXPENSE') o.exp += a;
      o.list.push(t);
    });
    return out;
  }

  function renderCalendar() {
    const y = selectedMonth.getFullYear(), m = selectedMonth.getMonth(), key = monthKey();
    if ($('calTitle'))$('calTitle').textContent = selectedMonth.toLocaleString('en-IN', { month: 'long', year: 'numeric' });
    const data = dayData(), sched = scheduledByDay(), today = todayStr();
    const lead = (new Date(y, m, 1).getDay() + 6) % 7;
    const days = new Date(y, m + 1, 0).getDate();
    let inc = 0, exp = 0;
    Object.values(data).forEach(d => { inc += d.inc; exp += d.exp; });
    const upcoming = Object.entries(sched).filter(([d]) => d >= today).reduce((s, [, rs]) => s + rs.reduce((a, r) => a + ((r.type || '').toUpperCase() === 'EXPENSE' ? -1 : 1) * (Number(r.amount) || 0), 0), 0);
    if ($('calSummary')) {$('calSummary').textContent = `In ${formatINR(inc)} | Out ${formatINR(exp)}` + (Object.keys(sched).length ? ` | Scheduled still to come this month: ${upcoming >= 0 ? '+' : '−'}${formatINR(Math.abs(upcoming))}` : '');
    }
    if (!calSel || !calSel.startsWith(key)) calSel = today.startsWith(key) ? today : null;
    let html = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(d => `<div class="cal-dow">${d}</div>`).join('');
    for (let i = 0; i < lead; i++) html += '<div class="cal-cell blank" aria-hidden="true"></div>';
    for (let d = 1; d <= days; d++) {
      const ds = `${key}-${pad(d)}`, info = data[ds], sc = sched[ds];
      html += `<button type="button" class="cal-cell${ds === today ? ' today' : ''}${ds === calSel ? ' sel' : ''}" data-d="${ds}" aria-label="${ds}">
        <span class="n">${d}</span>
        ${info && info.inc ? `<span class="i">+${compact(info.inc)}</span>` : ''}
        ${info && info.exp ? `<span class="e">−${compact(info.exp)}</span>` : ''}
        ${sc ? `<span class="s">⏱${sc.length > 1 ? sc.length : ''}</span>` : ''}
      </button>`;
    }
    if ($('calGrid'))$('calGrid').innerHTML = html;
    renderDay(data, sched);
  }

  function renderDay(data, sched) {
    const body = $('calDayBody');
    if (!body) return;
    if (!calSel) {
      $('calDayTitle').textContent = 'Pick a day';
      body.innerHTML = '<div class="empty-state"><span class="caption-text">Tap a date to see its transactions and scheduled items.</span></div>';
      return;
    }
    $('calDayTitle').textContent = new Date(calSel + 'T00:00:00').toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' });
    const txs = (data[calSel] || { list: [] }).list, sc = sched[calSel] || [];
    let html = '';
    sc.forEach(r => {
      const isExp = (r.type || '').toUpperCase() === 'EXPENSE';
      html += `<div class="ins-row" style="display:flex; justify-content:space-between; gap:0.5rem; padding:0.45rem 0; border-bottom:1px solid var(--border-subtle);">
        <span>⏱ ${sanitize(r.description)} <span class="caption-text">— scheduled (${sanitize(r.frequency)})</span></span>
        <span style="display:flex; align-items:center; gap:0.4rem;"><strong class="${isExp ? 'amount-neg' : 'amount-pos'}">${formatINR(r.amount)}</strong>
        ${calSel === r.next_date ? `<button class="btn btn-secondary btn-pill" style="padding:0.2rem 0.55rem; font-size:11px;" onclick="postRecurring(${r.id})">Add now</button>` : ''}</span></div>`;
    });
    txs.forEach(t => {
      const ty = (t.type || '').toUpperCase(), cat = categories.find(c => c.id === t.category_id);
      const sign = ty === 'INCOME' ? '+' : ty === 'EXPENSE' ? '−' : '⇄';
      html += `<div class="ledger-row" role="button" tabindex="0" onclick="inspectTransaction(${t.id})"><div class="row-left"><div class="row-icon">${sanitize((cat && cat.icon) || (ty === 'TRANSFER' ? '⇄' : '₹'))}</div>
        <div><div style="font-weight:600;">${sanitize(t.description)}</div><div class="caption-text">${sanitize(cat ? cat.name : ty === 'TRANSFER' ? 'Transfer' : 'Uncategorized')}</div></div></div>
        <div class="${ty === 'INCOME' ? 'amount-pos' : ty === 'EXPENSE' ? 'amount-neg' : 'amount-neutral'}">${sign} ${formatINR(t.amount)}</div></div>`;
    });
    body.innerHTML = html || '<div class="empty-state"><span class="caption-text">Nothing recorded or scheduled on this day.</span></div>';
  }

  async function gotoMonth(target) {
    const v = formatMonthStr(target), sel2 = $('monthSelector');
    if (!Array.from(sel2.options).some(o => o.value === v)) { showToast('That month is outside the available range', 'error'); return; }
    selectedMonth = target;
    sel2.value = v;
    await refreshData();
  }

  if ($('calPrev'))$('calPrev').onclick = () => gotoMonth(new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() - 1, 1));
  if ($('calNext'))$('calNext').onclick = () => gotoMonth(new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() + 1, 1));
  if ($('calToday'))$('calToday').onclick = async () => { await gotoMonth(startOfMonth(new Date())); calSel = todayStr(); renderCalendar(); };
  if ($('calGrid')) {$('calGrid').onclick = e => {
      const b = e.target.closest('.cal-cell[data-d]');
      if (!b) return;
      calSel = b.dataset.d;
      renderCalendar();
    };
  }

  registerHook('renderLedger', decorateLedger);
  registerHook('renderUI', renderCalendar);
  registerHook('switchTab', tabId => {
    const tab = $('tab-calendar');
    if (tab) tab.style.display = tabId === 'calendar' ? 'block' : 'none';
    const bar = $('bulkBar');
    if (bar) bar.style.visibility = tabId === 'ledger' ? 'visible' : 'hidden';
  });
})();