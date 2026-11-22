// Höhenlinien als Leaflet-Ebene: 10-m-Linien, alle 50 m kräftig und beschriftet.
// Eigener Code (kein Fremdpaket), liegt nur wegen der Server-Route unter /vendor/.
// Höhendaten: AWS Terrain Tiles im Terrarium-Format (frei, ohne Schlüssel, CORS offen),
// weltweit ~30 m SRTM, in Europa EU-DEM 25 m, in den USA feiner. Gerechnet wird je Kachel im Browser.
(function () {
  const QUELLE = "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png";
  const DEM_MAX = 15;          // höchste Zoomstufe der Terrarium-Kacheln
  const ZOOM_MIN = 10;         // so weit wird bei Datenlücken auf gröbere Kacheln zurückgegriffen
  const CACHE_MAX = 48;        // Höhenkacheln im Speicher
  const cache = new Map();     // "z/x/y" -> Promise<Float32Array | null>

  function demKachel(z, x, y) {
    const n = 1 << z;
    x = ((x % n) + n) % n;
    if (y < 0 || y >= n) return Promise.resolve(null);
    const key = z + "/" + x + "/" + y;
    if (cache.has(key)) {
      const p = cache.get(key); cache.delete(key); cache.set(key, p);  // zuletzt benutzt nach hinten
      return p;
    }
    const p = new Promise(fertig => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        const c = document.createElement("canvas"); c.width = c.height = 256;
        const g = c.getContext("2d"); g.drawImage(img, 0, 0);
        const d = g.getImageData(0, 0, 256, 256).data, h = new Float32Array(256 * 256);
        let leer = 0;
        for (let i = 0; i < h.length; i++) {
          h[i] = d[4 * i] * 256 + d[4 * i + 1] + d[4 * i + 2] / 256 - 32768;
          if (h[i] < -11000) { h[i] = NaN; leer++; }   // 0/0/0 = keine Daten
        }
        if (z > ZOOM_MIN) leer += fuellwerte(h);
        fertig(leer ? auffuellen(h, z, x, y) : h);
      };
      img.onerror = () => { cache.delete(key); fertig(z > ZOOM_MIN ? auffuellen(new Float32Array(65536).fill(NaN), z, x, y) : null); };
      img.src = QUELLE.replace("{z}", z).replace("{x}", x).replace("{y}", y);
    });
    cache.set(key, p);
    while (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
    return p;
  }

  // Wasser ist in den Kacheln nicht gemessen, sondern gefüllt: An der Ostseeküste liegt in Zoom 15
  // eine Fläche mit exakt 11,00 m und treppenförmigem Rand, dazu einzelne Ausreißer bis −340 m.
  // Die 10-m-Linie lief dort die Treppe entlang. Beides als Lücke markieren (dann aus der gröberen
  // Kachel gefüllt): jedes 5×5-Fenster mit exakt gleichen Werten (echtes Gelände ist interpoliert
  // und nie so glatt) und Pixel, die von allen vier Nachbarn um mehr als 30 m abweichen.
  function fuellwerte(h) {
    const weg = new Uint8Array(65536);
    for (let j = 0; j <= 251; j++) for (let i = 0; i <= 251; i++) {
      const w = h[j * 256 + i];
      let flach = w === w;
      for (let b = 0; flach && b < 5; b++) for (let a = 0; a < 5; a++)
        if (h[(j + b) * 256 + i + a] !== w) { flach = false; break; }
      if (flach) for (let b = 0; b < 5; b++) weg.fill(1, (j + b) * 256 + i, (j + b) * 256 + i + 5);
    }
    for (let j = 1; j < 255; j++) for (let i = 1; i < 255; i++) {
      const k = j * 256 + i, w = h[k], n = [h[k - 1], h[k + 1], h[k - 256], h[k + 256]];
      if (n.every(v => v - w > 30) || n.every(v => w - v > 30)) weg[k] = 1;
    }
    let anzahl = 0;
    for (let k = 0; k < 65536; k++) if (weg[k]) { h[k] = NaN; anzahl++; }
    return anzahl;
  }

  // Die AWS-Kacheln haben ab Zoom 14 Lücken (z. B. Oberengadin: 14 und 15 leer, 13 voll).
  // Leere Pixel bilinear aus der gröberen Kachel füllen, notfalls mehrere Stufen tiefer.
  async function auffuellen(h, z, x, y) {
    const e = z > ZOOM_MIN ? await demKachel(z - 1, x >> 1, y >> 1) : null;
    if (!e) return h;
    const ox = (x & 1) * 128, oy = (y & 1) * 128;
    for (let j = 0; j < 256; j++) for (let i = 0; i < 256; i++) {
      const k = j * 256 + i;
      if (h[k] === h[k]) continue;
      const u = Math.min(255, Math.max(0, ox + (i + .5) / 2 - .5)), v = Math.min(255, Math.max(0, oy + (j + .5) / 2 - .5));
      const u0 = Math.min(254, Math.floor(u)), v0 = Math.min(254, Math.floor(v)), fu = u - u0, fv = v - v0;
      h[k] = (e[v0 * 256 + u0] * (1 - fu) + e[v0 * 256 + u0 + 1] * fu) * (1 - fv)
           + (e[(v0 + 1) * 256 + u0] * (1 - fu) + e[(v0 + 1) * 256 + u0 + 1] * fu) * fv;
    }
    return h;
  }

  // Höhenraster (n+1)² für die Anzeigekachel, über Kachelgrenzen hinweg gelesen und 3×3 geglättet,
  // damit benachbarte Kacheln exakt dieselben Werte am Rand haben (keine Nähte)
  async function raster(c) {
    const d = Math.min(c.z, DEM_MAX), f = 1 << (c.z - d), n = 256 / f;
    const gx0 = c.x * n - 1, gy0 = c.y * n - 1, m = n + 3;   // 1 Pixel Rand für die Glättung
    const tx0 = Math.floor(gx0 / 256), tx1 = Math.floor((gx0 + m - 1) / 256);
    const ty0 = Math.floor(gy0 / 256), ty1 = Math.floor((gy0 + m - 1) / 256);
    const kacheln = {};
    const laden = [];
    for (let ty = ty0; ty <= ty1; ty++) for (let tx = tx0; tx <= tx1; tx++)
      laden.push(demKachel(d, tx, ty).then(h => { kacheln[tx + "," + ty] = h; }));
    await Promise.all(laden);
    const roh = new Float32Array(m * m), maxY = (256 << d) - 1;
    for (let j = 0; j < m; j++) {
      const gy = Math.min(maxY, Math.max(0, gy0 + j)), ty = Math.floor(gy / 256);
      for (let i = 0; i < m; i++) {
        const gx = gx0 + i, tx = Math.floor(gx / 256), h = kacheln[tx + "," + ty];
        roh[j * m + i] = h ? h[(gy - ty * 256) * 256 + (gx - tx * 256)] : NaN;
      }
    }
    const k = n + 1, aus = new Float32Array(k * k);
    for (let j = 0; j < k; j++) for (let i = 0; i < k; i++) {
      let s = 0;
      for (let b = 0; b < 3; b++) for (let a = 0; a < 3; a++) s += roh[(j + b) * m + i + a];
      aus[j * k + i] = s / 9;
    }
    return { h: aus, n };
  }

  L.GridLayer.Hoehenlinien = L.GridLayer.extend({
    options: { minZoom: 13, maxZoom: 22, maxNativeZoom: 22, abstand: 10, kraeftig: 50,
               hell: false, updateWhenIdle: true, keepBuffer: 1, zIndex: 250, className: "hoehenlinien" },

    // hell = Farben für das Satellitenbild
    setHell(hell) { if (this.options.hell !== hell) { this.options.hell = hell; this.redraw(); } },

    createTile(c, fertig) {
      const dpr = Math.min(2, window.devicePixelRatio || 1), px = 256 * dpr;
      const cv = document.createElement("canvas");
      cv.width = cv.height = px;
      raster(c).then(r => { this._zeichnen(cv, r, c.z, dpr); fertig(null, cv); })
               .catch(e => fertig(e, cv));
      return cv;
    },

    _zeichnen(cv, { h, n }, z, dpr) {
      const o = this.options, g = cv.getContext("2d"), s = cv.width / n, k = n + 1;
      const duenn = new Path2D(), dick = new Path2D(), schilder = [];
      const ABSTAND = 130 * dpr, RAND = 32 * dpr;   // Zahlen nicht zu dicht und nicht an der Kachelkante
      for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
        const a = h[j * k + i], b = h[j * k + i + 1], cc = h[(j + 1) * k + i + 1], d = h[(j + 1) * k + i];
        if (a !== a || b !== b || cc !== cc || d !== d) continue;   // NaN: keine Daten
        const mn = Math.min(a, b, cc, d), mx = Math.max(a, b, cc, d);
        // erst ab der ersten Linie über 0 m: Auf der Ostsee liefern die Kacheln statt Tiefen
        // Werte um 0 m mit ein paar Metern Rauschen, die 0-m-Linie lief dort quer durchs Wasser
        for (let L0 = Math.max(o.abstand, Math.ceil(mn / o.abstand) * o.abstand); L0 <= mx; L0 += o.abstand) {
          const A = a >= L0, B = b >= L0, C = cc >= L0, D = d >= L0;
          if (A === B && B === C && C === D) continue;
          // Schnittpunkte oben, rechts, unten, links (Kachelpixel)
          const p = [];
          if (A !== B) p.push([(i + (L0 - a) / (b - a)) * s, j * s]);
          if (B !== C) p.push([(i + 1) * s, (j + (L0 - b) / (cc - b)) * s]);
          if (D !== C) p.push([(i + (L0 - d) / (cc - d)) * s, (j + 1) * s]);
          if (A !== D) p.push([i * s, (j + (L0 - a) / (d - a)) * s]);
          const pfad = L0 % o.kraeftig === 0 ? dick : duenn;
          let paare;
          if (p.length === 4) {
            // Sattel: Mittelwert entscheidet, welche Ecken abgeschnitten werden
            const mitteOben = (a + b + cc + d) / 4 >= L0;
            paare = mitteOben === A ? [[0, 1], [2, 3]] : [[3, 0], [1, 2]];
          } else paare = [[0, 1]];
          for (const [u, v] of paare) {
            pfad.moveTo(p[u][0], p[u][1]); pfad.lineTo(p[v][0], p[v][1]);
          }
          if (pfad === dick) {
            const mx2 = (p[0][0] + p[1][0]) / 2, my2 = (p[0][1] + p[1][1]) / 2;
            if (mx2 < RAND || my2 < RAND || mx2 > cv.width - RAND || my2 > cv.height - RAND) continue;
            if (schilder.some(q => Math.hypot(q[0] - mx2, q[1] - my2) < ABSTAND)) continue;
            let w = Math.atan2(p[1][1] - p[0][1], p[1][0] - p[0][0]);
            if (w > Math.PI / 2) w -= Math.PI; else if (w < -Math.PI / 2) w += Math.PI;
            schilder.push([mx2, my2, w, L0]);
          }
        }
      }
      const farbeD = o.hell ? "rgba(255,226,170,.55)" : "rgba(150,90,40,.45)";
      const farbeK = o.hell ? "rgba(255,226,170,.9)" : "rgba(130,70,25,.8)";
      g.lineCap = "round";
      g.strokeStyle = farbeD; g.lineWidth = (z <= 13 ? .5 : .8) * dpr; g.stroke(duenn);
      g.strokeStyle = farbeK; g.lineWidth = 1.5 * dpr; g.stroke(dick);
      g.font = `600 ${11 * dpr}px -apple-system, "Segoe UI", Roboto, sans-serif`;
      g.textAlign = "center"; g.textBaseline = "middle";
      g.lineWidth = 3 * dpr; g.lineJoin = "round";
      g.strokeStyle = o.hell ? "rgba(0,0,0,.7)" : "rgba(255,255,255,.85)";
      g.fillStyle = o.hell ? "rgba(255,226,170,1)" : "rgba(110,60,20,1)";
      for (const [x, y, w, wert] of schilder) {
        g.save(); g.translate(x, y); g.rotate(w);
        g.strokeText(wert, 0, 0); g.fillText(wert, 0, 0);
        g.restore();
      }
    }
  });

  L.gridLayer.hoehenlinien = opts => new L.GridLayer.Hoehenlinien(opts);
})();
