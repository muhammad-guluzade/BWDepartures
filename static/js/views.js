// How a stop's departures are shown: by platform (as always), by time (one
// list, soonest first), or "My direction first" (the platforms of the way the
// visitor goes at the top). By platform / by time is one choice for every stop;
// "My direction first" belongs to the stop (and tab) where a direction was
// chosen, and only there (the user's rule, 2 November 2026). Both are kept in
// this browser (each device has its own); if the browser keeps nothing (a
// private window, blocked site data), as long as the page is open.
// The button by the stop's name opens the small menu made here; departures.js
// draws the board accordingly ('view-changed').
(function () {
  const VIEW = 'boardView';
  const DIRECTIONS = 'boardDirections';
  const VIEWS = ['platform', 'time', 'direction'];
  // how many stops' directions are kept (the oldest choice goes first)
  const KEPT = 200;
  // directions listed before "Show N more directions"
  const LISTED = 6;
  // line badges per direction before "…"
  const BADGES = 5;

  const memory = {};   // used when the browser's storage can't be used
  function read(key) {
    try {
      const text = localStorage.getItem(key);
      if (text !== null) return JSON.parse(text);
    } catch { /* nothing stored, damaged, or storage blocked */ }
    return memory[key];
  }
  function write(key, value) {
    memory[key] = value;
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* kept in memory only */ }
  }

  // the view of every stop that has no direction in use: 'platform' or 'time'
  // (a 'direction' stored by the first version counts as 'platform')
  function shared() {
    const stored = read(VIEW);
    return stored === 'time' ? 'time' : 'platform';
  }

  // {"<stop id>|<tab>": {stops: [next stops], on: true|false}}: a direction is
  // remembered by the next stops of its departures (the API's `next_stops`), not
  // by platform numbers, which can change from one day to the next; `on` says
  // whether the stop shows "My direction first" now (switched to another view,
  // the direction stays remembered for going back)
  const placeOf = (stopId, tab) => `${stopId}|${tab || ''}`;
  const isStops = v => Array.isArray(v) && v.length > 0 && v.every(x => typeof x === 'string');
  function chosenAll() {
    const stored = read(DIRECTIONS);
    if (!stored || typeof stored !== 'object' || Array.isArray(stored)) return {};
    const all = {};
    const oldView = read(VIEW) === 'direction';   // the first version: one view for all stops
    Object.entries(stored).forEach(([place, v]) => {
      if (isStops(v)) all[place] = { stops: v, on: oldView };
      else if (v && isStops(v.stops)) all[place] = { stops: v.stops, on: v.on === true };
    });
    return all;
  }
  function saved(stopId, tab) {
    return chosenAll()[placeOf(stopId, tab)] || null;
  }
  // the chosen next stops while this stop shows "My direction first", else null
  function direction(stopId, tab) {
    const entry = saved(stopId, tab);
    return entry && entry.on ? entry.stops : null;
  }
  // the view at this stop and tab
  function view(stopId, tab) {
    return stopId !== undefined && direction(stopId, tab) ? 'direction' : shared();
  }
  function store(all) {
    const places = Object.keys(all);
    places.slice(0, Math.max(0, places.length - KEPT)).forEach(p => delete all[p]);
    write(DIRECTIONS, all);
    if (read(VIEW) === 'direction') write(VIEW, 'platform');
    document.dispatchEvent(new CustomEvent('view-changed'));
  }
  // By platform / By time, chosen at a stop: for every stop; this stop leaves
  // "My direction first" (its direction stays remembered)
  function setView(name, stopId, tab) {
    if (name !== 'platform' && name !== 'time') return;
    write(VIEW, name);
    const all = chosenAll();
    const entry = stopId !== undefined && all[placeOf(stopId, tab)];
    if (entry) entry.on = false;
    store(all);
  }
  // a direction chosen at a stop: this stop shows it first, the others are not affected
  function choose(stopId, tab, nextStops) {
    const all = chosenAll();
    const place = placeOf(stopId, tab);
    delete all[place];           // (to the end: the newest)
    all[place] = { stops: nextStops.slice(), on: true };
    store(all);
  }
  // another tab of this site changed them
  window.addEventListener('storage', (e) => {
    if (e.key === VIEW || e.key === DIRECTIONS) document.dispatchEvent(new CustomEvent('view-changed'));
  });

  // the direction of a board that the remembered next stops belong to (in use
  // or not), or null
  function chosenOf(data) {
    const entry = saved(data.stop.id, data.tab);
    const stops = entry && entry.stops;
    if (!stops || !Array.isArray(data.directions)) return null;
    return data.directions.find(d => d.next_stops.some(s => stops.includes(s))) || null;
  }

  // "Platform 1", "Platforms 6, 7, 8", or the platforms' own names
  function platformsText(names) {
    const bare = names.map(name => /^Platform (\S+)$/.exec(name));
    if (bare.every(Boolean)) return (names.length === 1 ? 'Platform ' : 'Platforms ') + bare.map(m => m[1]).join(', ');
    return names.join(', ');
  }
  const directionName = d => (d.towards.length ? d.towards : d.next_stops).join(' · ');

  // --- the menu --------------------------------------------------------------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const shade = el('div');
  shade.id = 'view-shade';
  shade.hidden = true;
  const menu = el('div');
  menu.id = 'view-menu';
  menu.hidden = true;
  menu.setAttribute('role', 'dialog');
  menu.setAttribute('aria-modal', 'true');
  menu.setAttribute('aria-labelledby', 'view-menu-title');
  const top = el('div', 'view-menu-top');
  const title = el('h2', '', 'Show departures');
  title.id = 'view-menu-title';
  const close = el('button', 'view-menu-close', '✕');
  close.type = 'button';
  close.setAttribute('aria-label', 'Close');
  top.append(title, close);
  const body = el('div', 'view-menu-body');
  menu.append(top, body);
  document.body.append(shade, menu);

  let shownFor = null;   // {data, badge, anchor}: what the menu was opened with
  let showAll = false;   // all directions listed

  function closeMenu() {
    if (menu.hidden) return;
    menu.hidden = true;
    shade.hidden = true;
    shownFor = null;
    // back to the button (drawn anew in the meantime, perhaps)
    const button = document.querySelector('#station .view-button');
    if (button) button.focus({ preventScroll: true });
  }
  close.onclick = closeMenu;
  // a tap beside the menu closes it, and goes no further (not to the map)
  shade.addEventListener('pointerdown', (e) => { e.preventDefault(); e.stopPropagation(); });
  shade.addEventListener('click', (e) => { e.stopPropagation(); closeMenu(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !menu.hidden) closeMenu(); });
  window.addEventListener('resize', closeMenu);
  // the menu belongs to one stop
  document.addEventListener('stop-picked', closeMenu);
  document.addEventListener('stop-cleared', closeMenu);

  // under the button, as wide as there is room for; scrolls when the screen is low
  function place(anchor) {
    const box = anchor.getBoundingClientRect();
    const width = Math.min(360, window.innerWidth - 24);
    menu.style.width = width + 'px';
    menu.style.left = Math.max(12, Math.min(box.right - width + 40, window.innerWidth - width - 12)) + 'px';
    let topAt = box.bottom + 6;
    if (window.innerHeight - topAt < 240) topAt = 12;
    menu.style.top = topAt + 'px';
    menu.style.maxHeight = (window.innerHeight - topAt - 12) + 'px';
  }

  const VIEW_TEXT = {
    platform: ['By platform', 'Grouped by platform'],
    time: ['By time', 'One list, soonest first, the platform on each row'],
    direction: ['My direction first', 'The platforms going your way at the top'],
  };

  function showViews() {
    const { data } = shownFor;
    title.textContent = 'Show departures';
    body.innerHTML = '';
    const current = view(data.stop.id, data.tab);
    VIEWS.forEach(name => {
      const option = el('button', name === current ? 'view-opt on' : 'view-opt');
      option.type = 'button';
      option.dataset.view = name;
      option.setAttribute('aria-pressed', String(name === current));
      option.appendChild(el('span', `view-icon view-${name}`));
      const text = el('span', 'view-opt-text');
      text.appendChild(el('b', '', VIEW_TEXT[name][0]));
      const chosen = name === 'direction' && current === 'direction' && chosenOf(data);
      text.appendChild(el('small', '', chosen ? `Towards ${directionName(chosen)}` : VIEW_TEXT[name][1]));
      option.appendChild(text);
      if (name === current) option.appendChild(el('span', 'view-check', '✓'));
      option.onclick = () => {
        // (a direction is chosen in the next step; only then does this stop show it first)
        if (name === 'direction') {
          showDirections();
          return;
        }
        setView(name, data.stop.id, data.tab);
        closeMenu();
      };
      body.appendChild(option);
    });
  }

  function showDirections() {
    const { data, badge } = shownFor;
    title.textContent = 'Which way are you going?';
    body.innerHTML = '';
    const directions = Array.isArray(data.directions) ? data.directions : [];
    const platforms = new Set(data.modes.flatMap(mode => mode.platforms.map(p => `${mode.id}|${p.name}`)));
    if (platforms.size < 2) {
      body.appendChild(el('p', 'view-note', 'All departures here leave from one platform, so there is nothing to put first.'));
    } else if (directions.length < 2) {
      body.appendChild(el('p', 'view-note', 'All departures here go the same way.'));
    } else {
      const chosen = chosenOf(data);
      const at = chosen ? directions.indexOf(chosen) : -1;
      const listed = showAll || at >= LISTED ? directions : directions.slice(0, LISTED);
      listed.forEach(d => {
        const on = d === chosen;
        const option = el('button', on ? 'view-dir on' : 'view-dir');
        option.type = 'button';
        option.setAttribute('aria-pressed', String(on));
        option.appendChild(el('b', '', directionName(d)));
        const sub = el('span', 'view-dir-sub');
        // one badge per line (long-distance trains by their kind: "ICE", "IC/EC")
        const seen = new Set();
        const badges = [];
        d.lines.forEach(line => {
          const made = badge(line);
          if (!seen.has(made.textContent)) { seen.add(made.textContent); badges.push(made); }
        });
        badges.slice(0, BADGES).forEach(b => sub.appendChild(b));
        if (badges.length > BADGES) sub.appendChild(el('span', 'view-more-lines', '…'));
        sub.appendChild(el('span', 'view-dir-platforms', platformsText(d.platforms)));
        option.appendChild(sub);
        option.onclick = () => {
          choose(data.stop.id, data.tab, d.next_stops);
          closeMenu();
        };
        body.appendChild(option);
      });
      if (listed.length < directions.length) {
        const more = el('button', 'view-link', `Show ${directions.length - listed.length} more directions`);
        more.type = 'button';
        more.onclick = () => { showAll = true; showDirections(); };
        body.appendChild(more);
      }
    }
    const back = el('button', 'view-link', '‹ Other views');
    back.type = 'button';
    back.onclick = showViews;
    body.appendChild(back);
    const first = body.querySelector('.view-dir.on, .view-dir, .view-link');
    if (first) first.focus({ preventScroll: true });
  }

  // `badge(line)`: the line's badge, as the board draws it (departures.js).
  // It opens on the three views; "My direction first" leads on to the directions.
  function openMenu(anchor, data, badge) {
    shownFor = { data, badge };
    showAll = false;
    showViews();
    shade.hidden = false;
    menu.hidden = false;
    place(anchor);
    menu.scrollTop = 0;
    const focus = menu.querySelector('.view-opt.on, .view-dir.on, .view-dir, .view-opt');
    if (focus) focus.focus({ preventScroll: true });
  }

  // keyboard: the focus stays inside the menu while it is open
  menu.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const items = [...menu.querySelectorAll('button')];
    const at = items.indexOf(document.activeElement);
    if (e.shiftKey && at <= 0) { e.preventDefault(); items[items.length - 1].focus(); }
    else if (!e.shiftKey && at === items.length - 1) { e.preventDefault(); items[0].focus(); }
  });

  window.views = {
    view,
    setView,
    direction,
    choose,
    chosenOf,
    openMenu,
    closeMenu,
    isOpen: () => !menu.hidden,
  };
})();
