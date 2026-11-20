// flowmine project page: draws the network and the result charts from data.json,
// which scripts/build_site.py writes from results/raw/*.csv.
"use strict";

const NS = "http://www.w3.org/2000/svg";
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const tip = document.getElementById("tip");
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const SERIES = {
  edge_hw: { name: "Station (Holt-Winters)", c: "--edge" },
  central: { name: "Centre (spatial + lab)", c: "--central" },
  hybrid: { name: "Hybrid fusion", c: "--hybrid" },
};
const FAULTS = { spike: "Spike", stuck: "Stuck reading", noise_burst: "Noise burst", bias: "Bias", drift: "Drift" };

const fmt = (x, d = 2) => (x == null ? "n/a" : x.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (x, d = 0) => (x == null ? "n/a" : (100 * x).toFixed(x < 0.01 && d > 0 ? d + 1 : d) + "%");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const find = (arr, k, v) => arr.find((x) => x[k] === v);

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
  tip.style.left = Math.max(8, left) + "px";
  tip.style.top = Math.max(8, top) + "px";
}
const hideTip = () => { tip.hidden = true; };

/* ---------- network ---------- */
function netBase(svg, net) {
  svg.replaceChildren();
  const xs = [], ys = [];
  net.links.forEach((l) => l.pts.forEach(([x, y]) => { xs.push(x); ys.push(y); }));
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const pad = 3, w = maxX - minX + 2 * pad, h = maxY - minY + 2 * pad;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const X = (x) => x - minX + pad, Y = (y) => maxY - y + pad; // EPANET y points up
  svg.style.setProperty("--sw", (w / 380).toFixed(3));
  svg.style.setProperty("--dash", `${(w / 160).toFixed(2)} ${(w / 160).toFixed(2)}`);

  const pipes = el("g", {}, svg);
  net.links.forEach((l) => {
    const d = l.pts.map(([x, y], i) => `${i ? "L" : "M"}${X(x).toFixed(2)},${Y(y).toFixed(2)}`).join("");
    const p = el("path", { d, class: "pipe" + (l.type === "pump" ? " pump" : "") }, pipes);
    const len = p.getTotalLength ? p.getTotalLength() : 100;
    const cx = (l.pts[0][0] - minX) / (maxX - minX);
    p.style.setProperty("--len", len.toFixed(1));
    p.style.setProperty("--d", (cx * 0.7).toFixed(2) + "s");
  });
  net.nodes.forEach((n) => {
    if (n.type === "reservoir") el("rect", { x: X(n.x) - 1.3, y: Y(n.y) - 1.3, width: 2.6, height: 2.6, class: "src" }, svg);
  });
  return { X, Y, w, minX, maxX };
}

function drawNetwork(net) {
  const svg = document.getElementById("net");
  const { X, Y, w, minX, maxX } = netBase(svg, net);
  const r = Math.max(0.9, 1.25 * (w / 100));
  net.stations.forEach((s) => {
    const g = el("g", { class: `st st-${s.class}`, tabindex: "0", role: "button", "aria-label": `${s.id}: ${s.sensors.join(", ")}` }, svg);
    g.style.setProperty("--d", (((s.x - minX) / (maxX - minX)) * 0.5).toFixed(2) + "s");
    el("circle", { cx: X(s.x), cy: Y(s.y), r: r * 2.1, class: "halo" }, g);
    el("circle", { cx: X(s.x), cy: Y(s.y), r, class: "dot" }, g);
    const kind = { pump: "Pump station", tank: "Tank station", logger: "Pressure logger" }[s.class] || s.class;
    const html = `<b>${esc(s.id)}</b> <span class="muted">${kind}, node ${esc(s.node)}</span><br>${s.sensors.map(esc).join("<br>")}`;
    const at = (e) => { const b = g.getBoundingClientRect(); return e && e.clientX ? [e.clientX, e.clientY] : [b.right, b.top]; };
    g.addEventListener("mousemove", (e) => showTip(html, ...at(e)));
    g.addEventListener("mouseleave", hideTip);
    g.addEventListener("focus", () => { g.classList.add("on"); showTip(html, ...at()); });
    g.addEventListener("blur", () => { g.classList.remove("on"); hideTip(); });
  });
}

/* ---------- shared chart pieces ---------- */
function frame(host, height, m) {
  host.querySelector("svg")?.remove();
  const W = Math.max(280, host.clientWidth);
  const svg = el("svg", { viewBox: `0 0 ${W} ${height}`, width: W, height, role: "img", "aria-label": host.dataset.title || "" });
  host.appendChild(svg);
  return { svg, W, H: height, iw: W - m.l - m.r, ih: height - m.t - m.b, m };
}

function yAxis(f, max, ticks, fmtTick, title) {
  const g = el("g", { class: "grid" }, f.svg);
  ticks.forEach((t) => {
    const y = f.m.t + f.ih - (t / max) * f.ih;
    if (t > 0) el("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: y, y2: y }, g);
    const tx = el("text", { x: f.m.l - 8, y: y + 4, "text-anchor": "end" }, f.svg);
    tx.textContent = fmtTick(t);
  });
  el("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: f.m.t + f.ih, y2: f.m.t + f.ih, class: "base" }, f.svg);
  if (title) {
    const t = el("text", { x: f.m.l, y: f.m.t - 12, class: "ttl" }, f.svg);
    t.textContent = title;
  }
}

function legend(host, items) {
  host.querySelector(".legend")?.remove();
  const ul = document.createElement("ul");
  ul.className = "legend";
  ul.innerHTML = items.map((i) => `<li><i class="${i.cls || ""}" style="background:var(${i.c})"></i>${esc(i.name)}</li>`).join("");
  host.prepend(ul);
}

function numbers(host, head, rows) {
  if (host.querySelector("details.numbers")) return;
  const d = document.createElement("details");
  d.className = "numbers";
  d.innerHTML = `<summary>Show the numbers</summary><table class="tbl"><thead><tr>${head.map((h) => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr><th scope="row">${r[0]}</th>${r.slice(1).map((c) => `<td>${c}</td>`).join("")}</tr>`)
    .join("")}</tbody></table>`;
  host.appendChild(d);
}

function roundedTop(x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  if (h <= 0) return "";
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function roundedRight(x, y, w, h, r) {
  r = Math.min(r, h / 2, w);
  if (w <= 0) return "";
  return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
}

/* ---------- E1: recall by fault type ---------- */
function recallChart(host, e1, series = SERIES) {
  const SERIES = series;
  const keys = Object.keys(SERIES);
  legend(host, keys.map((k) => ({ name: SERIES[k].name, c: SERIES[k].c })));
  const narrow = host.clientWidth < 560;
  const f = frame(host, narrow ? 300 : 340, { t: 30, r: 8, b: 34, l: 44 });
  yAxis(f, 1, [0, 0.25, 0.5, 0.75, 1], (t) => pct(t), "Recall: share of faulty samples flagged");
  const groups = e1.faults;
  const gw = f.iw / groups.length;
  const bw = Math.min(34, (gw * 0.72) / keys.length);
  groups.forEach((ft, gi) => {
    const g0 = f.m.l + gi * gw + (gw - bw * keys.length - 2 * (keys.length - 1)) / 2;
    const vals = [];
    keys.forEach((k, si) => {
      const v = find(e1.recall.filter((x) => x.fault_type === ft), "method", k);
      vals.push([k, v]);
      const x = g0 + si * (bw + 2);
      const h = (v.mean / 1) * f.ih;
      el("path", { d: roundedTop(x, f.m.t + f.ih - h, bw, h, 4), fill: `var(${SERIES[k].c})`, class: "bar" }, f.svg);
      if (v.sd) {
        const top = Math.min(1, v.mean + v.sd), bot = Math.max(0, v.mean - v.sd);
        const cx = x + bw / 2, y1 = f.m.t + f.ih - top * f.ih, y2 = f.m.t + f.ih - bot * f.ih;
        el("line", { x1: cx, x2: cx, y1, y2, class: "err" }, f.svg);
        el("line", { x1: cx - 3, x2: cx + 3, y1, y2: y1, class: "err" }, f.svg);
      }
    });
    const lab = el("text", { x: f.m.l + gi * gw + gw / 2, y: f.H - 10, "text-anchor": "middle", class: "lab" }, f.svg);
    lab.textContent = narrow ? FAULTS[ft].split(" ")[0] : FAULTS[ft];
    const hit = el("rect", { x: f.m.l + gi * gw, y: f.m.t, width: gw, height: f.ih, class: "hit" }, f.svg);
    const html = `<b>${FAULTS[ft]}</b>` + vals.map(([k, v]) => `<div class="row"><span><i style="background:var(${SERIES[k].c})"></i>${SERIES[k].name}</span><span>${pct(v.mean)} ± ${pct(v.sd)}</span></div>`).join("");
    hit.addEventListener("mousemove", (e) => showTip(html, e.clientX, e.clientY));
    hit.addEventListener("mouseleave", hideTip);
  });
  numbers(host, ["Fault type", ...keys.map((k) => SERIES[k].name)],
    groups.map((ft) => [FAULTS[ft], ...keys.map((k) => { const v = find(e1.recall.filter((x) => x.fault_type === ft), "method", k); return `${pct(v.mean)} ± ${pct(v.sd)}`; })]));
}

function detectCallouts(e1) {
  const box = document.getElementById("detect-callouts");
  box.innerHTML = Object.keys(SERIES).map((k) => {
    const o = e1.overall[k];
    return `<div style="--c:var(${SERIES[k].c})"><span class="big">${fmt(o.f1.mean)} <small>F1</small></span><p>${SERIES[k].name}. False alarms on ${pct(o.false_alarm_rate.mean, 1)} of clean samples.</p></div>`;
  }).join("");
}

/* ---------- E2: reconstruction error vs gap ---------- */
function gapChart(host, e2) {
  const lines = [
    { k: "holt_winters", name: "Station forecast (Holt-Winters)", c: "--edge" },
    { k: "spatial", name: "Neighbouring sensors (spatial)", c: "--central" },
    { k: "linear_offline", name: "Linear interpolation (needs the future)", c: "--neutral", dash: true },
  ];
  legend(host, lines.map((l) => ({ name: l.name, c: l.c, cls: l.dash ? "ln dash" : "ln" })));
  const gaps = [...new Set(e2.pressure_mae.map((x) => x.gap_min))].sort((a, b) => a - b);
  const narrow = host.clientWidth < 560;
  const f = frame(host, narrow ? 300 : 330, { t: 30, r: narrow ? 16 : 150, b: 34, l: 44 });
  const data = lines.map((l) => ({ ...l, v: gaps.map((g) => find(e2.pressure_mae.filter((x) => x.method === l.k), "gap_min", g)) }));
  const max = Math.ceil(Math.max(...data.flatMap((d) => d.v.map((x) => x.mean))));
  yAxis(f, max, Array.from({ length: max + 1 }, (_, i) => i), (t) => t + " m", "Mean absolute error, pressure");
  const lx = (g) => f.m.l + ((Math.log(g) - Math.log(gaps[0])) / (Math.log(gaps.at(-1)) - Math.log(gaps[0]))) * f.iw;
  const ly = (v) => f.m.t + f.ih - (v / max) * f.ih;
  const gl = (g) => (g < 60 ? g + " min" : g / 60 + " h");
  gaps.forEach((g) => {
    const t = el("text", { x: lx(g), y: f.H - 10, "text-anchor": "middle", class: "lab" }, f.svg);
    t.textContent = gl(g);
  });
  const ends = [];
  data.forEach((d) => {
    const pts = d.v.map((x, i) => `${i ? "L" : "M"}${lx(gaps[i]).toFixed(1)},${ly(x.mean).toFixed(1)}`).join("");
    const p = el("path", { d: pts, class: "series-line", stroke: `var(${d.c})` }, f.svg);
    if (d.dash) p.setAttribute("stroke-dasharray", "6 5");
    d.v.forEach((x, i) => el("circle", { cx: lx(gaps[i]), cy: ly(x.mean), r: 4, fill: `var(${d.c})`, stroke: "var(--paper)", "stroke-width": 2 }, f.svg));
    ends.push({ d, y: ly(d.v.at(-1).mean) });
  });
  if (!narrow) {
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 16) ends[i].y = ends[i - 1].y + 16;
    ends.forEach(({ d, y }) => {
      const t = el("text", { x: f.m.l + f.iw + 10, y: y + 4, class: "lab" }, f.svg);
      t.textContent = d.name.split(" (")[0] + "  " + fmt(d.v.at(-1).mean) + " m";
    });
  }
  const xh = el("line", { class: "xh", y1: f.m.t, y2: f.m.t + f.ih, visibility: "hidden" }, f.svg);
  const hit = el("rect", { x: f.m.l - 10, y: f.m.t, width: f.iw + 20, height: f.ih, class: "hit" }, f.svg);
  hit.addEventListener("mousemove", (e) => {
    const b = f.svg.getBoundingClientRect();
    const mx = ((e.clientX - b.left) / b.width) * f.W;
    let gi = 0;
    gaps.forEach((g, i) => { if (Math.abs(lx(g) - mx) < Math.abs(lx(gaps[gi]) - mx)) gi = i; });
    xh.setAttribute("x1", lx(gaps[gi])); xh.setAttribute("x2", lx(gaps[gi])); xh.setAttribute("visibility", "visible");
    showTip(`<b>Gap of ${gl(gaps[gi])}</b>` + data.map((d) => `<div class="row"><span><i style="background:var(${d.c})"></i>${d.name.split(" (")[0]}</span><span>${fmt(d.v[gi].mean)} ± ${fmt(d.v[gi].sd)} m</span></div>`).join(""), e.clientX, e.clientY);
  });
  hit.addEventListener("mouseleave", () => { hideTip(); xh.setAttribute("visibility", "hidden"); });
  numbers(host, ["Gap", ...data.map((d) => d.name)], gaps.map((g, i) => [gl(g), ...data.map((d) => `${fmt(d.v[i].mean)} ± ${fmt(d.v[i].sd)} m`)]));
}

/* ---------- Phase 1 (v0.2 station) ---------- */
const V2 = {
  edge_hw: { name: "Station v0.1", c: "--neutral" },
  edge_v2: { name: "Station v0.2", c: "--edge" },
};

function v2Callouts(p) {
  const box = document.getElementById("v2-callouts");
  box.innerHTML = Object.keys(V2).map((k) => {
    const o = p.v1_v2[k];
    return `<div style="--c:var(${V2[k].c})"><span class="big">${fmt(o.f1.mean)} <small>F1</small></span><p>${V2[k].name}. False alarms on ${pct(o.false_alarm_rate.mean, 1)} of clean samples.</p></div>`;
  }).join("");
}

// Dot plot on log-log axes with the identity line: nominal level against empirical rate.
function conformalChart(host, rows) {
  const series = [
    { b: 1, name: "One threshold per sensor", c: "--edge" },
    { b: 4, name: "Four time-of-day buckets", c: "--neutral" },
  ];
  legend(host, series.map((s) => ({ name: s.name, c: s.c })));
  const narrow = host.clientWidth < 420;
  const f = frame(host, narrow ? 280 : 300, { t: 30, r: 14, b: 40, l: 54 });
  const lo = 0.0003, hi = 0.02;
  const L = (v) => (Math.log(Math.max(v, lo)) - Math.log(lo)) / (Math.log(hi) - Math.log(lo));
  const X = (v) => f.m.l + L(v) * f.iw, Y = (v) => f.m.t + f.ih - L(v) * f.ih;
  const ticks = [0.001, 0.005, 0.01];
  const g = el("g", { class: "grid" }, f.svg);
  ticks.forEach((t) => {
    el("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: Y(t), y2: Y(t) }, g);
    const ty = el("text", { x: f.m.l - 8, y: Y(t) + 4, "text-anchor": "end" }, f.svg); ty.textContent = pct(t, 1);
    const tx = el("text", { x: X(t), y: f.H - 22, "text-anchor": "middle", class: "lab" }, f.svg); tx.textContent = pct(t, 1);
  });
  const xt = el("text", { x: f.m.l + f.iw / 2, y: f.H - 4, "text-anchor": "middle", class: "lab" }, f.svg);
  xt.textContent = "Nominal level (calibrated on week 7)";
  const tt = el("text", { x: f.m.l, y: f.m.t - 12, class: "ttl" }, f.svg);
  tt.textContent = "Rejected clean samples, test week (log scale)";
  el("line", { x1: X(lo), y1: Y(lo), x2: X(hi), y2: Y(hi), class: "xh" }, f.svg);
  el("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: f.m.t + f.ih, y2: f.m.t + f.ih, class: "base" }, f.svg);
  const alphas = [...new Set(rows.map((r) => r.alpha))].sort((a, b) => a - b);
  series.forEach((s, si) => {
    rows.filter((r) => r.buckets === s.b).forEach((r) => {
      const dx = (si - 0.5) * 8;
      const cx = X(r.alpha) + dx, cy = Y(r.mean);
      if (r.sd) el("line", { x1: cx, x2: cx, y1: Y(r.mean + r.sd), y2: Y(Math.max(r.mean - r.sd, lo)), class: "err" }, f.svg);
      const c = el("circle", { cx, cy, r: 6, fill: `var(${s.c})`, stroke: "var(--paper)", "stroke-width": 2 }, f.svg);
      const html = `<b>${s.name}</b><br>Nominal ${pct(r.alpha, 1)}: rejected ${pct(r.mean, 2)} ± ${pct(r.sd, 2)}${r.mean === 0 ? "<br>(no finite threshold for most 15-minute sensors)" : ""}`;
      c.addEventListener("mousemove", (e) => showTip(html, e.clientX, e.clientY));
      c.addEventListener("mouseleave", hideTip);
    });
  });
  numbers(host, ["Nominal level", ...series.map((s) => s.name)], alphas.map((a) => [pct(a, 1), ...series.map((s) => { const r = rows.find((x) => x.alpha === a && x.buckets === s.b); return `${pct(r.mean, 2)} ± ${pct(r.sd, 2)}`; })]));
}

function coverageChart(host, rows, nominal) {
  const lines = [
    { k: "holt_winters", name: "Station forecast", c: "--edge" },
    { k: "spatial", name: "Neighbouring sensors", c: "--central" },
    { k: "hybrid_width", name: "Narrower interval chosen", c: "--hybrid" },
  ];
  legend(host, lines.map((l) => ({ name: l.name, c: l.c, cls: "ln" })));
  const gaps = [...new Set(rows.map((x) => x.gap_min))].sort((a, b) => a - b);
  const narrow = host.clientWidth < 420;
  const f = frame(host, narrow ? 280 : 300, { t: 30, r: 14, b: 34, l: 44 });
  const lo = 0.6;
  const ly = (v) => f.m.t + f.ih - ((v - lo) / (1 - lo)) * f.ih;
  const g = el("g", { class: "grid" }, f.svg);
  [0.6, 0.7, 0.8, 0.9, 1].forEach((t) => {
    if (t > lo) el("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: ly(t), y2: ly(t) }, g);
    const tx = el("text", { x: f.m.l - 8, y: ly(t) + 4, "text-anchor": "end" }, f.svg); tx.textContent = pct(t);
  });
  el("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: f.m.t + f.ih, y2: f.m.t + f.ih, class: "base" }, f.svg);
  const tt = el("text", { x: f.m.l, y: f.m.t - 12, class: "ttl" }, f.svg);
  tt.textContent = "Coverage of the intervals, test-week gaps";
  el("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: ly(nominal), y2: ly(nominal), class: "xh" }, f.svg);
  const nt = el("text", { x: f.m.l + 4, y: ly(nominal) - 6, class: "lab" }, f.svg);
  nt.textContent = "nominal " + pct(nominal);
  const lx = (gm) => f.m.l + 10 + ((Math.log(gm) - Math.log(gaps[0])) / (Math.log(gaps.at(-1)) - Math.log(gaps[0]))) * (f.iw - 20);
  const gl = (gm) => (gm < 60 ? gm + " min" : gm / 60 + " h");
  gaps.forEach((gm) => { const t = el("text", { x: lx(gm), y: f.H - 10, "text-anchor": "middle", class: "lab" }, f.svg); t.textContent = gl(gm); });
  const data = lines.map((l) => ({ ...l, v: gaps.map((gm) => rows.find((x) => x.method === l.k && x.gap_min === gm)) }));
  data.forEach((d) => {
    const pts = d.v.map((x, i) => `${i ? "L" : "M"}${lx(gaps[i]).toFixed(1)},${ly(Math.max(x.mean, lo)).toFixed(1)}`).join("");
    el("path", { d: pts, class: "series-line", stroke: `var(${d.c})` }, f.svg);
    d.v.forEach((x, i) => el("circle", { cx: lx(gaps[i]), cy: ly(Math.max(x.mean, lo)), r: 4, fill: `var(${d.c})`, stroke: "var(--paper)", "stroke-width": 2 }, f.svg));
  });
  const xh = el("line", { class: "xh", y1: f.m.t, y2: f.m.t + f.ih, visibility: "hidden" }, f.svg);
  const hit = el("rect", { x: f.m.l, y: f.m.t, width: f.iw, height: f.ih, class: "hit" }, f.svg);
  hit.addEventListener("mousemove", (e) => {
    const b = f.svg.getBoundingClientRect();
    const mx = ((e.clientX - b.left) / b.width) * f.W;
    let gi = 0;
    gaps.forEach((gm, i) => { if (Math.abs(lx(gm) - mx) < Math.abs(lx(gaps[gi]) - mx)) gi = i; });
    xh.setAttribute("x1", lx(gaps[gi])); xh.setAttribute("x2", lx(gaps[gi])); xh.setAttribute("visibility", "visible");
    showTip(`<b>Gap of ${gl(gaps[gi])}</b>` + data.map((d) => `<div class="row"><span><i style="background:var(${d.c})"></i>${d.name}</span><span>${pct(d.v[gi].mean)} ± ${pct(d.v[gi].sd)}</span></div>`).join(""), e.clientX, e.clientY);
  });
  hit.addEventListener("mouseleave", () => { hideTip(); xh.setAttribute("visibility", "hidden"); });
  numbers(host, ["Gap", ...data.map((d) => d.name)], gaps.map((gm, i) => [gl(gm), ...data.map((d) => `${pct(d.v[i].mean)} ± ${pct(d.v[i].sd)}`)]));
}

function memoryTable(rows) {
  const t = document.getElementById("t-memory");
  if (!rows) { t.closest("article").hidden = true; return; }
  const kind = { pump: "pump station", tank: "tank station", logger: "logger" };
  const sram = 520 * 1024;
  t.innerHTML = `<thead><tr><th scope="col">Station</th><th scope="col">Version</th><th scope="col">Live heap</th><th scope="col">Share of 520 KB</th><th scope="col">Model package</th></tr></thead><tbody>${rows
    .filter((r) => r.version !== "v0.2 float32")
    .map((r) => `<tr><th scope="row">${esc(r.station)} (${kind[r.cls] || r.cls})</th><td>${esc(r.version === "v0.2 int8" ? "v0.2" : r.version)}</td><td>${fmt(r.heap / 1024, 1)} KB</td><td>${pct(r.heap / sram, 1)}</td><td>${fmt(r.package / 1024, 1)} KB</td></tr>`).join("")}</tbody>`;
}

/* ---------- horizontal bars (uplink, twin) ---------- */
function hbars(host, rows, { unit, digits, axisTitle, labNarrow = 128 }) {
  const narrow = host.clientWidth < 420;
  const labW = narrow ? labNarrow : 172;
  const bh = 26, gap = 10;
  const f = frame(host, 30 + rows.length * (bh + gap) + 6, { t: 30, r: 78, b: 6, l: labW });
  const max = Math.max(...rows.map((r) => r.mean + (r.sd || 0)));
  const t = el("text", { x: 0, y: 14, class: "ttl" }, f.svg);
  t.textContent = axisTitle;
  rows.forEach((r, i) => {
    const y = f.m.t + i * (bh + gap);
    const w = (r.mean / max) * f.iw;
    const lab = el("text", { x: labW - 10, y: y + bh / 2 + 4, "text-anchor": "end", class: "lab" }, f.svg);
    lab.textContent = r.label;
    el("path", { d: roundedRight(labW, y, w, bh, 4), fill: `var(${r.c})` }, f.svg);
    const v = el("text", { x: labW + w + 8, y: y + bh / 2 + 4, class: "val" }, f.svg);
    v.textContent = fmt(r.mean, digits) + (unit ? " " + unit : "");
    const hit = el("rect", { x: 0, y, width: f.W, height: bh, class: "hit" }, f.svg);
    hit.addEventListener("mousemove", (e) => showTip(`<b>${esc(r.label)}</b><br>${fmt(r.mean, digits)} ± ${fmt(r.sd, digits)} ${unit}${r.extra ? "<br>" + r.extra : ""}`, e.clientX, e.clientY));
    hit.addEventListener("mouseleave", hideTip);
  });
  el("line", { x1: labW, x2: labW, y1: f.m.t - 4, y2: f.H - 2, class: "base" }, f.svg);
  numbers(host, ["", axisTitle], rows.map((r) => [r.label, `${fmt(r.mean, digits)} ± ${fmt(r.sd, digits)}${unit ? " " + unit : ""}`]));
}


/* ---------- Phase 2 ---------- */
const INPUTS = {
  raw: { name: "Raw readings", c: "--neutral" },
  edge_validated: { name: "Validated at the station", c: "--edge" },
  hybrid_validated: { name: "Hybrid validation", c: "--hybrid" },
  clean: { name: "Clean, no faults", c: "--neutral-2" },
};
const P2 = {
  edge_hw: { name: "Station (Holt-Winters)", c: "--edge" },
  central: { name: "Centre, spatial model", c: "--central" },
  central_model: { name: "Centre, twin residual", c: "--central-2" },
  hybrid: { name: "Hybrid fusion", c: "--hybrid" },
};

function leakMap(net, leak, input) {
  const svg = document.getElementById("leakmap");
  const { X, Y, w } = netBase(svg, net);
  const m = leak.map;
  const u = w / 100;
  m.sensors.forEach((s) => el("circle", { cx: X(s.x), cy: Y(s.y), r: 0.9 * u, class: "lk-sensor" }, svg));
  const run = m.inputs[input];
  const tipAt = (g, html) => {
    g.addEventListener("mousemove", (e) => showTip(html, e.clientX, e.clientY));
    g.addEventListener("mouseleave", hideTip);
    g.addEventListener("focus", () => { const b = g.getBoundingClientRect(); showTip(html, b.right, b.top); });
    g.addEventListener("blur", hideTip);
  };
  [...run.top].reverse().forEach((c, ri) => {
    const rank = run.top.length - ri;
    const g = el("g", { class: "lk-cand", tabindex: "0", "aria-label": `Candidate ${rank}: node ${c.node}` }, svg);
    const rr = (2.4 - 0.3 * (rank - 1)) * u;
    el("circle", { cx: X(c.x), cy: Y(c.y), r: rr, class: "dot" }, g);
    const t = el("text", { x: X(c.x), y: Y(c.y) + 0.45 * rr, "text-anchor": "middle", class: "lk-num", "font-size": (1.25 * rr).toFixed(2) }, g);
    t.textContent = rank;
    tipAt(g, `<b>Candidate ${rank}: node ${esc(c.node)}</b><br>Score ${fmt(c.score, 3)}<br>${fmt(c.dist_m, 0)} m of pipe from the leak`);
  });
  const g = el("g", { class: "lk-true", tabindex: "0", "aria-label": `Leak at node ${m.true_node}` }, svg);
  const [tx, ty] = m.true_xy;
  el("circle", { cx: X(tx), cy: Y(ty), r: 2.6 * u, class: "ring" }, g);
  el("path", { d: `M${X(tx) - 1.3 * u},${Y(ty)}H${X(tx) + 1.3 * u}M${X(tx)},${Y(ty) - 1.3 * u}V${Y(ty) + 1.3 * u}`, class: "cross" }, g);
  tipAt(g, `<b>Leak at node ${esc(m.true_node)}</b><br>Ranked ${run.rank} of ${leak.n_candidates} with this input`);
  document.getElementById("leak-cap").textContent =
    `${INPUTS[input].name}: the leak's node is ranked ${run.rank} of ${leak.n_candidates}; the first candidate is ${fmt(run.distance_m, 0)} m of pipe away.`;
}

function leakControls(net, leak) {
  const box = document.getElementById("leak-inputs");
  let current = "edge_validated";
  box.innerHTML = leak.inputs.map((k) => `<button type="button" data-k="${k}" aria-pressed="${k === current}">${INPUTS[k].name}</button>`).join("");
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    current = b.dataset.k;
    box.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.k === current)));
    leakMap(net, leak, current);
  });
  const m = leak.map;
  document.getElementById("leak-case").textContent =
    `Seed ${m.seed}, a leak of ${fmt(m.area_m2 * 1e4, 0)} cm² opening at node ${m.true_node}, starting ${fmt(m.onset_h, 1)} h after midnight.`;
  const host = document.getElementById("leakmap-wrap");
  numbers(host, ["Input", "Rank of the leak's node", "First candidate, m from leak", "Top 5 candidates"],
    leak.inputs.map((k) => { const r = m.inputs[k]; return [INPUTS[k].name, r.rank, fmt(r.distance_m, 0), r.top.map((c) => esc(c.node)).join(", ")]; }));
  return () => leakMap(net, leak, current);
}

function model2Callouts(e1p) {
  const box = document.getElementById("model-callouts");
  box.innerHTML = ["central", "central_model"].map((k) => {
    const o = e1p.overall[k];
    return `<div style="--c:var(${P2[k].c})"><span class="big">${fmt(o.f1.mean)} <small>F1</small></span><p>${P2[k].name}, pressure sensors. False alarms on ${pct(o.false_alarm_rate.mean, 1)} of clean samples.</p></div>`;
  }).join("");
}

const CL_CASES = [
  ["default (Net3.inp coefficients)", "Net3.inp, no decay", "--neutral"],
  ["estimated, lab", "Fitted to lab", "--central"],
  ["estimated, lab + analysers", "Fitted, lab + analysers", "--central-2"],
  ["true kb, model wall", "True kb, no wall term", "--neutral-2"],
  ["true kb and wall", "True kb and wall", "--neutral-2"],
];

function kbChart(host, cl) {
  const lines = [
    { k: "lab", name: "Lab results only", c: "--central" },
    { k: "lab_analyser", name: "Lab and analysers", c: "--central-2" },
  ];
  legend(host, [...lines.map((l) => ({ name: l.name, c: l.c, cls: "ln" })), { name: "True bulk kb", c: "--neutral", cls: "dash" }]);
  const tr = cl.trajectory;
  const days = [...new Set(tr.map((x) => x.day))].sort((a, b) => a - b);
  const narrow = host.clientWidth < 420;
  const f = frame(host, narrow ? 280 : 300, { t: 30, r: 14, b: 34, l: 50 });
  const vals = tr.flatMap((x) => [x.mean - (x.sd || 0), x.mean + (x.sd || 0)]).concat([cl.true_kb]);
  const lo = Math.floor((Math.min(...vals) - 0.05) * 10) / 10, hi = Math.ceil((Math.max(...vals) + 0.05) * 10) / 10;
  const ly = (v) => f.m.t + f.ih - ((v - lo) / (hi - lo)) * f.ih;
  const lx = (d) => f.m.l + ((d - days[0]) / (days.at(-1) - days[0])) * f.iw;
  const g = el("g", { class: "grid" }, f.svg);
  for (let t = lo; t <= hi + 1e-9; t += 0.1) {
    el("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: ly(t), y2: ly(t) }, g);
    const tx = el("text", { x: f.m.l - 8, y: ly(t) + 4, "text-anchor": "end" }, f.svg); tx.textContent = fmt(t, 1);
  }
  (narrow ? [7, 21, 35, 49] : [7, 14, 21, 28, 35, 42, 49]).filter((d) => d >= days[0] && d <= days.at(-1)).forEach((d) => {
    const t = el("text", { x: lx(d), y: f.H - 12, "text-anchor": "middle", class: "lab" }, f.svg); t.textContent = "day " + d;
  });
  const tt = el("text", { x: f.m.l, y: f.m.t - 12, class: "ttl" }, f.svg);
  tt.textContent = "Bulk decay coefficient kb (1/day), 14-day window";
  el("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: ly(cl.true_kb), y2: ly(cl.true_kb), class: "ref-dash" }, f.svg);
  const nt = el("text", { x: f.m.l + 4, y: ly(cl.true_kb) - 6, class: "lab" }, f.svg);
  nt.textContent = "true bulk kb " + fmt(cl.true_kb, 1);
  const data = lines.map((l) => ({ ...l, v: days.map((d) => tr.find((x) => x.variant === l.k && x.day === d)) }));
  data.forEach((d) => {
    const pts = d.v.map((x, i) => (x ? `${i ? "L" : "M"}${lx(days[i]).toFixed(1)},${ly(x.mean).toFixed(1)}` : "")).join("");
    el("path", { d: pts, class: "series-line", stroke: `var(${d.c})` }, f.svg);
  });
  const xh = el("line", { class: "xh", y1: f.m.t, y2: f.m.t + f.ih, visibility: "hidden" }, f.svg);
  const hit = el("rect", { x: f.m.l, y: f.m.t, width: f.iw, height: f.ih, class: "hit" }, f.svg);
  hit.addEventListener("mousemove", (e) => {
    const b = f.svg.getBoundingClientRect();
    const mx = ((e.clientX - b.left) / b.width) * f.W;
    let gi = 0;
    days.forEach((d, i) => { if (Math.abs(lx(d) - mx) < Math.abs(lx(days[gi]) - mx)) gi = i; });
    xh.setAttribute("x1", lx(days[gi])); xh.setAttribute("x2", lx(days[gi])); xh.setAttribute("visibility", "visible");
    showTip(`<b>Day ${days[gi]}</b>` + data.map((d) => `<div class="row"><span><i style="background:var(${d.c})"></i>${d.name}</span><span>${fmt(d.v[gi].mean)} ± ${fmt(d.v[gi].sd)}</span></div>`).join(""), e.clientX, e.clientY);
  });
  hit.addEventListener("mouseleave", () => { hideTip(); xh.setAttribute("visibility", "hidden"); });
  const shown = days.filter((d) => d % 7 === 0 || d === days[0]);
  numbers(host, ["Day", ...data.map((d) => d.name)], shown.map((day) => { const i = days.indexOf(day); return [day, ...data.map((d) => `${fmt(d.v[i].mean)} ± ${fmt(d.v[i].sd)}`)]; }));
}

/* ---------- tables ---------- */
function outageTable(rows) {
  const lab = { edge_store_forward: "Validation at the station", centralised_raw: "Validation in the cloud" };
  const t = document.getElementById("t-outage");
  const body = rows.sort((a, b) => b.outage_h - a.outage_h || (a.architecture < b.architecture ? 1 : -1))
    .map((r) => `<tr><th scope="row">${lab[r.architecture]}</th><td>${r.outage_h} h</td><td>${fmt(r.events_flagged_during_outage, 1)} of ${fmt(r.fault_events_in_window, 1)}</td><td>${fmt(r.backfill_h, 1)} h</td></tr>`).join("");
  t.innerHTML = `<thead><tr><th scope="col">Design</th><th scope="col">Outage</th><th scope="col">Fault events flagged during it</th><th scope="col">Catch-up after</th></tr></thead><tbody>${body}</tbody>`;
}

function trainTable(e5) {
  const order = [["centralised", "Pooled central training"], ["federated_finetuned", "Federated, then fine-tuned locally"], ["federated", "Federated averaging"], ["local", "The station's own data only"]];
  const best = Math.min(...e5.cold_one_step.map((x) => x.mean));
  const t = document.getElementById("t-train");
  t.innerHTML = `<thead><tr><th scope="col">Training</th><th scope="col">Next-step RMSE</th><th scope="col">Day-ahead RMSE</th></tr></thead><tbody>${order
    .map(([k, name]) => {
      const a = find(e5.cold_one_step, "paradigm", k), b = find(e5.cold_day_ahead, "paradigm", k);
      return `<tr><th scope="row">${name}</th><td class="${a.mean === best ? "best" : ""}">${fmt(a.mean)} m</td><td>${fmt(b.mean)} m</td></tr>`;
    }).join("")}</tbody>`;
}

/* ---------- copy buttons ---------- */
document.querySelectorAll(".copy").forEach((b) => b.addEventListener("click", async () => {
  const text = b.parentElement.querySelector("code").innerText.replace(/\s+#.*$/gm, "");
  try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; } catch { b.textContent = "Select and copy"; }
  setTimeout(() => (b.textContent = "Copy"), 1600);
}));

/* ---------- boot ---------- */
if (!reduced) document.body.classList.add("anim");

fetch("data.json").then((r) => r.json()).then((d) => {
  document.getElementById("f-stations").textContent = d.network.stations.length;
  document.getElementById("f-sensors").textContent = d.network.n_sensors;
  document.getElementById("f-weeks").textContent = d.weeks;
  document.querySelectorAll("#f-seeds, .n-seeds").forEach((n) => (n.textContent = d.seeds.length));
  const bin = Object.fromEntries(d.e3.binary.map((x) => [x.policy, x]));
  const js = Object.fromEntries(d.e3.json.map((x) => [x.policy, x]));
  document.getElementById("json-ratio").textContent = Math.round(js.validated.mean / bin.validated.mean);

  drawNetwork(d.network);
  detectCallouts(d.e1);
  outageTable(d.e4);
  trainTable(d.e5);
  v2Callouts(d.phase1);
  memoryTable(d.phase1.footprint);
  const p2 = d.phase2;
  const redrawLeak = leakControls(d.network, p2.leak);
  model2Callouts(p2.e1_pressure);
  document.getElementById("kb-true").textContent = fmt(p2.chlorine.true_kb, 1);
  document.getElementById("kb-est").textContent = fmt(find(p2.chlorine.kb, "case", "estimated, lab").mean, 1);
  document.querySelectorAll(".n-cand").forEach((n) => (n.textContent = p2.leak.n_candidates));
  document.querySelectorAll(".n-cases").forEach((n) => (n.textContent = p2.leak.cases_per_seed));
  const leakRows = (key, digits) => p2.leak.inputs.map((k) => { const v = find(p2.leak.summary[key], "input", k); return { label: INPUTS[k].name, mean: v.mean, sd: v.sd, c: INPUTS[k].c }; });
  const clRows = CL_CASES.map(([k, label, c]) => { const v = find(p2.chlorine.mae_lab, "case", k); const kb = find(p2.chlorine.kb, "case", k); return { label, mean: v.mean, sd: v.sd, c, extra: `kb ${fmt(kb.mean, 2)} /day` }; });

  const uplinkRows = [
    ["raw", "Every raw reading"], ["validated", "Validated readings"], ["summary", "15-minute summaries"], ["delta", "Send-on-delta"],
  ].map(([k, label]) => ({ label, mean: bin[k].mean, sd: bin[k].sd, c: "--edge", extra: `As SensorThings JSON: ${fmt(js[k].mean / 1000, 1)} kB` }));
  const twinRows = [
    ["raw", "Raw readings", "--neutral"], ["edge_validated", "Validated at the station", "--edge"],
    ["hybrid_validated", "Hybrid validation", "--hybrid"], ["clean", "Clean, no faults", "--neutral-2"],
  ].map(([k, label, c]) => { const v = find(d.e6.unmonitored, "input", k); return { label, mean: v.mean, sd: v.sd, c }; });

  const render = () => {
    recallChart(document.getElementById("c-recall"), d.e1);
    gapChart(document.getElementById("c-gap"), d.e2);
    hbars(document.getElementById("c-uplink"), uplinkRows, { unit: "B", digits: 0, axisTitle: "Binary bytes per station per day" });
    hbars(document.getElementById("c-twin"), twinRows, { unit: "m", digits: 2, axisTitle: "Pressure RMSE at unmonitored nodes" });
    recallChart(document.getElementById("c-v2"), d.e1, V2);
    conformalChart(document.getElementById("c-conformal"), d.phase1.conformal);
    coverageChart(document.getElementById("c-coverage"), d.phase1.coverage, d.phase1.nominal_coverage);
    redrawLeak();
    hbars(document.getElementById("c-leakdist"), leakRows("dist"), { unit: "m", digits: 0, axisTitle: "Median distance, first candidate to leak" });
    hbars(document.getElementById("c-leaktop5"), leakRows("top5"), { unit: "", digits: 2, axisTitle: "Share of leaks with their node in the top 5" });
    recallChart(document.getElementById("c-model"), p2.e1_pressure, P2);
    hbars(document.getElementById("c-clmae"), clRows, { unit: "mg/L", digits: 3, axisTitle: "Chlorine MAE at the lab sampling points, week 8", labNarrow: 156 });
    kbChart(document.getElementById("c-kb"), p2.chlorine);
  };
  render();
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(render, 150); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
}).catch((err) => {
  document.querySelectorAll(".chart").forEach((c) => (c.textContent = "The chart data did not load (" + err.message + "). The same numbers are in results/summary.md in the repository."));
});
