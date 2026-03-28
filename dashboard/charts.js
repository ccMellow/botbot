/**
 * charts.js
 * Henter trades.csv fra GitHub (raw), parser den og oppdaterer dashboard.
 * Bytt ut CSV_URL med raw-URL til din GitHub-repo.
 */

const CSV_URL = "https://raw.githubusercontent.com/ccMellow/botbot/main/logs/trades.csv";

const REFRESH_MS = 5 * 60 * 1000;  // Oppdater hvert 5. minutt

/* ===== Parsing ===== */
function parseCSV(text) {
  const lines = text.trim().split("\n");
  if (lines.length < 2) return [];
  const headers = lines[0].split(",");
  return lines.slice(1).map(line => {
    const vals = line.split(",");
    return Object.fromEntries(headers.map((h, i) => [h, vals[i] ?? ""]));
  });
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

/* ===== DOM-oppdatering ===== */
function updateStats(stats) {
  const pnlEl = document.getElementById("total-pnl");
  pnlEl.textContent = (stats.totalPnl >= 0 ? "+" : "") + stats.totalPnl.toFixed(2) + " USDT";
  pnlEl.className = "value " + (stats.totalPnl >= 0 ? "positive" : "negative");

  document.getElementById("trade-count").textContent = stats.tradeCount;
  document.getElementById("win-rate").textContent =
    stats.winRate !== "–" ? stats.winRate + "%" : "–";
  document.getElementById("avg-fee").textContent =
    stats.avgFee !== "–" ? stats.avgFee + " USDT" : "–";
}

function updateTable(rows) {
  const tbody = document.getElementById("log-body");
  const last50 = rows.slice(-50).reverse();
  tbody.innerHTML = last50.map(r => {
    const h = r.handling || "";
    const badge = `<span class="badge badge-${h.toLowerCase()}">${h}</span>`;
    const pnl = r.handling === "SELG"
      ? `<span class="${parseFloat(r.gevinst_usdt) >= 0 ? 'positive' : 'negative'}">
           ${(parseFloat(r.gevinst_usdt) >= 0 ? "+" : "")}${parseFloat(r.gevinst_usdt).toFixed(2)} USDT
         </span>`
      : "–";
    return `<tr>
      <td>${r.tidspunkt}</td>
      <td>${badge}</td>
      <td>${parseFloat(r.pris || 0).toLocaleString("no-NO", {minimumFractionDigits: 2})}</td>
      <td>${parseFloat(r.mengde_btc || 0).toFixed(6)}</td>
      <td>${parseFloat(r.fee_usdt || 0).toFixed(4)}</td>
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis" title="${r.grunn}">${r.grunn}</td>
      <td>${pnl}</td>
    </tr>`;
  }).join("");
}

/* ===== Prisgraf ===== */
let chartInstance = null;

function updateChart(rows) {
  const data = rows.slice(-100);
  const labels = data.map(r => r.tidspunkt?.slice(11, 16) ?? "");
  const prices = data.map(r => parseFloat(r.pris || 0));

  const buyPoints = data.map(r => r.handling === "KJØP" ? parseFloat(r.pris) : null);
  const sellPoints = data.map(r => r.handling === "SELG" ? parseFloat(r.pris) : null);

  const ctx = document.getElementById("priceChart").getContext("2d");

  if (chartInstance) chartInstance.destroy();

  chartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "BTC/USDT",
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

/* ===== Hoved-refresh ===== */
async function refresh() {
  try {
    const res = await fetch(CSV_URL + "?t=" + Date.now());
    if (!res.ok) throw new Error(res.statusText);
    const text = await res.text();
    const rows = parseCSV(text);

    updateStats(computeStats(rows));
    updateTable(rows);
    updateChart(rows);

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
