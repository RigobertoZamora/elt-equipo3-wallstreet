let marketChart = null;
let originalSeries = [];
let activeSeries = [];
let currentIndex = 0;
let timerId = null;
let isPlaying = false;
let isMuted = false;
let crashSimulated = false;
let criticalAlertTriggered = false;
let lastLoggedIndex = -1;

const WINDOW_SIZE = 65;

const DAG_COLORS = {
  SUCCESS: "#16a34a",
  FAILED: "#dc2626",
  SKIPPED: "#94a3b8",
  TRIGGERED: "#ea580c",
  PENDING: "#cbd5e1",
  RUNNING: "#2563eb",
};

const DAG_POSITIONS = {
  T1_extract_api: [20, 95],
  T2_load_bronze: [210, 95],
  T3_transform_silver: [400, 95],
  T4_ml_gold: [620, 28],
  T6_auditoria: [825, 95],
  T5_contingencia: [620, 162],
};

function escHtml(txt) {
  return String(txt ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function playAlarmSound() {
  if (isMuted) return;
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    const now = ctx.currentTime;
    [620, 480, 620].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(freq, now + i * 0.16);
      gain.gain.setValueAtTime(0.09, now + i * 0.16);
      gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.16 + 0.15);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + i * 0.16);
      osc.stop(now + i * 0.16 + 0.16);
    });
  } catch (e) {
    // Ignorar si el navegador bloquea audio sin interaccion previa
  }
}

function sendBrowserNotification(title, body) {
  if (!("Notification" in window)) return;
  if (Notification.permission === "granted") {
    new Notification(title, { body });
  }
}

function formatMoney(val) {
  return "$" + Number(val).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function appendHistoryRow(fecha, precio, varPeriodo, drawdown, nivel, mensaje) {
  const tbody = document.getElementById("history-table-body");
  const emptyRow = tbody.querySelector(".empty-row");
  if (emptyRow) emptyRow.remove();

  const tr = document.createElement("tr");
  const hora = new Date().toLocaleTimeString();
  const badgeClass = nivel === "CRITICO" ? "badge-danger" : "badge-warning";

  tr.innerHTML = `
    <td>${hora}</td>
    <td><strong>${escHtml(fecha)}</strong></td>
    <td>${formatMoney(precio)}</td>
    <td class="val-red">${varPeriodo >= 0 ? "+" : ""}${varPeriodo.toFixed(2)}%</td>
    <td class="val-red">${drawdown.toFixed(2)}%</td>
    <td><span class="badge-log ${badgeClass}">${nivel}</span> ${escHtml(mensaje)}</td>
  `;
  tbody.prepend(tr);
}

function initChart() {
  const ctx = document.getElementById("marketChart").getContext("2d");
  if (marketChart) marketChart.destroy();

  marketChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Índice Histórico Real (1928–2026)",
          data: [],
          borderColor: "#0C2D50",
          backgroundColor: "rgba(12, 45, 80, 0.11)",
          borderWidth: 2.4,
          pointRadius: 3.1,
          pointHoverRadius: 5,
          pointBackgroundColor: "#0C2D50",
          fill: true,
          tension: 0.14,
        },
        {
          label: "Predicción ML (2027–2031)",
          data: [],
          borderColor: "#7c3aed",
          backgroundColor: "rgba(124, 58, 237, 0.12)",
          borderWidth: 2.4,
          pointRadius: 3.3,
          pointBackgroundColor: "#7c3aed",
          fill: true,
          tension: 0.16,
        },
        {
          label: "Umbral Crítico (-12% desde máximo)",
          data: [],
          borderColor: "#dc2626",
          borderDash: [6, 5],
          borderWidth: 1.8,
          pointRadius: 0,
          fill: false,
          tension: 0.05,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "top",
          labels: { usePointStyle: true, font: { size: 12, weight: "600" }, color: "#1e293b" },
        },
      },
      scales: {
        x: {
          ticks: { color: "#64748b", maxRotation: 25, autoSkip: true, maxTicksLimit: 12 },
          grid: { color: "#f1f5f9" },
        },
        y: {
          title: { display: true, text: "Puntos del Índice", color: "#64748b", font: { weight: "600" } },
          ticks: { color: "#64748b" },
          grid: { color: "#e2e8f0" },
        },
      },
    },
  });
}

function updateViewAtCursor() {
  if (!activeSeries.length) return;
  const idx = Math.min(currentIndex, activeSeries.length - 1);
  const pt = activeSeries[idx];

  document.getElementById("kpi-date").textContent = pt.date;
  const modeTag = pt.is_prediction ? " [PREDICCIÓN ML]" : " [HISTÓRICO REAL]";
  document.getElementById("kpi-session-count").textContent = `Sesión ${idx + 1} de ${activeSeries.length}${modeTag}`;

  document.getElementById("kpi-price").textContent = formatMoney(pt.close);
  document.getElementById("kpi-peak").textContent = `Máximo registrado: ${formatMoney(pt.rolling_peak)}`;

  const changeEl = document.getElementById("kpi-change");
  const sign = pt.step_return_pct > 0 ? "+" : "";
  changeEl.textContent = `${sign}${pt.step_return_pct.toFixed(2)}%`;
  changeEl.style.color = pt.step_return_pct <= -3.0 ? "#dc2626" : pt.step_return_pct >= 0 ? "#16a34a" : "#0f172a";

  const ddEl = document.getElementById("kpi-drawdown");
  ddEl.textContent = `${pt.drawdown_pct.toFixed(2)}%`;
  ddEl.style.color = pt.drawdown_pct <= -7.0 ? "#dc2626" : "#0f172a";

  const detectorCard = document.getElementById("kpi-detector-card");
  const detectorState = document.getElementById("kpi-detector-state");
  const detectorReason = document.getElementById("kpi-detector-reason");
  const alertBanner = document.getElementById("top-alert-banner");
  const alertHeading = document.getElementById("alert-heading");
  const alertSub = document.getElementById("alert-subtext");

  const isCrack = idx > 0 && (pt.drawdown_pct <= -12.0 || pt.step_return_pct <= -4.5 || pt.is_simulated_crash);
  const isWarning = idx > 0 && !isCrack && (pt.step_return_pct <= -3.0 || pt.drawdown_pct <= -7.0);

  if (isCrack) {
    detectorCard.className = "kpi-card card detector-crash";
    detectorState.textContent = "CRACK DETECTADO";
    const reason = pt.step_return_pct <= -4.5
      ? `Desplome de ${pt.step_return_pct.toFixed(2)}% (supera límite de -4.50%)`
      : `Caída acumulada de ${pt.drawdown_pct.toFixed(2)}% desde máximo (supera umbral -12.00%)`;
    detectorReason.textContent = reason;

    alertBanner.classList.remove("hidden", "warning-mode");
    alertHeading.innerHTML = "&#128680; &iexcl;ALERTA CR&Iacute;TICA! CA&Iacute;DA DEL MERCADO DETECTADA";
    alertSub.textContent = `${pt.date} | Índice: ${formatMoney(pt.close)} | ${reason}`;

    if (lastLoggedIndex !== idx) {
      lastLoggedIndex = idx;
      appendHistoryRow(pt.date, pt.close, pt.step_return_pct, pt.drawdown_pct, "CRITICO", reason);
      if (pt.trigger_alarm || !criticalAlertTriggered) {
        playAlarmSound();
      }
      if (!criticalAlertTriggered) {
        criticalAlertTriggered = true;
        sendBrowserNotification("🚨 Alerta Crítica en Wall Street", `${pt.date}: ${reason}`);
        // Circuit Breaker: dispara el DAG de contingencia T5
        triggerDagRun({ force_refresh: false, simulate_corruption: true }, false);
      }
    }
  } else if (isWarning) {
    detectorCard.className = "kpi-card card detector-warning";
    detectorState.textContent = "ADVERTENCIA";
    const reason = `Retroceso fuerte (Var: ${pt.step_return_pct.toFixed(2)}%, Drawdown: ${pt.drawdown_pct.toFixed(2)}%)`;
    detectorReason.textContent = reason;

    alertBanner.classList.remove("hidden");
    alertBanner.classList.add("warning-mode");
    alertHeading.innerHTML = "&#9888; ADVERTENCIA: RETROCESO FUERTE DEL MERCADO";
    alertSub.textContent = `${pt.date} | ${reason}`;

    if (lastLoggedIndex !== idx) {
      lastLoggedIndex = idx;
      appendHistoryRow(pt.date, pt.close, pt.step_return_pct, pt.drawdown_pct, "ADVERTENCIA", reason);
    }
  } else {
    detectorCard.className = "kpi-card card detector-normal";
    detectorState.textContent = pt.is_prediction ? "PROYECCIÓN ML" : "ESTABLE";
    detectorReason.textContent = pt.is_prediction
      ? "Proyección aprendida (2027–2031)"
      : "Operación dentro de parámetros normales";
    if (!crashSimulated) {
      alertBanner.classList.add("hidden");
    }
  }

  const phaseBadge = document.getElementById("phase-badge");
  if (pt.is_simulated_crash) {
    phaseBadge.textContent = "Simulación de Pánico Activa";
    phaseBadge.className = "badge-pill badge-red";
  } else if (pt.is_prediction) {
    phaseBadge.textContent = "Predicción ML Futura (2027–2031)";
    phaseBadge.className = "badge-pill badge-purple";
  } else {
    phaseBadge.textContent = `Índice Histórico Real (${pt.year})`;
    phaseBadge.className = "badge-pill badge-blue";
  }

  const startSlice = Math.max(0, idx - WINDOW_SIZE + 1);
  const slice = activeSeries.slice(startSlice, idx + 1);

  const labels = slice.map((d) => d.date);
  const realData = slice.map((d) => (!d.is_prediction ? d.close : null));
  const predData = slice.map((d, i) => {
    if (d.is_prediction) return d.close;
    if (i + 1 < slice.length && slice[i + 1].is_prediction) return d.close;
    return null;
  });
  const thresholdData = slice.map((d) => d.critical_threshold);

  if (isCrack || crashSimulated) {
    marketChart.data.datasets[0].borderColor = "#dc2626";
    marketChart.data.datasets[0].backgroundColor = "rgba(220, 38, 38, 0.14)";
    marketChart.data.datasets[0].pointBackgroundColor = "#dc2626";
  } else {
    marketChart.data.datasets[0].borderColor = "#0C2D50";
    marketChart.data.datasets[0].backgroundColor = "rgba(12, 45, 80, 0.11)";
    marketChart.data.datasets[0].pointBackgroundColor = "#0C2D50";
  }

  marketChart.data.labels = labels;
  marketChart.data.datasets[0].data = realData;
  marketChart.data.datasets[1].data = predData;
  marketChart.data.datasets[2].data = thresholdData;
  marketChart.update("none");
}

function stopTimer() {
  if (timerId !== null) {
    clearInterval(timerId);
    timerId = null;
  }
  isPlaying = false;
}

function startPlayback() {
  stopTimer();
  if (!activeSeries.length) return;

  if (currentIndex >= activeSeries.length - 1) {
    currentIndex = 0;
  }

  isPlaying = true;
  const btnPlay = document.getElementById("btn-toggle-play");
  btnPlay.textContent = "Pausar";

  const delayMs = Number(document.getElementById("speed-select").value) || 120;
  updateViewAtCursor();

  timerId = setInterval(() => {
    if (currentIndex < activeSeries.length - 1) {
      currentIndex += 1;
      updateViewAtCursor();
    } else {
      stopTimer();
      btnPlay.textContent = "Finalizado";
    }
  }, delayMs);
}

function simulateMarketCrash() {
  if (!activeSeries.length) return;
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }

  crashSimulated = true;
  criticalAlertTriggered = false;
  isMuted = false;

  const baseSlice = activeSeries.slice(0, currentIndex + 1).map((item) => ({ ...item }));
  const anchor = baseSlice[baseSlice.length - 1];
  let prevClose = anchor.close;
  let peak = anchor.rolling_peak;

  const shockDrops = [-0.056, -0.092, -0.128, -0.074, -0.049, -0.032];
  const baseDate = new Date(anchor.date + "T00:00:00");

  const crashPoints = shockDrops.map((drop, i) => {
    const nextDate = new Date(baseDate);
    nextDate.setDate(baseDate.getDate() + (i + 1) * 3);
    const yyyy = nextDate.getFullYear();
    const mm = String(nextDate.getMonth() + 1).padStart(2, "0");
    const dd = String(nextDate.getDate()).padStart(2, "0");

    const newClose = Number((prevClose * (1 + drop)).toFixed(2));
    const ddPct = Number((((newClose - peak) / peak) * 100).toFixed(2));
    prevClose = newClose;

    return {
      date: `${yyyy}-${mm}-${dd}`,
      year: yyyy,
      close: newClose,
      step_return_pct: Number((drop * 100).toFixed(2)),
      rolling_peak: peak,
      critical_threshold: Number((peak * 0.88).toFixed(2)),
      drawdown_pct: ddPct,
      is_prediction: anchor.is_prediction,
      is_simulated_crash: true,
      trigger_alarm: i === 0 || i === 2,
    };
  });

  activeSeries = [...baseSlice, ...crashPoints];
  currentIndex += 1;
  playAlarmSound();

  stopTimer();
  isPlaying = true;
  const btnPlay = document.getElementById("btn-toggle-play");
  btnPlay.textContent = "Pausar";
  updateViewAtCursor();

  timerId = setInterval(() => {
    if (currentIndex < activeSeries.length - 1) {
      currentIndex += 1;
      updateViewAtCursor();
    } else {
      stopTimer();
      btnPlay.textContent = "Finalizado";
    }
  }, 260);
}

function restartNormalCycle() {
  crashSimulated = false;
  criticalAlertTriggered = false;
  lastLoggedIndex = -1;
  document.getElementById("top-alert-banner").classList.add("hidden");
  activeSeries = originalSeries.map((item) => ({ ...item }));
  currentIndex = 0;

  document.querySelectorAll(".jump-chip").forEach((c) => c.classList.remove("active"));
  const firstChip = document.querySelector('.jump-chip[data-jump="start"]');
  if (firstChip) firstChip.classList.add("active");

  startPlayback();
}

function jumpToEra(era) {
  if (!originalSeries.length) return;
  crashSimulated = false;
  criticalAlertTriggered = false;
  lastLoggedIndex = -1;
  document.getElementById("top-alert-banner").classList.add("hidden");
  activeSeries = originalSeries.map((item) => ({ ...item }));

  let targetYear = 1928;
  if (era === "2008") targetYear = 2007;
  else if (era === "2020") targetYear = 2020;
  else if (era === "2026") targetYear = 2025;

  const foundIdx = activeSeries.findIndex((d) => d.year >= targetYear);
  currentIndex = foundIdx >= 0 ? foundIdx : 0;
  startPlayback();
}

function renderDagSvg(tasks, dependencies) {
  const container = document.getElementById("dag-svg-container");
  if (!container || !tasks) return;

  const nodeW = 165;
  const nodeH = 56;
  const stateMap = Object.fromEntries(tasks.map((t) => [t.task_id, t.state]));

  const edgesHtml = (dependencies || [])
    .map(([src, dst]) => {
      const [x1, y1] = DAG_POSITIONS[src] || [20, 95];
      const [x2, y2] = DAG_POSITIONS[dst] || [210, 95];
      const dstState = stateMap[dst];
      const color = dstState === "TRIGGERED" ? "#ea580c" : "#64748b";
      const dash = dstState === "SKIPPED" ? ' stroke-dasharray="6 5"' : "";
      return `<line x1="${x1 + nodeW}" y1="${y1 + nodeH / 2}" x2="${x2}" y2="${y2 + nodeH / 2}" stroke="${color}" stroke-width="2.2"${dash} marker-end="url(#arrow)" />`;
    })
    .join("");

  const nodesHtml = tasks
    .map((t) => {
      const [x, y] = DAG_POSITIONS[t.task_id] || [20, 95];
      const fill = DAG_COLORS[t.state] || "#cbd5e1";
      const textColor = t.state === "PENDING" ? "#334155" : "#ffffff";
      return `
        <g>
          <rect x="${x}" y="${y}" width="${nodeW}" height="${nodeH}" rx="10" fill="${fill}" stroke="#0f172a" stroke-width="1.3" />
          <text x="${x + nodeW / 2}" y="${y + 23}" text-anchor="middle" font-size="11" font-weight="700" fill="${textColor}">${escHtml(t.task_id)}</text>
          <text x="${x + nodeW / 2}" y="${y + 42}" text-anchor="middle" font-size="10" font-weight="600" fill="${textColor}">${escHtml(t.state)} (${t.duration_ms} ms)</text>
        </g>`;
    })
    .join("");

  container.innerHTML = `
    <svg viewBox="0 0 1010 245" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="#64748b" />
        </marker>
      </defs>
      ${edgesHtml}
      ${nodesHtml}
    </svg>`;
}

function renderDagAndTables(data) {
  if (!data) return;

  const report = data.dag_report;
  if (report) {
    const badge = document.getElementById("dag-overall-badge");
    badge.textContent = report.overall_status;
    badge.className = `st-pill ${report.overall_status === "SUCCESS" ? "st-SUCCESS" : "st-TRIGGERED"}`;

    document.getElementById("dag-run-id").textContent = report.run_id || "--";
    document.getElementById("dag-duration").textContent = `${report.total_duration_ms} ms`;

    renderDagSvg(report.tasks, report.dependencies);

    const tbody = document.getElementById("dag-task-body");
    tbody.innerHTML = (report.tasks || [])
      .map((t) => {
        const lastLog = t.logs && t.logs.length ? t.logs[t.logs.length - 1] : "--";
        return `
          <tr>
            <td><strong>${escHtml(t.task_id)}</strong><br><small style="color:#64748b;">${escHtml(t.name)}</small></td>
            <td><strong>${escHtml(t.layer)}</strong></td>
            <td><code>${escHtml(t.trigger_rule)}</code></td>
            <td><span class="st-pill st-${t.state}">${t.state}</span></td>
            <td>${t.duration_ms}</td>
            <td><small>${escHtml(lastLog)}</small></td>
          </tr>`;
      })
      .join("");
  }

  const gold = data.gold_payload;
  if (gold) {
    document.getElementById("src-api-name").textContent = gold.source_api;
    document.getElementById("src-records").textContent = `${gold.playback_series.length} puntos (${gold.raw_total_days.toLocaleString("en-US")} días API)`;

    const mlBody = document.getElementById("ml-table-body");
    mlBody.innerHTML = (gold.annual_forecast || [])
      .map(
        (r) => `
        <tr>
          <td><strong>${r.year}</strong></td>
          <td class="val-red">${formatMoney(r.bearish_end)}</td>
          <td class="val-green">${formatMoney(r.close_end)}</td>
          <td>${formatMoney(r.optimistic_end)}</td>
        </tr>`
      )
      .join("");
  }

  const bronze = data.bronze_meta;
  if (bronze) {
    const bGrid = document.getElementById("bronze-audit-grid");
    bGrid.innerHTML = `
      <div class="bronze-box"><span>API Fuente (Sin Hardcode)</span><strong>${escHtml(bronze.source_api)}</strong></div>
      <div class="bronze-box"><span>Rango Temporal Crudo</span><strong>${escHtml(bronze.date_min)} &rarr; ${escHtml(bronze.date_max)}</strong></div>
      <div class="bronze-box"><span>Total Sesiones Crudas</span><strong>${Number(bronze.raw_rows).toLocaleString("en-US")} registros</strong></div>
      <div class="bronze-box"><span>Huella Inmutable SHA-256</span><code>${escHtml(bronze.sha256_checksum)}</code></div>
    `;
  }
}

async function loadInitialData() {
  initChart();
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    renderDagAndTables(data);

    if (data.gold_payload && data.gold_payload.playback_series) {
      originalSeries = data.gold_payload.playback_series;
      activeSeries = originalSeries.map((item) => ({ ...item }));
      currentIndex = 0;
      startPlayback();
    }
  } catch (err) {
    console.error("Error cargando datos iniciales:", err);
  }
}

async function triggerDagRun(options, resetPlayer = true) {
  if (resetPlayer) stopTimer();
  const badge = document.getElementById("dag-overall-badge");
  badge.textContent = "RUNNING";
  badge.className = "st-pill st-RUNNING";

  try {
    const res = await fetch("/api/run-dag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const data = await res.json();
    renderDagAndTables(data);
    if (resetPlayer && data.gold_payload && data.gold_payload.playback_series) {
      originalSeries = data.gold_payload.playback_series;
      restartNormalCycle();
    }
  } catch (err) {
    console.error(err);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("btn-simulate-crash").addEventListener("click", simulateMarketCrash);

  document.getElementById("btn-toggle-play").addEventListener("click", (e) => {
    if (isPlaying) {
      stopTimer();
      e.target.textContent = "Reanudar";
    } else {
      startPlayback();
    }
  });

  document.getElementById("btn-restart").addEventListener("click", restartNormalCycle);

  document.getElementById("speed-select").addEventListener("change", () => {
    if (isPlaying) startPlayback();
  });

  document.getElementById("btn-mute").addEventListener("click", (e) => {
    isMuted = !isMuted;
    e.target.textContent = isMuted ? "Activar sonido" : "Silenciar";
  });

  document.getElementById("btn-refresh-api").addEventListener("click", () => {
    triggerDagRun({ force_refresh: true, simulate_corruption: false }, true);
  });

  document.getElementById("btn-dag-normal").addEventListener("click", () => {
    triggerDagRun({ force_refresh: false, simulate_corruption: false }, false);
  });

  document.getElementById("btn-dag-crisis").addEventListener("click", () => {
    triggerDagRun({ force_refresh: false, simulate_corruption: true }, false);
  });

  document.getElementById("btn-clear-history").addEventListener("click", () => {
    document.getElementById("history-table-body").innerHTML =
      `<tr class="empty-row"><td colspan="6">Historial limpio. Esperando nuevos eventos...</td></tr>`;
  });

  document.querySelectorAll(".jump-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".jump-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      jumpToEra(chip.getAttribute("data-jump"));
    });
  });

  loadInitialData();
});
