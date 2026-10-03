/* Generic drawing/formatting helpers shared by the page and the Python-side checker.
   Pure JS: no DOM access, so it also runs inside the headless QuickJS checker.
   Generated spec code calls these as H.*; every helper returns an SVG/HTML string. */
var H = (function () {
  function num(x) { x = Number(x); return isFinite(x) ? x : 0; }
  function fmt(x, d) {
    if (typeof x !== 'number') return String(x);
    if (isNaN(x)) return 'NaN';
    if (!isFinite(x)) return x > 0 ? '∞' : '-∞';
    d = d == null ? 3 : d;
    var r = Number(x.toFixed(d));
    if (r === 0 && x !== 0) r = Number(x.toPrecision(2)); // never show a tiny non-zero value (e.g. 0.0001) as 0
    return String(r);
  }
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  // Theme-aware colours: CSS variables defined per theme in template.html, so charts follow the theme switcher.
  var C = { ink: 'var(--ink)', mute: 'var(--mute)', chart1: 'var(--chart1)', chart2: 'var(--chart2)', chart3: 'var(--chart3)',
            good: 'var(--ok)', bad: 'var(--bad)', grid: 'var(--grid)', axis: 'var(--axis)', bg: 'var(--card)' };
  // sequential colour, t in [0,1]: theme's pale heat colour -> theme's deep heat colour
  function color(t) {
    t = Math.max(0, Math.min(1, num(t)));
    return 'color-mix(in srgb, var(--heat) ' + Math.round(t * 100) + '%, var(--heat0))';
  }
  function ink(t) { return num(t) > 0.55 ? 'var(--onheat)' : 'var(--ink)'; }
  function svg(w, h, inner, label) {
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + w + ' ' + h +
      '" width="100%" style="max-width:' + w + 'px;height:auto" role="img" aria-label="' + esc(label || 'visualization') +
      '" font-family="system-ui,Segoe UI,Arial,sans-serif">' + inner + '</svg>';
  }
  function rect(x, y, w, h, fill, extra) {
    return '<rect x="' + num(x) + '" y="' + num(y) + '" width="' + Math.max(0, num(w)) + '" height="' + Math.max(0, num(h)) +
      '" fill="' + (fill || 'var(--grid)') + '" ' + (extra || '') + '/>';
  }
  function text(x, y, s, o) {
    o = o || {};
    if (s === undefined || s === null || (typeof s === 'number' && !isFinite(s))) s = '';
    return '<text x="' + num(x) + '" y="' + num(y) + '" font-size="' + (o.size || 12) + '" text-anchor="' + (o.anchor || 'start') +
      '" fill="' + (o.fill || 'var(--ink)') + '"' + (o.weight ? ' font-weight="' + o.weight + '"' : '') +
      (o.rotate ? ' transform="rotate(' + o.rotate + ' ' + num(x) + ' ' + num(y) + ')"' : '') + '>' + esc(s) + '</text>';
  }
  function line(x1, y1, x2, y2, stroke, w, dash) {
    return '<line x1="' + num(x1) + '" y1="' + num(y1) + '" x2="' + num(x2) + '" y2="' + num(y2) + '" stroke="' + (stroke || 'var(--axis)') +
      '" stroke-width="' + (w || 1) + '"' + (dash ? ' stroke-dasharray="' + dash + '"' : '') + '/>';
  }
  function circle(x, y, r, fill, extra) {
    return '<circle cx="' + num(x) + '" cy="' + num(y) + '" r="' + num(r) + '" fill="' + (fill || 'var(--chart2)') + '" ' + (extra || '') + '/>';
  }
  // Matrix as coloured cells with numbers. o: x,y,cw,ch,d,max,min,rows,cols,title,signed
  function heat(M, o) {
    o = o || {};
    var x0 = o.x || 0, y0 = o.y || 0, cw = o.cw || 54, ch = o.ch || 34, d = o.d == null ? 2 : o.d;
    var flat = [].concat.apply([], M), mx = o.max != null ? o.max : Math.max.apply(null, flat.map(Math.abs).concat([1e-9]));
    var mn = o.min != null ? o.min : 0;
    var out = '';
    if (!Array.isArray(o.rows)) o.rows = null;
    if (!Array.isArray(o.cols)) o.cols = null;
    if (o.title) out += text(x0, y0 - 8 - (o.cols ? 14 : 0), o.title, { weight: 'bold', size: 13 });
    for (var i = 0; i < M.length; i++) {
      if (o.rows && o.rows[i] != null) out += text(x0 - 6, y0 + i * ch + ch / 2 + 4, o.rows[i], { anchor: 'end', size: 11 });
      for (var j = 0; j < M[i].length; j++) {
        var v = num(M[i][j]), t = mx > mn ? (Math.abs(v) - mn) / (mx - mn) : 0;
        out += rect(x0 + j * cw, y0 + i * ch, cw - 2, ch - 2, color(t), 'rx="3"') +
          text(x0 + j * cw + (cw - 2) / 2, y0 + i * ch + ch / 2 + 4, fmt(v, d), { anchor: 'middle', size: 12, fill: ink(t) });
      }
    }
    if (o.cols) for (var k = 0; k < (M[0] || []).length && k < o.cols.length; k++) out += text(x0 + k * cw + cw / 2, y0 - 6, o.cols[k], { anchor: 'middle', size: 11 });
    return out;
  }
  // Vertical bars. o: x,y,w,h,labels,max,d,fill,fills,title,ylabel
  function bars(vals, o) {
    o = o || {};
    var x0 = o.x || 40, y0 = o.y || 10, w = o.w || 300, h = o.h || 160, d = o.d == null ? 3 : o.d;
    var mx = o.max != null ? o.max : Math.max.apply(null, vals.map(num).concat([1e-9]));
    var n = vals.length, bw = w / Math.max(1, n), out = '';
    if (o.title) out += text(x0, y0 - 2, o.title, { weight: 'bold', size: 13 });
    out += line(x0, y0 + h, x0 + w, y0 + h, 'var(--axis)') + line(x0, y0, x0, y0 + h, 'var(--axis)');
    for (var t = 0; t <= 4; t++) {
      var yy = y0 + h - h * t / 4;
      out += line(x0 - 3, yy, x0, yy, 'var(--axis)') + text(x0 - 6, yy + 4, fmt(mx * t / 4, 2), { anchor: 'end', size: 10 });
      if (t > 0) out += line(x0, yy, x0 + w, yy, 'var(--grid)');
    }
    for (var i = 0; i < n; i++) {
      var v = num(vals[i]), bh = mx > 0 ? Math.max(0, h * v / mx) : 0, bx = x0 + i * bw + bw * 0.15;
      out += rect(bx, y0 + h - bh, bw * 0.7, bh, (o.fills && o.fills[i]) || o.fill || 'var(--chart1)') +
        text(bx + bw * 0.35, y0 + h - bh - 4, fmt(v, d), { anchor: 'middle', size: 11 });
      if (Array.isArray(o.labels) && o.labels[i] != null) out += text(bx + bw * 0.35, y0 + h + 15, o.labels[i], { anchor: 'middle', size: 11 });
    }
    if (o.ylabel) out += text(12, y0 + h / 2, o.ylabel, { anchor: 'middle', size: 11, rotate: -90 });
    return out;
  }
  // Line plot. series:[{xs,ys,color,label,dash}] o: x,y,w,h,xmin,xmax,ymin,ymax,xlabel,ylabel,title,marks:[{x,y,label}]
  function plot(series, o) {
    o = o || {};
    var x0 = o.x || 46, y0 = o.y || 14, w = o.w || 320, h = o.h || 200, all = [];
    series.forEach(function (s) { s.xs.forEach(function (_, i) { all.push([num(s.xs[i]), num(s.ys[i])]); }); });
    var xmin = o.xmin != null ? o.xmin : Math.min.apply(null, all.map(function (p) { return p[0]; }));
    var xmax = o.xmax != null ? o.xmax : Math.max.apply(null, all.map(function (p) { return p[0]; }));
    var ymin = o.ymin != null ? o.ymin : Math.min.apply(null, all.map(function (p) { return p[1]; }));
    var ymax = o.ymax != null ? o.ymax : Math.max.apply(null, all.map(function (p) { return p[1]; }));
    if (xmax === xmin) xmax = xmin + 1;
    if (ymax === ymin) ymax = ymin + 1;
    function X(v) { return x0 + (num(v) - xmin) / (xmax - xmin) * w; }
    function Y(v) { return y0 + h - (num(v) - ymin) / (ymax - ymin) * h; }
    var out = '';
    if (o.title) out += text(x0, y0 - 4, o.title, { weight: 'bold', size: 13 });
    for (var t = 0; t <= 4; t++) {
      var gx = x0 + w * t / 4, gy = y0 + h - h * t / 4;
      out += line(gx, y0, gx, y0 + h, 'var(--grid)') + line(x0, gy, x0 + w, gy, 'var(--grid)') +
        text(gx, y0 + h + 15, fmt(xmin + (xmax - xmin) * t / 4, 2), { anchor: 'middle', size: 10 }) +
        text(x0 - 6, gy + 4, fmt(ymin + (ymax - ymin) * t / 4, 2), { anchor: 'end', size: 10 });
    }
    out += line(x0, y0 + h, x0 + w, y0 + h, 'var(--axis)') + line(x0, y0, x0, y0 + h, 'var(--axis)');
    series.forEach(function (s, k) {
      var pts = s.xs.map(function (_, i) { return X(s.xs[i]).toFixed(1) + ',' + Y(s.ys[i]).toFixed(1); }).join(' ');
      out += '<polyline points="' + pts + '" fill="none" stroke="' + (s.color || ['var(--chart1)', 'var(--chart2)', 'var(--chart3)'][k % 3]) +
        '" stroke-width="2.2"' + (s.dash ? ' stroke-dasharray="' + s.dash + '"' : '') + '/>';
      if (s.label) out += text(x0 + w - 4, y0 + 14 + 14 * k, s.label, { anchor: 'end', size: 11, fill: s.color || ['var(--chart1)', 'var(--chart2)', 'var(--chart3)'][k % 3] });
    });
    (o.marks || []).forEach(function (m) {
      out += circle(X(m.x), Y(m.y), 5, m.color || 'var(--chart2)') +
        (m.label ? text(X(m.x) + 8, Y(m.y) - 8, m.label, { size: 11 }) : '');
    });
    if (o.xlabel) out += text(x0 + w / 2, y0 + h + 32, o.xlabel, { anchor: 'middle', size: 11 });
    if (o.ylabel) out += text(12, y0 + h / 2, o.ylabel, { anchor: 'middle', size: 11, rotate: -90 });
    return out;
  }
  // HTML table of a matrix. o: rows, cols, d
  function table(M, o) {
    o = o || {};
    if (!Array.isArray(M[0])) M = [M];
    var d = o.d == null ? 3 : o.d, s = '<table class="mat">';
    if (!Array.isArray(o.rows)) o.rows = null;
    if (Array.isArray(o.cols)) s += '<tr><th></th>' + o.cols.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') + '</tr>';
    M.forEach(function (r, i) {
      s += '<tr>' + (o.rows && o.rows[i] != null ? '<th>' + esc(o.rows[i]) + '</th>' : (o.rows ? '<th></th>' : '')) +
        r.map(function (v) { return '<td>' + fmt(v, d) + '</td>'; }).join('') + '</tr>';
    });
    return s + '</table>';
  }
  // ---- small numeric helpers (for compute code and invariant tests) ----
  function sum(a) { var t = 0; for (var i = 0; i < a.length; i++) t += a[i]; return t; }
  // numbers, or arrays/matrices of any depth compared element by element (same shape required)
  function close(a, b, tol) {
    if (Array.isArray(a) || Array.isArray(b)) {
      if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
      for (var i = 0; i < a.length; i++) if (!close(a[i], b[i], tol)) return false;
      return true;
    }
    return Math.abs(a - b) <= (tol == null ? 1e-9 : tol) * Math.max(1, Math.abs(a), Math.abs(b));
  }
  function rowSums(M) { return M.map(sum); }
  function transpose(M) { return M[0].map(function (_, j) { return M.map(function (r) { return r[j]; }); }); }
  function matmul(A, B) {
    return A.map(function (r) { return B[0].map(function (_, j) { var t = 0; for (var k = 0; k < r.length; k++) t += r[k] * B[k][j]; return t; }); });
  }
  return { c: C, sum: sum, close: close, rowSums: rowSums, transpose: transpose, matmul: matmul, num: num, fmt: fmt, esc: esc, color: color, ink: ink, svg: svg, rect: rect, text: text, line: line, circle: circle, heat: heat, bars: bars, plot: plot, table: table };
})();
