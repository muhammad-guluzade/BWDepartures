// How a stop's departures are shown: by platform (as always), by time (one
// list, soonest first), or "My direction first" (the platforms of the way the
// visitor goes at the top). The view, and the direction chosen at each stop,
// are kept in this browser (each device has its own); if the browser keeps
// nothing (a private window, blocked site data), as long as the page is open.
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

  function view() {
    const stored = read(VIEW);
    return VIEWS.includes(stored) ? stored : 'platform';
  }
  function setView(name) {
    if (!VIEWS.includes(name)) return;
    write(VIEW, name);
    document.dispatchEvent(new CustomEvent('view-changed'));
  }

  // {"<stop id>|<tab>": [next stops]}: a direction is remembered by the next
  // stops of its departures (the API's `next_stops`), not by platform numbers,
  // which can change from one day to the next
  function chosenAll() {
    const stored = read(DIRECTIONS);
    return stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {};
  }
  const placeOf = (stopId, tab) => `${stopId}|${tab || ''}`;
  function direction(stopId, tab) {
    const stops = chosenAll()[placeOf(stopId, tab)];
    return Array.isArray(stops) && stops.length && stops.every(s => typeof s === 'string') ? stops : null;
  }
  function choose(stopId, tab, nextStops) {
    const all = chosenAll();
    const place = placeOf(stopId, tab);
    delete all[place];           // (to the end: the newest)
    all[place] = nextStops.slice();
    const places = Object.keys(all);
    places.slice(0, Math.max(0, places.length - KEPT)).forEach(p => delete all[p]);
    write(DIRECTIONS, all);
    document.dispatchEvent(new CustomEvent('view-changed'));
  }
  // another tab of this site changed them
  window.addEventListener('storage', (e) => {
    if (e.key === VIEW || e.key === DIRECTIONS) document.dispatchEvent(new CustomEvent('view-changed'));
  });

  // the direction of a board that the chosen next stops belong to, or null
  function chosenOf(data) {
    const stops = direction(data.stop.id, data.tab);
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
    const current = view();
    VIEWS.forEach(name => {
      const option = el('button', name === current ? 'view-opt on' : 'view-opt');
      option.type = 'button';
      option.dataset.view = name;
      option.setAttribute('aria-pressed', String(name === current));
      option.appendChild(el('span', `view-icon view-${name}`));
      const text = el('span', 'view-opt-text');
      text.appendChild(el('b', '', VIEW_TEXT[name][0]));
      const chosen = name === 'direction' && chosenOf(data);
      text.appendChild(el('small', '', chosen ? `Towards ${directionName(chosen)}` : VIEW_TEXT[name][1]));
      option.appendChild(text);
      if (name === current) option.appendChild(el('span', 'view-check', '✓'));
      option.onclick = () => {
        if (name === 'direction') {
          if (current !== 'direction') setView('direction');
          showDirections();
          return;
        }
        setView(name);
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
