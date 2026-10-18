/* DhanSutra - Fun layer bridge.
   Loaded from user_dashboard.html AFTER /static/fun.js and the other modules. It plugs into the dashboard
   through registerHook() like insights.js / calendar.js / settings.js and feeds DhanFun the numbers the
   dashboard has already loaded (budgets, transactions, prevTransactions, categories, selectedMonth).
   It never calls the API and never changes data. */
(function () {
  if (!window.DhanFun) { console.warn('fun-bridge: /static/fun.js is not loaded'); return; }
  const Fun = window.DhanFun;
  const arr = v => (Array.isArray(v) ? v : []);
  const has = name => { try { return typeof (0, eval)(name) !== 'undefined'; } catch (e) { return false; } };

  // ---------- numbers ----------
  // Same maths as the "Budget Discipline" bar in renderHealthScore(), so weather and health score agree.
  function budgetUsedPct() {
    const capped = arr(typeof budgets !== 'undefined' ? budgets : []).filter(b => Number(b.limit_amount) > 0);
    const cap = capped.reduce((s, b) => s + Number(b.limit_amount), 0);
    if (!(cap > 0)) return null; // no budget set this month: no weather
    const spent = capped.reduce((s, b) => s + (Number(b.spent_amount) || 0), 0);
    return Math.round((spent / cap) * 100);
  }

  const catName = id => {
    const c = arr(typeof categories !== 'undefined' ? categories : []).find(x => x.id === id);
    return c ? c.name : 'Uncategorized';
  };

  function topCategory() {
    const sums = {};
    arr(typeof transactions !== 'undefined' ? transactions : []).forEach(t => {
      if ((t.type || '').toUpperCase() !== 'EXPENSE') return;
      const n = catName(t.category_id);
      sums[n] = (sums[n] || 0) + (Number(t.amount) || 0);
    });
    const top = Object.entries(sums).sort((a, b) => b[1] - a[1])[0];
    return top ? { name: top[0], amount: top[1] } : null;
  }

  function periodLabel() {
    const now = new Date();
    const same = selectedMonth.getFullYear() === now.getFullYear() && selectedMonth.getMonth() === now.getMonth();
    return same ? 'this month' : 'in ' + selectedMonth.toLocaleString('en-IN', { month: 'long', year: 'numeric' });
  }

  // Personality uses the month BEFORE the selected one: it is complete, so the label does not flip around
  // while the current month fills up.
  function lastMonthStats() {
    if (typeof prevTransactions === 'undefined' || !Array.isArray(prevTransactions)) return null;
    const s = { income: 0, expense: 0, txCount: 0, categories: {} };
    prevTransactions.forEach(t => {
      const ty = (t.type || '').toUpperCase(), a = Number(t.amount) || 0;
      if (ty === 'INCOME') s.income += a;
      else if (ty === 'EXPENSE') {
        s.expense += a;
        s.txCount++;
        const n = catName(t.category_id);
        s.categories[n] = (s.categories[n] || 0) + a;
      }
    });
    return s;
  }

  // ---------- jokey empty states ----------
  // The dashboard renders its empty states as plain HTML; swap the headline text after each render.
  // [container selector, kind (or function), which child holds the headline]
  const EMPTIES = [
    ['#ledgerRowsContainer > .empty-state', () => (arr(typeof transactions !== 'undefined' ? transactions : []).length ? 'search' : 'transactions'), 'p'],
    ['#rankedOutflowContainer > .empty-state', 'expenses', 'p'],
    ['#budgetCategoryRows > .empty-state', 'budgets', 'p'],
    ['#goalsContainer > .empty-state', 'goals', 'p'],
    ['#upcomingContainer > .empty-state', 'upcoming', 'span'], // "nothing due in 30 days", not "no recurring items"
    ['#nwChart > .empty-state', 'trend', 'span']
  ];
  function jokeEmpties() {
    if (!Fun.isEnabled()) return;
    EMPTIES.forEach(([sel, kind, child]) => {
      const box = document.querySelector(sel);
      const el = box && box.querySelector(child);
      if (el) Fun.emptyState(typeof kind === 'function' ? kind() : kind, el);
    });
  }

  // ---------- render ----------
  function render() {
    const on = Fun.isEnabled();
    Fun.update({ budgetUsedPct: budgetUsedPct(), topCategory: topCategory(), periodLabel: periodLabel() });

    const card = document.getElementById('funPersonalityCard');
    if (card) card.style.display = on ? '' : 'none';
    if (on) {
      const prev = new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() - 1, 1);
      Fun.renderPersonality(lastMonthStats(), prev.toLocaleString('en-IN', { month: 'long' }));
    }
    jokeEmpties();
  }

  registerHook('renderUI', render);
  registerHook('renderLedger', jokeEmpties); // typing in the search box re-renders only the ledger
  // renderLedger() returns early when nothing matches, so its hook never runs for an empty search result.
  // Watch the containers instead: whenever the dashboard swaps their content, re-apply the jokes.
  if (window.MutationObserver) {
    EMPTIES.forEach(([sel]) => {
      const container = document.querySelector(sel.split(' > ')[0]);
      if (container) new MutationObserver(jokeEmpties).observe(container, { childList: true });
    });
  }

  // Switching the fun layer on or off in Settings: re-run the dashboard render so the original empty-state
  // text comes back (off) or the jokes return (on). renderUI() is the dashboard's own function.
  Fun.onToggle(() => {
    try {
      if (typeof renderUI === 'function') renderUI(typeof lastBudgetList !== 'undefined' ? lastBudgetList : []);
      else render();
    } catch (e) { console.error(e); }
  });

  // First paint: the dashboard may have rendered before this script loaded.
  try { render(); } catch (e) { /* data not loaded yet; the first renderUI hook will fill everything in */ }
})();
