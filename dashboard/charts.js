/**
 * charts.js
 * Henter trades.csv og status.json fra GitHub.
 * Prisgraf og RSI-historikk hentes fra Binance klines API.
 */

const CSV_URL    = "https://raw.githubusercontent.com/ccMellow/botbot/main/logs/trades.csv";
const STATUS_URL = "https://raw.githubusercontent.com/ccMellow/botbot/main/dashboard/status.json";
const KLINES_URL = "https://api.binance.com/api/v3/klines";
const FNG_URL    = "https://api.alternative.me/fng/";
const REFRESH_MS = 5 * 60 * 1000;
const SYMBOLS    = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

let candleInterval = "15m"; // oppdateres fra bot_config

/* ===== CSV Parsing ===== */
function parseCSV(text) {
  const lines = text.trim().split("\n");
  if (lines.length < 2) return [];
  const headers = lines[0].split(",");
  return lines.slice(1).map(line => {
    const vals = line.split(",");
    return Object.fromEntries(headers.map((h, i) => [h.trim(), (vals[i] ?? "").trim()]));
  });
}

function filterByCoin(rows, symbol) {
  return rows.filter(r => r.symbol === symbol);
}

/* ===== Statistikk ===== */
function computeStats(rows) {
  const trades = rows.filter(r => r.handling === "SELG");
  const totalPnl = trades.reduce((s, r) => s + parseFloat(r.gevinst_usdt || 0), 0);
  const winners = trades.filter(r => parseFloat(r.gevinst_usdt) > 0).length;
  const winRate = trades.length > 0 ? (winners / trades.length * 100).toFixed(1) : "–";
  const allFees = rows.filter(r => r.handling !== "VENTER")
                      .reduce((s, r) => s + parseFloat(r.fee_usdt || 0), 0);
  const tradeCount = rows.filter(r => r.handling !== "VENTER").length;
  const avgFee = tradeCount > 0 ? (allFees / tradeCount).toFixed(4) : "–";
  return { totalPnl, winRate, tradeCount, avgFee };
}

function updateTotalStats(rows) {
  const stats = computeStats(rows);
  const pnlEl = document.getElementById("total-pnl");
  pnlEl.textContent = (stats.totalPnl >= 0 ? "+" : "") + stats.totalPnl.toFixed(2) + " USDT";
  pnlEl.className = "value " + (stats.totalPnl >= 0 ? "positive" : "negative");
  document.getElementById("total-trade-count").textContent = stats.tradeCount;
  document.getElementById("total-win-rate").textContent =
    stats.winRate !== "–" ? stats.winRate + "%" : "–";
  document.getElementById("total-avg-fee").textContent =
    stats.avgFee !== "–" ? stats.avgFee + " USDT" : "–";
}

function updateCoinStats(symbol, rows) {
  const stats = computeStats(rows);
  const pnlEl = document.getElementById(symbol + "-pnl");
  if (!pnlEl) return;
  pnlEl.textContent = (stats.totalPnl >= 0 ? "+" : "") + stats.totalPnl.toFixed(2) + " USDT";
  pnlEl.className = "value " + (stats.totalPnl >= 0 ? "positive" : "negative");
  document.getElementById(symbol + "-trade-count").textContent = stats.tradeCount;
  document.getElementById(symbol + "-win-rate").textContent =
    stats.winRate !== "–" ? stats.winRate + "%" : "–";
  document.getElementById(symbol + "-avg-fee").textContent =
    stats.avgFee !== "–" ? stats.avgFee + " USDT" : "–";
}

function update24hPnL(rows) {
  const cutoff = Date.now() - 24 * 60 * 60 * 1000;
  const recent = rows.filter(r => new Date(r.tidspunkt).getTime() >= cutoff);
  const trades = recent.filter(r => r.handling === "SELG");
  const pnl = trades.reduce((s, r) => s + parseFloat(r.gevinst_usdt || 0), 0);
  const count = recent.filter(r => r.handling !== "VENTER").length;

  const pnlEl = document.getElementById("pnl24-total");
  if (pnlEl) {
    pnlEl.textContent = (pnl >= 0 ? "+" : "") + pnl.toFixed(2) + " USDT";
    pnlEl.className = "value " + (pnl >= 0 ? "positive" : "negative");
  }
  const countEl = document.getElementById("pnl24-trades");
  if (countEl) countEl.textContent = count;
}

/* ===== Åpne posisjoner (DCA) ===== */
function updateOpenPositions(symbol, rows) {
  const el = document.getElementById(symbol + "-positions");
  if (!el) return;
  const buys = rows.filter(r => r.handling === "KJØP");
  const sells = rows.filter(r => r.handling === "SELG");
  let openCount = buys.length;
  sells.forEach(s => { openCount -= parseInt(s.dca_level || 1); });
  openCount = Math.max(0, openCount);

  if (openCount === 0) {
    el.innerHTML = '<p class="muted">Ingen åpne posisjoner</p>';
    return;
  }
  const openBuys = buys.slice(-openCount);
  el.innerHTML = openBuys.map(b => {
    const dca = b.dca_level || "?";
    return `<div class="position-card">
      <span class="pos-label">DCA #${dca}</span>
      <span class="pos-price">$${parseFloat(b.pris || 0).toLocaleString("no-NO", {minimumFractionDigits: 2})}</span>
      <span class="pos-amount">${parseFloat(b.mengde_coin || 0).toFixed(6)} coin</span>
      <span class="pos-usdt">${parseFloat(b.beløp_usdt || 0).toFixed(2)} USDT</span>
    </div>`;
  }).join("");
}

/* ===== Logg-tabell ===== */
function updateTable(symbol, rows) {
  const tbody = document.getElementById(symbol + "-log-body");
  if (!tbody) return;
  const last20 = rows.slice(-20).reverse();
  if (last20.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8">Ingen data ennå</td></tr>';
    return;
  }
  tbody.innerHTML = last20.map(r => {
    const h = r.handling || "";
    const badge = `<span class="badge badge-${h.toLowerCase()}">${h}</span>`;
    const pnl = h === "SELG"
      ? `<span class="${parseFloat(r.gevinst_usdt) >= 0 ? 'positive' : 'negative'}">
           ${parseFloat(r.gevinst_usdt) >= 0 ? "+" : ""}${parseFloat(r.gevinst_usdt).toFixed(2)} USDT
         </span>`
      : "–";
    return `<tr>
      <td>${r.tidspunkt}</td>
      <td>${badge}</td>
      <td>${parseFloat(r.pris || 0).toLocaleString("no-NO", {minimumFractionDigits: 2})}</td>
      <td>${parseFloat(r.mengde_coin || 0).toFixed(6)}</td>
      <td>${parseFloat(r.fee_usdt || 0).toFixed(4)}</td>
      <td>${r.dca_level || "–"}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis" title="${r.grunn}">${r.grunn}</td>
      <td>${pnl}</td>
    </tr>`;
  }).join("");
}

/* ===== RSI-beregning ===== */
function computeRSI(closes, period = 14) {
  const rsi = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return rsi;
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const delta = closes[i] - closes[i - 1];
    if (delta > 0) avgGain += delta; else avgLoss -= delta;
  }
  avgGain /= period;
  avgLoss /= period;
  rsi[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < closes.length; i++) {
    const delta = closes[i] - closes[i - 1];
    const gain = delta > 0 ? delta : 0;
    const loss = delta < 0 ? -delta : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    rsi[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return rsi;
}

/* ===== Klines (Binance) ===== */
async function fetchKlines(symbol, interval, limit = 120) {
  const res = await fetch(`${KLINES_URL}?symbol=${symbol}&interval=${interval}&limit=${limit}`);
  if (!res.ok) throw new Error(`Klines feilet for ${symbol}: ${res.statusText}`);
  return await res.json();
}

/* ===== Prisgraf (klines-basert) ===== */
const chartInstances = {};

function updatePriceChart(symbol, klines, tradeRows) {
  const canvas = document.getElementById("chart-" + symbol);
  if (!canvas) return;

  const labels  = klines.map(k => {
    const d = new Date(k[0]);
    return d.toLocaleTimeString("no-NO", { hour: "2-digit", minute: "2-digit" });
  });
  const closes  = klines.map(k => parseFloat(k[4]));
  const kTimes  = klines.map(k => k[0]);

  const buyPoints  = new Array(klines.length).fill(null);
  const sellPoints = new Array(klines.length).fill(null);

  tradeRows.forEach(row => {
    if (row.handling !== "KJØP" && row.handling !== "SELG") return;
    const t = new Date(row.tidspunkt).getTime();
    let nearest = 0, minDiff = Infinity;
    kTimes.forEach((kt, i) => {
      const diff = Math.abs(kt - t);
      if (diff < minDiff) { minDiff = diff; nearest = i; }
    });
    if (row.handling === "KJØP")  buyPoints[nearest]  = parseFloat(row.pris);
    else                          sellPoints[nearest] = parseFloat(row.pris);
  });

  const ctx = canvas.getContext("2d");
  if (chartInstances[symbol]) chartInstances[symbol].destroy();

  chartInstances[symbol] = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: symbol.replace("USDT", "/USDT"), data: closes,
          borderColor: "#63b3ed", borderWidth: 1.5, pointRadius: 0, tension: 0.2, fill: false },
        { label: "Kjøp",  data: buyPoints,
          borderColor: "transparent", backgroundColor: "#48bb78",
          pointRadius: 7, pointStyle: "triangle", showLine: false },
        { label: "Selg",  data: sellPoints,
          borderColor: "transparent", backgroundColor: "#fc8181",
          pointRadius: 7, pointStyle: "triangle", pointRotation: 180, showLine: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#718096", font: { size: 11 } } },
        tooltip: { backgroundColor: "#1a1d27", titleColor: "#e2e8f0", bodyColor: "#a0aec0" },
      },
      scales: {
        x: { ticks: { color: "#718096", maxTicksLimit: 8 }, grid: { color: "#2a2d3a" } },
        y: { ticks: { color: "#718096", callback: v => "$" + v.toLocaleString() }, grid: { color: "#2a2d3a" } },
      },
    },
  });
}

/* ===== RSI-historikk-graf ===== */
const rsiChartInstances = {};

function updateRsiChart(symbol, klines, buyThreshold, sellThreshold) {
  const canvas = document.getElementById("rsi-chart-" + symbol);
  if (!canvas) return;

  const closes = klines.map(k => parseFloat(k[4]));
  const labels = klines.map(k => {
    const d = new Date(k[0]);
    return d.toLocaleTimeString("no-NO", { hour: "2-digit", minute: "2-digit" });
  });
  const rsiValues = computeRSI(closes);

  const ctx = canvas.getContext("2d");
  if (rsiChartInstances[symbol]) rsiChartInstances[symbol].destroy();

  rsiChartInstances[symbol] = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "RSI", data: rsiValues,
          borderColor: "#b794f4", borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false },
        { label: `Kjøp (${buyThreshold})`,
          data: new Array(labels.length).fill(buyThreshold),
          borderColor: "rgba(72,187,120,0.6)", borderWidth: 1.5,
          borderDash: [5, 5], pointRadius: 0, fill: false },
        { label: `Selg (${sellThreshold})`,
          data: new Array(labels.length).fill(sellThreshold),
          borderColor: "rgba(252,129,129,0.6)", borderWidth: 1.5,
          borderDash: [5, 5], pointRadius: 0, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#718096", font: { size: 11 } } },
        tooltip: { backgroundColor: "#1a1d27", titleColor: "#e2e8f0", bodyColor: "#a0aec0" },
      },
      scales: {
        x: { ticks: { color: "#718096", maxTicksLimit: 8 }, grid: { color: "#2a2d3a" } },
        y: { min: 0, max: 100, ticks: { color: "#718096", stepSize: 25 }, grid: { color: "#2a2d3a" } },
      },
    },
  });
}

/* ===== Status.json: saldo og posisjoner ===== */
function updateBalances(status) {
  const bal = status.balances || {};
  ["USDT", "BTC", "ETH", "SOL"].forEach(asset => {
    const el = document.getElementById("bal-" + asset);
    if (!el) return;
    const val = bal[asset] ?? 0;
    el.textContent = asset === "USDT"
      ? val.toLocaleString("no-NO", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " USDT"
      : val.toFixed(6) + " " + asset;
  });
  const upd = document.getElementById("status-updated");
  if (upd && status.updated) upd.textContent = "Oppdatert: " + status.updated;
}

function updatePositionsTable(status) {
  const tbody = document.getElementById("positions-body");
  if (!tbody) return;
  const positions = status.positions || {};
  const rows = [];
  SYMBOLS.forEach(symbol => {
    const pos = positions[symbol];
    if (!pos || pos.dca_count === 0) return;
    const coin = symbol.replace("USDT", "");
    rows.push(`<tr>
      <td><strong>${coin}/USDT</strong></td>
      <td>${pos.dca_count} / 3</td>
      <td>$${pos.avg_entry_price.toLocaleString("no-NO", { minimumFractionDigits: 2 })}</td>
      <td class="positive">$${pos.take_profit_price.toLocaleString("no-NO", { minimumFractionDigits: 2 })}</td>
      <td class="negative">$${pos.stop_loss_price.toLocaleString("no-NO", { minimumFractionDigits: 2 })}</td>
      <td>${pos.total_coin.toFixed(6)} ${coin}</td>
      <td>${pos.total_usdt.toFixed(2)} USDT</td>
    </tr>`);
  });
  tbody.innerHTML = rows.length > 0
    ? rows.join("")
    : '<tr><td colspan="7" style="color:var(--muted)">Ingen åpne posisjoner</td></tr>';
}

/* ===== Indikator-panel ===== */
function updateIndicators(symbol, ind) {
  if (!ind) return;
  const rsi  = ind.rsi;
  const buyT = ind.rsi_buy_threshold;
  const sellT = ind.rsi_sell_threshold;

  const priceEl = document.getElementById(symbol + "-ind-price");
  if (priceEl) priceEl.textContent =
    "$" + ind.price.toLocaleString("no-NO", { minimumFractionDigits: 2 });

  const rsiEl = document.getElementById(symbol + "-ind-rsi");
  if (rsiEl) {
    rsiEl.textContent = rsi.toFixed(1);
    rsiEl.className = "ind-value " + (rsi <= buyT ? "positive" : rsi >= sellT ? "negative" : "");
  }

  const fill = document.getElementById(symbol + "-rsi-fill");
  if (fill) {
    fill.style.width = rsi + "%";
    fill.style.background = rsi <= buyT ? "var(--green)" : rsi >= sellT ? "var(--red)" : "var(--blue)";
  }

  const buyLine = document.getElementById(symbol + "-rsi-buy-line");
  if (buyLine) buyLine.style.left = buyT + "%";
  const sellLine = document.getElementById(symbol + "-rsi-sell-line");
  if (sellLine) sellLine.style.left = sellT + "%";

  const toBuyEl = document.getElementById(symbol + "-ind-to-buy");
  if (toBuyEl) {
    if (ind.rsi_to_buy <= 0) {
      toBuyEl.textContent = "AKTIVT";
      toBuyEl.className = "ind-value positive";
    } else {
      toBuyEl.textContent = "+" + ind.rsi_to_buy.toFixed(1) + " poeng";
      toBuyEl.className = "ind-value";
    }
  }

  const toSellEl = document.getElementById(symbol + "-ind-to-sell");
  if (toSellEl) {
    if (ind.rsi_to_sell <= 0) {
      toSellEl.textContent = "AKTIVT";
      toSellEl.className = "ind-value negative";
    } else {
      toSellEl.textContent = ind.rsi_to_sell.toFixed(1) + " poeng";
      toSellEl.className = "ind-value";
    }
  }

  // EMA200: vis faktisk pris og prosent
  const emaEl = document.getElementById(symbol + "-ind-ema200");
  if (emaEl && ind.ema200 != null) {
    const pct = Math.abs(ind.price_vs_ema200_pct).toFixed(2);
    const emaPrice = "$" + ind.ema200.toLocaleString("no-NO", { minimumFractionDigits: 2 });
    if (ind.price_above_ema200) {
      emaEl.textContent = emaPrice + " ▲ +" + pct + "% over";
      emaEl.className = "ind-value positive";
    } else {
      emaEl.textContent = emaPrice + " ▼ " + pct + "% under";
      emaEl.className = "ind-value negative";
    }
  }
}

/* ===== Strategy config panel ===== */
function updateStrategyConfig(botConfig) {
  if (!botConfig) return;
  const el = id => document.getElementById(id);
  const strategy = el("cfg-strategy");
  if (strategy) strategy.textContent = botConfig.active_strategy || "–";
  const rsi = el("cfg-rsi");
  if (rsi) rsi.textContent = (botConfig.rsi_buy ?? "–") + " / " + (botConfig.rsi_sell ?? "–");
  const sl = el("cfg-stoploss");
  if (sl) sl.textContent = botConfig.stop_loss_pct != null ? "-" + botConfig.stop_loss_pct + "%" : "–";
  const tp = el("cfg-takeprofit");
  if (tp) tp.textContent = botConfig.take_profit_pct != null ? "+" + botConfig.take_profit_pct + "%" : "–";
  const tu = el("cfg-trade-usdt");
  if (tu) tu.textContent = botConfig.trade_usdt != null ? botConfig.trade_usdt + " USDT" : "–";
}

/* ===== Countdown timer ===== */
let countdownInterval = null;

function parseIntervalMinutes(str) {
  if (!str) return null;
  const m = str.match(/^(\d+)([mh])$/);
  if (!m) return null;
  return m[2] === "h" ? parseInt(m[1]) * 60 : parseInt(m[1]);
}

function startCountdown(updatedStr, intervalStr) {
  if (countdownInterval) clearInterval(countdownInterval);
  const mins = parseIntervalMinutes(intervalStr);
  if (!mins) return;
  const updated = new Date(updatedStr.replace(" ", "T")).getTime();
  const nextRun = updated + mins * 60 * 1000;

  function tick() {
    const el = document.getElementById("cfg-countdown");
    if (!el) return;
    const remaining = Math.max(0, nextRun - Date.now());
    if (remaining === 0) {
      el.textContent = "Snart...";
      el.className = "cfg-value positive";
    } else {
      const m = Math.floor(remaining / 60000);
      const s = Math.floor((remaining % 60000) / 1000);
      el.textContent = m + "m " + s.toString().padStart(2, "0") + "s";
      el.className = "cfg-value";
    }
  }
  tick();
  countdownInterval = setInterval(tick, 1000);
}

/* ===== Fear & Greed Index ===== */
async function fetchFearAndGreed() {
  try {
    const res = await fetch(FNG_URL);
    if (!res.ok) return;
    const data = await res.json();
    const entry = data?.data?.[0];
    if (!entry) return;
    const value = parseInt(entry.value);
    const label = entry.value_classification;

    const valEl = document.getElementById("fng-value");
    const lblEl = document.getElementById("fng-label");
    if (valEl) {
      valEl.textContent = value;
      if      (value <= 25) valEl.className = "fng-value fng-extreme-fear";
      else if (value <= 45) valEl.className = "fng-value fng-fear";
      else if (value <= 55) valEl.className = "fng-value fng-neutral";
      else if (value <= 75) valEl.className = "fng-value fng-greed";
      else                  valEl.className = "fng-value fng-extreme-greed";
    }
    if (lblEl) lblEl.textContent = label;
  } catch (err) {
    console.error("Fear & Greed feil:", err);
  }
}

/* ===== Export CSV ===== */
function setupExportButtons() {
  SYMBOLS.forEach(symbol => {
    const btn = document.getElementById("export-" + symbol);
    if (!btn) return;
    btn.addEventListener("click", async () => {
      try {
        const res = await fetch(CSV_URL + "?t=" + Date.now());
        if (!res.ok) throw new Error(res.statusText);
        const text = await res.text();
        const lines = text.trim().split("\n");
        const header = lines[0];
        const filtered = lines.slice(1).filter(l => {
          const cols = l.split(",");
          return cols[1]?.trim() === symbol;
        });
        const csvContent = [header, ...filtered].join("\n");
        const blob = new Blob([csvContent], { type: "text/csv" });
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement("a");
        a.href = url;
        a.download = `trades_${symbol}.csv`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (err) {
        console.error("CSV-eksport feilet:", err);
      }
    });
  });
}

/* ===== Hoved-refresh ===== */
async function refresh() {
  try {
    const bust = "?t=" + Date.now();

    const [csvRes, statusRes, ...klineResults] = await Promise.all([
      fetch(CSV_URL + bust),
      fetch(STATUS_URL + bust),
      ...SYMBOLS.map(s => fetchKlines(s, candleInterval).catch(() => null)),
    ]);

    const [csvText, statusData] = await Promise.all([
      csvRes.ok ? csvRes.text() : Promise.resolve(""),
      statusRes.ok ? statusRes.json() : Promise.resolve(null),
    ]);

    const rows = csvText ? parseCSV(csvText) : [];

    updateTotalStats(rows);
    update24hPnL(rows);

    const klinesBySymbol = {};
    SYMBOLS.forEach((s, i) => { klinesBySymbol[s] = klineResults[i]; });

    SYMBOLS.forEach(symbol => {
      const coinRows = filterByCoin(rows, symbol);
      const ind      = statusData?.indicators?.[symbol];
      const buyT     = ind?.rsi_buy_threshold  ?? 35;
      const sellT    = ind?.rsi_sell_threshold ?? 65;

      updateCoinStats(symbol, coinRows);
      updateOpenPositions(symbol, coinRows);
      updateTable(symbol, coinRows);

      const klines = klinesBySymbol[symbol];
      if (klines && klines.length > 0) {
        updatePriceChart(symbol, klines, coinRows);
        updateRsiChart(symbol, klines, buyT, sellT);
      }
    });

    if (statusData) {
      updateBalances(statusData);
      updatePositionsTable(statusData);
      if (statusData.indicators) {
        SYMBOLS.forEach(s => updateIndicators(s, statusData.indicators[s]));
      }
      if (statusData.bot_config) {
        updateStrategyConfig(statusData.bot_config);
        startCountdown(statusData.updated, statusData.bot_config.candle_interval);
        if (statusData.bot_config.candle_interval) {
          candleInterval = statusData.bot_config.candle_interval;
        }
      }
    }

    document.getElementById("last-updated").textContent =
      "Sist oppdatert: " + new Date().toLocaleString("no-NO");
  } catch (err) {
    document.getElementById("last-updated").textContent =
      "Feil ved lasting: " + err.message;
    console.error(err);
  }
}

/* ===== Navigation ===== */
function showPage(pageId) {
  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  const target = document.getElementById('page-' + pageId);
  if (target) target.classList.remove('hidden');
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.page === pageId);
  });
  document.getElementById('main-nav').classList.add('nav-closed');
}

document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => showPage(btn.dataset.page));
});

document.getElementById('hamburger').addEventListener('click', () => {
  document.getElementById('main-nav').classList.toggle('nav-closed');
});

showPage('overview');

/* ===== Live pris-ticker ===== */
const TICKER_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
const TICKER_MS      = 10 * 1000;

async function refreshTicker() {
  try {
    const url = "https://api.binance.com/api/v3/ticker/24hr?symbols=" +
      encodeURIComponent(JSON.stringify(TICKER_SYMBOLS));
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    data.forEach(t => {
      const el = document.getElementById("ticker-" + t.symbol);
      if (!el) return;
      const pct = parseFloat(t.priceChangePercent);
      el.querySelector(".ticker-price").textContent =
        "$" + parseFloat(t.lastPrice).toLocaleString("no-NO", { minimumFractionDigits: 2 });
      const changeEl = el.querySelector(".ticker-change");
      changeEl.textContent = (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
      changeEl.className   = "ticker-change " + (pct >= 0 ? "positive" : "negative");
    });
  } catch (err) {
    console.error("Ticker-feil:", err);
  }
}

/* ===== Init ===== */
refresh();
setInterval(refresh, REFRESH_MS);
refreshTicker();
setInterval(refreshTicker, TICKER_MS);
fetchFearAndGreed();
setInterval(fetchFearAndGreed, 30 * 60 * 1000);
setupExportButtons();
