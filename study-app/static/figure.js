"use strict";
/* Figures inside questions and lesson steps.
   - kind "diagram": a spec of named points, segments, angles, arrows … drawn here, so labels sit on the right parts.
   - kind "graph": a spec (expressions or points) that is plotted here exactly, so graphs are never "drawn by eye". */

function figureHTML(f) {
  if (!f || !f.kind) return "";
  let body = "";
  try { body = f.kind === "diagram" ? diagramSVG(f.diagram) : f.kind === "graph" ? graphSVG(f.graph || {}) : ""; }
  catch (e) { body = ""; }
  if (!body) return "";
  return `<figure class="figure">${body}${f.caption ? `<figcaption>${esc(f.caption)}</figcaption>` : ""}</figure>`;
}

/* ---------- diagrams (drawn from a spec: named points, segments, angles, arrows ...) ---------- */
const GREEK = { theta: "θ", alpha: "α", beta: "β", phi: "φ", pi: "π", omega: "ω", lambda: "λ", mu: "μ", Delta: "Δ", delta: "δ", degree: "°", circ: "°" };
// "$\theta$", "55^\circ" → "θ", "55°"
const tidy = t => String(t ?? "").replace(/\$/g, "").replace(/\^\s*\{?\\circ\}?/g, "°").replace(/\\([A-Za-z]+)/g, (m, n) => GREEK[n] || n).replace(/[{}]/g, "").trim();
let FIG_ID = 0;

function diagramSVG(d) {
  if (!d) return "";
  const pts = {};
  for (const p of d.points || []) if (p && p.name != null && num(p.x) && num(p.y)) pts[p.name] = { x: p.x, y: p.y, show: p.show };
  // scale so the drawing is ~340 units wide/tall whatever units Gemini used; flip y (spec is y-up)
  const raw = [...Object.values(pts), ...(d.boxes || []).flatMap(b => [{ x: b.x, y: b.y }, { x: b.x + b.w, y: b.y + b.h }]), ...(d.texts || [])].filter(p => num(p.x) && num(p.y));
  if (!raw.length) return "";
  const minX = Math.min(...raw.map(p => p.x)), maxX = Math.max(...raw.map(p => p.x)), minY = Math.min(...raw.map(p => p.y)), maxY = Math.max(...raw.map(p => p.y));
  const k = 340 / Math.max(maxX - minX, maxY - minY, 1e-9);
  const S = p => ({ x: (p.x - minX) * k, y: (maxY - p.y) * k });
  const P = {}; for (const n in pts) P[n] = { ...S(pts[n]), show: pts[n].show };
  const all = Object.values(P);
  const C = all.length ? { x: all.reduce((s, p) => s + p.x, 0) / all.length, y: all.reduce((s, p) => s + p.y, 0) / all.length } : { x: 170, y: 120 };
  const id = "ah" + (++FIG_ID);
  const out = [], bb = [];
  const f1 = v => v.toFixed(1);
  const keep = (x, y) => bb.push({ x, y });
  const text = (x, y, t, anchor = "middle", cls = "d-label") => { if (!t) return; out.push(`<text class="${cls}" x="${f1(x)}" y="${f1(y)}" dy=".35em" text-anchor="${anchor}">${esc(tidy(t))}</text>`); const w = tidy(t).length * 7.5; keep(anchor === "end" ? x - w : anchor === "start" ? x + w : x - w / 2, y - 9); keep(anchor === "end" ? x : anchor === "start" ? x : x + w / 2, y + 9); };
  const away = (m, nx, ny) => ((m.x - C.x) * nx + (m.y - C.y) * ny) < 0 ? [-nx, -ny] : [nx, ny];
  const unit = (dx, dy) => { const l = Math.hypot(dx, dy) || 1; return [dx / l, dy / l]; };
  const dash = on => on ? ` stroke-dasharray="6 5"` : "";

  for (const g of d.ground || []) {
    const A = P[g.a], B = P[g.b]; if (!A || !B) continue;
    const [ux, uy] = unit(B.x - A.x, B.y - A.y); let [nx, ny] = away({ x: (A.x + B.x) / 2, y: (A.y + B.y) / 2 }, -uy, ux);
    const L = Math.hypot(B.x - A.x, B.y - A.y);
    out.push(`<line class="d-ground" x1="${f1(A.x)}" y1="${f1(A.y)}" x2="${f1(B.x)}" y2="${f1(B.y)}"/>`);
    for (let t = 6; t < L; t += 12) { const x = A.x + ux * t, y = A.y + uy * t; out.push(`<line class="d-hatch" x1="${f1(x)}" y1="${f1(y)}" x2="${f1(x + nx * 8 - ux * 8)}" y2="${f1(y + ny * 8 - uy * 8)}"/>`); }
    keep(A.x + nx * 10, A.y + ny * 10); keep(B.x + nx * 10, B.y + ny * 10);
  }
  for (const c of d.circles || []) {
    const O = P[c.center]; if (!O || !num(c.r)) continue;
    const r = c.r * k; out.push(`<circle class="d-line" cx="${f1(O.x)}" cy="${f1(O.y)}" r="${f1(r)}"${dash(c.dashed)}/>`); keep(O.x - r, O.y - r); keep(O.x + r, O.y + r);
  }
  for (const b of d.boxes || []) {
    if (![b.x, b.y, b.w, b.h].every(num)) continue;
    const p = S({ x: b.x, y: b.y + b.h }), w = b.w * k, h = b.h * k;
    out.push(`<rect class="d-box" x="${f1(p.x)}" y="${f1(p.y)}" width="${f1(w)}" height="${f1(h)}" rx="2"/>`); keep(p.x, p.y); keep(p.x + w, p.y + h);
    text(p.x + w / 2, p.y + h / 2, b.label);
  }
  for (const a of d.arcs || []) {
    const O = P[a.center], A = P[a.a], B = P[a.b]; if (!O || !A || !B) continue;
    const r = Math.hypot(A.x - O.x, A.y - O.y);
    const ta = Math.atan2(O.y - A.y, A.x - O.x), tb = Math.atan2(O.y - B.y, B.x - O.x);   // math angles
    let sweep = tb - ta; while (sweep <= 0) sweep += 2 * Math.PI;
    const ex = O.x + r * Math.cos(tb), ey = O.y - r * Math.sin(tb);
    out.push(`<path class="d-line" d="M${f1(A.x)} ${f1(A.y)}A${f1(r)} ${f1(r)} 0 ${sweep > Math.PI ? 1 : 0} 0 ${f1(ex)} ${f1(ey)}"/>`);
    const tm = ta + sweep / 2; keep(O.x + r * Math.cos(tm), O.y - r * Math.sin(tm));
    text(O.x + (r + 16) * Math.cos(tm), O.y - (r + 16) * Math.sin(tm), a.label);
  }
  for (const s of d.segments || []) {
    const A = P[s.a], B = P[s.b]; if (!A || !B) continue;
    out.push(`<line class="d-line${s.arrow ? " d-vec" : ""}" x1="${f1(A.x)}" y1="${f1(A.y)}" x2="${f1(B.x)}" y2="${f1(B.y)}"${dash(s.dashed)}${s.arrow ? ` marker-end="url(#${id})"` : ""}/>`);
    keep(A.x, A.y); keep(B.x, B.y);
    if (!s.label) continue;
    const [ux, uy] = unit(B.x - A.x, B.y - A.y);
    if (s.arrow) {  // force/vector: label just past the tip
      const x = B.x + ux * 14, y = B.y + uy * 14;
      text(x + (Math.abs(ux) < 0.3 ? 6 : 0), y, s.label, Math.abs(ux) < 0.3 ? "start" : ux > 0 ? "start" : "end");
    } else {
      const m = { x: (A.x + B.x) / 2, y: (A.y + B.y) / 2 }; const [nx, ny] = away(m, -uy, ux);
      const off = 14, x = m.x + nx * off, y = m.y + ny * off;
      text(x, y, s.label, Math.abs(nx) < 0.35 ? "middle" : nx > 0 ? "start" : "end");
    }
  }
  for (const c of d.curves || []) {
    const Q = (c.through || []).map(n => P[n]).filter(Boolean); if (Q.length < 2) continue;
    let path = `M${f1(Q[0].x)} ${f1(Q[0].y)}`;
    for (let i = 0; i < Q.length - 1; i++) {   // Catmull-Rom through the points
      const p0 = Q[i - 1] || Q[i], p1 = Q[i], p2 = Q[i + 1], p3 = Q[i + 2] || p2;
      path += `C${f1(p1.x + (p2.x - p0.x) / 6)} ${f1(p1.y + (p2.y - p0.y) / 6)} ${f1(p2.x - (p3.x - p1.x) / 6)} ${f1(p2.y - (p3.y - p1.y) / 6)} ${f1(p2.x)} ${f1(p2.y)}`;
    }
    Q.forEach(q => keep(q.x, q.y));
    out.push(`<path class="d-line" d="${path}"${dash(c.dashed)}${c.arrow ? ` marker-end="url(#${id})"` : ""}/>`);
  }
  for (const a of d.angles || []) {
    const V = P[a.vertex], A = P[a.a], B = P[a.b]; if (!V || !A || !B) continue;
    const [ux, uy] = unit(A.x - V.x, A.y - V.y), [wx, wy] = unit(B.x - V.x, B.y - V.y);
    const shortest = Math.min(Math.hypot(A.x - V.x, A.y - V.y), Math.hypot(B.x - V.x, B.y - V.y));
    let [bx, by] = unit(ux + wx, uy + wy);
    if (Math.hypot(ux + wx, uy + wy) < 1e-6) [bx, by] = [-uy, ux];
    if (a.right) {
      const s = Math.min(12, shortest * 0.3);
      out.push(`<path class="d-thin" d="M${f1(V.x + ux * s)} ${f1(V.y + uy * s)}L${f1(V.x + (ux + wx) * s)} ${f1(V.y + (uy + wy) * s)}L${f1(V.x + wx * s)} ${f1(V.y + wy * s)}"/>`);
      if (a.label && a.label !== "90°") text(V.x + bx * 30, V.y + by * 30, a.label);
      continue;
    }
    const r = Math.max(12, Math.min(26, shortest * 0.4));
    const cross = ux * wy - uy * wx;  // screen orientation picks the sweep of the smaller angle
    out.push(`<path class="d-thin" d="M${f1(V.x + ux * r)} ${f1(V.y + uy * r)}A${f1(r)} ${f1(r)} 0 0 ${cross > 0 ? 1 : 0} ${f1(V.x + wx * r)} ${f1(V.y + wy * r)}"/>`);
    text(V.x + bx * (r + 14), V.y + by * (r + 14), a.label);
  }
  for (const n in P) {
    const p = P[n]; keep(p.x, p.y);
    if (!p.show) continue;
    out.push(`<circle class="d-dot" cx="${f1(p.x)}" cy="${f1(p.y)}" r="2.6"/>`);
    let [nx, ny] = unit(p.x - C.x, p.y - C.y); if (!isFinite(nx) || (Math.abs(p.x - C.x) < 1 && Math.abs(p.y - C.y) < 1)) [nx, ny] = [-0.7, 0.7];
    text(p.x + nx * 14, p.y + ny * 14, n, "middle", "d-name");
  }
  for (const t of d.texts || []) if (num(t.x) && num(t.y)) { const p = S(t); text(p.x, p.y, t.text, "middle", "d-note"); }

  if (!out.length) return "";
  const x0 = Math.min(...bb.map(p => p.x)) - 14, x1 = Math.max(...bb.map(p => p.x)) + 14, y0 = Math.min(...bb.map(p => p.y)) - 14, y1 = Math.max(...bb.map(p => p.y)) + 14;
  return `<div class="figure-svg diagram"><svg viewBox="${f1(x0)} ${f1(y0)} ${f1(x1 - x0)} ${f1(y1 - y0)}" role="img" aria-label="Diagram">
    <defs><marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="currentColor"/></marker></defs>
    ${out.join("")}</svg></div>`;
}

/* ---------- graphs ---------- */
const FN = {
  sin: "Math.sin", cos: "Math.cos", tan: "Math.tan", asin: "Math.asin", acos: "Math.acos", atan: "Math.atan",
  sqrt: "Math.sqrt", abs: "Math.abs", ln: "Math.log", log: "Math.log10", exp: "Math.exp", pi: "Math.PI", e: "Math.E",
  sind: "_sd", cosd: "_cd", tand: "_td", x: "x",
};
// Compile "2*sin(x)^2 + 1" into a function of x. Only numbers, operators and the names above are allowed.
function compileExpr(src) {
  let s = String(src).toLowerCase().replace(/\s+/g, "").replace(/π/g, "pi").replace(/^y=/, "").replace(/[×·]/g, "*").replace(/−/g, "-");
  if (!/^[0-9a-z+\-*/^().,]*$/.test(s)) return null;
  const names = s.match(/[a-z]+/g) || [];
  if (names.some(n => !(n in FN))) return null;
  s = s.replace(/(\d|\))(?=[a-z(])/g, "$1*")     // 2x, 3sin(x), (x+1)(x-1)
       .replace(/\^/g, "**")
       .replace(/[a-z]+/g, n => FN[n]);
  try {
    const f = new Function("x", "_sd", "_cd", "_td", `"use strict"; return (${s});`);
    const D = Math.PI / 180;
    const g = x => f(x, d => Math.sin(d * D), d => Math.cos(d * D), d => Math.tan(d * D));
    g(1);
    return g;
  } catch { return null; }
}

function niceStep(range, target = 6) {
  const raw = range / target, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
  return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p;
}
const num = v => typeof v === "number" && isFinite(v);
const fmtTick = v => { const r = Math.round(v * 1000) / 1000; return Math.abs(r) < 1e-9 ? "0" : String(r).replace("-", "−"); };

function graphSVG(g) {
  const series = (g.series || []).map(s => ({
    label: s.label || "", f: s.expr ? compileExpr(s.expr) : null,
    pts: (s.points || []).filter(p => num(p.x) && num(p.y)).sort((a, b) => a.x - b.x),
  })).filter(s => s.f || s.pts.length);
  if (!series.length) return "";

  // x range: given, else from the points, else −5…5
  const allPts = series.flatMap(s => s.pts).concat((g.marks || []).filter(m => num(m.x) && num(m.y)));
  let x0 = num(g.x_min) ? g.x_min : allPts.length ? Math.min(...allPts.map(p => p.x)) : -5;
  let x1 = num(g.x_max) ? g.x_max : allPts.length ? Math.max(...allPts.map(p => p.x)) : 5;
  if (!(x1 > x0)) { x0 -= 1; x1 += 1; }
  const N = 480, samples = series.map(s => s.f ? Array.from({ length: N + 1 }, (_, i) => { const x = x0 + (x1 - x0) * i / N; let y; try { y = s.f(x); } catch { y = NaN; } return { x, y }; }) : s.pts);

  // y range: given, else from the data (ignoring huge values near asymptotes)
  let y0 = g.y_min, y1 = g.y_max;
  if (!num(y0) || !num(y1) || !(y1 > y0)) {
    const ys = samples.flat().map(p => p.y).filter(y => num(y) && Math.abs(y) < 1e4).sort((a, b) => a - b);
    if (!ys.length) return "";
    const lo = ys[Math.floor(ys.length * 0.02)], hi = ys[Math.ceil(ys.length * 0.98) - 1];
    const pad = (hi - lo) * 0.12 || 1;
    y0 = num(g.y_min) ? g.y_min : Math.min(lo - pad, allPts.length ? Math.min(...allPts.map(p => p.y)) : lo);
    y1 = num(g.y_max) ? g.y_max : Math.max(hi + pad, allPts.length ? Math.max(...allPts.map(p => p.y)) : hi);
    if (lo >= 0 && y0 < 0 && lo - pad < 0) y0 = Math.min(0, lo);   // data that starts at 0 shouldn't dip below the axis
  }

  const W = 440, H = 270, L = 48, R = 16, T = 16, B = 40;
  const X = x => L + (x - x0) / (x1 - x0) * (W - L - R);
  const Y = y => H - B - (y - y0) / (y1 - y0) * (H - T - B);
  const f1 = v => v.toFixed(1);
  const parts = [];

  // grid + tick labels
  const sx = niceStep(x1 - x0), sy = niceStep(y1 - y0);
  for (let v = Math.ceil(x0 / sx) * sx; v <= x1 + 1e-9; v += sx) {
    parts.push(`<line class="g-grid" x1="${f1(X(v))}" x2="${f1(X(v))}" y1="${T}" y2="${H - B}"/>`,
      `<text class="g-tick" x="${f1(X(v))}" y="${H - B + 16}" text-anchor="middle">${fmtTick(v)}</text>`);
  }
  for (let v = Math.ceil(y0 / sy) * sy; v <= y1 + 1e-9; v += sy) {
    parts.push(`<line class="g-grid" x1="${L}" x2="${W - R}" y1="${f1(Y(v))}" y2="${f1(Y(v))}"/>`,
      `<text class="g-tick" x="${L - 7}" y="${f1(Y(v) + 4)}" text-anchor="end">${fmtTick(v)}</text>`);
  }
  // axes: through zero when zero is in range, else along the edge
  const ax = y0 <= 0 && 0 <= y1 ? Y(0) : H - B, ay = x0 <= 0 && 0 <= x1 ? X(0) : L;
  parts.push(`<line class="g-axis" x1="${L}" x2="${W - R}" y1="${f1(ax)}" y2="${f1(ax)}"/>`,
    `<line class="g-axis" x1="${f1(ay)}" x2="${f1(ay)}" y1="${T}" y2="${H - B}"/>`);
  if (g.x_label) parts.push(`<text class="g-label" x="${W - R}" y="${H - 6}" text-anchor="end">${esc(g.x_label)}</text>`);
  if (g.y_label) parts.push(`<text class="g-label" x="${L - 40}" y="${T - 4}" text-anchor="start">${esc(g.y_label)}</text>`);

  // series
  const COLORS = ["var(--pen)", "var(--amber)", "var(--green)", "var(--red)"];
  const span = y1 - y0;
  samples.forEach((pts, i) => {
    const c = COLORS[i % COLORS.length];
    let d = "", pen = false, prev = null;
    for (const p of pts) {
      const ok = num(p.y) && p.y > y0 - span * 2 && p.y < y1 + span * 2;
      const jump = prev && series[i].f && Math.abs(p.y - prev.y) > span * 1.5;   // asymptote: don't join
      if (!ok || jump) { pen = false; prev = ok ? p : null; if (!ok) continue; }
      d += `${pen ? "L" : "M"}${f1(X(p.x))} ${f1(Y(Math.max(y0 - span, Math.min(y1 + span, p.y))))}`;
      pen = true; prev = p;
    }
    parts.push(`<path class="g-line" d="${d}" style="stroke:${c}"/>`);
    if (!series[i].f && pts.length <= 30) pts.forEach(p => parts.push(`<circle class="g-dot" cx="${f1(X(p.x))}" cy="${f1(Y(p.y))}" r="3.5" style="fill:${c}"/>`));
  });
  // labelled points
  for (const m of g.marks || []) {
    if (!num(m.x) || !num(m.y) || m.x < x0 || m.x > x1 || m.y < y0 || m.y > y1) continue;
    const cx = X(m.x), cy = Y(m.y), right = cx < W - 90;
    parts.push(`<circle class="g-mark" cx="${f1(cx)}" cy="${f1(cy)}" r="4"/>`);
    if (m.label) parts.push(`<text class="g-mlabel" x="${f1(cx + (right ? 8 : -8))}" y="${f1(cy - 8)}" text-anchor="${right ? "start" : "end"}">${esc(m.label)}</text>`);
  }
  const legend = series.length > 1 && series.some(s => s.label)
    ? `<div class="g-legend">${series.map((s, i) => `<span><i style="background:${COLORS[i % COLORS.length]}"></i>${esc(s.label || `Series ${i + 1}`)}</span>`).join("")}</div>` : "";
  return `<div class="figure-svg graph"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Graph${g.y_label ? " of " + esc(g.y_label) : ""}${g.x_label ? " against " + esc(g.x_label) : ""}">${parts.join("")}</svg></div>${legend}`;
}
