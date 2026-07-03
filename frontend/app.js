const state = {
  status: null,
  summary: null,
  liveImages: null,
  lastImagesKey: null,
  busy: false,
  serverBusy: {},
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// Classifica cada linha do log ao vivo para colorir por tipo.
function classifyLogLine(line) {
  const l = line.toLowerCase();
  if (line.startsWith("$ ")) return "log-cmd";
  if (
    l.includes("connection_error") ||
    l.includes("http_error") ||
    l.includes("[erro]") ||
    l.includes("[timeout]") ||
    l.includes("error") ||
    l.includes("timeout") ||
    l.includes("falha")
  )
    return "log-error";
  if (/(^|\s)ok(\s|$)/.test(l)) return "log-ok";
  if (l.trimStart().startsWith("python:")) return "log-py";
  if (l.trimStart().startsWith("cpp:")) return "log-cpp";
  return "log-info";
}

function noiseHidden() {
  const box = $("#hideNoise");
  return box ? box.checked : false;
}

function fmtNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return new Intl.NumberFormat("pt-BR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(Number(value));
}

function fmtMs(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${fmtNumber(value, value >= 100 ? 0 : 2)} ms`;
}

function fmtBytes(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let size = Number(value);
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${fmtNumber(size, size >= 100 ? 0 : 1)} ${units[unit]}`;
}

function cssServer(server) {
  return server === "python" ? "server-python" : "server-cpp";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

// Ciclo rapido (~2s): estado dos servidores, log ao vivo e imagens surgindo.
async function refreshFast() {
  try {
    const [status, live] = await Promise.all([api("/api/status"), api("/api/live-images")]);
    state.status = status;
    state.liveImages = live;
    renderStatusPanels();
  } catch (error) {
    $("#runMessage").textContent = `Falha ao atualizar: ${error.message}`;
  }
}

// Ciclo lento (~3.5s): agregados, tabelas, graficos e saturacao.
async function refreshSlow() {
  try {
    const summary = await api("/api/summary");
    state.summary = summary;
    renderSummaryPanels();
  } catch (error) {
    $("#runMessage").textContent = `Falha ao atualizar: ${error.message}`;
  }
}

async function refreshAll() {
  await Promise.all([refreshFast(), refreshSlow()]);
}

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
        ["RSS", data.rss_bytes ? fmtBytes(data.rss_bytes) : "-"],
      ]
    : [
        ["Estado", "sem conexao"],
        ["Detalhe", payload?.error || "-"],
      ];

  list.innerHTML = metrics.map(([name, value]) => `<div><dt>${name}</dt><dd>${value}</dd></div>`).join("");

  // Botao de ligar/desligar controlado pelo estado real do servidor.
  const info = managed || {};
  const pending = state.serverBusy[target];
  toggle.dataset.target = target;
  if (pending) {
    toggle.disabled = true;
    toggle.className = "server-toggle";
    toggle.textContent = pending === "start" ? "Ligando..." : "Desligando...";
    toggle.dataset.action = pending;
  } else if (online && info.managed) {
    toggle.disabled = false;
    toggle.className = "server-toggle is-stop";
    toggle.textContent = "Desligar";
    toggle.dataset.action = "stop";
  } else if (online) {
    // Online mas iniciado fora do painel: nao ha processo para encerrar aqui.
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

  // Se o servidor foi iniciado pelo painel mas ainda nao respondeu (ou caiu),
  // mostramos o final do log para revelar erros de inicializacao.
  const showLog = !online && info.log_tail && (info.managed || info.return_code !== undefined);
  if (showLog) {
    logEl.hidden = false;
    logEl.textContent = info.log_tail;
  } else {
    logEl.hidden = true;
    logEl.textContent = "";
  }
}

async function controlServer(target, action) {
  if (!action || state.serverBusy[target]) return;
  state.serverBusy[target] = action;
  render();
  try {
    await api("/api/server-control", {
      method: "POST",
      body: JSON.stringify({ target, action }),
    });
    $("#runMessage").textContent =
      action === "start" ? `Ligando servidor ${target}...` : `Desligando servidor ${target}...`;
  } catch (error) {
    $("#runMessage").textContent = `Falha ao ${action === "start" ? "ligar" : "desligar"} ${target}: ${error.message}`;
  } finally {
    // Da um instante para o servidor subir/descer antes de reavaliar o estado.
    setTimeout(() => {
      delete state.serverBusy[target];
      refreshAll();
    }, 1500);
  }
}

function renderLive(status) {
  const runs = status.dashboard_runs || [];
  const running = runs.find((item) => item.status === "running");
  const current = running || runs[0];
  const badge = $("#liveStatus");
  const logEl = $("#liveLog");
  if (!current) {
    badge.className = "live-badge is-idle";
    badge.textContent = "ocioso";
    return;
  }

  const kind = current.type === "load" ? "saturacao" : "comparativo";
  const label =
    current.type === "load"
      ? `${current.server} · ${current.clients} clientes · ${current.rate_per_minute} req/min`
      : `${current.model} · ${current.count} amostra(s) · ganho ${current.gain}`;

  if (current.status === "running") {
    badge.className = "live-badge is-running";
    badge.textContent = `executando · ${kind}`;
  } else if (current.status === "completed") {
    badge.className = "live-badge is-done";
    badge.textContent = `concluido · ${kind}`;
  } else {
    badge.className = "live-badge is-failed";
    badge.textContent = `${current.status} · ${kind}`;
  }

  const lines = current.log && current.log.length ? current.log : ["(aguardando saida...)"];
  const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
  const header = `<span class="log-line log-muted">${escapeHtml(label)}</span>`;
  const body = lines
    .map((line) => `<span class="log-line ${classifyLogLine(line)}">${escapeHtml(line)}</span>`)
    .join("");
  logEl.innerHTML = header + body;
  // Rola para o fim automaticamente enquanto o usuario nao rolou para cima.
  if (atBottom) logEl.scrollTop = logEl.scrollHeight;
}

function renderKpis(summary) {
  const rows = summary.recent_rows || [];
  const aggregates = summary.aggregates || [];
  const okRows = rows.filter((row) => row.status === "ok" && row.reconstruction_ms);
  const bestRecon = okRows.length
    ? okRows.reduce((best, row) => (row.reconstruction_ms < best.reconstruction_ms ? row : best), okRows[0])
    : null;
  const bestThroughput = aggregates
    .filter((item) => item.images_per_second)
    .sort((a, b) => b.images_per_second - a.images_per_second)[0];
  const lastReport = (summary.reports || []).at(-1);

  $("#totalRuns").textContent = fmtNumber(summary.total_rows || 0, 0);
  $("#bestRecon").textContent = bestRecon ? `${fmtMs(bestRecon.reconstruction_ms)} ${bestRecon.server}` : "-";
  $("#bestThroughput").textContent = bestThroughput
    ? `${fmtNumber(bestThroughput.images_per_second, 2)} img/s`
    : "-";
  $("#lastReport").textContent = lastReport ? lastReport.file.replace("comparison-", "") : "-";
}

function renderTables(summary) {
  const hide = noiseHidden();
  let aggregates = summary.aggregates || [];
  if (hide) {
    // Remove grupos de ruido: modelo "unknown" e servidores fora de python/cpp
    // (linhas que so existem por causa de connection_error).
    aggregates = aggregates.filter(
      (item) => item.model_id !== "unknown" && (item.server === "python" || item.server === "cpp")
    );
  }
  $("#aggregateTable").innerHTML = aggregates.length
    ? aggregates
        .map(
          (item) => `
            <tr>
              <td class="server-name ${cssServer(item.server)}">${item.server}</td>
              <td>${item.model_id}</td>
              <td>${fmtNumber(item.ok_runs, 0)}/${fmtNumber(item.runs, 0)}</td>
              <td>${fmtNumber(item.avg_iterations, 1)}</td>
              <td>${fmtMs(item.avg_reconstruction_ms)}</td>
              <td>${fmtMs(item.avg_cpu_ms)}</td>
              <td>${fmtBytes(item.avg_rss_bytes)}</td>
              <td>${fmtNumber(item.images_per_second, 2)}</td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="8">Sem execucoes registradas.</td></tr>`;

  let recentSource = summary.recent_rows || [];
  if (hide) recentSource = recentSource.filter((row) => row.status === "ok");
  const recent = recentSource.slice(-12).reverse();
  $("#recentCount").textContent = fmtNumber(recentSource.length || 0, 0);
  $("#recentTable").innerHTML = recent.length
    ? recent
        .map(
          (row) => `
            <tr>
              <td class="server-name ${cssServer(row.server)}">${row.server || "-"}</td>
              <td>${row.signal_file || "-"}</td>
              <td>${row.status || "-"}</td>
              <td>${fmtMs(row.reconstruction_ms)}</td>
              <td>${fmtMs(row.roundtrip_ms)}</td>
              <td>${fmtNumber(row.error_abs, 6)}</td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="6">Sem requisicoes recentes.</td></tr>`;
}

function renderLatestLoad(summary) {
  const latest = summary.latest_load;
  const table = $("#latestLoadTable");
  if (!latest || !latest.servers?.length) {
    $("#latestLoadFile").textContent = "-";
    $("#latestLoadSummary").innerHTML = `<div class="empty-state">Nenhuma execucao de saturacao registrada.</div>`;
    table.innerHTML = `<tr><td colspan="11">Sem dados.</td></tr>`;
    return;
  }

  $("#latestLoadFile").textContent = latest.file || "-";
  $("#latestLoadSummary").innerHTML = `
    <div>
      <span class="kpi-label">Gerado em</span>
      <strong>${latest.generated_at || "-"}</strong>
    </div>
    <div>
      <span class="kpi-label">Requisicoes totais</span>
      <strong>${fmtNumber(latest.total, 0)}</strong>
    </div>
    <div>
      <span class="kpi-label">Servidores</span>
      <strong>${latest.servers.map((item) => item.server).join(" + ")}</strong>
    </div>
  `;
  table.innerHTML = latest.servers
    .map(
      (item) => `
        <tr>
          <td class="server-name ${cssServer(item.server)}">${item.server}</td>
          <td>${fmtNumber(item.ok, 0)}/${fmtNumber(item.total, 0)}</td>
          <td>${fmtNumber(item.failed, 0)}</td>
          <td>${fmtNumber(Number(item.error_rate || 0) * 100, 2)}%</td>
          <td>${fmtNumber(item.planned_rate_per_minute, 1)}</td>
          <td>${fmtNumber(item.achieved_requests_per_minute, 2)}</td>
          <td>${fmtMs(item.avg_reconstruction_ms)}</td>
          <td>${fmtMs(item.p95_roundtrip_ms)}</td>
          <td>${fmtMs(item.avg_queue_ms)}</td>
          <td>${fmtMs(item.avg_cpu_ms)}</td>
          <td>${fmtBytes(item.peak_rss_bytes)}</td>
        </tr>
      `
    )
    .join("");
}

function renderImages(live) {
  const images = (live && live.images) || [];
  $("#imageCount").textContent = fmtNumber((live && live.total) || images.length || 0, 0);
  // So redesenha se a lista de imagens mudou (evita re-baixar 12 imagens a cada 2s).
  const key = images.map((row) => row.image_path).join("|");
  if (key === state.lastImagesKey) return;
  state.lastImagesKey = key;
  const now = Date.now();
  $("#imageGrid").innerHTML = images.length
    ? images
        .map((row) => {
          const src = `/api/image?path=${encodeURIComponent(row.image_path)}`;
          const gen = row.generated_at ? new Date(row.generated_at).getTime() : 0;
          // "Nova" = gerada nos ultimos 20s (aparece durante a execucao).
          const fresh = gen && now - gen < 20000;
          const badge = fresh ? '<span class="image-badge">novo</span>' : "";
          return `
            <article class="image-card ${fresh ? "is-fresh" : ""}">
              <img src="${src}" alt="Imagem reconstruida ${row.model_id} ${row.server}">
              <div class="image-meta">
                <strong>${row.server || "-"} | ${row.model_id || "-"}${badge}</strong>
                <span>${row.generated_at || "-"}</span>
              </div>
            </article>
          `;
        })
        .join("")
    : `<div class="empty-state">Sem imagens reconstruidas.</div>`;
}

function renderLoad(summary) {
  const windows = (summary.load_windows || []).slice(-12).reverse();
  $("#loadCount").textContent = fmtNumber(summary.load_windows?.length || 0, 0);
  $("#loadTable").innerHTML = windows.length
    ? windows
        .map(
          (row) => `
            <tr>
              <td class="server-name ${cssServer(row.server)}">${row.server || "-"}</td>
              <td>${fmtNumber(row.window, 0)}</td>
              <td>${fmtNumber(row.planned_rate_per_minute, 1)} req/min</td>
              <td>${fmtNumber(row.ok, 0)}/${fmtNumber(row.total, 0)}</td>
              <td>${fmtNumber(Number(row.error_rate || 0) * 100, 2)}%</td>
              <td>${fmtMs(row.p95_roundtrip_ms)}</td>
              <td>${fmtMs(row.avg_queue_ms)}</td>
              <td>${row.decision || (row.healthy ? "manter" : "reduzir")}</td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="8">Sem teste de saturacao registrado.</td></tr>`;
}

function clearCanvas(canvas) {
  const ctx = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  return { ctx, width: rect.width, height: rect.height };
}

function drawEmpty(canvas, label) {
  const { ctx, width, height } = clearCanvas(canvas);
  ctx.fillStyle = "#66706a";
  ctx.font = "14px Segoe UI, Arial";
  ctx.textAlign = "center";
  ctx.fillText(label, width / 2, height / 2);
}

function renderBarChart(aggregates) {
  const canvas = $("#barChart");
  const values = (aggregates || [])
    .filter((item) => item.avg_reconstruction_ms)
    .map((item) => ({
      label: `${item.server} ${item.model_id}`,
      value: Number(item.avg_reconstruction_ms),
      color: item.server === "python" ? "#087f7a" : "#a33a6b",
    }));
  if (!values.length) return drawEmpty(canvas, "Sem dados para grafico.");

  const { ctx, width, height } = clearCanvas(canvas);
  const padding = { top: 18, right: 18, bottom: 46, left: 52 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;
  const max = Math.max(...values.map((item) => item.value)) * 1.15;
  const barW = Math.max(22, innerW / values.length - 16);

  ctx.strokeStyle = "#d9ddd4";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top);
  ctx.lineTo(padding.left, padding.top + innerH);
  ctx.lineTo(padding.left + innerW, padding.top + innerH);
  ctx.stroke();

  ctx.fillStyle = "#66706a";
  ctx.font = "12px Segoe UI, Arial";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i += 1) {
    const value = (max / 4) * i;
    const y = padding.top + innerH - (value / max) * innerH;
    ctx.fillText(fmtNumber(value, 0), padding.left - 8, y + 4);
    ctx.strokeStyle = i === 0 ? "#d9ddd4" : "#edf0e9";
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(padding.left + innerW, y);
    ctx.stroke();
  }

  values.forEach((item, index) => {
    const x = padding.left + index * (innerW / values.length) + 8;
    const h = (item.value / max) * innerH;
    const y = padding.top + innerH - h;
    ctx.fillStyle = item.color;
    ctx.fillRect(x, y, barW, h);
    ctx.fillStyle = "#202124";
    ctx.textAlign = "center";
    ctx.font = "12px Segoe UI, Arial";
    ctx.fillText(fmtNumber(item.value, 0), x + barW / 2, Math.max(14, y - 6));
    ctx.save();
    ctx.translate(x + barW / 2, padding.top + innerH + 16);
    ctx.rotate(-0.28);
    ctx.fillStyle = "#66706a";
    ctx.fillText(item.label, 0, 0);
    ctx.restore();
  });
}

function renderTimeline(rows) {
  const canvas = $("#timelineChart");
  const values = (rows || [])
    .filter((row) => row.status === "ok" && row.reconstruction_ms)
    .slice(-28)
    .map((row, index) => ({
      index,
      server: row.server,
      value: Number(row.reconstruction_ms),
    }));
  if (values.length < 2) return drawEmpty(canvas, "Historico insuficiente.");

  const { ctx, width, height } = clearCanvas(canvas);
  const padding = { top: 18, right: 18, bottom: 36, left: 52 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;
  const max = Math.max(...values.map((item) => item.value)) * 1.15;
  const min = Math.min(...values.map((item) => item.value)) * 0.85;
  const range = Math.max(1, max - min);

  ctx.strokeStyle = "#d9ddd4";
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top);
  ctx.lineTo(padding.left, padding.top + innerH);
  ctx.lineTo(padding.left + innerW, padding.top + innerH);
  ctx.stroke();

  ctx.fillStyle = "#66706a";
  ctx.font = "12px Segoe UI, Arial";
  ctx.textAlign = "right";
  [min, (min + max) / 2, max].forEach((value) => {
    const y = padding.top + innerH - ((value - min) / range) * innerH;
    ctx.fillText(fmtNumber(value, 0), padding.left - 8, y + 4);
  });

  ["python", "cpp"].forEach((server) => {
    const points = values.filter((item) => item.server === server);
    if (!points.length) return;
    ctx.strokeStyle = server === "python" ? "#087f7a" : "#a33a6b";
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((item, pointIndex) => {
      const x = padding.left + (item.index / Math.max(1, values.length - 1)) * innerW;
      const y = padding.top + innerH - ((item.value - min) / range) * innerH;
      if (pointIndex === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    points.forEach((item) => {
      const x = padding.left + (item.index / Math.max(1, values.length - 1)) * innerW;
      const y = padding.top + innerH - ((item.value - min) / range) * innerH;
      ctx.fillStyle = server === "python" ? "#087f7a" : "#a33a6b";
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
    });
  });
}

function renderRuns(status) {
  const running = (status.dashboard_runs || []).find((item) => item.status === "running");
  if (running) {
    if (running.type === "load") {
      $("#runMessage").textContent = `Saturacao em execucao: ${running.server}, ${running.clients} clientes, ${running.rate_per_minute} req/min.`;
    } else {
      $("#runMessage").textContent = `Executando ${running.model}, ${running.count} amostra(s).`;
    }
    $("#runButton").disabled = true;
    $("#loadButton").disabled = true;
  } else if (!state.busy) {
    $("#runButton").disabled = false;
    $("#loadButton").disabled = false;
  }
}

// Paines do ciclo rapido (servidores, log ao vivo, imagens ao vivo).
function renderStatusPanels() {
  const status = state.status || {};
  renderServerCard("#pythonStatus", "python", status.servers?.python, status.managed?.python);
  renderServerCard("#cppStatus", "cpp", status.servers?.cpp, status.managed?.cpp);
  renderRuns(status);
  renderLive(status);
  renderImages(state.liveImages || {});
}

// Paines do ciclo lento (agregados, tabelas, graficos, saturacao).
function renderSummaryPanels() {
  const summary = state.summary || {};
  renderKpis(summary);
  renderLatestLoad(summary);
  renderTables(summary);
  renderLoad(summary);
  renderBarChart(renderableAggregates(summary));
  renderTimeline(summary.recent_rows || []);
  $("#summaryTime").textContent = summary.generated_at || "-";
}

// Agregados para o grafico, respeitando o filtro de ruido.
function renderableAggregates(summary) {
  let aggregates = summary.aggregates || [];
  if (noiseHidden()) {
    aggregates = aggregates.filter(
      (item) => item.model_id !== "unknown" && (item.server === "python" || item.server === "cpp")
    );
  }
  return aggregates;
}

function render() {
  renderStatusPanels();
  renderSummaryPanels();
}

async function startRun() {
  if (state.busy) return;
  state.busy = true;
  $("#runButton").disabled = true;
  $("#runMessage").textContent = "Disparando teste...";
  const payload = {
    model: $("#modelSelect").value,
    count: Number($("#countInput").value),
    gain: $("#gainSelect").value,
  };
  try {
    const run = await api("/api/run", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#runMessage").textContent = `Teste iniciado: ${run.model}, ${run.count} amostra(s).`;
    await refreshAll();
  } catch (error) {
    $("#runMessage").textContent = `Falha ao iniciar: ${error.message}`;
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
  $("#runMessage").textContent = "Disparando saturacao...";
  const payload = {
    server: $("#loadServerSelect").value,
    model: $("#modelSelect").value,
    mode: "fixed",
    clients: Number($("#loadClientsInput").value),
    rate_per_minute: Number($("#loadRateInput").value),
    requests: Number($("#loadRequestsInput").value),
    gain: "none",
  };
  try {
    const run = await api("/api/load-run", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#runMessage").textContent = `Saturacao iniciada: ${run.server}, ${run.clients} clientes, ${run.rate_per_minute} req/min.`;
    await refreshAll();
  } catch (error) {
    $("#runMessage").textContent = `Falha ao iniciar saturacao: ${error.message}`;
  } finally {
    state.busy = false;
    $("#runButton").disabled = false;
    $("#loadButton").disabled = false;
  }
}

$("#refreshButton").addEventListener("click", refreshAll);
$("#runButton").addEventListener("click", startRun);
$("#loadButton").addEventListener("click", startLoadRun);
// Aplicar/retirar o filtro de ruido reaproveita os dados ja carregados.
$("#hideNoise").addEventListener("change", () => {
  renderSummaryPanels();
});

document.querySelectorAll(".server-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    controlServer(button.dataset.target, button.dataset.action);
  });
});

window.addEventListener("resize", () => {
  if (state.summary) {
    renderBarChart(renderableAggregates(state.summary));
    renderTimeline(state.summary.recent_rows || []);
  }
});

// Ciclo rapido: log ao vivo e imagens surgindo durante a execucao.
setInterval(() => {
  if ($("#autoRefresh").checked) refreshFast();
}, 2000);

// Ciclo lento: agregados, tabelas e graficos.
setInterval(() => {
  if ($("#autoRefresh").checked) refreshSlow();
}, 3500);

refreshAll();
