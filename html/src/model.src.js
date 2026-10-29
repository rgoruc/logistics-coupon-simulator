/* Logistics Coupon Simulator, model v12 (source of html/coupon_planner.html; build with html/build_html.py).
   Fictional case: the Drugstore (Baden-Württemberg, 450 stores) runs an app coupon on Deo Test Brand, activated in the
   Drugstore app and redeemed at the till. Goal: the incremental units caused by the coupon and what they mean for logistics.
   - Normal demand of a day = baseline[weekday, hour] x season x calendar x weather x price (the brand's price against the
     volume-weighted category price index).
     The drivers are measured together in one regression on the log of daily demand, fitted on the years before the
     plan year, outside Deo Test Brand's own coupons (the plan year's weeks with data are the test).
   - Baseline: the same-weekday rule (last 6 weeks, outlier rule, median window), per hour 08:00-20:00, on neutral
     values (demand / the drivers of the day). The window rolls with every week, skips weeks of Deo Test Brand's own
     coupons and the dip after them, and is frozen during a coupon at the 6 weeks before it.
   - The control brand (Deo Control Brand) gets the same model: the cross-check of the control assumption.
   - Plan: the frozen neutral baseline x the drivers of each plan day (this year's calendar, the season, normal weather
     or a cold / warm scenario); incremental units from redemptions; fill rate, volume to commit 96 days ahead,
     shelf facings. The Python model (model/) gives the same numbers; tests/check_numbers.py checks it. */
const Model = (() => {
  const NAME = "Deo Test Brand", CONTROL = "Deo Control Brand";
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const HOURS = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]; // opening hours 08:00-20:00
  const PLAN_WEEKS = [43, 44, 45, 46, 47, 48, 49, 50, 51, 52]; // weeks after the data
  const YEAR_WEEKS = Array.from({ length: 52 }, (_, i) => i + 1); // a coupon can start in any week and has to end by week 52
  const YEAR = 2026, LAST_DATA = 42;
  // Deo Test Brand's own coupons in the data: coupon weeks, partial last week and the three dip weeks after
  const OWN = [
    { year: 2024, start: 18, full: [18, 19, 20, 21], partialWeek: null, post: [22, 23, 24] },
    { year: 2025, start: 43, full: [43, 44, 45, 46], partialWeek: 47, post: [48, 49, 50] },
  ];
  const C25 = OWN.find((c) => c.year === 2025), C26 = OWN.find((c) => c.year === YEAR) || null; // no coupon in the plan year's data: null
  const TRAIN_YEARS = [2024, 2025]; // the drivers are fitted here; 2026 is the test
  const MAU_2026 = __MAU__;
  const DELIVERY_DEFAULT = __DELIVERY__;
  // plan defaults from data/input/plan_assumptions.csv (null = computed from the past coupons)
  const DEFAULTS = __DEFAULTS__;
  const SHELF = __SHELF__;
  // Past Deo Test Brand app coupons in the region. rateObs = redemptions / app users; upr = units per redemption.
  // All three are measured from the data with the counterfactual.
  const PAST = __PAST__;
  const WEEKLY_CSV = `__WEEKLY__`;
  const HOURLY_CSV = `__HOURLY__`;
  const TEMPERATURE_CSV = `__TEMPERATURE__`;  // date;tmax (Karlsruhe, Open-Meteo archive)
  const HOLIDAYS_CSV = `__HOLIDAYS__`;        // date;name;kind;close_hour (Baden-Württemberg)
  const SEASON_HARMONICS = 5, TEMP_HARMONICS = 2;
  const DRIVER_NAMES = ["season", "calendar", "weather", "price"];

  const sum = (a) => a.reduce((x, y) => x + y, 0);
  const mean = (a) => sum(a) / a.length;
  const rms = (a) => Math.sqrt(mean(a.map((x) => x * x)));
  const DAY_MS = 86400000;
  const isoMonday = (year, week) => {
    const jan4 = Date.UTC(year, 0, 4);
    const dow = (new Date(jan4).getUTCDay() + 6) % 7;
    return new Date(jan4 - dow * DAY_MS + (week - 1) * 7 * DAY_MS);
  };
  const iso = (d) => d.toISOString().slice(0, 10);
  const dayKey = (y, w, d) => iso(new Date(isoMonday(y, w).getTime() + d * DAY_MS));
  const wk = (y, w) => `${y}-${w}`;
  const parseDate = (s) => new Date(s + "T00:00:00Z");
  // ISO year and week of a date (the year of the week's Thursday)
  const isoYearWeek = (d) => {
    const mon = new Date(d.getTime() - ((d.getUTCDay() + 6) % 7) * DAY_MS);
    const y = new Date(mon.getTime() + 3 * DAY_MS).getUTCFullYear();
    return [y, Math.round((mon.getTime() - isoMonday(y, 1).getTime()) / DAY_MS / 7) + 1];
  };

  function parseWeekly(csv) {
    const lines = csv.trim().split(/\r?\n/), head = lines[0].split(";"), map = new Map(), order = [];
    for (const line of lines.slice(1)) {
      const c = line.split(";"), row = {};
      head.forEach((h, i) => { row[h] = c[i] === "" ? null : Number(c[i]); });
      map.set(wk(row.year, row.week), row);
      order.push([row.year, row.week]);
    }
    return { map, order };
  }
  function parseHourly(csv) {
    const lines = csv.trim().split(/\r?\n/), out = new Map();
    for (const line of lines.slice(1)) {
      const c = line.split(";"), v = c.slice(1).map(Number);
      out.set(c[0], { test: v.slice(0, 12), control: v.slice(12, 24) });
    }
    return out;
  }
  const WK = parseWeekly(WEEKLY_CSV);
  const HOURLY = parseHourly(HOURLY_CSV);
  const row = (y, w) => WK.map.get(wk(y, w));
  const fr = (y, g, w) => row(y, w)[`fr_${g}`];
  const price = (y, g, w) => row(y, w)[`price_${g}`];
  const HD = new Map(), DD = new Map();
  const hourDemand = (y, g, w, d) => {
    const k = `${g}|${y}|${w}|${d}`;
    if (!HD.has(k)) { const r = HOURLY.get(dayKey(y, w, d)); HD.set(k, r ? r[g].map((v) => v / fr(y, g, w)) : null); }
    return HD.get(k);
  };
  const dayDemand = (y, g, w, d) => {
    const k = `${g}|${y}|${w}|${d}`;
    if (!DD.has(k)) { const h = hourDemand(y, g, w, d); DD.set(k, h ? sum(h) : null); }
    return DD.get(k);
  };
  const daySales = (y, g, w, d) => { const r = HOURLY.get(dayKey(y, w, d)); return r ? sum(r[g]) : null; };

  // position of every week in time (weeks with data first; plan weeks of 2026 follow the last data week)
  const ORDER = [...WK.order, ...PLAN_WEEKS.map((w) => [YEAR, w])];
  const POS = new Map(ORDER.map(([y, w], i) => [wk(y, w), i]));
  const ownOf = (y, w) => OWN.find((c) => c.year === y && (c.full.includes(w) || c.partialWeek === w || c.post.includes(w)));
  const hasData = (y, w) => WK.map.has(wk(y, w));

  /* The 6 weeks a week's baseline comes from: the 6 latest weeks before it with data and without Deo Test Brand's own
     coupon or the dip after it. During a coupon and its dip the window stays frozen at the 6 weeks before the coupon. */
  function windowFor(y, w) {
    const own = ownOf(y, w);
    let i = POS.get(wk(own ? own.year : y, own ? own.start : w)) - 1;
    const out = [];
    while (out.length < 6 && i >= 0) {
      const [yy, ww] = ORDER[i];
      if (hasData(yy, ww) && !ownOf(yy, ww)) out.unshift([yy, ww]);
      i--;
    }
    return out;
  }

  /* The baseline rule (from the minute-level TV baseline), here per hour: the same weekday and hour in the last 6 weeks.
     Rank high to low. Outlier = above the value at rank floor(n/2)+1 (rank 31 of 60) + 2 x the standard deviation of
     the middle ranks (11-50 of 60: the top and bottom sixth left out) + 2. Baseline = average of the values around the
     median of the values that are not outliers: rank n - floor((n - outliers)/2), +/- 1 (+/- 2 with 60 values).
     Closed days and closed hours are left out. items: [{ key, value }] */
  function robustLevel(items) {
    const v = items.filter((it) => it.value != null).sort((a, b) => b.value - a.value);
    const n = v.length;
    const status = new Map(items.map((it) => [it.key, it.value == null ? "closed" : "not used"]));
    if (!n) return { level: 0, threshold: null, nOut: 0, n, status };
    const mid = v[Math.floor(n / 2)].value;
    const k = Math.max(1, Math.round(n / 6));
    const middle = v.slice(k, n - k).map((it) => it.value);
    const m = middle.length ? mean(middle) : mid;
    const sd = middle.length > 1 ? Math.sqrt(mean(middle.map((x) => (x - m) ** 2))) : 0;
    const threshold = mid + 2 * sd + 2;
    const nOut = v.filter((it) => it.value > threshold).length;
    const c = n - Math.floor((n - nOut) / 2);
    const hw = n >= 20 ? 2 : 1;
    const used = v.slice(Math.max(nOut + 1, c - hw) - 1, Math.min(n, c + hw));
    v.forEach((it, r) => { if (r < nOut) status.set(it.key, "outlier"); });
    used.forEach((it) => status.set(it.key, "used"));
    return { level: used.length ? mean(used.map((it) => it.value)) : NaN, threshold, nOut, n, status };
  }

  /* ---------------- the drivers: small helpers (the same code as model/drivers.py) ---------------- */
  const yearFraction = (d) => {
    const y = d.getUTCFullYear();
    const days = (y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0)) ? 366 : 365;
    return Math.round((d.getTime() - Date.UTC(y, 0, 1)) / DAY_MS) / days;
  };
  const fourier = (frac, harmonics) => {
    const out = [];
    for (let k = 1; k <= harmonics; k++) { out.push(Math.cos(2 * Math.PI * k * frac)); out.push(Math.sin(2 * Math.PI * k * frac)); }
    return out;
  };
  // the Christmas run-up: 0 outside it, rising from 1/23 on 1 December to 1 on 23 and 24 December
  const christmasRamp = (d) => { if (d.getUTCMonth() !== 11 || d.getUTCDate() < 1 || d.getUTCDate() > 24) return 0; return Math.min(1, d.getUTCDate() / 23); };

  // A x = b by Gaussian elimination with partial pivoting
  function solve(a, b) {
    const n = b.length, m = a.map((r, i) => [...r, b[i]]);
    for (let col = 0; col < n; col++) {
      let piv = col;
      for (let r = col + 1; r < n; r++) if (Math.abs(m[r][col]) > Math.abs(m[piv][col])) piv = r;
      [m[col], m[piv]] = [m[piv], m[col]];
      const p = m[col][col];
      if (p === 0) throw new Error("singular design matrix");
      for (let r = 0; r < n; r++) {
        if (r === col) continue;
        const f = m[r][col] / p;
        if (f === 0) continue;
        for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c];
      }
    }
    return m.map((r, i) => r[n] / r[i]);
  }
  function inverse(a) {
    const n = a.length, m = a.map((r, i) => [...r, ...a.map((_, j) => (i === j ? 1 : 0))]);
    for (let col = 0; col < n; col++) {
      let piv = col;
      for (let r = col + 1; r < n; r++) if (Math.abs(m[r][col]) > Math.abs(m[piv][col])) piv = r;
      [m[col], m[piv]] = [m[piv], m[col]];
      const p = m[col][col];
      for (let c = 0; c < 2 * n; c++) m[col][c] /= p;
      for (let r = 0; r < n; r++) {
        if (r === col) continue;
        const f = m[r][col];
        if (f === 0) continue;
        for (let c = 0; c < 2 * n; c++) m[r][c] -= f * m[col][c];
      }
    }
    return m.map((r) => r.slice(n));
  }
  // ordinary least squares (no intercept is added)
  function ols(rows, y) {
    const n = rows.length, p = rows[0].length;
    const xtx = Array.from({ length: p }, () => new Array(p).fill(0)), xty = new Array(p).fill(0);
    rows.forEach((row, k) => {
      const v = y[k];
      for (let i = 0; i < p; i++) {
        const xi = row[i];
        if (xi === 0) continue;
        xty[i] += xi * v;
        const xr = xtx[i];
        for (let j = 0; j < p; j++) xr[j] += xi * row[j];
      }
    });
    const coef = solve(xtx, xty);
    const res = rows.map((row, k) => y[k] - sum(row.map((x, i) => coef[i] * x)));
    const s2 = sum(res.map((r) => r * r)) / (n - p);
    const inv = inverse(xtx);
    return { coef, se: coef.map((_, i) => Math.sqrt(Math.max(0, s2 * inv[i][i]))), sd: Math.sqrt(s2), n, residuals: res };
  }

  // the holiday calendar
  const CAL = (() => {
    const closed = new Set(), short = new Map(), names = new Map();
    for (const line of HOLIDAYS_CSV.trim().split(/\r?\n/).slice(1)) {
      const [date, name, kind, close] = line.split(";");
      names.set(date, name);
      if (kind === "short") short.set(date, Number(close) || 14); else closed.add(date);
    }
    const isOpen = (d) => d.getUTCDay() !== 0 && !closed.has(iso(d));
    const closeHour = (d) => (isOpen(d) ? (short.get(iso(d)) ?? HOURS[HOURS.length - 1] + 1) : 0);
    // how many closed holidays (Monday to Saturday) have this day as their first or second shopping day before
    const preHoliday = (d) => {
      let n1 = 0, n2 = 0;
      if (!isOpen(d)) return [0, 0];
      for (const hs of closed) {
        const h = parseDate(hs);
        if (h.getUTCDay() === 0 || h <= d || (h - d) / DAY_MS > 6) continue;
        let k = 0, day = new Date(h.getTime() - DAY_MS);
        while (k < 2 && day >= d) {
          if (isOpen(day)) { k++; if (day.getTime() === d.getTime()) { if (k === 1) n1++; else n2++; break; } }
          day = new Date(day.getTime() - DAY_MS);
        }
      }
      return [n1, n2];
    };
    return { closed, short, names, isOpen, closeHour, preHoliday };
  })();

  // weather: the daily maximum against its seasonal normal (intercept + 2 sine/cosine pairs through all days)
  const WEATHER = (() => {
    const tmax = new Map();
    for (const line of TEMPERATURE_CSV.trim().split(/\r?\n/).slice(1)) { const [date, t] = line.split(";"); tmax.set(date, Number(t)); }
    const days = [...tmax.keys()].sort();
    const fit = ols(days.map((s) => [1, ...fourier(yearFraction(parseDate(s)), TEMP_HARMONICS)]), days.map((s) => tmax.get(s)));
    const normal = (d) => sum([1, ...fourier(yearFraction(d), TEMP_HARMONICS)].map((v, i) => fit.coef[i] * v));
    const anomaly = (d) => { const t = tmax.get(iso(d)); return t == null ? null : t - normal(d); };
    return { tmax, coef: fit.coef, normal, anomaly, lastDay: days[days.length - 1] };
  })();

  // each brand's price against the category price index, against its norm (the median over the weeks with data, log)
  const zNorm = (g) => { const v = WK.order.map(([y, w]) => Math.log(price(y, g, w) / price(y, "category", w))).sort((a, b) => a - b); return v[Math.floor(v.length / 2)]; };
  const Z_NORM = zNorm("test"), Z_NORM_C = zNorm("control");

  // the features of every day from the first data day to the end of the plan year
  const FEATS = (() => {
    const out = new Map();
    const first = isoMonday(...WK.order[0]), last = Date.UTC(YEAR, 11, 31);
    for (let t = first.getTime(); t <= last; t += DAY_MS) {
      const d = new Date(t), [iy, iw] = isoYearWeek(d);
      const has = hasData(iy, iw);
      const z = has ? Math.log(price(iy, "test", iw) / price(iy, "category", iw)) - Z_NORM : 0;
      const zc = has ? Math.log(price(iy, "control", iw) / price(iy, "category", iw)) - Z_NORM_C : 0;
      const [n1, n2] = CAL.preHoliday(d);
      out.set(iso(d), { date: d, weekday: (d.getUTCDay() + 6) % 7, open: CAL.isOpen(d), closeHour: CAL.closeHour(d), frac: yearFraction(d),
        season: fourier(yearFraction(d), SEASON_HARMONICS), pre1: n1, pre2: n2, xmas: christmasRamp(d), anomaly: WEATHER.anomaly(d),
        z, zc, tYears: Math.round((t - first.getTime()) / DAY_MS) / 365.25 });
    }
    return out;
  })();
  const feat = (y, w, d) => FEATS.get(dayKey(y, w, d));

  /* ---------------- the drivers regression ---------------- */
  const COLUMNS = [...[0, 1, 2, 3, 4, 5].map((d) => `wd${d}`), ...Array.from({ length: SEASON_HARMONICS }, (_, k) => [`s${k + 1}c`, `s${k + 1}s`]).flat(),
    "pre1", "pre2", "xmas", "anomaly", "z", "trend"];
  const designRow = (f, brand) => {
    const r = [0, 1, 2, 3, 4, 5].map((d) => (f.weekday === d ? 1 : 0));
    r.push(...f.season, f.pre1, f.pre2, f.xmas, f.anomaly ?? 0, brand === "test" ? f.z : f.zc, f.tYears);
    return r;
  };
  function fitDrivers(brand) {
    const rows = [], y = [];
    for (const [yy, ww] of WK.order) {
      if (!TRAIN_YEARS.includes(yy) || (brand === "test" && ownOf(yy, ww))) continue;
      for (let d = 0; d < 6; d++) {
        const f = feat(yy, ww, d);
        if (!f.open || f.closeHour <= HOURS[HOURS.length - 1] || f.anomaly == null) continue;
        const P = dayDemand(yy, brand, ww, d);
        if (!P) continue;
        rows.push(designRow(f, brand)); y.push(Math.log(P));
      }
    }
    const fit = ols(rows, y);
    const names = COLUMNS;
    const seasonCoef = fit.coef.slice(6, 6 + 2 * SEASON_HARMONICS);
    const seasonMean = mean(Array.from({ length: 365 }, (_, i) => sum(fourier((i + 0.5) / 365, SEASON_HARMONICS).map((v, j) => seasonCoef[j] * v))));
    const c = (name) => fit.coef[names.indexOf(name)], se = (name) => fit.se[names.indexOf(name)];
    const seasonLog = (frac) => sum(fourier(frac, SEASON_HARMONICS).map((v, j) => seasonCoef[j] * v)) - seasonMean;
    const parts = (f, anomaly) => {
      const a = anomaly === undefined ? f.anomaly : anomaly;
      return {
        season: Math.exp(seasonLog(f.frac)),
        calendar: Math.exp(c("pre1") * f.pre1 + c("pre2") * f.pre2 + c("xmas") * f.xmas),
        weather: Math.exp(c("anomaly") * (a || 0)),
        price: Math.exp(c("z") * (brand === "test" ? f.z : f.zc)),
      };
    };
    const factor = (f, anomaly) => { const p = parts(f, anomaly); let out = 1; for (const k of DRIVER_NAMES) out *= p[k]; return out; };
    const effects = () => ["pre1", "pre2", "xmas", "anomaly", "z", "trend"].map((key) => ({ key, coef: c(key), se: se(key) }));
    return { brand, fit, names, seasonCoef, seasonMean, seasonLog, parts, factor, effects, coef: c, se };
  }
  // the drivers of a day as the plan knows them: season and calendar, weather at the given anomaly, the rest unknown
  const planFactor = (drv, f, anomaly = 0) => { const p = drv.parts(f, anomaly); return p.season * p.calendar * Math.exp(drv.coef("anomaly") * anomaly); };

  /* Levels of one window: the baseline rule on neutral values (demand / the drivers of the day), per weekday and hour */
  function levelsFor(win, brand, drv) {
    const cells = [0, 1, 2, 3, 4, 5].map((d) => HOURS.map((h, i) => robustLevel(win.map(([y, w]) => {
      const f = feat(y, w, d), hd = hourDemand(y, brand, w, d);
      const ok = hd != null && f.open && h < f.closeHour;
      return { key: wk(y, w), value: ok ? hd[i] / drv.factor(f) : null };
    }))));
    const day = cells.map((r) => sum(r.map((c) => c.level)));
    const week = sum(day);
    const L = { cells, day, week, dayShare: day.map((v) => v / week), hourShare: cells.map((r, d) => r.map((c) => c.level / day[d])) };
    const dayLevel = (d, closeHour) => sum(cells[d].filter((c, i) => HOURS[i] < closeHour).map((c) => c.level));
    return { win, L, dayShare: L.dayShare, hourShare: L.hourShare, dayLevel };
  }

  // daily counterfactual of one brand for every day with data; weekly sums
  function counterfactuals(brand, drv) {
    const cache = new Map();
    const levels = (y, w) => {
      const win = windowFor(y, w), key = win.map(([a, b]) => wk(a, b)).join(",");
      if (!cache.has(key)) cache.set(key, levelsFor(win, brand, drv));
      return cache.get(key);
    };
    const days = [], weeks = [];
    for (const [y, w] of WK.order) {
      const v = levels(y, w), own = ownOf(y, w);
      const ds = [0, 1, 2, 3, 4, 5].map((d) => {
        const f = feat(y, w, d), P = dayDemand(y, brand, w, d);
        const base = { year: y, week: w, d, date: dayKey(y, w, d), own: !!own };
        if (P == null || !f.open) return { ...base, closed: true, cf: 0, effect: 0 };
        const F = drv.factor(f), cf = v.dayLevel(d, f.closeHour) * F;
        return { ...base, P, Psales: daySales(y, brand, w, d), F, parts: drv.parts(f), cf, effect: P - cf };
      });
      days.push(...ds);
      const actual = sum(ds.map((p) => p.P || 0)), cf = sum(ds.map((p) => p.cf || 0));
      const lv = sum(ds.filter((p) => !p.closed).map((p) => v.dayLevel(p.d, feat(y, w, p.d).closeHour)));
      weeks.push({ year: y, week: w, actual, cf, effect: actual - cf, own: !!own, sales: sum(ds.map((p) => p.Psales || 0)),
        level: v.L.week, window: v.win, fr: fr(y, brand, w), factor: cf / lv });
    }
    const week = (y, w) => weeks.find((r) => r.year === y && r.week === w);
    return { days, weeks, levels, week };
  }

  function lineFit(points) {
    const mx = mean(points.map((p) => p.x)), my = mean(points.map((p) => p.y));
    const sxx = sum(points.map((p) => (p.x - mx) ** 2)), sxy = sum(points.map((p) => (p.x - mx) * (p.y - my)));
    const slope = sxy / sxx;
    return { slope, intercept: my - slope * mx, min: Math.min(...points.map((p) => p.x)), max: Math.max(...points.map((p) => p.x)) };
  }

  // normal demand of every day of the given weeks at normal weather: the neutral level x the plan's drivers
  function planDays(lv, drv, y, weeks) {
    const wkv = {}, days = {}, parts = {};
    for (const w of weeks) {
      const vals = [], pw = Object.fromEntries(DRIVER_NAMES.map((k) => [k, 0]));
      for (let d = 0; d < 7; d++) {
        const f = feat(y, w, d);
        if (d === 6 || !f.open) { vals.push(0); continue; }
        const v = lv.dayLevel(d, f.closeHour) * planFactor(drv, f);
        vals.push(v);
        const p = drv.parts(f, 0);
        for (const k of DRIVER_NAMES) pw[k] += v * (k === "season" || k === "calendar" ? p[k] : 1);
      }
      const s = sum(vals);
      wkv[w] = s; days[w] = vals; parts[w] = Object.fromEntries(DRIVER_NAMES.map((k) => [k, pw[k] / s]));
    }
    return { weeks: wkv, days, parts };
  }

  function calibrate() {
    // the control brand gets the same model: the cross-check of the control assumption in the coupon weeks
    const drvC = fitDrivers("control");
    const allC = counterfactuals("control", drvC);
    const drv = fitDrivers("test");
    const all = counterfactuals("test", drv);
    const sdIn = drv.fit.sd;
    // out-of-sample errors and normal weeks: every week with a full rolling window (the 6 weeks before it have data),
    // outside the own coupons and the dip after them
    const full = new Set(all.weeks.filter((r) => r.window.length === 6).map((r) => wk(r.year, r.week)));
    const oos = all.days.filter((p) => !p.closed && !p.own && full.has(wk(p.year, p.week)));
    const sdD = rms(oos.map((p) => Math.log(p.P / p.cf)));
    const sdL = Math.sqrt(Math.max(0, sdD * sdD - sdIn * sdIn));
    const wkOos = all.weeks.filter((r) => !r.own && full.has(wk(r.year, r.week)));
    const sdW = rms(wkOos.map((r) => Math.log(r.actual / r.cf)));
    // holdout: the plan year's weeks the drivers never saw
    const hw = wkOos.filter((r) => r.year === YEAR), hd = oos.filter((p) => p.year === YEAR);
    const holdout = { year: YEAR, weeks: hw.length, days: hd.length, weeklyRms: rms(hw.map((r) => Math.log(r.actual / r.cf))),
      weeklyBias: mean(hw.map((r) => r.actual / r.cf - 1)), weeklyMad: mean(hw.map((r) => Math.abs(r.actual / r.cf - 1))), dailyRms: rms(hd.map((p) => Math.log(p.P / p.cf))) };

    // measurement of an own coupon: coupon weeks and the dip weeks after it, cumulative 95% interval
    function measure(c) {
      const weeks = [...c.full, ...(c.partialWeek ? [c.partialWeek] : [])];
      const post = [...weeks, ...c.post];
      const days = all.days.filter((p) => p.year === c.year && post.includes(p.week));
      // the level error of the frozen window is one number for the whole coupon and its dip (the same 6 weeks feed every cell)
      let cum = 0, s2 = 0, sCf = 0, half = 0;
      for (const p of days) {
        if (!p.closed) {
          cum += p.effect; s2 += p.cf * p.cf; sCf += p.cf;
          half = 1.96 * Math.sqrt(sdIn * sdIn * s2 + (sCf * sdL) ** 2);
        }
        p.cum = cum; p.cumHalf = half;
      }
      const wkr = (w) => all.week(c.year, w);
      const extra = sum(weeks.map((w) => wkr(w).effect)), dip = -sum(c.post.map((w) => wkr(w).effect));
      const lastC = days.filter((p) => weeks.includes(p.week) && !p.closed).pop(), lastAll = days.filter((p) => !p.closed).pop();
      const frc = sum(weeks.map((w) => Math.max(0, wkr(w).effect) * fr(c.year, "test", w))) / sum(weeks.map((w) => Math.max(0, wkr(w).effect)));
      return { c, weeks, days, extra, dip, extraHalf: lastC.cumHalf, netHalf: lastAll.cumHalf, wkr, frc,
        cfCoupon: sum(weeks.map((w) => wkr(w).cf)), lost: sum(weeks.map((w) => wkr(w).actual - wkr(w).sales)) };
    }
    const ms = OWN.map((c) => measure(c));
    const m25 = ms[OWN.indexOf(C25)], m26 = C26 ? ms[OWN.indexOf(C26)] : null;
    const lv25 = all.levels(C25.year, C25.start);
    const profile = { full: C25.full.map((w) => m25.wkr(w).effect / m25.extra), partial: m25.wkr(C25.partialWeek).effect / m25.extra };
    const tail = mean(profile.full.slice(1));
    const lastChance = profile.partial / (tail * (lv25.L.dayShare[0] + lv25.L.dayShare[1]));
    const dipWeights = C25.post.map((w) => -m25.wkr(w).effect / m25.dip);
    const normalWeeks = wkOos;
    const frNormal = mean(normalWeeks.map((r) => r.fr));
    const extraOnSales25 = sum(m25.weeks.map((w) => m25.wkr(w).sales - m25.wkr(w).cf * frNormal));
    // the 2025 coupon by hour: actual demand against the counterfactual on full coupon days
    const hours25 = { base: HOURS.map(() => 0), actual: HOURS.map(() => 0), days: 0 };
    for (const p of m25.days.filter((q) => C25.full.includes(q.week) && !q.closed)) {
      const hd_ = hourDemand(2025, "test", p.week, p.d);
      hours25.days++;
      HOURS.forEach((h, i) => { hours25.base[i] += p.cf * lv25.L.hourShare[p.d][i]; hours25.actual[i] += hd_[i]; });
    }
    hours25.base = hours25.base.map((v) => v / hours25.days);
    hours25.actual = hours25.actual.map((v) => v / hours25.days);
    hours25.extra = hours25.actual.map((v, i) => v - hours25.base[i]);
    hours25.baseShare = hours25.base.map((v) => v / sum(hours25.base));
    hours25.extraShare = hours25.extra.map((v) => v / sum(hours25.extra));

    // the control brand against its own counterfactual: a cross-check of the control assumption
    const checks = {};
    for (const c of OWN) {
      const weeks = [...c.full, ...(c.partialWeek ? [c.partialWeek] : [])];
      const wc = (w) => allC.week(c.year, w);
      checks[c.year] = { coupon: sum(weeks.map((w) => wc(w).effect)) / sum(weeks.map((w) => wc(w).cf)), dip: sum(c.post.map((w) => wc(w).effect)) / sum(c.post.map((w) => wc(w).cf)) };
    }
    const fullC = new Set(allC.weeks.filter((r) => r.window.length === 6).map((r) => wk(r.year, r.week)));
    const pairs = wkOos.filter((r) => fullC.has(wk(r.year, r.week))).map((r) => { const q = allC.week(r.year, r.week); return [Math.log(r.actual / r.cf), Math.log(q.actual / q.cf)]; });
    const ma = mean(pairs.map((p) => p[0])), mb = mean(pairs.map((p) => p[1]));
    const sab = sum(pairs.map(([a, b]) => (a - ma) * (b - mb))), saa = sum(pairs.map(([a]) => (a - ma) ** 2)), sbb = sum(pairs.map(([, b]) => (b - mb) ** 2));
    const controlCheck = { years: checks, weeklyCorr: sab / Math.sqrt(saa * sbb), weeks: pairs.length, sdWControl: rms(pairs.map((p) => p[1])) };

    // the plan: the frozen neutral baseline before the first plan week x the drivers of every plan day
    const lv26 = all.levels(YEAR, PLAN_WEEKS[0]);
    const p26 = planDays(lv26, drv, YEAR, PLAN_WEEKS);
    const B26 = { ...lv26.L, levels: lv26, levelWeek: lv26.L.week, window: lv26.win, weeks: p26.weeks, days: p26.days, parts: p26.parts,
      week: mean(PLAN_WEEKS.map((w) => p26.weeks[w])), dayShare: lv26.dayShare, hourShare: lv26.hourShare };
    // the same plan one year earlier: what it missed in the coupon weeks (weather, prices, shocks)
    const p25 = planDays(all.levels(C25.year, C25.start), drv, C25.year, m25.weeks);
    const planErr = m25.weeks.map((w) => p25.weeks[w] / m25.wkr(w).cf - 1);
    const baseBias25 = mean(planErr), baseErr25 = rms(planErr);

    const past = PAST.map((p) => {
      const r = { ...p };
      if (p.own != null) {
        const m = ms[p.own];
        r.fillRate = m.frc;
        r.rateFull = p.rateObs / r.fillRate;
        r.couponUnitsFull = p.mau * r.rateFull * p.upr;
        r.extra = m.extra / r.couponUnitsFull; r.dip = m.dip / m.extra; r.extraHalf = m.extraHalf / r.couponUnitsFull; r.measured = true;
      } else {
        r.rateFull = p.rateObs / r.fillRate;
        r.couponUnitsFull = p.mau * r.rateFull * p.upr;
      }
      return r;
    });
    const fitRate = lineFit(past.map((p) => ({ x: p.discount, y: p.rateFull })));
    const fitShare = lineFit(past.map((p) => ({ x: p.discount, y: p.extra })));
    const m = past.find((p) => p.year === 2025);
    // the worked example of the rule: the frozen window of the plan, one weekday and hour
    const sample = (d, i) => lv26.win.map(([y, w]) => {
      const f = feat(y, w, d), P = hourDemand(y, "test", w, d);
      const ok = P != null && f.open && HOURS[i] < f.closeHour;
      const F = ok ? drv.factor(f) : null;
      return { year: y, week: w, date: dayKey(y, w, d), raw: ok ? P[i] : null, factor: F, parts: ok ? drv.parts(f) : null, adj: ok ? P[i] / F : null, status: lv26.L.cells[d][i].status.get(wk(y, w)) };
    });
    // the season curve by week (the Thursday of each week) and the cold / warm scenario
    const seasonCurve = YEAR_WEEKS.map((w) => Math.exp(drv.seasonLog(feat(YEAR, w, 3).frac)));
    const wkAnom = new Map();
    for (const f of FEATS.values()) if (f.anomaly != null) { const k = wk(...isoYearWeek(f.date)); if (!wkAnom.has(k)) wkAnom.set(k, []); wkAnom.get(k).push(f.anomaly); }
    const a = [...wkAnom.values()].filter((v) => v.length === 7).map((v) => mean(v));
    const anomalySd = Math.sqrt(mean(a.map((x) => (x - mean(a)) ** 2)));
    return {
      drivers: drv, driversControl: drvC, trainYears: TRAIN_YEARS, sdIn, sdD, sdL, sdW, oosDays: oos.length, all, allC, ms, m25, m26, lv25,
      extra25: m25.extra, dip25: m25.dip, extraHalf: m25.extraHalf, netHalf: m25.netHalf, frc25: m25.frc, lost25: m25.lost, extraOnSales25,
      profile, tail, lastChance, dipWeights, frNormal, hours25, past, fitRate, fitShare, measured: m,
      upr: mean(past.map((p) => p.upr)), dipDefault: mean(past.map((p) => p.dip)), shareRelSE: (m.extraHalf / 1.96) / m.extra,
      B26, planWin: lv26, planErr, baseBias25, baseErr25, sample, holdout, controlCheck, seasonCurve, anomalySd,
      cf26: new Map(all.weeks.filter((x) => x.year === YEAR).map((x) => [x.week, x])),
    };
  }

  // Past coupons all ran 4 weeks, so the reference line gives a 4-week rate. A 2-week coupon is planned as the first two
  // weeks of a 4-week coupon: the share of the reference coupon's incremental units that came in its first two full weeks.
  const COUPON_LENGTHS = [2, 4];
  const lengthShare = (cal, weeks) => { const p = cal.profile.full; return sum(p.slice(0, Math.min(weeks, p.length))) / sum(p); };
  const reference = (cal, discount, weeks = 4) => {
    const share = lengthShare(cal, weeks);
    return { rate: (cal.fitRate.intercept + cal.fitRate.slope * discount) * share, extrapolated: discount < cal.fitRate.min - 1e-9 || discount > cal.fitRate.max + 1e-9, lengthShare: share };
  };
  // weeks of the plan year a coupon of `weeks` weeks can start in (it has to end by week 52); weeks with data are a what-if
  const startWeeks = (weeks) => YEAR_WEEKS.filter((w) => w + weeks - 1 <= 52);
  // the weather driver at a temperature anomaly (degrees above the seasonal normal)
  const weatherFactor = (cal, anomaly) => Math.exp(cal.drivers.coef("anomaly") * anomaly);
  // normal demand of a week: the counterfactual from the data for weeks with data (season, calendar, weather and
  // prices as they were), else the plan's normal demand (this year's calendar and the season, the weather of the scenario)
  const normalDemand = (cal, w, anomaly = 0) => (w <= LAST_DATA ? cal.cf26.get(w).cf : cal.B26.weeks[w] * weatherFactor(cal, anomaly));
  // weekday shares and closed days of a week: from that week's daily counterfactual (weeks with data), else the plan's daily normal demand
  function dayShares(cal, w) {
    if (w <= LAST_DATA) {
      const ds = cal.all.days.filter((p) => p.year === YEAR && p.week === w);
      const tot = sum(ds.map((p) => p.cf));
      return { share: [...ds.map((p) => p.cf / tot), 0], closed: [...ds.map((p) => !!p.closed), true] };
    }
    const vals = cal.B26.days[w], tot = sum(vals);
    return { share: vals.map((v) => v / tot), closed: vals.map((v) => v === 0) };
  }
  // hourly shares of a weekday: the baseline of the 6 weeks before that week (the plan baseline after the data), over the open hours
  const hourShares = (cal, w, d) => {
    const lv = w <= LAST_DATA ? cal.all.levels(YEAR, w) : cal.B26.levels;
    const close = feat(YEAR, w, d).closeHour, cells = lv.L.cells[d];
    const s = sum(cells.filter((c, i) => HOURS[i] < close).map((c) => c.level));
    return cells.map((c, i) => (HOURS[i] < close && s ? c.level / s : 0));
  };
  const shareFor = (cal, discount) => cal.fitShare.intercept + cal.fitShare.slope * discount;

  function couponPlan(cal, sc) {
    const weeks = [], raw = [], days = {};
    for (let i = 0; i < sc.fullWeeks; i++) {
      const w = sc.startWeek + i;
      weeks.push(w); raw.push(i < cal.profile.full.length ? cal.profile.full[i] : cal.tail); days[w] = [0, 1, 2, 3, 4, 5];
    }
    if (sc.partialLastWeek) {
      const w = sc.startWeek + sc.fullWeeks;
      weeks.push(w); raw.push(cal.tail * (cal.B26.dayShare[0] + cal.B26.dayShare[1]) * cal.lastChance); days[w] = [0, 1];
    }
    if (Math.max(...weeks) > 52) throw new Error("Coupon runs past week 52.");
    const total = sum(raw);
    return { weeks, share: Object.fromEntries(weeks.map((w, i) => [w, raw[i] / total])), days };
  }

  // 2026 plan: the plan counterfactual + incremental units of the coupon - the dip after it
  function forecast(cal, sc) {
    const plan = couponPlan(cal, sc);
    const redemptions = sc.mau * sc.rate;
    const couponUnits = redemptions * sc.upr;
    const incremental = couponUnits * sc.incShare;
    const dip = incremental * sc.pullShare;
    const rows = YEAR_WEEKS.map((w) => {
      const share = plan.share[w] || 0;
      return { week: w, monday: isoMonday(YEAR, w), coupon: share > 0, share, couponDays: plan.days[w] || [], baseline: normalDemand(cal, w, sc.planAnomaly || 0),
        redemptions: redemptions * share, couponUnits: couponUnits * share, incremental: incremental * share, dip: 0 };
    });
    const byWeek = Object.fromEntries(rows.map((r) => [r.week, r]));
    const end = Math.max(...plan.weeks);
    let dipOutside = 0;
    cal.dipWeights.forEach((wgt, j) => { const r = byWeek[end + 1 + j]; if (r) { r.dip = dip * wgt; r.dipWeek = true; } else dipOutside += dip * wgt; });
    for (const r of rows) { r.demand = r.baseline + r.incremental - r.dip; r.sales = r.demand * sc.fillRate; r.lost = r.demand - r.sales; }
    const cRows = rows.filter((r) => r.coupon);
    const mu = sum(cRows.map((r) => r.demand));
    const baseC = sum(cRows.map((r) => r.baseline)), incC = sum(cRows.map((r) => r.incremental));
    const parts = { base: cal.baseErr25 * baseC, share: incC * cal.shareRelSE, rate: incC * sc.rateUnc };
    const sigma = Math.sqrt(parts.base ** 2 + parts.share ** 2 + parts.rate ** 2);
    const fillAt = (z) => 1 - sigma * lossG(z) / mu;
    let lo = -3, hi = 5;
    for (let i = 0; i < 80; i++) { const z = (lo + hi) / 2; if (fillAt(z) < sc.fillRate) lo = z; else hi = z; }
    const z = (lo + hi) / 2;
    const promo = sc.price * (1 - sc.discount);
    return {
      rows, plan,
      summary: {
        weeks: plan.weeks, redemptions, couponUnits, anyway: couponUnits - incremental, incremental, dip, net: incremental - dip, dipOutside,
        couponDemand: mu, couponBaseline: baseC, couponSales: mu * sc.fillRate, couponLost: mu * (1 - sc.fillRate),
        commit: { mu, sigma, z, q: mu + z * sigma, buffer: z * sigma, fillIfMu: fillAt(0), parts },
        price: { regular: sc.price, promo, control: sc.controlPrice, rpRegular: sc.price / sc.controlPrice, rpCoupon: promo / sc.controlPrice, marketAvg: sc.marketAvg },
        startDate: isoMonday(YEAR, sc.startWeek),
      },
    };
  }

  // standard normal helpers (Abramowitz-Stegun erf)
  const phi = (z) => Math.exp(-z * z / 2) / Math.sqrt(2 * Math.PI);
  const Phi = (z) => {
    const t = 1 / (1 + 0.3275911 * Math.abs(z) / Math.SQRT2);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-z * z / 2);
    return z >= 0 ? (1 + y) / 2 : (1 - y) / 2;
  };
  const lossG = (z) => phi(z) - z * (1 - Phi(z));

  // daily plan of one week: normal demand split by that week's weekday shares; incremental units on the open coupon days
  function daily(cal, sc, res, week) {
    const r = res.rows.find((x) => x.week === week);
    const { share, closed } = dayShares(cal, week);
    const cDays = r.couponDays.filter((d) => !closed[d]);
    const cTot = sum(cDays.map((d) => share[d])) || 1;
    return [0, 1, 2, 3, 4, 5, 6].map((d) => {
      const date = dayKey(YEAR, week, d);
      if (closed[d]) return { date, day: DAYS[d], closed: true, baseline: 0, incremental: 0, dip: 0, demand: 0 };
      const s = share[d];
      const baseline = r.baseline * s, incremental = cDays.includes(d) ? r.incremental * s / cTot : 0, dip = r.dip * s;
      return { date, day: DAYS[d], closed: false, share: s, baseline, incremental, dip, demand: baseline + incremental - dip, coupon: cDays.includes(d) };
    });
  }
  function hourly(cal, sc, res, week, d) {
    const day = daily(cal, sc, res, week)[d];
    const hs = day.closed ? null : hourShares(cal, week, d);
    return { day, rows: HOURS.map((h, i) => {
      const s = day.closed ? 0 : hs[i];
      return { hour: h, open: !day.closed, share: s, baseline: day.baseline * s, incremental: day.incremental * s, dip: day.dip * s, demand: day.demand * s };
    }) };
  }
  // the drivers of one day of the plan year, as the plan sees them (weeks with data: as they were)
  function dayDrivers(cal, week, d, anomaly = 0) {
    const f = feat(YEAR, week, d);
    if (!f.open) return null;
    if (week <= LAST_DATA) { const p = cal.all.days.find((q) => q.year === YEAR && q.week === week && q.d === d); return { ...p.parts, total: p.F, actual: true }; }
    const p = cal.drivers.parts(f, anomaly);
    return { season: p.season, calendar: p.calendar, weather: p.weather, price: 1, total: planFactor(cal.drivers, f, anomaly), actual: false };
  }

  function intervals(deliveryDays) {
    const ds = [...deliveryDays].sort((a, b) => a - b);
    return ds.map((d, i) => { const next = ds[(i + 1) % ds.length], span = []; let k = d; do { if (k !== 6) span.push(k); k = (k + 1) % 7; } while (k !== next); return span; });
  }
  const spanLabel = (span) => (span.length === 1 ? DAYS[span[0]] : `${DAYS[span[0]]}–${DAYS[span[span.length - 1]]}`);
  function peakInterval(cal, sc, res, deliveryDays) {
    const iv = intervals(deliveryDays);
    const share = [...cal.B26.dayShare, 0];
    const normal = Math.max(...iv.map((span) => sum(span.map((d) => share[d]))));
    let best = { factor: 0 };
    for (const r of res.rows.filter((x) => x.coupon)) {
      const dly = daily(cal, sc, res, r.week);
      // measured against the plan's typical week (the shelf data describe a normal week), so a busy season shows too
      iv.forEach((span) => {
        const factor = sum(span.map((d) => dly[d].demand)) / cal.B26.week;
        if (factor > best.factor) best = { factor, week: r.week, span, base: sum(span.map((d) => dly[d].baseline)) / cal.B26.week };
      });
    }
    return { ...best, label: best.span ? spanLabel(best.span) : "", normal };
  }
  function shelfCheck(cal, sc, res, deliveryDays, depth) {
    const peak = peakInterval(cal, sc, res, deliveryDays);
    const items = SHELF.map((s) => {
      const peakDemand = s.normalWeek * peak.factor, capacity = s.facings * depth;
      return { sku: s.sku, normalWeek: s.normalWeek, peakDemand, normalDemand: s.normalWeek * peak.base, capacity, facings: s.facings, needed: Math.max(s.facings, Math.ceil(peakDemand / depth - 1e-9)) };
    });
    let suggestion = null;
    for (let d = 0; d < 6; d++) {
      if (deliveryDays.includes(d)) continue;
      const p = peakInterval(cal, sc, res, [...deliveryDays, d]);
      if (!suggestion || p.factor < suggestion.factor) suggestion = { day: DAYS[d], factor: p.factor, label: p.label, week: p.week };
    }
    if (suggestion) suggestion.fitsAll = SHELF.every((s) => s.normalWeek * suggestion.factor <= s.facings * depth + 1e-9);
    return { peak, items, suggestion };
  }

  return { NAME, CONTROL, DAYS, HOURS, PLAN_WEEKS, YEAR_WEEKS, YEAR, LAST_DATA, OWN, C25, C26, TRAIN_YEARS, MAU_2026, DELIVERY_DEFAULT, DEFAULTS, SHELF, WK, HOURLY,
    CAL, WEATHER, FEATS, DRIVER_NAMES, feat, robustLevel, windowFor, calibrate, reference, shareFor, lengthShare, startWeeks, normalDemand, weatherFactor, dayShares, hourShares, dayDrivers,
    COUPON_LENGTHS, forecast, daily, hourly, intervals, spanLabel, shelfCheck,
    deadline: (start, lead) => new Date(start.getTime() - lead * DAY_MS), isoMonday, dayKey, price, fr, row };
})();
if (typeof module !== "undefined") module.exports = Model;
