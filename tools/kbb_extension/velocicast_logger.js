/* Логгер торгов Velocicast (CarMax) — тот же скрипт, что в навыке Claude «velocicast-auction-logger», без изменений (только символы U+FFFE / U+FFFF в одном регулярном выражении записаны как \uFFFE / \uFFFF:
   Chrome не загружает файл расширения с такими символами).
   Расширение (auction_join.js) запускает его на вкладке Velocicast в мире страницы (world: MAIN) и каждые 15 минут
   забирает window.__AUC.combinedCSV() в программу. Повторный запуск безопасен: идущая запись сохраняется. */
/* Velocicast / CarMax Auctions live logger + Excel exporter.
 * Inject once into the open Velocicast tab (carmax.velocicast.io) with all lanes open.
 * Idempotent: re-injecting keeps the running logger and only (re)installs the exporter/UI.
 * Data lives in window.__AUC (log, vlist, es) and is mirrored to localStorage every 30 s.
 */
(() => {
  const A = (window.__AUC = window.__AUC || {});
  const iso = () => new Date().toISOString();
  const T = (el) => (el ? el.innerText.replace(/\s+/g, ' ').trim() : '');

  // ---------------------------------------------------------------- restore after reload
  if (!A.log) {
    A.log = []; A.lanes = {}; A.vlist = {}; A.es = [];
    try {
      const saved = JSON.parse(localStorage.getItem('__AUC_log') || '[]');
      const last = saved.length ? Date.parse(saved[saved.length - 1][0]) : 0;
      if (saved.length && Date.now() - last < 3 * 3600e3) {
        A.log = saved;
        A.vlist = JSON.parse(localStorage.getItem('__AUC_vlist') || '{}');
        A.log.push([iso(), '*', 'RESTORED', '', String(saved.length)]);
      }
    } catch (e) { A.restoreErr = String(e); }
  }
  A.lanes = A.lanes || {}; A.vlist = A.vlist || {}; A.es = A.es || [];

  // ---------------------------------------------------------------- DOM logger
  if (!A.running) {
    A.running = true; A.started = A.started || iso();
    const laneOf = (p) => T(p.querySelector('.lane-title-bar__name'));
    const scan = (p) => {
      const lane = laneOf(p); if (!lane) return;
      const L = (A.lanes[lane] = A.lanes[lane] || { item: null, state: null, hist: [] });
      const item = T(p.querySelector('.vc-itemnum'));
      const desc = T(p.querySelector('.vc-desc'));
      if (item && item !== L.item) { A.log.push([iso(), lane, 'ITEM', item, desc]); L.item = item; }
      const st = T(p.querySelector('.vc-bid-msg')) + ' | ' + T(p.querySelector('.vc-bid-amt'));
      if (st !== L.state) { A.log.push([iso(), lane, 'STATE', L.item, st]); L.state = st; }
      const hist = [...p.querySelectorAll('.vc-bidhist-bid li')].map(T);
      const old = L.hist;
      if (hist.join('\u0001') !== old.join('\u0001')) {
        // history list is newest-first; find how many new entries were prepended
        let k = -1;
        for (let i = 0; i <= hist.length; i++) {
          const tail = hist.slice(i);
          if (tail.length === old.length && tail.every((v, j) => v === old[j])) { k = i; break; }
          if (tail.length < old.length) break;
        }
        if (k === -1) { A.log.push([iso(), lane, 'HRESET', L.item, String(hist.length)]); k = hist.length; }
        for (let i = k - 1; i >= 0; i--) A.log.push([iso(), lane, 'BID', L.item, hist[i]]);
        L.hist = hist;
      }
    };
    const scanVList = (p) => {
      const lane = laneOf(p); if (!lane) return;
      const V = (A.vlist[lane] = A.vlist[lane] || {});
      p.querySelectorAll('.lane-content li, .lane-content tr').forEach((r) => {
        const t = T(r); if (!/VIN:/.test(t)) return;
        const m = t.match(/^#(\d+)-\s*(.+?)\s+VIN:\s*(\S+)\s+(.*?)\s+([\d,]+)\s*M\s+(.*)$/);
        if (m) V[m[1]] = { n: +m[1], desc: m[2], vin: m[3], color: m[4], miles: m[5], status: m[6] };
      });
    };
    let pending = false;
    const panels = () => document.querySelectorAll('.lane-panel-container');
    const scanAll = () => { pending = false; panels().forEach((p) => { try { scan(p); } catch (e) { A.err = String(e); } }); };
    const mo = new MutationObserver(() => { if (!pending) { pending = true; setTimeout(scanAll, 60); } });
    const attach = () => panels().forEach((p) => { if (!p.__aucObs) { p.__aucObs = 1; mo.observe(p, { subtree: true, childList: true, characterData: true }); } });
    attach(); scanAll();
    A.iv1 = setInterval(() => { attach(); scanAll(); }, 1000);
    const vl = () => panels().forEach((p) => { try { scanVList(p); } catch (e) { A.err2 = String(e); } });
    vl(); A.iv2 = setInterval(vl, 15000);
    A.iv3 = setInterval(() => {
      try { localStorage.setItem('__AUC_log', JSON.stringify(A.log)); localStorage.setItem('__AUC_vlist', JSON.stringify(A.vlist)); A.persistOk = iso(); A.persistErr = null; }
      catch (e) { A.persistErr = String(e); }
    }, 30000);
  }

  // ---------------------------------------------------------------- WebSocket (socket.io EventStats) backup
  if (!A.wsCompact) {
    A.wsCompact = true; A.wsTypes = A.wsTypes || {};
    const cur = Object.getOwnPropertyDescriptor(MessageEvent.prototype, 'data');
    Object.defineProperty(MessageEvent.prototype, 'data', {
      configurable: true, enumerable: true,
      get() {
        const v = cur.get.call(this);
        try {
          if (!this.__aucC && typeof v === 'string' && v.startsWith('42["message"')) {
            this.__aucC = 1;
            const tm = v.match(/^42\["message",\{"type":"(\w+)"/); const ty = tm ? tm[1] : '?';
            A.wsTypes[ty] = (A.wsTypes[ty] || 0) + 1;
            if (ty === 'EventStats') {
              const s = JSON.parse(v.slice(2))[1].doc.Stats;
              // [ms, eventId, itemId, bidId, amount, badge, dealer, bidType, status, laneLetter]
              A.es.push([Date.now(), s.ID, s.CurrentItemID, s.CurrentBidID, s.CurrentBidAmount, s.CurrentBidBadge, s.CurrentBidDealer, s.CurrentBidType, s.Status, s.EventName]);
            }
          }
        } catch (e) { A.wsErr = String(e); }
        return v;
      },
    });
  }

  // ---------------------------------------------------------------- build report
  const num = (s) => (s == null ? null : +String(s).replace(/[^\d.]/g, '') || 0);
  const money = (n) => (n == null ? '' : '$' + Number(n).toLocaleString('en-US'));
  const tLocal = (t) => { const d = new Date(t); return isNaN(d) ? '' : d.toLocaleTimeString('ru-RU'); };
  const parseRow = (text) => {
    let m;
    if ((m = text.match(/^(\S+) - (.+) - \$([\d,]+)$/))) return { kind: 'Дилер', badge: m[1], name: m[2], amt: num(m[3]) };
    if ((m = text.match(/^Floor\b.*?\$\s?([\d,]+)/i))) return { kind: 'Floor', amt: num(m[1]) };
    if ((m = text.match(/^ASKING\b.*?\$\s?([\d,]+)/i))) return { kind: 'Asking', amt: num(m[1]) };
    if ((m = text.match(/^SOLD for \$\s?([\d,]+)/i))) return { kind: 'Продано', amt: num(m[1]) };
    if ((m = text.match(/increment:\s*\$\s?([\d,]+)/i))) return { kind: 'Шаг торгов', amt: num(m[1]) };
    return { kind: 'Сообщение', text };
  };
  const splitLane = (lane) => { const m = lane.match(/^Lane\s+(\S+)\s+-\s+(.+)$/); return m ? { loc: m[2], lane: m[1] } : { loc: lane, lane: '' }; };
  const splitDesc = (d) => { const m = (d || '').match(/^(\d{4})\s+(.+)$/); return m ? { year: +m[1], model: m[2] } : { year: '', model: d || '' }; };

  // ---------------------------------------------------------------- auction locations: state + distance from ORIGIN
  // Key = auction name without "CarMax" / "Auction Center", lowercase.
  // [city, state, lat, lon, approx. driving miles from ORIGIN (estimate, ±10%; null = unknown)]
  const ORIGIN = { name: 'Irvine, CA 92620', lat: 33.7106, lon: -117.7636 };
  const AUCTIONS = {
    'chino': ['Chino', 'CA', 34.0122, -117.6889, 25],
    'oxnard': ['Oxnard', 'CA', 34.1975, -119.1771, 90],
    'murrieta': ['Murrieta', 'CA', 33.5539, -117.2139, 50],
    'oceanside': ['Oceanside', 'CA', 33.1959, -117.3795, 45],
    'las vegas': ['North Las Vegas', 'NV', 36.1989, -115.1175, 270],
    'fresno': ['Fresno', 'CA', 36.7378, -119.7871, 250],
    'el mirage': ['El Mirage', 'AZ', 33.6131, -112.3246, 345],
    'fremont': ['Fremont', 'CA', 37.5485, -121.9886, 390],
    'pleasanton': ['Pleasanton', 'CA', 37.6624, -121.8747, 400],
    'sacramento': ['Sacramento', 'CA', 38.5816, -121.4944, 420],
    'roseville': ['Roseville', 'CA', 38.7521, -121.288, 435],
    'albuquerque': ['Albuquerque', 'NM', 35.0844, -106.6504, 730],
    'el paso': ['El Paso', 'TX', 31.7619, -106.485, 780],
    'san marcos': ['San Marcos', 'TX', 29.8833, -97.9414, 1300],
    'conroe': ['Conroe', 'TX', 30.3119, -95.4561, 1480],
    'des moines': ['Des Moines', 'IA', 41.5868, -93.625, 1680],
    'brooklyn park': ['Brooklyn Park', 'MN', 45.0941, -93.3563, 1900],
    'tuscaloosa': ['Tuscaloosa', 'AL', 33.2098, -87.5692, 1950],
    'new kent': ['West Point', 'VA', 37.5315, -76.7964, 2650],
    'rosedale': ['Rosedale', 'MD', 39.3201, -76.5155, 2660],
  };
  const STATE_RU = { AL: 'Алабама', AZ: 'Аризона', CA: 'Калифорния', IA: 'Айова', MD: 'Мэриленд', MN: 'Миннесота', NM: 'Нью-Мексико', NV: 'Невада', TX: 'Техас', VA: 'Вирджиния' };
  const miles = (la1, lo1, la2, lo2) => {
    const r = (x) => (x * Math.PI) / 180, h = Math.sin(r(la2 - la1) / 2) ** 2 + Math.cos(r(la1)) * Math.cos(r(la2)) * Math.sin(r(lo2 - lo1) / 2) ** 2;
    return Math.round(2 * 3958.8 * Math.asin(Math.sqrt(h)));
  };
  A.auctionInfo = (loc) => {
    const k = String(loc || '').replace(/^CarMax\s+/i, '').replace(/\s+Auction Center$/i, '').trim().toLowerCase();
    const a = AUCTIONS[k];
    if (!a) return { city: '', state: '', straight: null, road: null };
    return { city: a[0], state: a[1], straight: miles(ORIGIN.lat, ORIGIN.lon, a[2], a[3]), road: a[4] };
  };
  A.auctionList = () => Object.entries(AUCTIONS)
    .map(([k, a]) => { const s = miles(ORIGIN.lat, ORIGIN.lon, a[2], a[3]); return [k.replace(/\b\w/g, (c) => c.toUpperCase()), a[0], a[1], STATE_RU[a[1]] || '', s, Math.round(s * 1.609344), a[4]]; })
    .sort((x, y) => x[4] - y[4]);

  A.build = function () {
    const groups = new Map();
    const getG = (lane, item) => {
      const k = lane + '|' + item; let g = groups.get(k);
      if (!g) { g = { lane, item, n: num(item), desc: '', rows: [], states: [], seen: new Set(), t0: null }; groups.set(k, g); }
      return g;
    };
    const laneBadge = {}, globalBadge = {};
    for (const [t, lane, type, item, text] of A.log) {
      if (!item || lane === '*') continue;
      const g = getG(lane, item);
      if (!g.t0) g.t0 = t;
      if (type === 'ITEM') g.desc = g.desc || text;
      else if (type === 'STATE') g.states.push([t, text]);
      else if (type === 'BID') {
        if (!text || g.seen.has(text)) continue;
        g.seen.add(text);
        const p = parseRow(text); p.t = t; p.text = text; g.rows.push(p);
        if (p.badge) { (laneBadge[lane] = laneBadge[lane] || {})[p.badge] = p.name; if (!globalBadge[p.badge]) globalBadge[p.badge] = p.name; }
      }
    }
    for (const e of A.es || []) if (e[5] && e[6] && !globalBadge[String(e[5])]) globalBadge[String(e[5])] = e[6];

    // union of vehicle list + logged items
    const keys = new Map();
    for (const [lane, V] of Object.entries(A.vlist || {})) for (const v of Object.values(V)) if (v && v.n) keys.set(lane + '|#' + v.n, { lane, n: v.n });
    for (const g of groups.values()) if (g.n) keys.set(g.lane + '|' + g.item, { lane: g.lane, n: g.n });
    const ordered = [...keys.values()].sort((a, b) => (a.lane < b.lane ? -1 : a.lane > b.lane ? 1 : a.n - b.n));

    const summary = [], bids = [], dealerAgg = {};
    for (const { lane, n } of ordered) {
      const g = groups.get(lane + '|#' + n);
      const v = (A.vlist[lane] || {})[n] || {};
      const { loc, lane: ln } = splitLane(lane);
      const ai = A.auctionInfo(loc);
      const desc = v.desc || (g && g.desc) || '';
      const { year, model } = splitDesc(desc);
      const vstat = v.status || '';
      const dealerBids = g ? g.rows.filter((r) => r.kind === 'Дилер') : [];
      let sold = null, lastState = '';
      if (g) for (const [t, s] of g.states) {
        const m = s.match(/SOLD to (.+?) \| \$?([\d,]*)/i);
        if (m) sold = { badge: m[1].trim(), price: num(m[2]), t };
        if (s && !/^Finalizing/i.test(s) && !/^\s*\|\s*$/.test(s)) lastState = s;
      }
      const soldRow = g && g.rows.find((r) => r.kind === 'Продано');
      const vSold = vstat.match(/^Sold\s*\$?([\d,]+)/i);
      const isSold = !!(sold || soldRow || vSold);
      let result = 'Не выставлялась / нет данных';
      if (isSold) result = 'Продано';
      else if (/no sale/i.test(vstat)) result = 'Не продано';
      else if (/if|pending|approval/i.test(vstat)) result = vstat;
      else if (g && dealerBids.length && !/^#/.test(lastState)) result = 'Не продано / нет финала';
      const price = (sold && sold.price) || (soldRow && soldRow.amt) || (vSold && num(vSold[1])) || null;
      let badge = sold ? sold.badge : '', buyerNote = '';
      if (isSold && !badge && dealerBids.length) { badge = dealerBids[dealerBids.length - 1].badge; buyerNote = 'по последней ставке'; }
      if (isSold && !badge) buyerNote = g ? 'нет данных' : 'прошла до запуска логгера';
      let buyer = '';
      if (badge) {
        const inLot = [...dealerBids].reverse().find((r) => r.badge === badge);
        buyer = (inLot && inLot.name) || (laneBadge[lane] || {})[badge] || globalBadge[badge] || '';
        if (!buyer && /you/i.test(badge)) buyer = 'ВЫ';
      }
      const high = dealerBids.length ? Math.max(...dealerBids.map((r) => r.amt)) : null;
      const under = [...dealerBids].reverse().find((r) => r.badge !== badge);
      const floors = g ? g.rows.filter((r) => r.kind === 'Floor') : [];
      const asks = g ? g.rows.filter((r) => r.kind === 'Asking') : [];
      const ladder = dealerBids.map((r) => `${money(r.amt)} ${r.name} (#${r.badge})`).join(' → ');
      const bidders = new Set(dealerBids.map((r) => r.badge)).size;
      if (price && sold && vSold && num(vSold[1]) !== price) buyerNote = (buyerNote ? buyerNote + '; ' : '') + `в списке ${money(num(vSold[1]))}`;
      summary.push([loc, ai.state, ai.straight, ai.road, ln, n, year, model, v.vin || '', v.color || '', v.miles ? num(v.miles) : null, result, price, badge, buyer, buyerNote,
        under ? under.name : '', under ? under.amt : null, high, g ? dealerBids.length : null, g ? bidders : null,
        asks.length ? asks[0].amt : null, floors.length ? floors[floors.length - 1].amt : null,
        sold ? tLocal(sold.t) : '', ladder]);
      if (g) g.rows.forEach((r, i) => bids.push([loc, ai.state, ln, n, year, model, v.vin || '', i + 1, tLocal(r.t), r.kind, r.badge || '', r.name || '', r.amt == null ? null : r.amt, r.kind === 'Сообщение' ? r.text : '']));
      // dealer aggregation
      for (const r of dealerBids) {
        const d = (dealerAgg[r.name] = dealerAgg[r.name] || { badges: new Set(), lots: new Set(), bids: 0, won: 0, spent: 0 });
        d.badges.add(r.badge); d.lots.add(lane + n); d.bids++;
      }
      if (isSold && buyer) {
        const d = (dealerAgg[buyer] = dealerAgg[buyer] || { badges: new Set(), lots: new Set(), bids: 0, won: 0, spent: 0 });
        d.won++; d.spent += price || 0; if (badge) d.badges.add(badge);
      }
    }
    const dealers = Object.entries(dealerAgg)
      .map(([name, d]) => [name, [...d.badges].join(', '), d.won, d.spent || null, d.won ? Math.round(d.spent / d.won) : null, d.lots.size, d.bids])
      .sort((a, b) => b[2] - a[2] || (b[3] || 0) - (a[3] || 0) || b[6] - a[6]);
    return { summary, bids, dealers };
  };

  // ---------------------------------------------------------------- minimal XLSX writer (no deps)
  const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
  const crc32 = (b) => { let c = 0xffffffff; for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const zipStore = (files) => {
    const enc = new TextEncoder(); const parts = [], central = []; let off = 0;
    for (const f of files) {
      const nm = enc.encode(f.name), data = typeof f.data === 'string' ? enc.encode(f.data) : f.data, crc = crc32(data), sz = data.length;
      const lh = new DataView(new ArrayBuffer(30));
      lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true); lh.setUint16(12, 0x21, true);
      lh.setUint32(14, crc, true); lh.setUint32(18, sz, true); lh.setUint32(22, sz, true); lh.setUint16(26, nm.length, true);
      parts.push(new Uint8Array(lh.buffer), nm, data);
      const ch = new DataView(new ArrayBuffer(46));
      ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true); ch.setUint16(8, 0x0800, true); ch.setUint16(14, 0x21, true);
      ch.setUint32(16, crc, true); ch.setUint32(20, sz, true); ch.setUint32(24, sz, true); ch.setUint16(28, nm.length, true); ch.setUint32(42, off, true);
      central.push(new Uint8Array(ch.buffer), nm);
      off += 30 + nm.length + sz;
    }
    const cd = central.reduce((a, b) => a + b.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true); end.setUint32(12, cd, true); end.setUint32(16, off, true);
    return [...parts, ...central, new Uint8Array(end.buffer)];
  };
  const esc = (s) => String(s).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const col = (i) => { let s = ''; i++; while (i > 0) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); } return s; };
  const sheetXml = (sh) => {
    const out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'];
    sh.cols.forEach((c, i) => out.push(`<col min="${i + 1}" max="${i + 1}" width="${c.w || 12}" customWidth="1"/>`));
    out.push('</cols><sheetData>');
    const cell = (v, r, c, s) => {
      if (v === null || v === undefined || v === '') return '';
      const ref = col(c) + r, st = s ? ` s="${s}"` : '';
      if (typeof v === 'number' && isFinite(v)) return `<c r="${ref}"${st}><v>${v}</v></c>`;
      return `<c r="${ref}" t="inlineStr"${st}><is><t xml:space="preserve">${esc(v)}</t></is></c>`;
    };
    out.push('<row r="1">' + sh.cols.map((c, i) => cell(c.h, 1, i, 1)).join('') + '</row>');
    sh.rows.forEach((row, ri) => out.push(`<row r="${ri + 2}">` + row.map((v, ci) => cell(v, ri + 2, ci, sh.cols[ci].f || 0)).join('') + '</row>'));
    out.push('</sheetData>');
    const ref = `A1:${col(sh.cols.length - 1)}${sh.rows.length + 1}`;
    out.push(`<autoFilter ref="${ref}"/></worksheet>`);
    sh.ref = ref;
    return out.join('');
  };
  A.xlsxParts = (sheets) => {
    const NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main', R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
    const xmls = sheets.map(sheetXml);
    const styles = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="${NS}"><numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFDDE7F5"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;
    const wb = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="${NS}" xmlns:r="${R}"><sheets>${sheets.map((s, i) => `<sheet name="${esc(s.name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join('')}</sheets><definedNames>${sheets.map((s, i) => `<definedName name="_xlnm._FilterDatabase" localSheetId="${i}" hidden="1">'${esc(s.name)}'!${s.ref.replace(/([A-Z]+)(\d+)/g, '$$$1$$$2')}</definedName>`).join('')}</definedNames></workbook>`;
    const wbRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((s, i) => `<Relationship Id="rId${i + 1}" Type="${R}/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join('')}<Relationship Id="rId${sheets.length + 1}" Type="${R}/styles" Target="styles.xml"/></Relationships>`;
    const ct = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>${sheets.map((s, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`;
    const rels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${R}/officeDocument" Target="xl/workbook.xml"/></Relationships>`;
    return zipStore([
      { name: '[Content_Types].xml', data: ct }, { name: '_rels/.rels', data: rels },
      { name: 'xl/workbook.xml', data: wb }, { name: 'xl/_rels/workbook.xml.rels', data: wbRels }, { name: 'xl/styles.xml', data: styles },
      ...xmls.map((x, i) => ({ name: `xl/worksheets/sheet${i + 1}.xml`, data: x })),
    ]);
  };

  const M = 2, N = 3; // style ids: money, thousands
  A.sheets = function () {
    const { summary, bids, dealers } = A.build();
    return [
      { name: 'Итоги', rows: summary, cols: [
        { h: 'Аукцион', w: 22 }, { h: 'Штат', w: 6 }, { h: 'По прямой от 92620, mi', w: 10, f: N }, { h: 'По дороге ≈, mi', w: 10, f: N }, { h: 'Лейн', w: 6 }, { h: 'Лот #', w: 7 }, { h: 'Год', w: 6 }, { h: 'Марка / модель', w: 34 }, { h: 'VIN', w: 20 },
        { h: 'Цвет', w: 10 }, { h: 'Пробег, mi', w: 11, f: N }, { h: 'Результат', w: 16 }, { h: 'Цена продажи', w: 13, f: M },
        { h: 'Бейдж покупателя', w: 10 }, { h: 'Покупатель', w: 30 }, { h: 'Примечание', w: 22 },
        { h: '2-й участник (андербиддер)', w: 28 }, { h: 'Ставка андербиддера', w: 13, f: M }, { h: 'Макс. ставка дилера', w: 13, f: M },
        { h: 'Кол-во ставок', w: 9 }, { h: 'Кол-во дилеров', w: 9 }, { h: 'Asking (старт)', w: 12, f: M }, { h: 'Floor (последний)', w: 12, f: M },
        { h: 'Время продажи', w: 10 }, { h: 'Лесенка ставок', w: 120 } ] },
      { name: 'Ставки', rows: bids, cols: [
        { h: 'Аукцион', w: 22 }, { h: 'Штат', w: 6 }, { h: 'Лейн', w: 6 }, { h: 'Лот #', w: 7 }, { h: 'Год', w: 6 }, { h: 'Марка / модель', w: 30 }, { h: 'VIN', w: 20 },
        { h: 'Шаг №', w: 7 }, { h: 'Время', w: 10 }, { h: 'Тип', w: 12 }, { h: 'Бейдж', w: 8 }, { h: 'Дилер', w: 32 }, { h: 'Сумма', w: 11, f: M }, { h: 'Текст', w: 40 } ] },
      { name: 'Дилеры', rows: dealers, cols: [
        { h: 'Дилер', w: 34 }, { h: 'Бейдж(и)', w: 12 }, { h: 'Куплено машин', w: 10 }, { h: 'Потрачено', w: 13, f: M }, { h: 'Средняя цена', w: 13, f: M },
        { h: 'Лотов со ставками', w: 11 }, { h: 'Всего ставок', w: 10 } ] },
      { name: 'Аукционы', rows: A.auctionList(), cols: [
        { h: 'Аукцион', w: 16 }, { h: 'Город', w: 18 }, { h: 'Штат', w: 6 }, { h: 'Штат (полностью)', w: 16 },
        { h: 'По прямой от 92620, mi', w: 12, f: N }, { h: 'По прямой, km', w: 12, f: N }, { h: 'По дороге ≈, mi', w: 12, f: N } ] },
    ];
  };

  // one flat CSV: per car an "ИТОГ" row, then its "ставка" rows (car + result repeated for filtering)
  A.combinedCSV = function () {
    const [S, B] = A.sheets();
    const q = (v) => { if (v === null || v === undefined) return ''; const s = String(v); return /[",\r\n;]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    const byCar = new Map();
    for (const b of B.rows) { const k = b[0] + '|' + b[2] + '|' + b[3]; if (!byCar.has(k)) byCar.set(k, []); byCar.get(k).push(b); }
    const ID = 11; // loc, state, straight, road, lane, lot, year, model, vin, color, miles
    const rest = S.cols.length - ID; // 14
    const head = [...S.cols.slice(0, ID).map((c) => c.h), 'Строка', ...S.cols.slice(ID).map((c) => c.h), 'Шаг №', 'Время ставки', 'Тип ставки', 'Бейдж', 'Дилер', 'Сумма ставки', 'Текст'];
    const out = [head.map(q).join(',')];
    let nCars = 0, nBids = 0;
    for (const s of S.rows) {
      out.push([...s.slice(0, ID), 'ИТОГ', ...s.slice(ID), '', '', '', '', '', '', ''].map(q).join(',')); nCars++;
      for (const b of byCar.get(s[0] + '|' + s[4] + '|' + s[5]) || []) {
        out.push([...s.slice(0, ID), 'ставка', ...s.slice(ID, ID + 4), ...Array(rest - 4).fill(''), ...b.slice(7)].map(q).join(',')); nBids++;
      }
    }
    return { text: '﻿' + out.join('\r\n') + '\r\n', nCars, nBids };
  };
  A.downloadCombinedCSV = function () {
    const { text, nCars, nBids } = A.combinedCSV();
    const d = new Date(), p = (x) => String(x).padStart(2, '0');
    const name = `auction_all_${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}.csv`;
    const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 5000);
    return { name, kb: Math.round(blob.size / 1024), nCars, nBids };
  };

  A.download = function () {
    const parts = A.xlsxParts(A.sheets());
    const blob = new Blob(parts, { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const d = new Date(), p = (x) => String(x).padStart(2, '0');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `auction_results_${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}.xlsx`;
    document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 5000);
    return a.download;
  };

  A.stats = function () {
    const lots = new Set(), sold = new Set(); let bids = 0;
    for (const r of A.log) {
      if (r[2] === 'ITEM') lots.add(r[1] + r[3]);
      else if (r[2] === 'BID' && /^\S+ - .+ - \$/.test(r[4])) bids++;
      else if (r[2] === 'STATE' && /SOLD to/i.test(r[4])) sold.add(r[1] + r[3]);
    }
    const last = A.log.length ? A.log[A.log.length - 1][0] : null;
    return { lots: lots.size, sold: sold.size, bids, rows: A.log.length, last, lanes: Object.keys(A.lanes).length, persistErr: A.persistErr || null, err: A.err || null };
  };

  // ---------------------------------------------------------------- floating widget
  if (typeof document !== 'undefined' && !document.getElementById('auc-widget')) {
    const el = (tag, css, text) => { const e = document.createElement(tag); if (css) e.style.cssText = css; if (text) e.textContent = text; return e; };
    const w = el('div', 'position:fixed;right:12px;bottom:12px;z-index:2147483647;background:#111827;color:#f9fafb;font:12px/1.45 system-ui,-apple-system,sans-serif;padding:8px 10px;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.35);width:220px');
    w.id = 'auc-widget';
    const head = el('div', 'display:flex;justify-content:space-between;align-items:center;font-weight:600');
    const title = el('span', '', '● Логгер аукциона');
    const min = el('span', 'cursor:pointer;color:#9ca3af;padding:0 4px', '–');
    head.append(title, min);
    const body = el('div', 'margin-top:6px');
    const stats = el('div', 'color:#d1d5db;margin-bottom:6px;white-space:pre-line', '…');
    const btn = el('button', 'width:100%;padding:7px;border:0;border-radius:6px;background:#2563eb;color:#fff;font-weight:600;cursor:pointer;font-size:12px', 'Скачать итоги (Excel)');
    btn.onclick = () => { try { btn.textContent = 'Готовлю…'; const f = A.download(); btn.textContent = 'Скачано ✓'; setTimeout(() => (btn.textContent = 'Скачать итоги (Excel)'), 3000); A.lastDownload = f; } catch (e) { btn.textContent = 'Ошибка: ' + e.message; } };
    const reset = el('div', 'margin-top:6px;color:#6b7280;cursor:pointer;text-align:right;font-size:11px', 'новый аукцион (очистить)');
    reset.onclick = () => { if (confirm('Очистить все записанные данные логгера?')) { A.log.length = 0; A.es.length = 0; A.vlist = {}; for (const k in A.lanes) A.lanes[k] = { item: null, state: null, hist: [] }; try { localStorage.removeItem('__AUC_log'); localStorage.removeItem('__AUC_vlist'); } catch (e) {} } };
    const csv = el('button', 'width:100%;margin-top:6px;padding:7px;border:0;border-radius:6px;background:#059669;color:#fff;font-weight:600;cursor:pointer;font-size:12px', 'Скачать итоги (CSV)');
    csv.onclick = () => { try { A.downloadCombinedCSV(); csv.textContent = 'Скачано ✓'; setTimeout(() => (csv.textContent = 'Скачать итоги (CSV)'), 3000); } catch (e) { csv.textContent = 'Ошибка: ' + e.message; } };
    body.append(stats, btn, csv, reset);
    w.append(head, body);
    min.onclick = () => { const h = body.style.display === 'none'; body.style.display = h ? '' : 'none'; min.textContent = h ? '–' : '+'; };
    document.body.appendChild(w);
    const upd = () => {
      const s = A.stats();
      const age = s.last ? Math.round((Date.now() - Date.parse(s.last)) / 1000) : null;
      title.style.color = age != null && age < 120 ? '#34d399' : '#fbbf24';
      stats.textContent = `Лейнов: ${s.lanes} · лотов: ${s.lots} · продано: ${s.sold}\nСтавок дилеров: ${s.bids}\nПоследнее событие: ${age == null ? '—' : age + ' с назад'}` + (s.persistErr ? '\n⚠ резервная копия не пишется (переполнена)' : '');
    };
    upd(); A.ivUI = setInterval(upd, 3000);
  }

  return A.stats();
})();
