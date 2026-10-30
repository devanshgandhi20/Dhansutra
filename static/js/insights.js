/* DhanSutra Insights add-on.
   Loaded from user_dashboard.html AFTER the main <script> (it uses that script's globals:
   selectedMonth, transactions, trendCache, recurringList, formatINR, normDesc, advanceDate, ...):
       <script src="/static/js/insights.js"></script>
   It plugs in through registerHook() like calendar.js and settings.js. */
(function () {
    const $ = id => document.getElementById(id);
    // Every transaction the dashboard has already downloaded: the selected month plus the other months it
    // cached for the trend / burn-rate cards, de-duplicated. The comparisons below (last month, 3-month
    // average, subscriptions over 6 months) need that history; the selected month alone made them always empty.
    let historyMemo = null; // built once per renderInsights() call instead of on every lookup
    const T = () => {
        if (historyMemo) return historyMemo;
        const byKey = new Map();
        const add = t => { if (t) byKey.set(t.id != null ? 'id:' + t.id : `${t.date}|${t.description}|${t.amount}`, t); };
        (typeof transactions !== 'undefined' ? transactions : []).forEach(add);
        if (typeof trendCache !== 'undefined') Object.values(trendCache).forEach(list => (Array.isArray(list) ? list : []).forEach(add));
        return Array.from(byKey.values());
    };
    const recs = () => (typeof recurringList !== 'undefined' ? recurringList : []);
    const expenses = () => T().filter(t => (t.type || '').toUpperCase() === 'EXPENSE');
    const mo = off => new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() + off, 1);
    const row = (l, r, cls = '') => `<div class="ins-row"><span>${l}</span><span class="${cls}" style="font-weight:700; text-align:right;">${r}</span></div>`;
    const note = t => `<div class="caption-text" style="margin-top:0.6rem;">${t}</div>`;
    const empty = t => `<div class="empty-state"><span class="caption-text">${t}</span></div>`;
    const pctTxt = (a, b) => (b > 0 ? `${a >= b ? '+' : ''}${(((a - b) / b) * 100).toFixed(0)}%` : '—');

    // ---------- UI shell ----------
    document.head.insertAdjacentHTML('beforeend', `<style>
        .ins-row { display:flex; justify-content:space-between; gap:0.5rem; padding:0.4rem 0; border-bottom:1px solid var(--border-subtle); }
        .fab { display:none; position:fixed; right:16px; bottom:78px; z-index:1100; width:52px; height:52px; border-radius:50%; border:none; font-size:26px; color:#fff;
               background:linear-gradient(135deg,var(--saffron),var(--gold)); box-shadow:0 6px 18px rgba(0,0,0,.3); cursor:pointer; }
        .fab-menu { display:none; position:fixed; right:16px; bottom:140px; z-index:1100; flex-direction:column; gap:8px; }
        .fab-menu.open { display:flex; }
        @media (max-width:920px) { .fab { display:block; } }
    </style>`);

    document.querySelector('.desktop-nav').insertAdjacentHTML('beforeend', `<button class="nav-link" id="navInsDesktop" onclick="switchTab('insights')">Insights</button>`);
    const mobItem = document.createElement('button');
    mobItem.className = 'mob-item';
    mobItem.id = 'navInsMobile';
    mobItem.innerHTML = '<span>💡</span><span>Insights</span>';
    mobItem.onclick = () => switchTab('insights', mobItem);
    document.querySelector('.mobile-bottom-nav').insertBefore(mobItem, document.querySelector('.mobile-bottom-nav').lastElementChild);

    document.querySelector('main.container').insertAdjacentHTML('beforeend', `
        <section id="tab-insights" style="display:none;">
            <div class="dashboard-grid">
                <div class="card"><div class="card-header"><h2 class="section-title">Next 30 Days</h2></div><div id="insForecast"></div></div>
                <div class="card"><div class="card-header"><h2 class="section-title">Subscriptions</h2></div><div id="insSubs"></div></div>
            </div>
            <div class="dashboard-grid">
                <div class="card"><div class="card-header"><h2 class="section-title">Top Merchants</h2></div><div id="insMerchants"></div></div>
                <div class="card"><div class="card-header"><h2 class="section-title" id="insReviewTitle">Monthly Review</h2></div><div id="insReview"></div></div>
            </div>
        </section>`);

    // ---------- Cash-flow forecast ----------
    function occurrences(r, end) {
        let n = 0, d = String(r.next_date || '').split('T')[0];
        while (d && d <= end && n < 40) { n++; d = advanceDate(d, r.frequency); }
        return n;
    }

    function renderForecast() {
        const endDate = toDateStr(new Date(Date.now() + 30 * 864e5));
        let inc = 0, bills = 0;
        recs().forEach(r => {
            const v = occurrences(r, endDate) * (Number(r.amount) || 0);
            if ((r.type || '').toUpperCase() === 'INCOME') inc += v; else bills += v;
        });

        const today = todayStr();
        const ex = expenses().filter(t => t.date <= today);
        const first = ex.map(t => t.date).sort()[0];
        const days = first ? Math.min(90, Math.max(7, Math.round((Date.now() - new Date(first + 'T00:00:00')) / 864e5) + 1)) : 30;
        const from = toDateStr(new Date(Date.now() - days * 864e5));
        const spent = ex.filter(t => t.date >= from).reduce((s, t) => s + (Number(t.amount) || 0), 0);
        const other = Math.max((spent / days) * 30 - bills, 0);

        const accs = (dashboardData && dashboardData.accounts) || [];
        const cash = accs.filter(a => ['BANK', 'CASH'].includes(String(a.type || '').toUpperCase())).reduce((s, a) => s + (Number(a.current_balance) || 0), 0);
        const free = cash + inc - bills - other;

        $('insForecast').innerHTML =
            row('Bank + cash today', formatINR(cash)) +
            row('Expected income (recurring)', '+ ' + formatINR(inc), 'amount-pos') +
            row('Scheduled bills (recurring)', '− ' + formatINR(bills), 'amount-neg') +
            row('Other spending (estimate)', '− ' + formatINR(other), 'amount-neg') +
            row('Projected balance in 30 days', formatINR(free), free >= 0 ? 'amount-pos' : 'amount-neg') +
            note(recs().length ? `Estimate uses your recurring items plus your average daily spending over the last ${days} days.` : 'Add recurring items on the dashboard for a sharper forecast.') +
            (free < 0 ? note('⚠ You are projected to run short of cash within 30 days.') : '');
    }

    // ---------- Subscription detection ----------
    function detectSubs() {
        const groups = {};
        const cutoff = toDateStr(new Date(Date.now() - 190 * 864e5));
        expenses().filter(t => t.date >= cutoff).forEach(t => {
            const k = normDesc(t.description);
            if (k) (groups[k] = groups[k] || []).push(t);
        });
        const subs = [];
        Object.values(groups).forEach(list => {
            const months = new Set(list.map(t => t.date.slice(0, 7)));
            if (months.size < 3 || list.length > months.size * 1.5) return;
            const amts = list.map(t => Number(t.amount)).sort((a, b) => a - b);
            const med = amts[Math.floor(amts.length / 2)];
            if (!amts.every(a => Math.abs(a - med) <= med * 0.1)) return;
            subs.push({ name: list[0].description, amount: med, last: list.map(t => t.date).sort().pop() });
        });
        return subs.sort((a, b) => b.amount - a.amount);
    }

    function renderSubs() {
        const subs = detectSubs();
        if (!subs.length) { $('insSubs').innerHTML = empty('No subscriptions detected yet. A charge that repeats monthly for 3+ months shows up here.'); return; }
        const monthly = subs.reduce((s, x) => s + x.amount, 0);
        $('insSubs').innerHTML = subs.map(s => row(`${sanitize(s.name)}<div class="caption-text">last ${sanitize(s.last)}</div>`, formatINR(s.amount) + ' / mo')).join('') +
            row('Monthly total', formatINR(monthly)) + row('Annualised', formatINR(monthly * 12), 'amount-neg');
    }

    // ---------- Merchants ----------
    function renderMerchants() {
        const sumFor = (k, d) => expenses().filter(t => normDesc(t.description) === k && String(t.date).startsWith(formatMonthStr(d)))
            .reduce((s, t) => s + (Number(t.amount) || 0), 0);
        const cur = {}, names = {}, counts = {};
        expenses().filter(t => String(t.date).startsWith(formatMonthStr(selectedMonth))).forEach(t => {
            const k = normDesc(t.description);
            cur[k] = (cur[k] || 0) + (Number(t.amount) || 0);
            names[k] = names[k] || t.description;
            counts[k] = (counts[k] || 0) + 1;
        });
        const top = Object.entries(cur).sort((a, b) => b[1] - a[1]).slice(0, 5);
        if (!top.length) { $('insMerchants').innerHTML = empty('No spending recorded for this month.'); return; }
        $('insMerchants').innerHTML = top.map(([k, amt]) => {
            const last = sumFor(k, mo(-1));
            const avg3 = [1, 2, 3].reduce((s, i) => s + sumFor(k, mo(-i)), 0) / 3;
            return row(`${sanitize(names[k])}<div class="caption-text">${counts[k]} txn · last month ${formatINR(last)} (${pctTxt(amt, last)}) · 3-mo avg ${formatINR(avg3)}</div>`, formatINR(amt));
        }).join('');
    }

    // ---------- Monthly review ----------
    // Reporting totals use attributed months; expense categories use actual dates.
    function expenseCategories(d) {
        const k = formatMonthStr(d);
        const cats = {};

        T().forEach(t => {
            if ((t.type || '').toUpperCase() !== 'EXPENSE' ||
                !String(t.date).startsWith(k)) return;

            const a = Number(t.amount) || 0;
            const c = categories.find(c => c.id === t.category_id);
            const n = c ? c.name : 'Uncategorized';
            cats[n] = (cats[n] || 0) + a;
        });

        return cats;
    }

    function reportingStats(d, summary, summaryMonth) {
        if (summaryMonth !== formatMonthStr(d) ||
            !isValidDashboardSummary(summary)) return null;

        return {
            inc: Number(summary.monthly_income),
            exp: Number(summary.monthly_expense),
            saved: Number(summary.monthly_savings),
            rate: Number(summary.savings_rate)
        };
    }

    function renderReview() {
        const c = reportingStats(
            selectedMonth, dashboardData, dashboardSummaryMonth
        );

        const p = reportingStats(
            mo(-1), previousReportingSummary, previousReportingMonth
        );

        $('insReviewTitle').textContent =
            selectedMonth.toLocaleString('en-IN', {
                month: 'long',
                year: 'numeric'
            }) + ' Review';

        if (!c) {
            $('insReview').innerHTML =
                empty('Monthly reporting unavailable for the selected month.');
            return;
        }

        if (!c.inc && !c.exp) {
            $('insReview').innerHTML =
                empty('No income or expenses reported for this month.');
            return;
        }

        const currentCats = dataFreshness.transactions
            ? expenseCategories(selectedMonth)
            : {};

        const previousCats = dataFreshness.prevTransactions
            ? expenseCategories(mo(-1))
            : {};

        const topCat = Object.entries(currentCats)
            .sort((a, b) => b[1] - a[1])[0];

        const rises =
            dataFreshness.transactions && dataFreshness.prevTransactions
                ? Object.entries(currentCats)
                    .map(([n, v]) => [
                        n,
                        v - (previousCats[n] || 0),
                        previousCats[n] || 0,
                        v
                    ])
                    .filter(x => x[1] > 0 && x[2] > 0)
                    .sort((a, b) => b[1] - a[1])
                : [];

        const lines = [];

        if (topCat) {
            lines.push(
                `Biggest category: ${sanitize(topCat[0])} ` +
                `(${formatINR(topCat[1])}).`
            );
        }

        if (rises[0]) {
            lines.push(
                `${sanitize(rises[0][0])} rose ` +
                `${formatINR(rises[0][1])} ` +
                `(${pctTxt(rises[0][3], rises[0][2])}) vs last month.`
            );
        }

        if (p && p.inc > 0 && c.inc > 0) {
            lines.push(
                `Savings rate ${c.rate >= p.rate ? 'improved' : 'fell'} ` +
                `from ${p.rate.toFixed(1)}% to ${c.rate.toFixed(1)}%.`
            );
        }

        if (!dataFreshness.summary) {
            lines.push(
                'Current reporting figures are stale (last known values).'
            );
        }

        if (!p) {
            lines.push(
                'Previous month reporting unavailable; comparisons omitted.'
            );
        } else if (!dataFreshness.previousSummary) {
            lines.push(
                'Previous month reporting figures are stale (last known values).'
            );
        }

        if (!dataFreshness.transactions) {
            lines.push(
                'Expense-category detail unavailable because the transaction list did not refresh.'
            );
        }

        $('insReview').innerHTML =
            row('Income', formatINR(c.inc), 'amount-pos') +
            row('Expenses', formatINR(c.exp), 'amount-neg') +
            row(
                'Saved',
                formatINR(c.saved),
                c.saved >= 0 ? 'amount-pos' : 'amount-neg'
            ) +
            row('Savings rate', c.rate.toFixed(1) + '%') +
            row(
                'Expenses vs last month',
                p ? pctTxt(c.exp, p.exp) : '-'
            ) +
            lines.map(l => note(' - ' + l)).join('');
    }

    function renderInsights() {
        historyMemo = null;
        historyMemo = T();
        try { renderForecast(); renderSubs(); renderMerchants(); renderReview(); }
        finally { historyMemo = null; }
    }

    // ---------- hook into the dashboard ----------
    // (This used to overwrite the global renderUI/switchTab functions, which breaks if another script
    // does the same or the main script ever declares them differently. Hooks are the supported way.)
    registerHook('renderUI', () => { try { renderInsights(); } catch (e) { console.error(e); } });
    registerHook('switchTab', tabId => {
        const tab = $('tab-insights');
        if (tab) tab.style.display = tabId === 'insights' ? 'block' : 'none';
        ['navInsDesktop', 'navInsMobile'].forEach(id => { const b = $(id); if (b) b.classList.toggle('active', tabId === 'insights'); });
    });

    // ---------- mobile quick-add ----------
    const menu = document.createElement('div');
    menu.className = 'fab-menu';
    [['expense', '− Expense'], ['income', '+ Income'], ['transfer', '⇄ Transfer']].forEach(([type, label]) => {
        const b = document.createElement('button');
        b.className = 'btn btn-primary btn-pill';
        b.textContent = label;
        b.onclick = () => { menu.classList.remove('open'); openTransactionModal(); setFormType(type); };
        menu.appendChild(b);
    });
    const fab = document.createElement('button');
    fab.className = 'fab';
    fab.textContent = '+';
    fab.setAttribute('aria-label', 'Quick add transaction');
    fab.onclick = () => menu.classList.toggle('open');
    document.body.append(menu, fab);
})();