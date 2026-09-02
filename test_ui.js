// Render the real index.html against a real /api/state payload and assert the
// board actually draws. A JS error here means a blank page on draft night.
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(process.argv[2], 'utf8');
const state = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

const errors = [];
const posted = [];

// Stubs must exist before the inline script parses, hence beforeParse.
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  beforeParse(w) {
    w.addEventListener('error', e => errors.push('window error: ' + e.message));
    w.console.error = (...a) => errors.push('console.error: ' + a.join(' '));
    w.fetch = (url, opts) => {
      if (opts && opts.method === 'POST') posted.push([url, opts.body]);
      if (url === '/api/state')
        return Promise.resolve({ ok: true, json: () => Promise.resolve(state) });
      return Promise.resolve({
        ok: true,
        text: () => Promise.resolve('{}'),
        json: () => Promise.resolve({}),
      });
    };
    w.EventSource = class { constructor() {} close() {} };
  },
});
const w = dom.window;

setTimeout(() => {
  const d = w.document;
  const rows = d.querySelectorAll('tr.p');
  const tiers = d.querySelectorAll('tr.tierhdr');
  const chips = d.querySelectorAll('.chip');
  const clock = d.getElementById('clock').textContent;
  const until = d.getElementById('until').textContent;
  const dot = d.getElementById('dot').className;
  const slotOpts = d.getElementById('slot').options.length;
  const foot = d.getElementById('foot').textContent;
  const tabs = [...d.querySelectorAll('nav button')].map(b => b.textContent);
  const firstName = rows[0] && rows[0].querySelector('td.nm').textContent.trim();
  const badges = [...d.querySelectorAll('.b')].map(b => b.textContent);

  console.log('player rows      :', rows.length);
  console.log('tier headers     :', tiers.length);
  console.log('chips            :', chips.length, [...chips].slice(0, 4).map(c => c.textContent));
  console.log('clock            :', JSON.stringify(clock));
  console.log('until            :', JSON.stringify(until.trim()));
  console.log('dot class        :', dot);
  console.log('slot options     :', slotOpts, '(1 blank + teams)');
  console.log('tabs             :', tabs.join(' '));
  console.log('first row        :', firstName);
  console.log('survival badges  :', [...new Set(badges)].join(','));
  console.log('footer           :', foot);
  console.log('banner shown     :', !!d.getElementById('banner').textContent.trim());
  console.log('JS ERRORS        :', errors.length ? errors : 'none');

  // Exercise a position tab to make sure re-render works.
  [...d.querySelectorAll('nav button')].find(b => b.textContent === 'TE').click();
  const teRows = d.querySelectorAll('tr.p');
  const allTE = [...teRows].every(r => /TE ·/.test(r.querySelector('td.nm').textContent));
  console.log('after TE tab     :', teRows.length, 'rows, all TE:', allTE);

  // Clicking a row must cross a player off; clicking + must claim him.
  [...d.querySelectorAll('nav button')].find(b => b.textContent === 'ALL').click();
  d.querySelector('tr.p').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  d.querySelector('tr.p td.act').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  console.log('POSTs from clicks:', JSON.stringify(posted));

  // Recent-picks feed
  const feed = d.querySelectorAll('.fp');
  console.log('feed entries     :', feed.length, '| mine:', d.querySelectorAll('.fp.me').length);
  console.log('age label        :', JSON.stringify(d.getElementById('age').textContent));

  // Need weighting: default is weighted, which drops tier bands on purpose
  // and pushes covered positions down without removing them.
  const tagged = [...d.querySelectorAll('.tag')].map(t => t.textContent);
  console.log('need tags        :', [...new Set(tagged)].join(',') || '(none)');
  const posOf = list => { const r = list.find(x => /QB ·/.test(x.textContent));
                          return r ? list.indexOf(r) : -1; };
  const qbWeighted = posOf([...d.querySelectorAll('tr.p')]);
  const bandedWeighted = d.querySelectorAll('tr.tierhdr').length;

  d.getElementById('mode').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const qbBoard = posOf([...d.querySelectorAll('tr.p')]);
  const bandedBoard = d.querySelectorAll('tr.tierhdr').length;
  console.log('tier bands       : weighted', bandedWeighted, '/ board', bandedBoard);
  console.log('first QB row     : weighted', qbWeighted, '/ board', qbBoard);
  d.getElementById('mode').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));

  // Scarcity: a vanishing tier must surface both as a chip and on the row,
  // and must disappear entirely in raw Board order.
  const scarceBadges = d.querySelectorAll('.tag.sc').length;
  const scarceChips = d.querySelectorAll('.chip.scarce').length;
  d.getElementById('mode').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const scarceInBoard = d.querySelectorAll('.tag.sc').length;
  d.getElementById('mode').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  console.log('scarcity         : badges', scarceBadges, '| chips', scarceChips,
              '| in board mode', scarceInBoard);

  // Scarcity may promote a position I still need -- that's the feature. And
  // late on, when starters are filled, the best available legitimately *are*
  // depth players. The invariant is relative: a covered position must sit
  // lower than it would on the raw board, never higher.
  const wRows = [...d.querySelectorAll('tr.p')];
  const depthRow = wRows.find(r => r.classList.contains('dep'));
  let demoted = false;
  if (!depthRow) {
    console.log('depth demoted    : SKIP - no depth rows in this fixture');
    demoted = true;
  } else {
    const id = depthRow.dataset.id;
    const wIdx = wRows.indexOf(depthRow);
    d.getElementById('mode').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    const bIdx = [...d.querySelectorAll('tr.p')].findIndex(r => r.dataset.id === id);
    d.getElementById('mode').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    // bIdx must resolve, or the check is vacuous.
    demoted = bIdx >= 0 && wIdx >= bIdx;
    console.log('depth demoted    : id', id, '| board', bIdx, '-> weighted', wIdx);
  }

  const ok = rows.length > 0
    && bandedWeighted === 0 && bandedBoard > 0
    && demoted
    && feed.length > 0
    && scarceBadges > 0 && scarceChips > 0 && scarceInBoard === 0
    && errors.length === 0;
  console.log(ok ? '\nPASS' : '\nFAIL');
  process.exit(ok ? 0 : 1);
}, 400);
