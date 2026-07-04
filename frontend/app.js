"use strict";

const state = {
  status: null,
  summary: null,
  liveImages: null,
  lastImagesKey: null,
  busy: false,
  serverBusy: {},
};

const $ = (s) => document.querySelector(s);
const PY = "#0b7d78";
const CPP = "#b23a6b";

/* ----------------------------- helpers ----------------------------- */
function num(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  return new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 0, maximumFractionDigits: digits }).format(Number(v));
}
function ms(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  const n = Number(v);
  return n >= 1000 ? `${num(n / 1000, 2)} s` : `${num(n, n >= 100 ? 0 : 1)} ms`;
}
function mb(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  const n = Number(v);
  return n >= 1024 ? `${num(n / 1024, 2)} GB` : `${num(n, 0)} MB`;
}
function bytesToMb(v) {
  return v === null || v === undefined ? null : Number(v) / (1024 * 1024);
}
function escapeHtml(v) {
  return String(v).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
function cssServer(s) {
  return s === "python" ? "server-python" : "server-cpp";
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await res.json();
  if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
  return payload;
}

/* Remove ruido: modelo "unknown" e servidores fora de python/cpp (linhas de erro). */
function cleanAggregates(aggregates) {
  return (aggregates || []).filter((a) => a.model_id !== "unknown" && (a.server === "python" || a.server === "cpp"));
}

/* ----------------------------- refresh ----------------------------- */
async function refreshFast() {
  try {
    const [status, live] = await Promise.all([api("/api/status"), api("/api/live-images")]);
    state.status = status;
    state.liveImages = live;
    renderServerCard("#pythonStatus", "python", status.servers?.python, status.managed?.python);
    renderServerCard("#cppStatus", "cpp", status.servers?.cpp, status.managed?.cpp);
    renderMachineCard(status.machine);
    renderRuns(status);
    renderLive(status);
    renderImages(state.liveImages);
  } catch (e) {
    $("#runMessage").textContent = `Falha ao atualizar: ${e.message}`;
  }
}

async function refreshSlow() {
  try {
    const summary = await api("/api/summary");
    state.summary = summary;
    renderComparison(summary);
    renderMachineByServer(summary);
    renderLatestLoad(summary);
    renderLoadWindows(summary);
  } catch (e) {
    $("#runMessage").textContent = `Falha ao atualizar: ${e.message}`;
  }
}

async function refreshAll() {
  await Promise.all([refreshFast(), refreshSlow()]);
}

/* ----------------------------- servidores ----------------------------- */
function renderServerCard(selector, target, payload, managed) {
  const online = Boolean(payload && payload.online);
  const data = online ? payload.data : {};
  const card = $(selector);
  const pill = card.querySelector(".status-pill");
  const list = card.querySelector(".metric-list");
  const toggle = card.querySelector(".server-toggle");
  const logEl = card.querySelector(".server-log");

  pill.className = `status-pill ${online ? "status-online" : "status-offline"}`;
  pill.textContent = online ? "online" : "offline";

  const metrics = online
    ? [
        ["Fila", data.queue_size ?? "-"],
        ["Ativos", data.active_jobs ?? "-"],
        ["Concluidos", data.completed_jobs ?? "-"],
        ["Rejeitados", data.rejected_jobs ?? "-"],
        ["Workers", data.max_workers ?? "-"],
        ["RAM processo", data.rss_bytes ? mb(bytesToMb(data.rss_bytes)) : "-"],
      ]
    : [["Estado", "sem conexao"], ["Detalhe", payload?.error ? "desligado" : "-"]];
  list.innerHTML = metrics.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");

  const info = managed || {};
  const pending = state.serverBusy[target];
  if (pending) {
    toggle.disabled = true;
    toggle.className = "server-toggle";
    toggle.textContent = pending === "start" ? "Ligando..." : "Desligando...";
  } else if (online && info.managed) {
    toggle.disabled = false;
    toggle.className = "server-toggle is-stop";
    toggle.textContent = "Desligar";
    toggle.dataset.action = "stop";
  } else if (online) {
    toggle.disabled = true;
    toggle.className = "server-toggle is-external";
    toggle.textContent = "Externo";
    toggle.dataset.action = "";
  } else {
    toggle.disabled = false;
    toggle.className = "server-toggle";
    toggle.textContent = "Ligar";
    toggle.dataset.action = "start";
  }

  const showLog = !online && info.log_tail && (info.managed || info.return_code !== undefined);
  logEl.hidden = !showLog;
  logEl.textContent = showLog ? info.log_tail : "";
}

function renderMachineCard(machine) {
  const m = machine || {};
  const cpu = m.cpu_percent;
  const load = m.mem_load_percent;
  $("#machineCpu").textContent = cpu === null || cpu === undefined ? "-" : `${num(cpu, 0)}%`;
  $("#machineCpuBar").style.width = `${Math.max(0, Math.min(100, Number(cpu) || 0))}%`;
  $("#machineRam").textContent = load === null || load === undefined ? "-" : `${num(load, 0)}%`;
  $("#machineRamBar").style.width = `${Math.max(0, Math.min(100, Number(load) || 0))}%`;
  $("#machineRamSub").textContent =
    m.mem_used_mb != null ? `${mb(m.mem_used_mb)} de ${mb(m.mem_total_mb)} em uso` : "-";
}

async function controlServer(target, action) {
  if (!action || state.serverBusy[target]) return;
  state.serverBusy[target] = action;
  refreshFast();
  try {
    await api("/api/server-control", { method: "POST", body: JSON.stringify({ target, action }) });
    $("#runMessage").textContent = action === "start" ? `Ligando ${target}...` : `Desligando ${target}...`;
  } catch (e) {
    $("#runMessage").textContent = `Falha ao ${action} ${target}: ${e.message}`;
  } finally {
    setTimeout(() => {
      delete state.serverBusy[target];
      refreshAll();
    }, 1500);
  }
}

/* ----------------------------- log ao vivo ----------------------------- */
function classifyLogLine(line) {
  const l = line.toLowerCase();
  if (line.startsWith("$ ")) return "log-cmd";
  if (/connection_error|http_error|\[erro\]|\[timeout\]|error|timeout|falha/.test(l)) return "log-error";
  if (/(^|\s)ok(\s|$)/.test(l)) return "log-ok";
  if (l.trimStart().startsWith("python:")) return "log-py";
  if (l.trimStart().startsWith("cpp:")) return "log-cpp";
  return "log-info";
}

function renderRuns(status) {
  const running = (status.dashboard_runs || []).find((r) => r.status === "running");
  if (running) {
    $("#runButton").disabled = true;
    $("#loadButton").disabled = true;
  } else if (!state.busy) {
    $("#runButton").disabled = false;
    $("#loadButton").disabled = false;
  }
}

function renderLive(status) {
  const runs = status.dashboard_runs || [];
  const current = runs.find((r) => r.status === "running") || runs[0];
  const badge = $("#liveStatus");
  const logEl = $("#liveLog");
  if (!current) {
    badge.className = "live-badge is-idle";
    badge.textContent = "ocioso";
    return;
  }
  const kind = current.type === "load" ? "saturacao" : "comparativo";
  if (current.status === "running") {
    badge.className = "live-badge is-running";
    badge.textContent = `executando - ${kind}`;
  } else if (current.status === "completed") {
    badge.className = "live-badge is-done";
    badge.textContent = `concluido - ${kind}`;
  } else {
    badge.className = "live-badge is-failed";
    badge.textContent = `${current.status} - ${kind}`;
  }
  const lines = current.log && current.log.length ? current.log : ["(aguardando saida...)"];
  const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
  logEl.innerHTML = lines
    .map((l) => `<span class="log-line ${classifyLogLine(l)}">${escapeHtml(l)}</span>`)
    .join("");
  if (atBottom) logEl.scrollTop = logEl.scrollHeight;
}

/* ----------------------------- comparacao Python x C++ ----------------------------- */
// Pivota os agregados por modelo -> {python, cpp}
function pivotByModel(aggregates) {
  const byModel = {};
  for (const a of cleanAggregates(aggregates)) {
    byModel[a.model_id] = byModel[a.model_id] || {};
    byModel[a.model_id][a.server] = a;
  }
  return byModel;
}

const COMPARISON_METRICS = [
  { key: "avg_reconstruction_ms", label: "Tempo reconstrucao", fmt: ms, better: "low" },
  { key: "avg_roundtrip_ms", label: "Roundtrip total", fmt: ms, better: "low" },
  { key: "avg_cpu_ms", label: "CPU por reconstrucao", fmt: ms, better: "low" },
  { key: "avg_rss_bytes", label: "RAM do processo", fmt: (v) => mb(bytesToMb(v)), better: "low" },
  { key: "avg_iterations", label: "Iteracoes", fmt: (v) => num(v, 1), better: "none" },
  { key: "avg_error_abs", label: "Erro medio", fmt: (v) => num(v, 6), better: "low" },
  { key: "images_per_second", label: "Vazao (img/s)", fmt: (v) => num(v, 2), better: "high" },
];

function renderComparison(summary) {
  const byModel = pivotByModel(summary.aggregates);
  const models = Object.keys(byModel).sort();
  $("#comparisonMeta").textContent = models.length
    ? `${models.length} modelo(s) - atualizado ${summary.generated_at || ""}`
    : "sem dados ainda";

  // Graficos: tempo de reconstrucao e CPU
  drawGroupedBars($("#timeChart"), models, byModel, "avg_reconstruction_ms", "ms");
  drawGroupedBars($("#cpuChart"), models, byModel, "avg_cpu_ms", "ms");

  // Tabela lado a lado
  const rows = [];
  for (const model of models) {
    const py = byModel[model].python;
    const cpp = byModel[model].cpp;
    COMPARISON_METRICS.forEach((metric, i) => {
      const pv = py ? py[metric.key] : null;
      const cv = cpp ? cpp[metric.key] : null;
      rows.push(
        `<tr>
          <td>${i === 0 ? `<strong>${model}</strong>` : ""}</td>
          <td class="metric-name">${metric.label}</td>
          <td class="col-py">${pv == null ? "-" : metric.fmt(pv)}</td>
          <td class="col-cpp">${cv == null ? "-" : metric.fmt(cv)}</td>
          <td>${winnerCell(pv, cv, metric.better)}</td>
        </tr>`
      );
    });
  }
  $("#comparisonTable").innerHTML = rows.length
    ? rows.join("")
    : `<tr><td colspan="5">Rode um comparativo (Iniciar comparativo) para ver os numeros.</td></tr>`;
}

function winnerCell(pv, cv, better) {
  if (better === "none" || pv == null || cv == null) return "-";
  const pyWins = better === "low" ? pv < cv : pv > cv;
  const winner = pyWins ? "python" : "cpp";
  const hi = Math.max(Math.abs(pv), Math.abs(cv));
  const lo = Math.min(Math.abs(pv), Math.abs(cv));
  const ratio = lo > 0 ? hi / lo : null;
  const label = winner === "python" ? "Python" : "C++";
  const ratioTxt = ratio && ratio >= 1.05 ? ` (${num(ratio, 1)}x)` : "";
  return `<span class="win-${winner === "python" ? "py" : "cpp"}">${label}${ratioTxt}</span>`;
}

/* Grafico de barras agrupadas: uma dupla (Python, C++) por modelo. */
function drawGroupedBars(canvas, models, byModel, key, unit) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const W = Math.max(1, Math.floor(rect.width * ratio));
  const H = Math.max(1, Math.floor((rect.height || 260) * ratio));
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height || 260;
  ctx.clearRect(0, 0, width, height);

  const data = models
    .map((m) => ({ model: m, py: byModel[m].python?.[key] ?? null, cpp: byModel[m].cpp?.[key] ?? null }))
    .filter((d) => d.py != null || d.cpp != null);

  if (!data.length) {
    ctx.fillStyle = "#8a938c";
    ctx.font = "14px Segoe UI, Arial";
    ctx.textAlign = "center";
    ctx.fillText("Sem dados - rode um comparativo.", width / 2, height / 2);
    return;
  }

  const pad = { top: 26, right: 16, bottom: 42, left: 56 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const maxVal = Math.max(...data.flatMap((d) => [d.py || 0, d.cpp || 0])) * 1.18 || 1;

  // eixo + gridlines
  ctx.strokeStyle = "#e3e6de";
  ctx.fillStyle = "#8a938c";
  ctx.font = "11px Segoe UI, Arial";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const val = (maxVal / 4) * i;
    const y = pad.top + innerH - (val / maxVal) * innerH;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + innerW, y);
    ctx.stroke();
    ctx.fillText(num(val, 0), pad.left - 8, y + 4);
  }

  const groupW = innerW / data.length;
  const barW = Math.min(46, (groupW - 18) / 2);
  data.forEach((d, i) => {
    const cx = pad.left + i * groupW + groupW / 2;
    const pairs = [
      { v: d.py, color: PY, dx: -barW - 3 },
      { v: d.cpp, color: CPP, dx: 3 },
    ];
    pairs.forEach((p) => {
      if (p.v == null) return;
      const h = (p.v / maxVal) * innerH;
      const x = cx + p.dx;
      const y = pad.top + innerH - h;
      ctx.fillStyle = p.color;
      ctx.fillRect(x, y, barW, h);
      ctx.fillStyle = "#3a403c";
      ctx.font = "11px Segoe UI, Arial";
      ctx.textAlign = "center";
      ctx.fillText(num(p.v, p.v >= 100 ? 0 : 1), x + barW / 2, Math.max(pad.top + 10, y - 5));
    });
    ctx.fillStyle = "#4a524c";
    ctx.font = "12px Segoe UI, Arial";
    ctx.textAlign = "center";
    ctx.fillText(d.model, cx, pad.top + innerH + 18);
  });

  // legenda
  const legend = [
    { label: "Python", color: PY },
    { label: "C++", color: CPP },
  ];
  let lx = pad.left;
  const ly = 14;
  legend.forEach((item) => {
    ctx.fillStyle = item.color;
    ctx.fillRect(lx, ly - 8, 12, 12);
    ctx.fillStyle = "#4a524c";
    ctx.font = "12px Segoe UI, Arial";
    ctx.textAlign = "left";
    ctx.fillText(item.label, lx + 17, ly + 2);
    lx += 17 + ctx.measureText(item.label).width + 22;
  });
  ctx.fillStyle = "#8a938c";
  ctx.textAlign = "right";
  ctx.fillText(unit, width - pad.right, ly + 2);
}

/* ----------------------------- maquina por servidor ----------------------------- */
function renderMachineByServer(summary) {
  const data = summary.machine_by_server || {};
  const servers = data.servers || {};
  const total = data.mem_total_mb;
  $("#machineByServerMeta").textContent = data.file ? `fonte: ${data.file}` : "sem saturacao registrada";
  const order = ["python", "cpp"].filter((s) => servers[s]);
  $("#machineTable").innerHTML = order.length
    ? order
        .map((s) => {
          const m = servers[s];
          return `<tr>
            <td class="server-name ${cssServer(s)}">${s}</td>
            <td>${m.cpu_peak_percent == null ? "-" : num(m.cpu_peak_percent, 1) + "%"}</td>
            <td>${m.cpu_avg_percent == null ? "-" : num(m.cpu_avg_percent, 1) + "%"}</td>
            <td>${mb(m.mem_peak_mb)}</td>
            <td>${mb(m.mem_avg_mb)}</td>
            <td>${mb(total)}</td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="6">Rode uma saturacao (Iniciar saturacao) para medir a maquina.</td></tr>`;
}

/* ----------------------------- saturacao ----------------------------- */
function renderLatestLoad(summary) {
  const latest = summary.latest_load;
  const table = $("#latestLoadTable");
  if (!latest || !latest.servers?.length) {
    $("#latestLoadFile").textContent = "sem dados";
    $("#latestLoadWhen").textContent = "-";
    table.innerHTML = `<tr><td colspan="10">Nenhuma saturacao registrada.</td></tr>`;
    return;
  }
  $("#latestLoadFile").textContent = latest.file || "-";
  $("#latestLoadWhen").textContent = latest.generated_at || "-";
  const servers = latest.servers.filter((s) => s.server === "python" || s.server === "cpp");
  table.innerHTML = servers
    .map(
      (s) => `<tr>
        <td class="server-name ${cssServer(s.server)}">${s.server}</td>
        <td>${num(s.ok, 0)}/${num(s.total, 0)}</td>
        <td>${num(s.failed, 0)}</td>
        <td>${num(Number(s.error_rate || 0) * 100, 1)}%</td>
        <td>${num(s.planned_rate_per_minute, 0)}</td>
        <td>${num(s.achieved_requests_per_minute, 1)}</td>
        <td>${ms(s.avg_reconstruction_ms)}</td>
        <td>${ms(s.p95_roundtrip_ms)}</td>
        <td>${ms(s.avg_queue_ms)}</td>
        <td>${mb(bytesToMb(s.peak_rss_bytes))}</td>
      </tr>`
    )
    .join("");
}

function renderLoadWindows(summary) {
  const windows = (summary.load_windows || []).slice(-14).reverse();
  $("#loadCount").textContent = num(summary.load_windows?.length || 0, 0);
  $("#loadTable").innerHTML = windows.length
    ? windows
        .map((r) => {
          const decision = r.decision || (r.healthy ? "manter" : "reduzir");
          return `<tr>
            <td class="server-name ${cssServer(r.server)}">${r.server || "-"}</td>
            <td>${num(r.window, 0)}</td>
            <td>${num(r.planned_rate_per_minute, 0)} req/min</td>
            <td>${num(r.ok, 0)}/${num(r.total, 0)}</td>
            <td>${num(Number(r.error_rate || 0) * 100, 1)}%</td>
            <td>${ms(r.p95_roundtrip_ms)}</td>
            <td>${ms(r.avg_queue_ms)}</td>
            <td class="decision-${decision}">${decision}</td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="8">Sem teste de saturacao registrado.</td></tr>`;
}

/* ----------------------------- imagens ----------------------------- */
function renderImages(live) {
  const images = (live && live.images) || [];
  $("#imageCount").textContent = num((live && live.total) || images.length || 0, 0);
  const key = images.map((r) => r.image_path).join("|");
  if (key === state.lastImagesKey) return;
  state.lastImagesKey = key;
  const now = Date.now();
  $("#imageGrid").innerHTML = images.length
    ? images
        .map((r) => {
          const src = `/api/image?path=${encodeURIComponent(r.image_path)}`;
          const gen = r.generated_at ? new Date(r.generated_at).getTime() : 0;
          const fresh = gen && now - gen < 20000;
          const badge = fresh ? '<span class="image-badge">novo</span>' : "";
          return `<article class="image-card ${fresh ? "is-fresh" : ""}">
            <img src="${src}" alt="Reconstrucao ${r.model_id} ${r.server}" loading="lazy">
            <div class="image-meta">
              <strong>${r.server || "-"} | ${r.model_id || "-"}${badge}</strong>
              <span>${r.generated_at || "-"}</span>
            </div>
          </article>`;
        })
        .join("")
    : `<div class="empty-state">Sem imagens reconstruidas ainda.</div>`;
}

/* ----------------------------- disparar execucoes ----------------------------- */
async function startRun() {
  if (state.busy) return;
  const servers = state.status?.servers || {};
  if (!servers.python?.online && !servers.cpp?.online) {
    $("#runMessage").textContent = 'Ligue ao menos um servidor (botao "Ligar") antes de iniciar o comparativo.';
    return;
  }
  state.busy = true;
  $("#runButton").disabled = true;
  $("#runMessage").textContent = "Disparando comparativo...";
  try {
    const run = await api("/api/run", {
      method: "POST",
      body: JSON.stringify({
        model: $("#modelSelect").value,
        count: Number($("#countInput").value),
        gain: $("#gainSelect").value,
      }),
    });
    $("#runMessage").textContent = `Comparativo iniciado: ${run.model}, ${run.count} amostra(s).`;
    await refreshFast();
  } catch (e) {
    $("#runMessage").textContent = `Falha ao iniciar: ${e.message}`;
  } finally {
    state.busy = false;
    $("#runButton").disabled = false;
  }
}

async function startLoadRun() {
  if (state.busy) return;
  state.busy = true;
  $("#runButton").disabled = true;
  $("#loadButton").disabled = true;
  $("#runMessage").textContent = "Disparando saturacao (liga/roda/desliga um servidor por vez)...";
  try {
    const run = await api("/api/load-run", {
      method: "POST",
      body: JSON.stringify({
        server: $("#loadServerSelect").value,
        model: $("#loadModelSelect").value,
        mode: "fixed",
        clients: Number($("#loadClientsInput").value),
        rate_per_minute: Number($("#loadRateInput").value),
        requests: Number($("#loadRequestsInput").value),
        gain: "none",
      }),
    });
    $("#runMessage").textContent = `Saturacao iniciada: ${run.server}, ${run.clients} clientes, ${run.rate_per_minute} req/min.`;
    await refreshFast();
  } catch (e) {
    $("#runMessage").textContent = `Falha ao iniciar saturacao: ${e.message}`;
  } finally {
    state.busy = false;
    $("#runButton").disabled = false;
    $("#loadButton").disabled = false;
  }
}

/* ----------------------------- eventos + loop ----------------------------- */
$("#refreshButton").addEventListener("click", refreshAll);
$("#runButton").addEventListener("click", startRun);
$("#loadButton").addEventListener("click", startLoadRun);
document.querySelectorAll(".server-toggle").forEach((b) =>
  b.addEventListener("click", () => controlServer(b.dataset.target, b.dataset.action))
);
window.addEventListener("resize", () => {
  if (state.summary) renderComparison(state.summary);
});

setInterval(() => { if ($("#autoRefresh").checked) refreshFast(); }, 2000);
setInterval(() => { if ($("#autoRefresh").checked) refreshSlow(); }, 3500);
refreshAll();
