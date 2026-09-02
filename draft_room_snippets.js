/* Paste one of these into the ESPN draft room's DevTools console (Cmd+Opt+J).
 * They only read the DOM; nothing is modified in the draft.
 *
 * Set MY_TEAM to your team name exactly as the draft room prints it, or your
 * own picks are only crossed off, not tracked as yours.
 *
 * Stop either one with:  clearInterval(window.__dov)
 */

// ---------------------------------------------------------------------------
// A. DIRECT  -- needs chrome://flags/#block-insecure-private-network-requests
//    set to Disabled (then relaunch Chrome). No clipboard hijacking.
// ---------------------------------------------------------------------------
window.__dov && clearInterval(window.__dov);
{
  const MY_TEAM = 'PUT YOUR TEAM NAME HERE';
  const PORT = 8777;                       // 8777 = real league board
  const rows = () => [...document.querySelectorAll('[class*="pick__message"]')];
  const nameOf = li => (li.querySelector('.playerinfo__playername') || {}).textContent?.trim();
  window.__dov = setInterval(async () => {
    const all = rows();
    const names = all.map(nameOf).filter(Boolean);
    const mine = all.filter(li => li.textContent.includes(MY_TEAM)).map(nameOf).filter(Boolean);
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/api/scrape`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ names, mine }),
      });
      const j = await r.json();
      console.log(`pushed ${names.length} (mine ${mine.length}) -> matched ${j.matched}`,
                  j.unmatched.length ? `| unmatched: ${j.unmatched.join(', ')}` : '');
    } catch (e) { console.warn('bridge down:', e.message); }
  }, 2000);
  console.log('direct bridge running');
}

// ---------------------------------------------------------------------------
// B. CLIPBOARD -- works with no browser settings changed. Pair with:
//       python3 clipboard_bridge.py
//    The draft tab must stay focused or the clipboard API throws.
//    NOTE: console's copy() does NOT work inside setInterval; use this instead.
// ---------------------------------------------------------------------------
window.__dov && clearInterval(window.__dov);
{
  const MY_TEAM = 'PUT YOUR TEAM NAME HERE';
  const rows = () => [...document.querySelectorAll('[class*="pick__message"]')];
  const nameOf = li => (li.querySelector('.playerinfo__playername') || {}).textContent?.trim();
  window.__dov = setInterval(async () => {
    const all = rows();
    const names = all.map(nameOf).filter(Boolean);
    const mine = all.filter(li => li.textContent.includes(MY_TEAM)).map(nameOf).filter(Boolean);
    try {
      await navigator.clipboard.writeText(JSON.stringify({ names, mine }));
      console.log(`copied ${names.length}, mine ${mine.length}`);
    } catch (e) { console.warn('clipboard blocked:', e.message); }
  }, 3000);
  console.log('clipboard bridge running');
}

// ---------------------------------------------------------------------------
// C. RE-DISCOVER selectors, if ESPN changes its markup before the draft.
//    Prints the DOM containers holding your board's player names, ranked by
//    hit count. The pick history is normally the second-largest group.
// ---------------------------------------------------------------------------
{
  const names = (await (await fetch('http://127.0.0.1:8777/api/names')).json()).names;
  const norm = s => s.toLowerCase().normalize('NFKD')
    .replace(/[^a-z0-9 ]/g, '').replace(/\b(jr|sr|ii|iii|iv|v)\b/g, '').trim();
  const want = new Set(names.map(norm));
  const hits = [];
  document.querySelectorAll('*').forEach(el => {
    if (el.children.length) return;
    if (want.has(norm(el.textContent || ''))) hits.push(el);
  });
  const sig = el => {
    const p = []; let n = el;
    for (let i = 0; i < 5 && n; i++, n = n.parentElement)
      p.push(n.tagName.toLowerCase() + (typeof n.className === 'string' && n.className
        ? '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.') : ''));
    return p.join(' < ');
  };
  const g = {};
  hits.forEach(h => (g[sig(h)] ||= []).push(h.textContent.trim()));
  console.log('total hits:', hits.length);
  Object.entries(g).sort((a, b) => b[1].length - a[1].length).slice(0, 6)
    .forEach(([k, v]) => console.log(`\n[${v.length}] ${k}\n   e.g. ${v.slice(0, 4).join(' | ')}`));
}
