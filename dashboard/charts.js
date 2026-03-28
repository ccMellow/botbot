/**
 * charts.js
 * Henter trades.csv fra GitHub, parser og oppdaterer dashboard per mynt.
 */

const CSV_URL = "https://raw.githubusercontent.com/ccMellow/botbot/main/logs/trades.csv";
const STATUS_URL = "https://raw.githubusercontent.com/ccMellow/botbot/main/dashboard/status.json";
const REFRESH_MS = 5 * 60 * 1000;
const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

/* ===== Parsing ===== */
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

/* ===== Totalt sammendrag ===== */
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

/* ===== Per-mynt stats ===== */
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

/* ===== Åpne posisjoner (DCA) ===== */
function updateOpenPositions(symbol, rows) {
  const el = document.getElementById(symbol + "-positions");
  if (!el) return;

  // Finn KJØP-rader som ikke har fått tilhørende SELG etter seg
  // Enklest tilnærming: tell alle KJØP minus alle SELG, vis siste N KJØP
  const buys = rows.filter(r => r.handling === "KJØP");
  const sells = rows.filter(r => r.handling === "SELG");

  // Beregn antall åpne posisjoner: summer DCA-nivåer solgt vs kjøpt
  let openCount = buys.length;
  sells.forEach(s => { openCount -= parseInt(s.dca_level || 1); });
  openCount = Math.max(0, openCount);

  if (openCount === 0) {
    el.innerHTML = '<p class="muted">Ingen åpne posisjoner</p>';
    return;
  }

  // Vis de siste openCount kjøpene
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

/* ===== Prisgraf ===== */
const chartInstances = {};

function updateChart(symbol, rows) {
  const data = rows.slice(-100);
  const labels = data.map(r => r.tidspunkt?.slice(11, 16) ?? "");
  const prices = data.map(r => parseFloat(r.pris || 0));
  const buyPoints = data.map(r => r.handling === "KJØP" ? parseFloat(r.pris) : null);
  const sellPoints = data.map(r => r.handling === "SELG" ? parseFloat(r.pris) : null);

  const canvas = document.getElementById("chart-" + symbol);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  if (chartInstances[symbol]) chartInstances[symbol].destroy();

  chartInstances[symbol] = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: symbol.replace("USDT", "/USDT"),
          data: prices,
          borderColor: "#63b3ed",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
          fill: false,
        },
        {
          label: "Kjøp",
          data: buyPoints,
          borderColor: "transparent",
          backgroundColor: "#48bb78",
          pointRadius: 6,
          pointStyle: "triangle",
          showLine: false,
        },
        {
          label: "Selg",
          data: sellPoints,
          borderColor: "transparent",
          backgroundColor: "#fc8181",
          pointRadius: 6,
          pointStyle: "triangle",
          pointRotation: 180,
          showLine: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#718096", font: { size: 11 } } },
        tooltip: { backgroundColor: "#1a1d27", titleColor: "#e2e8f0", bodyColor: "#a0aec0" },
      },
      scales: {
        x: {
          ticks: { color: "#718096", maxTicksLimit: 8 },
          grid: { color: "#2a2d3a" },
        },
        y: {
          ticks: {
            color: "#718096",
            callback: v => "$" + v.toLocaleString(),
          },
          grid: { color: "#2a2d3a" },
        },
      },
    },
  });
}

/* ===== Status.json: saldo og åpne posisjoner ===== */
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

  const rsi = ind.rsi;
  const buyT = ind.rsi_buy_threshold;
  const sellT = ind.rsi_sell_threshold;

  // Pris
  const priceEl = document.getElementById(symbol + "-ind-price");
  if (priceEl) priceEl.textContent =
    "$" + ind.price.toLocaleString("no-NO", { minimumFractionDigits: 2 });

  // RSI verdi + farge
  const rsiEl = document.getElementById(symbol + "-ind-rsi");
  if (rsiEl) {
    rsiEl.textContent = rsi.toFixed(1);
    rsiEl.className = "ind-value " + (rsi <= buyT ? "positive" : rsi >= sellT ? "negative" : "");
  }

  // RSI gauge fill
  const fill = document.getElementById(symbol + "-rsi-fill");
  if (fill) {
    fill.style.width = rsi + "%";
    fill.style.background = rsi <= buyT ? "var(--green)" : rsi >= sellT ? "var(--red)" : "var(--blue)";
  }

  // Kjøp/selg-markeringslinjer
  const buyLine = document.getElementById(symbol + "-rsi-buy-line");
  if (buyLine) buyLine.style.left = buyT + "%";
  const sellLine = document.getElementById(symbol + "-rsi-sell-line");
  if (sellLine) sellLine.style.left = sellT + "%";

  // Til kjøp
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

  // Til salg
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

  // EMA200
  const emaEl = document.getElementById(symbol + "-ind-ema200");
  if (emaEl) {
    const pct = Math.abs(ind.price_vs_ema200_pct).toFixed(2);
    if (ind.price_above_ema200) {
      emaEl.textContent = "▲ +" + pct + "% over";
      emaEl.className = "ind-value positive";
    } else {
      emaEl.textContent = "▼ " + pct + "% under";
      emaEl.className = "ind-value negative";
    }
  }
}

/* ===== Hoved-refresh ===== */
async function refresh() {
  try {
    const bust = "?t=" + Date.now();
    const [csvRes, statusRes] = await Promise.all([
      fetch(CSV_URL + bust),
      fetch(STATUS_URL + bust),
    ]);

    if (!csvRes.ok) throw new Error("CSV: " + csvRes.statusText);
    const text = await csvRes.text();
    const rows = parseCSV(text);

    updateTotalStats(rows);
    SYMBOLS.forEach(symbol => {
      const coinRows = filterByCoin(rows, symbol);
      updateCoinStats(symbol, coinRows);
      updateOpenPositions(symbol, coinRows);
      updateTable(symbol, coinRows);
      updateChart(symbol, coinRows);
    });

    if (statusRes.ok) {
      const status = await statusRes.json();
      updateBalances(status);
      updatePositionsTable(status);
      if (status.indicators) {
        SYMBOLS.forEach(symbol => updateIndicators(symbol, status.indicators[symbol]));
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

refresh();
setInterval(refresh, REFRESH_MS);

/* ===== Live pris-ticker ===== */
const TICKER_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
const TICKER_MS = 10 * 1000;

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
      changeEl.className = "ticker-change " + (pct >= 0 ? "positive" : "negative");
    });
  } catch (err) {
    console.error("Ticker-feil:", err);
  }
}

refreshTicker();
setInterval(refreshTicker, TICKER_MS);
