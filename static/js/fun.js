/* DhanSutra fun layer. Drop-in, no dependencies.
 * Load at the end of <body>:  <script src="/static/fun.js" defer></script>
 * Public API: window.DhanFun (see bottom). Everything is opt-in through data-fun="..." hooks,
 * and the whole layer can be switched off with DhanFun.setEnabled(false).
 */
(function () {
  'use strict';

  /* ---------- settings you may want to edit ---------- */
  var CONFIG = {
    currency: '₹',
    // Percent of the monthly budget used at which the weather / mascot changes.
    weather: { cloudyAt: 75, stormyAt: 100 },
    // Rough prices for the "silly conversions". Edit freely.
    units: [
      { one: 'movie ticket', many: 'movie tickets', price: 250 },
      { one: 'cup of chai', many: 'cups of chai', price: 20 },
      { one: 'large pizza', many: 'large pizzas', price: 450 },
      { one: 'metro ride', many: 'metro rides', price: 40 },
      { one: 'paperback book', many: 'paperback books', price: 350 },
      { one: 'birthday cake', many: 'birthday cakes', price: 600 },
      { one: 'cinema popcorn tub', many: 'cinema popcorn tubs', price: 300 },
      { one: 'auto-rickshaw ride', many: 'auto-rickshaw rides', price: 100 }
    ]
  };

  // Page-level overrides: window.DHANFUN_CONFIG = { currency: '$', units: [...] } before this script loads.
  if (window.DHANFUN_CONFIG) {
    Object.keys(window.DHANFUN_CONFIG).forEach(function (k) { CONFIG[k] = window.DHANFUN_CONFIG[k]; });
  }

  var reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  /* ---------- small helpers ---------- */
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } }
  function ssGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function ssSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* storage blocked */ } }
  function isEnabled() { return lsGet('dhanfun_enabled') !== '0'; }
  function pick(list) { return list[Math.floor(Math.random() * list.length)]; }
  function qs(sel) { return document.querySelector(sel); }
  function h(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function money(n) { return CONFIG.currency + Math.round(n).toLocaleString('en-IN'); }
  function hash(str) {
    var x = 0;
    for (var i = 0; i < str.length; i++) { x = (x * 31 + str.charCodeAt(i)) | 0; }
    return Math.abs(x);
  }

  var toastEl, toastTimer;
  function toast(msg, ms) {
    if (!document.body) return;
    if (!toastEl) {
      toastEl = h('div', 'fun-toast fun-ui');
      toastEl.setAttribute('role', 'status');
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    void toastEl.offsetWidth;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, ms || 3400);
  }

  /* ---------- 1. sun / moon theme toggle (always on, it is a real feature) ----------
   * This is only a view over the page's own dark mode: the dashboard already uses body.dark and
   * localStorage 'dhanSutraDark', so the toggle flips those. No second theme system. */
  var THEME_KEY = 'dhanSutraDark';
  function isDark() { return !!document.body && document.body.classList.contains('dark'); }
  function syncThemeButton() {
    var b = qs('.fun-theme');
    if (b) b.setAttribute('aria-pressed', String(isDark()));
  }
  function setDark(on) {
    document.body.classList.toggle('dark', !!on);
    lsSet(THEME_KEY, String(!!on));
    syncThemeButton();
    if (typeof window.refreshSettings === 'function') { try { window.refreshSettings(); } catch (e) { /* ignore */ } }
  }
  function buildThemeToggle() {
    if (qs('.fun-theme')) return;
    var b = h('button', 'fun-theme fun-ui');
    b.type = 'button';
    b.setAttribute('aria-label', 'Switch between light and dark theme');
    b.setAttribute('aria-pressed', String(isDark()));
    b.appendChild(h('span', 'sun'));
    b.appendChild(h('span', 'moon'));
    b.addEventListener('click', function () { setDark(!isDark()); });
    var mount = qs('[data-fun="theme-toggle"]');
    if (mount) { mount.appendChild(b); } else { b.classList.add('fun-floating'); document.body.appendChild(b); }
    // keep the button in step when something else (e.g. the Settings row) flips the theme
    if (window.MutationObserver) new MutationObserver(syncThemeButton).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  }

  /* ---------- 2. spending weather ---------- */
  function weatherFor(pct) {
    if (pct == null || isNaN(pct)) return null;
    if (pct > CONFIG.weather.stormyAt) return { key: 'stormy', icon: '⛈️', label: 'Stormy', line: 'Budget blown. Take cover.' };
    if (pct >= CONFIG.weather.cloudyAt) return { key: 'cloudy', icon: '☁️', label: 'Cloudy', line: 'Clouds are gathering over the budget.' };
    return { key: 'sunny', icon: '☀️', label: 'Sunny', line: 'Plenty of room left in the budget.' };
  }
  var weatherEl;
  function renderWeather(pct) {
    var w = weatherFor(pct);
    if (!weatherEl) {
      var mount = qs('[data-fun="weather"]');
      weatherEl = mount || h('div', 'fun-floating');
      weatherEl.classList.add('fun-weather');
      if (!mount) document.body.appendChild(weatherEl);
    }
    if (!w || !isEnabled()) { weatherEl.style.display = 'none'; return; }
    weatherEl.style.display = '';
    weatherEl.setAttribute('data-weather', w.key);
    weatherEl.title = w.line;
    weatherEl.textContent = '';
    weatherEl.appendChild(h('span', 'fun-weather-icon', w.icon));
    weatherEl.appendChild(h('span', 'fun-weather-label', w.label));
  }

  /* ---------- 3. mood mascot (an original coin with a face) ---------- */
  var FACES = {
    smug: '<path d="M17 27h12M35 27h12" class="line"/><circle cx="23" cy="30" r="2.6" class="ink"/><circle cx="41" cy="30" r="2.6" class="ink"/>' +
          '<path d="M36 20q5-3 11 0" class="line"/><path d="M22 43q10 7 21-3" class="line"/>',
    nervous: '<circle cx="23" cy="28" r="5.5" class="white"/><circle cx="41" cy="28" r="5.5" class="white"/>' +
             '<circle cx="24.5" cy="28" r="2.2" class="ink"/><circle cx="39.5" cy="28" r="2.2" class="ink"/>' +
             '<path d="M22 44q3-4 5 0t5 0t5 0t5 0" class="line"/><path d="M53 12q4 7 0 10q-4-3 0-10z" class="drop"/>',
    fainted: '<path d="M18 23l10 10M28 23l-10 10M36 23l10 10M46 23l-10 10" class="line"/>' +
             '<path d="M24 42h16" class="line"/><path d="M33 42v6a3 3 0 0 0 6 0v-6" class="tongue"/>' +
             '<path d="M54 6l1.5 3.5L59 11l-3.5 1.5L54 16l-1.5-3.5L49 11l3.5-1.5z" class="star"/>'
  };
  var QUIPS = {
    smug: ['Look at you, comfortably under budget.', 'Smooth. Your wallet sends its regards.', 'Not a care in the world. You’ve got this.'],
    nervous: ['We’re near the limit. Totally fine. Sweating.', 'Maybe skip the impulse buy? Just a thought.', 'I can see the budget line from here.'],
    fainted: ['I… can’t… look.', 'Budget exceeded. I’m lying down now.', 'Please tell me that was a refund.']
  };
  var mascotEl, bubbleEl, bubbleTimer, mascotState;
  function mascotStateFor(pct) {
    if (pct == null || isNaN(pct) || pct < CONFIG.weather.cloudyAt) return 'smug';
    return pct > CONFIG.weather.stormyAt ? 'fainted' : 'nervous';
  }
  function say(text) {
    if (!bubbleEl) return;
    bubbleEl.textContent = text;
    bubbleEl.classList.add('show');
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(function () { bubbleEl.classList.remove('show'); }, 3600);
  }
  function setMascot(state, announce) {
    if (!mascotEl) {
      mascotEl = h('button', 'fun-mascot fun-ui');
      mascotEl.type = 'button';
      mascotEl.setAttribute('aria-label', 'Mood coin. Click for a comment.');
      bubbleEl = h('div', 'fun-bubble fun-ui');
      bubbleEl.setAttribute('role', 'status');
      mascotEl.addEventListener('click', function () { say(pick(QUIPS[mascotState])); });
      document.body.appendChild(bubbleEl);
      document.body.appendChild(mascotEl);
    }
    if (!isEnabled()) { mascotEl.style.display = 'none'; bubbleEl.style.display = 'none'; return; }
    mascotEl.style.display = '';
    bubbleEl.style.display = '';
    var changed = mascotState !== undefined && state !== mascotState; // never talk on the very first render
    mascotState = state;
    mascotEl.setAttribute('data-state', state);
    mascotEl.innerHTML = '<svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="28" class="coin"/>' +
      '<circle cx="32" cy="32" r="22" class="ring"/>' + FACES[state] + '</svg>';
    if (changed && announce) say(pick(QUIPS[state]));
  }

  /* ---------- 4. jokey empty states ---------- */
  var emptyPicked = {}; // one joke per kind per page load, so re-renders do not make the text flicker
  var EMPTY = {
    upcoming: { plain: 'Nothing scheduled.', fun: ['Nothing due soon. Enjoy the quiet.', 'No bills on the horizon. Suspiciously peaceful.'] },
    transactions: { plain: 'No transactions yet.', fun: ['No transactions yet. Either you’re a monk or the sync is asleep.', 'Nothing here. Your wallet is suspiciously quiet.', 'Zero transactions. Bold strategy.'] },
    budgets: { plain: 'No budgets set.', fun: ['No budgets yet. Living dangerously, I see.', 'Set a budget and I’ll start judging you properly.'] },
    goals: { plain: 'No goals yet.', fun: ['No goals yet. Dream big, or at least dream something.', 'The piggy bank is waiting for a reason.'] },
    recurring: { plain: 'No recurring items.', fun: ['No recurring payments. Either organised or in denial.', 'Nothing repeats here. Rare, and a little suspicious.'] },
    accounts: { plain: 'No accounts yet.', fun: ['No accounts yet. Money has to live somewhere.', 'An empty wallet is just a very minimalist wallet.'] },
    categories: { plain: 'No categories yet.', fun: ['No categories. Everything is “misc”. Brave.'] },
    search: { plain: 'No results.', fun: ['Nothing matched. Even the search is confused.', 'No results. Try a different spelling, or a different universe.'] },
    expenses: { plain: 'No expenses yet.', fun: ['No expenses yet. Either you’re a monk or the sync is asleep.', 'Zero spending. Suspiciously disciplined.'] },
    trend: { plain: 'Not enough history yet.', fun: ['Not enough history for a trend yet. Give it a few months.', 'One dot is not a trend. Come back with more data.'] }
  };
  // The same joke stays for the whole page visit (so re-renders don't flicker); reshuffle=true picks a new one.
  var SEED = Math.floor(Math.random() * 1e6);
  function emptyState(kind, el, reshuffle) {
    var entry = EMPTY[kind] || { plain: 'Nothing here yet.', fun: ['Nothing here yet. Very zen.'] };
    var text;
    if (!isEnabled()) text = entry.plain;
    else if (reshuffle) { SEED = Math.floor(Math.random() * 1e6); text = entry.fun[SEED % entry.fun.length]; }
    else text = entry.fun[(SEED + hash(kind)) % entry.fun.length];
    if (el) el.textContent = text;
    return text;
  }

  /* ---------- 5. Konami code makes coins rain ---------- */
  var KONAMI = ['arrowup', 'arrowup', 'arrowdown', 'arrowdown', 'arrowleft', 'arrowright', 'arrowleft', 'arrowright', 'b', 'a'];
  var keyBuf = [];
  var raining = false;
  function coinRain() {
    if (raining || !document.body) return;
    if (reduceMotion) { toast('🪙 Coins! (animation skipped, reduced motion is on)'); return; }
    raining = true;
    var box = h('div', 'fun-rain fun-ui');
    for (var i = 0; i < 70; i++) {
      var size = 18 + Math.random() * 18;
      var c = h('span', 'fun-coin', CONFIG.currency);
      c.style.left = (Math.random() * 100) + '%';
      c.style.width = c.style.height = size + 'px';
      c.style.fontSize = (size * 0.55) + 'px';
      c.style.animationDuration = (2.2 + Math.random() * 2) + 's';
      c.style.animationDelay = (Math.random() * 1.2) + 's';
      box.appendChild(c);
    }
    document.body.appendChild(box);
    setTimeout(function () { box.remove(); raining = false; }, 6200);
  }
  document.addEventListener('keydown', function (e) {
    if (!isEnabled()) return;
    var t = e.target;
    if (t && (/^(input|textarea|select)$/i.test(t.tagName) || t.isContentEditable)) return;
    keyBuf.push(String(e.key).toLowerCase());
    keyBuf = keyBuf.slice(-KONAMI.length);
    if (keyBuf.join() === KONAMI.join()) { keyBuf = []; coinRain(); }
  });

  /* ---------- 6. click the logo five times ---------- */
  var SECRETS = [
    'You found it. Your money says hi. 🪙',
    'Secret unlocked: you are officially nosy about your own finances.',
    'Fun fact: tracking spending is the first step. Ignoring it is the second.',
    'Psst. The Konami code does something. Just saying.'
  ];
  document.addEventListener('click', function (e) {
    if (!isEnabled() || !e.target.closest) return;
    var logo = e.target.closest('[data-fun="logo"]');
    if (!logo) return;
    var now = Date.now();
    var times = [];
    try { times = JSON.parse(ssGet('dhanfun_logo') || '[]'); } catch (err) { times = []; }
    times = times.filter(function (x) { return now - x < 2500; });
    times.push(now);
    if (times.length >= 5) {
      times = [];
      if (!reduceMotion) {
        var spinner = logo.querySelector('.brand-icon') || logo; // spin just the emblem when there is one
        spinner.classList.remove('fun-spin');
        void spinner.offsetWidth;
        spinner.classList.add('fun-spin');
        setTimeout(function () { spinner.classList.remove('fun-spin'); }, 1000);
      }
      toast(pick(SECRETS), 4200);
    }
    ssSet('dhanfun_logo', JSON.stringify(times));
  });

  /* ---------- 7. greeting (changes after midnight) ---------- */
  function displayName(name) {
    if (!name) return '';
    // "dkgandhi246.dg@gmail.com" -> "Dkgandhi": first part of the email name, digits dropped.
    // Too short or empty after that (e.g. "x1@..."): no name, the greeting just says "Good evening".
    var first = String(name).split('@')[0].split(/[._\-+\s]+/)[0].replace(/[0-9]+/g, '');
    if (first.length < 2) return '';
    return first.charAt(0).toUpperCase() + first.slice(1).toLowerCase();
  }
  function greeting(name, now) {
    var d = now || new Date();
    var hr = d.getHours();
    var who = displayName(name);
    var tail = who ? ', ' + who : '';
    if (isEnabled() && hr < 5) {
      var h12 = (hr % 12) || 12;
      return 'Still tracking at ' + h12 + ' AM? Your money will be here tomorrow.';
    }
    if (hr < 12) return 'Good morning' + tail;
    if (hr < 17) return 'Good afternoon' + tail;
    return 'Good evening' + tail;
  }

  /* ---------- 8. silly conversions ---------- */
  function conversion(amount, categoryName, periodLabel) {
    if (!isEnabled() || !(amount > 0)) return '';
    var period = periodLabel || 'this month';
    var seed = hash(String(Math.round(amount)) + (categoryName || ''));
    var usable = CONFIG.units.filter(function (u) {
      var n = amount / u.price;
      return n >= 2 && n <= 400;
    });
    if (!usable.length) return '';
    var u = usable[seed % usable.length];
    var n = Math.round(amount / u.price);
    var where = categoryName ? ' on ' + categoryName : '';
    return 'You spent the equivalent of ' + n.toLocaleString('en-IN') + ' ' + (n === 1 ? u.one : u.many) + where + ' ' + period + '.';
  }

  /* ---------- 9. money personality ---------- */
  var ANIMALS = {
    squirrel: { emoji: '🐿️', name: 'The Squirrel', blurb: 'You stash away a big slice of your income. Winter is coming and you are ready.' },
    magpie: { emoji: '🐦', name: 'The Magpie', blurb: 'Shiny things find you. A lot of your spending goes to shopping and treats.' },
    peacock: { emoji: '🦚', name: 'The Peacock', blurb: 'Life is a dinner party. Food and dining out take a large share of your money.' },
    owl: { emoji: '🦉', name: 'The Owl', blurb: 'Watchful and consistent. You track everything and stay inside your budget.' },
    bee: { emoji: '🐝', name: 'The Bee', blurb: 'Always buzzing. Lots of small transactions add up through the month.' },
    tortoise: { emoji: '🐢', name: 'The Tortoise', blurb: 'Slow, steady and hard to rattle. No big swings in your spending.' }
  };
  var SHOP = /shop|cloth|fashion|gadget|electronic|entertain|gaming|hobby|gift|treat/i;
  var FOOD = /dining|restaurant|food|cafe|coffee|snack|delivery|takeaway|eating/i;
  function personality(s) {
    s = s || {};
    var expense = Number(s.expense) || 0;
    var income = Number(s.income) || 0;
    var rate = s.savingsRate != null ? Number(s.savingsRate) : (income > 0 ? (income - expense) / income : null);
    var cats = s.categories || {};
    var shop = 0, food = 0;
    Object.keys(cats).forEach(function (k) {
      if (SHOP.test(k)) shop += Number(cats[k]) || 0;
      if (FOOD.test(k)) food += Number(cats[k]) || 0;
    });
    var tx = Number(s.txCount) || 0;
    var key = 'tortoise';
    if (rate != null && rate >= 0.3) key = 'squirrel';
    else if (expense > 0 && shop / expense >= 0.3) key = 'magpie';
    else if (expense > 0 && food / expense >= 0.3) key = 'peacock';
    else if (tx >= 20 && (s.budgetUsedPct != null ? s.budgetUsedPct <= 100 : (rate != null && rate >= 0))) key = 'owl';
    else if (tx >= 50) key = 'bee';
    var a = ANIMALS[key];
    return { key: key, emoji: a.emoji, name: a.name, blurb: a.blurb };
  }
  function renderPersonality(stats, monthLabel) {
    var el = qs('[data-fun="personality"]');
    var p = personality(stats);
    if (!el) return p;
    el.textContent = '';
    if (!isEnabled()) { el.style.display = 'none'; return p; }
    el.style.display = '';
    el.classList.add('fun-personality');
    if (!stats || !(Number(stats.income) > 0 || Number(stats.expense) > 0)) {
      el.appendChild(h('span', 'fun-personality-emoji', '🥚'));
      el.appendChild(h('span', 'fun-personality-text', 'Your money personality hatches after a month with some transactions in it.'));
      return p;
    }
    el.appendChild(h('span', 'fun-personality-emoji', p.emoji));
    var box = h('span', 'fun-personality-text');
    box.appendChild(h('strong', null, p.name + (monthLabel ? ' (' + monthLabel + ')' : '')));
    box.appendChild(h('span', null, p.blurb));
    el.appendChild(box);
    return p;
  }

  /* ---------- wiring ---------- */
  var lastOpts = {};
  function update(opts) {
    lastOpts = opts || lastOpts;
    var pct = lastOpts.budgetUsedPct;
    renderWeather(pct);
    setMascot(mascotStateFor(pct), true);
    var line = qs('[data-fun="conversion"]');
    if (line) {
      var top = lastOpts.topCategory;
      var text = top ? conversion(top.amount, top.name, lastOpts.periodLabel) : '';
      line.textContent = text;
      line.style.display = text ? '' : 'none';
    }
  }
  function refresh() {
    var g = qs('[data-fun="greeting"]');
    if (g) g.textContent = greeting(g.getAttribute('data-name'));
    var empties = document.querySelectorAll('[data-fun-empty]');
    for (var i = 0; i < empties.length; i++) emptyState(empties[i].getAttribute('data-fun-empty'), empties[i]);
    update();
  }
  var toggleListeners = [];
  function onToggle(fn) { if (typeof fn === 'function') toggleListeners.push(fn); }
  function setEnabled(on) {
    lsSet('dhanfun_enabled', on ? '1' : '0');
    var cb = qs('[data-fun="enable-toggle"]');
    if (cb) cb.checked = !!on;
    refresh();
    toggleListeners.forEach(function (fn) { try { fn(!!on); } catch (e) { console.error(e); } });
  }
  function init() {
    buildThemeToggle();
    var cb = qs('[data-fun="enable-toggle"]');
    if (cb) {
      cb.checked = isEnabled();
      cb.addEventListener('change', function () { setEnabled(cb.checked); });
    }
    refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();

  window.DhanFun = {
    config: CONFIG,
    update: update,            // update({ budgetUsedPct: 82, topCategory: { name: 'Food', amount: 4200 }, periodLabel: 'this month' })
    refresh: refresh,          // re-scan greeting / empty-state placeholders
    greeting: greeting,        // greeting('prateek@example.com')
    emptyState: emptyState,    // emptyState('transactions', optionalElement, reshuffle?)
    conversion: conversion,    // conversion(4200, 'Food')
    personality: personality,  // personality({ income, expense, txCount, budgetUsedPct, categories: { Food: 1200 } })
    renderPersonality: renderPersonality,
    coinRain: coinRain,
    toast: toast,
    setEnabled: setEnabled,
    isEnabled: isEnabled,
    onToggle: onToggle,        // onToggle(function (on) { ... }) runs after the layer is switched on/off
    setDark: setDark           // setDark(true|false) flips body.dark like the Settings theme row does
  };
})();