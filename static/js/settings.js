/* DhanSutra - System Settings & Recurring Manager Module */
(function () {
  const $ = id => document.getElementById(id);
  const MEMORY_KEY = 'dhanSutraCatMemory';
  const recs = () => (typeof recurringList !== 'undefined' ? recurringList : []);
  const learnedCount = () => { try { return Object.keys(JSON.parse(localStorage.getItem(MEMORY_KEY) || '{}')).length; } catch { return 0; } };

  document.head.insertAdjacentHTML('beforeend', `<style>
    .st-group { border:1px solid var(--border-subtle); border-radius:12px; margin-bottom:0.75rem; background:var(--surface); }
    .st-group > summary { cursor:pointer; padding:0.7rem 0.9rem; font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; color:var(--text-muted); list-style:none; display:flex; justify-content:space-between; align-items:center; }
    .st-group > summary::-webkit-details-marker { display:none; }
    .st-group > summary::after { content:'›'; transition:transform 0.15s; }
    .st-group[open] > summary::after { transform:rotate(90deg); }
    .st-body { display:flex; flex-direction:column; gap:0.45rem; padding:0 0.75rem 0.75rem; }
    .st-row { width:100%; justify-content:space-between !important; white-space:normal !important; text-align:left; text-decoration:none; padding:0.6rem 0.85rem !important; }
    .st-row .t { display:block; font-weight:600; } .st-row .d { display:block; font-weight:400; }
  </style>`);

  const row = (icon, title, desc, action, id = '') => {
    const inner = `<span style="display:flex; gap:10px; align-items:center;"><span aria-hidden="true">${icon}</span><span><span class="t">${title}</span><span class="caption-text d"${id ? ` id="${id}"` : ''}>${desc}</span></span></span><span aria-hidden="true">›</span>`;
    return action.startsWith('href:')
      ? `<a class="btn btn-secondary st-row" href="${action.slice(5)}">${inner}</a>`
      : `<button type="button" class="btn btn-secondary st-row" onclick="${action}">${inner}</button>`;
  };
  const group = (title, body, open = false) => `<details class="st-group"${open ? ' open' : ''}><summary>${title}</summary><div class="st-body">${body}</div></details>`;

  const box = document.querySelector('#modalSettings .modal-box');
  if (box) {
    const header = box.querySelector('.card-header');
    box.innerHTML = '';
    if (header) box.appendChild(header);
    box.insertAdjacentHTML('beforeend',
      group('Accounts & Money',
        row('🏦', 'Accounts & opening balances', 'Starting balance for each account', 'openAccountsModal()') +
        row('📈', 'Investment valuations', 'Update market values without creating cash flow', 'openValuationsModal()') +
        row('🏷️', 'Categories', 'Create, delete and reassign categories', 'openCategoryModal()'), true) +
      group('Automation',
        row('✉️', 'Gmail receipt sync', 'Fetch bank alerts, review and import', 'openGmailModal()') +
        row('🔁', 'Recurring transactions', 'Rent, SIPs, salary, subscriptions', 'openRecurringManager()', 'stRecurringDesc') +
        row('🧠', 'Categorisation memory', 'Merchants DhanSutra has learned', 'resetLearnedCategories()', 'stLearnedDesc'), true) +
      group('Data',
        row('📊', 'Export CSV statement', 'All transactions as a spreadsheet', 'href:/api/finance/imports/export/csv') +
        row('💾', 'Download JSON archive', 'Full copy of your data', 'href:/api/finance/imports/export/json')) +
      group('Preferences',
        row('🌓', 'Theme', 'Light or dark', 'toggleTheme(); refreshSettings()', 'stThemeDesc')) +
      group('Security',
        row('🚪', 'Log out', 'End this session on this device', 'href:/auth/logout')));
  }

  window.refreshSettings = function () {
    const n = recs().length, l = learnedCount();
    const rEl = $('stRecurringDesc'), lEl = $('stLearnedDesc'), tEl =$('stThemeDesc');
    if (rEl) rEl.textContent = n ? `${n} scheduled — add, review or remove` : 'Rent, SIPs, salary, subscriptions — none yet';
    if (lEl) lEl.textContent = l ? `${l} merchant choice${l > 1 ? 's' : ''} remembered — tap to forget them` : 'Nothing learned yet — fills in as you import receipts';
    if (tEl) tEl.textContent = document.body.classList.contains('dark') ? 'Dark mode is on — tap to switch to light' : 'Light mode is on — tap to switch to dark';
  };

  window.resetLearnedCategories = function () {
    const l = learnedCount();
    if (!l) { showToast('Nothing to reset yet'); return; }
    confirmAction('Forget learned categories', `Forget ${l} remembered merchant → category choice${l > 1 ? 's' : ''}? Future imports will go back to keyword rules only.`, () => {
      try { localStorage.removeItem(MEMORY_KEY); } catch {}
      refreshSettings();
      showToast('✓ Learned categories cleared');
    });
  };

  document.body.insertAdjacentHTML('beforeend', `
    <div class="modal-overlay" id="modalRecurringMgr" role="dialog" aria-modal="true" aria-labelledby="recMgrTitle">
      <div class="modal-box">
        <div class="card-header">
          <h3 class="section-title" id="recMgrTitle">Recurring Transactions</h3>
          <button class="btn btn-ghost" onclick="closeModal('modalRecurringMgr')" aria-label="Close dialog">✕</button>
        </div>
        <div id="recMgrList"></div>
        <button type="button" class="btn btn-primary" style="width:100%; margin-top:1rem;" onclick="closeModal('modalRecurringMgr'); openRecurringModal();">+ Add recurring item</button>
      </div>
    </div>`);

  function renderRecurringMgr() {
    const list = recs().slice().sort((a, b) => String(a.next_date).localeCompare(String(b.next_date)));
    const mgrList = $('recMgrList');
    if (!mgrList) return;
    mgrList.innerHTML = list.length ? list.map(r => `
      <div style="display:flex; justify-content:space-between; align-items:center; gap:0.5rem; padding:0.55rem 0; border-bottom:1px solid var(--border-subtle);">
        <div><div style="font-weight:600;">${(r.type || '').toUpperCase() === 'INCOME' ? '↓' : '↑'} ${sanitize(r.description)}</div>
             <div class="caption-text">${sanitize(r.frequency)} — next ${sanitize(r.next_date)}</div></div>
        <div style="display:flex; align-items:center; gap:0.4rem;">
          <strong class="${(r.type || '').toUpperCase() === 'INCOME' ? 'amount-pos' : 'amount-neg'}">${formatINR(r.amount)}</strong>
          <button type="button" class="btn btn-destructive btn-pill" style="padding:0.2rem 0.55rem; font-size:11px;" aria-label="Delete ${sanitize(r.description)}" onclick="removeRecurring(${r.id})">Delete</button>
        </div>
      </div>`).join('') : '<div class="empty-state"><span class="caption-text">No recurring items yet.</span></div>';
  }

  window.openRecurringManager = function () {
    closeModal('modalSettings');
    renderRecurringMgr();
    openModal('modalRecurringMgr');
  };

  registerHook('beforeOpenSettings', refreshSettings);
  registerHook('renderUI', () => {
    const recModal = $('modalRecurringMgr');
    if (recModal && recModal.classList.contains('active')) renderRecurringMgr();
    refreshSettings();
  });
  refreshSettings();
})();