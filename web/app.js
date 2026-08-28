/* Video Factory — cliente da API local. Sem framework, sem build. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  config: null,
  projectId: null,
  pipeline: null,
  generations: [],
  selected: null,
  poll: null,
};

const api = {
  async request(method, url, body) {
    const options = { method, headers: {} };
    if (body instanceof FormData) options.body = body;
    else if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  },
  get: (url) => api.request("GET", url),
  post: (url, body) => api.request("POST", url, body),
  patch: (url, body) => api.request("PATCH", url, body),
  del: (url) => api.request("DELETE", url),
};

const escapeHtml = (value = "") =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fillSelect = (el, values, selected) => {
  if (!el) return;
  el.innerHTML = values.map((v) => `<option value="${v}">${v}</option>`).join("");
  if (selected) el.value = selected;
};

const mediaTag = (generation) =>
  (generation.mime_type || "").startsWith("image/")
    ? `<img src="/api/generations/${generation.id}/media" alt="${escapeHtml(generation.prompt.slice(0, 60))}" />`
    : `<video src="/api/generations/${generation.id}/media" controls playsinline></video>`;

/* ------------------------------------------------------------ navegacao */

function showTab(name) {
  $$(".nav-link").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  ["pipeline", "studio", "drafts", "library"].forEach((tab) => {
    $(`#tab-${tab}`).hidden = tab !== name;
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* -------------------------------------------------------------- projetos */

async function loadProjects(selectId) {
  const projects = await api.get("/api/projects");
  const select = $("#project-select");
  select.innerHTML = projects
    .map((p) => `<option value="${p.id}">${escapeHtml(p.name)} · ${p.generation_count}</option>`)
    .join("");
  state.projectId = selectId || projects[0].id;
  select.value = state.projectId;
  await refreshProject();
}

async function refreshProject() {
  const [{ generations, stats }, pipelines] = await Promise.all([
    api.get(`/api/projects/${state.projectId}/generations`),
    api.get(`/api/projects/${state.projectId}/pipelines`),
  ]);
  state.generations = generations;
  renderLibrary(stats);
  renderDrafts();
  renderStudioScene();
  if (pipelines.length && (!state.pipeline || state.pipeline.project_id !== state.projectId)) {
    await loadPipeline(pipelines[0].id);
  } else if (!pipelines.length) {
    state.pipeline = null;
    renderPipeline();
  }
}

/* -------------------------------------------------------- passo 1: contexto */

async function submitContext(event) {
  event.preventDefault();
  const button = $("#context-submit");
  const error = $("#context-error");
  error.textContent = "";
  button.disabled = true;
  button.textContent = "Escrevendo os cinco atos…";
  try {
    const form = new FormData(event.target);
    const payload = Object.fromEntries(form.entries());
    payload.duration_seconds = Number(payload.duration_seconds);
    const file = $("#reference-file").files[0];
    if (file) {
      const upload = new FormData();
      upload.append("file", file);
      upload.append("kind", "image");
      const asset = await api.post(`/api/projects/${state.projectId}/assets`, upload);
      payload.reference_asset_id = asset.id;
    }
    const pipeline = await api.post(`/api/projects/${state.projectId}/pipelines`, payload);
    state.pipeline = pipeline;
    renderPipeline();
    $("#step-2").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    error.textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = "Gerar storytelling →";
  }
}

async function loadPipeline(pipelineId) {
  state.pipeline = await api.get(`/api/pipelines/${pipelineId}`);
  renderPipeline();
}

/* ---------------------------------------------------------- render dos passos */

function renderPipeline() {
  renderConsole();
  renderStory();
  renderBoard();
  renderRender();
}

function renderConsole() {
  const pipeline = state.pipeline;
  const steps = [
    ["01", "contexto"],
    ["02", "story"],
    ["03", "board"],
    ["04", "render"],
  ];
  let reached = 0;
  if (pipeline) reached = pipeline.status === "draft" ? 3 : 4;
  const renders = pipeline?.renders || [];
  const completed = renders.filter((r) => r.status === "completed");
  const running = pipeline?.status === "rendering";

  $("#console-ref").textContent = pipeline ? `${pipeline.id.toUpperCase()} / ${pipeline.status.toUpperCase()}` : "—";
  $("#console-title").textContent = pipeline?.title || "Nenhum filme em produção";
  $("#console-metrics").innerHTML = [
    [String(completed.reduce((sum, r) => sum + r.duration_seconds, 0)).padStart(2, "0"), "segundos prontos"],
    [String(pipeline?.storyboard?.segments?.length || 0).padStart(2, "0"), "peças"],
    [String(pipeline?.story?.acts?.length || 0).padStart(2, "0"), "atos"],
  ]
    .map(([value, label]) => `<div><b>${value}</b><span>${label}</span></div>`)
    .join("");
  $("#console-steps").innerHTML = steps
    .map(([num, label], index) => {
      const done = index + 1 < reached || (index + 1 === reached && !running);
      const active = index + 1 === reached && running;
      return `<li class="${active ? "active" : done ? "done" : ""}">
        <span class="bead">${num}</span><small>${label}</small></li>`;
    })
    .join("");

  const warning = pipeline?.error || pipeline?.story?.warning || pipeline?.storyboard?.warning;
  const note = $("#console-note");
  note.hidden = !warning;
  if (warning) $("div", note).textContent = warning;

  $("#console-owner").textContent = `Provider ${state.config.provider}`;
  $("#console-rule").textContent = `Cena contínua ≤ ${state.config.max_cumulative_seconds}s`;
}

function renderStory() {
  const pipeline = state.pipeline;
  $("#story-empty").hidden = !!pipeline;
  $("#story-body").hidden = !pipeline;
  if (!pipeline) return;
  const story = pipeline.story;
  $("#story-head").innerHTML = `
    <h4>${escapeHtml(story.title || "")}</h4>
    <p>${escapeHtml(story.logline || "")}</p>
    <span class="pill">${story.source === "model" ? "gerado pelo modelo" : "template local"}</span>`;
  $("#acts").innerHTML = (story.acts || [])
    .map(
      (act, index) => `
      <div class="act" data-index="${index}">
        <div class="act-id">Ato ${act.n} · ${escapeHtml(act.timecode || "")}
          <b>${escapeHtml(act.name || "")}</b>${escapeHtml(act.beat || "")}</div>
        <div><span class="label">Locução (PT-BR)</span>
          <textarea rows="4" data-field="vo">${escapeHtml(act.vo || "")}</textarea></div>
        <div class="en"><span class="label">Ação e câmera (EN)</span>
          <textarea rows="4" data-field="action_camera">${escapeHtml(act.action_camera || "")}</textarea></div>
      </div>`
    )
    .join("");
  $("#story-note").textContent = story.direction_notes?.music || "";
}

function collectStory() {
  const story = structuredClone(state.pipeline.story);
  $$("#acts .act").forEach((node) => {
    const act = story.acts[Number(node.dataset.index)];
    $$("textarea", node).forEach((area) => {
      act[area.dataset.field] = area.value;
    });
  });
  return story;
}

function renderBoard() {
  const pipeline = state.pipeline;
  const board = pipeline?.storyboard;
  $("#board-empty").hidden = !!board?.segments?.length;
  $("#board-body").hidden = !board?.segments?.length;
  if (!board?.segments?.length) return;

  $("#board-meta").innerHTML = [
    ["Scene context", board.scene_context],
    ["Characters", board.characters],
    ["Format mode", board.format_mode],
    ["Lighting", board.lighting],
    ["Audio", board.audio],
  ]
    .map(([label, value]) => `<div class="box"><span>${label}</span><p>${escapeHtml(value || "")}</p></div>`)
    .join("");

  $("#segments").innerHTML = board.segments
    .map(
      (segment, index) => `
      <article class="segment" data-index="${index}">
        <header>
          <span class="tc">PEÇA ${String(segment.index).padStart(2, "0")} · ${escapeHtml(segment.timecode || "")}</span>
          <span class="mode">${escapeHtml(segment.mode || "")}</span>
          <span class="mode">${segment.duration_seconds}s</span>
          <span class="grow"></span>
          <span class="pill">atos ${(segment.acts || []).join(", ")}</span>
        </header>
        <div class="body">
          <div class="col">
            <div><span class="label" style="font:10px/1 var(--mono);letter-spacing:.14em;color:var(--ink-40);text-transform:uppercase">Locução</span>
              <textarea rows="3" data-field="vo">${escapeHtml(segment.vo || "")}</textarea></div>
            <div><span class="label" style="font:10px/1 var(--mono);letter-spacing:.14em;color:var(--ink-40);text-transform:uppercase">Primeiro frame (EN)</span>
              <textarea rows="3" data-field="first_frame">${escapeHtml(segment.first_frame || "")}</textarea></div>
            <div><span class="label" style="font:10px/1 var(--mono);letter-spacing:.14em;color:var(--ink-40);text-transform:uppercase">Ação e câmera (EN)</span>
              <textarea rows="5" data-field="shot_sequence">${escapeHtml(segment.shot_sequence || "")}</textarea></div>
            <div><span class="label" style="font:10px/1 var(--mono);letter-spacing:.14em;color:var(--ink-40);text-transform:uppercase">Continuidade (EN)</span>
              <textarea rows="3" data-field="continuity">${escapeHtml(segment.continuity || "")}</textarea></div>
          </div>
          <div class="col">
            <div class="prompt-block">${highlightPrompt(segment.prompt || "")}</div>
            <button class="btn-mini" data-copy="${index}">copiar prompt</button>
          </div>
        </div>
      </article>`
    )
    .join("");
  $("#board-note").textContent = `${board.segments.length} peças de ${state.config.segment_seconds}s · ${
    board.segments.length * state.config.segment_seconds
  }s no total`;
}

function highlightPrompt(prompt) {
  return escapeHtml(prompt).replace(/^([A-Z][A-Z &]+)$/gm, '<span class="k">$1</span>');
}

function collectBoard() {
  const board = structuredClone(state.pipeline.storyboard);
  $$("#segments .segment").forEach((node) => {
    const segment = board.segments[Number(node.dataset.index)];
    $$("textarea", node).forEach((area) => {
      segment[area.dataset.field] = area.value;
    });
    segment.prompt = "";
  });
  return board;
}

function renderRender() {
  const pipeline = state.pipeline;
  const renders = pipeline?.renders || [];
  $("#render-empty").hidden = !!pipeline;
  $("#render-body").hidden = !pipeline;
  if (!pipeline) return;

  $("#render-list").innerHTML = renders.length
    ? renders
        .map(
          (generation) => `
        <li data-id="${generation.id}" class="${state.selected === generation.id ? "active" : ""}">
          <span class="idx">${String(generation.segment_index).padStart(2, "0")}</span>
          <span class="txt">${escapeHtml(generation.label || generation.prompt.slice(0, 60))}</span>
          <span class="pill ${generation.status}">${generation.status}</span>
        </li>`
        )
        .join("")
    : `<li><span class="txt">Nenhuma peça renderizada.</span></li>`;

  const current = renders.find((r) => r.id === state.selected) || renders.find((r) => r.status === "completed");
  const player = $("#player");
  player.innerHTML = current?.status === "completed"
    ? mediaTag(current)
    : `<p class="empty">${pipeline.status === "rendering" ? "renderizando…" : "sem peça pronta"}</p>`;

  const busy = pipeline.status === "rendering";
  $("#render-start").disabled = busy;
  $("#render-start").textContent = busy ? "Renderizando…" : "Renderizar filme";
  $("#render-note").textContent = pipeline.error || (busy ? "Cada extensão começa quando a peça anterior termina." : "");
  $("#render-note").className = pipeline.error ? "note error" : "note";
}

/* ------------------------------------------------------------- biblioteca */

function clipCard(generation, extraActions = "") {
  const media = generation.status === "completed"
    ? mediaTag(generation)
    : `<span class="empty">${generation.status}</span>`;
  return `<article class="clip" data-id="${generation.id}">
    <div class="media">${media}</div>
    <div class="body">
      <div class="meta">
        <span class="pill ${generation.status}">${generation.status}</span>
        <span class="pill">${generation.mode}</span>
        <span class="pill">${generation.resolution}</span>
        <span class="pill">${generation.cumulative_seconds}s</span>
      </div>
      <p class="prompt">${escapeHtml(generation.label ? generation.label + " — " : "")}${escapeHtml(generation.prompt.slice(0, 220))}</p>
      ${generation.error ? `<p class="note error">${escapeHtml(generation.error)}</p>` : ""}
      <div class="actions">
        <button class="btn-mini" data-open="${generation.id}">abrir no studio</button>
        ${extraActions}
      </div>
    </div>
  </article>`;
}

function renderLibrary(stats) {
  $("#library-stats").textContent =
    `${stats.completed} prontos · ${stats.pending} na fila · ${stats.failed} com falha · ` +
    `${stats.seconds_generated}s gerados · ${stats.cost_units} unidades de custo`;
  $("#library").innerHTML = state.generations.length
    ? state.generations.map((g) => clipCard(g)).join("")
    : `<div class="empty-slot">Nenhum clipe neste projeto.</div>`;
}

function renderDrafts() {
  const drafts = state.generations.filter((g) => g.batch_id);
  $("#draft-grid").innerHTML = drafts
    .map((g) => clipCard(g, `<button class="btn-mini" data-promote="${g.id}">promover</button>`))
    .join("");
}

/* ---------------------------------------------------------------- studio */

const MEDIA_SLOTS = {
  text_to_video: [],
  image_to_video: [{ role: "first_frame", label: "frame inicial", kind: "image" }],
  interpolate: [
    { role: "first_frame", label: "primeiro frame", kind: "image" },
    { role: "last_frame", label: "último frame", kind: "image" },
  ],
  reference_to_video: [
    { role: "reference", label: "referência 1", kind: "image" },
    { role: "reference", label: "referência 2", kind: "image" },
    { role: "reference", label: "referência 3 (vídeo ≤ 3s)", kind: "video" },
  ],
};

function renderMediaSlots() {
  const slots = MEDIA_SLOTS[$("#mode").value] || [];
  const container = $("#media-slots");
  container.hidden = !slots.length;
  container.innerHTML = slots
    .map(
      (slot, index) => `<div class="slot">
        <span class="role">${slot.label}</span>
        <input type="file" data-role="${slot.role}" data-kind="${slot.kind}" data-index="${index}"
               accept="${slot.kind === "image" ? "image/*" : "video/*"}" />
      </div>`
    )
    .join("");
}

function updateCost() {
  const perSecond = state.config.cost_units_per_second[$("#resolution").value] || 0;
  const seconds = Number($("#duration").value || 0);
  $("#cost-estimate").textContent = `custo relativo ≈ ${(perSecond * seconds).toFixed(1)} unidades`;
}

async function submitComposer(event) {
  event.preventDefault();
  const error = $("#composer-error");
  error.textContent = "";
  try {
    const media = [];
    for (const input of $$("#media-slots input[type=file]")) {
      const file = input.files[0];
      if (!file) continue;
      const upload = new FormData();
      upload.append("file", file);
      upload.append("kind", input.dataset.kind);
      const asset = await api.post(`/api/projects/${state.projectId}/assets`, upload);
      media.push({ asset_id: asset.id, kind: input.dataset.kind, role: input.dataset.role });
    }
    const generation = await api.post(`/api/projects/${state.projectId}/generations`, {
      prompt: $("#prompt").value,
      mode: $("#mode").value,
      resolution: $("#resolution").value,
      aspect_ratio: $("#aspect").value,
      duration_seconds: Number($("#duration").value),
      media,
    });
    state.selected = generation.id;
    await refreshProject();
  } catch (err) {
    error.textContent = err.message;
  }
}

async function renderStudioScene() {
  const generation = state.generations.find((g) => g.id === state.selected);
  const player = $("#studio-player");
  if (!generation) {
    player.innerHTML = `<p class="empty">nenhum clipe selecionado</p>`;
    $("#scene-meta").innerHTML = "";
    $("#scene-actions").innerHTML = "";
    $("#chain").innerHTML = "";
    return;
  }
  player.innerHTML = generation.status === "completed"
    ? mediaTag(generation)
    : `<p class="empty">${generation.status}</p>`;
  $("#scene-meta").innerHTML = [
    generation.status,
    generation.mode,
    generation.resolution,
    generation.aspect_ratio,
    `${generation.duration_seconds}s clipe`,
    `${generation.cumulative_seconds}s cena`,
    `${generation.cost_units} un.`,
  ]
    .map((v) => `<span class="pill">${escapeHtml(v)}</span>`)
    .join("");

  const canExtend =
    generation.status === "completed" &&
    generation.cumulative_seconds + state.config.extension_seconds <= state.config.max_cumulative_seconds;
  $("#scene-actions").innerHTML = [
    canExtend ? `<button class="btn-mini" data-extend="${generation.id}">estender +${state.config.extension_seconds}s</button>` : "",
    generation.status === "completed" ? `<button class="btn-mini" data-upscale="1080p">upscale 1080p</button>` : "",
    generation.status === "completed" ? `<button class="btn-mini" data-upscale="4k">upscale 4k</button>` : "",
  ].join("");

  const chain = await api.get(`/api/generations/${generation.id}/chain`);
  $("#chain").innerHTML = chain
    .map(
      (item, index) => `<li data-id="${item.id}" class="${item.id === generation.id ? "active" : ""}">
        <span class="idx">${String(index + 1).padStart(2, "0")}</span>
        <span class="txt">${escapeHtml(item.label || item.prompt.slice(0, 70))}</span>
        <span class="pill ${item.status}">${item.status}</span>
      </li>`
    )
    .join("");
}

async function extendScene(parentId) {
  const prompt = window.prompt("O que acontece nos próximos 10 segundos?", "Continue the video.");
  if (prompt === null) return;
  const parent = state.generations.find((g) => g.id === parentId);
  const generation = await api.post(`/api/projects/${state.projectId}/generations`, {
    prompt,
    mode: "extend",
    parent_id: parentId,
    resolution: parent.resolution,
  });
  state.selected = generation.id;
  await refreshProject();
}

async function upscale(resolution) {
  const generation = await api.post(`/api/projects/${state.projectId}/generations`, {
    prompt: "",
    mode: "upscale",
    parent_id: state.selected,
    resolution,
  });
  state.selected = generation.id;
  await refreshProject();
}

/* ------------------------------------------------------------ draft room */

function addVariation(value = "") {
  const rows = $$("#draft-inputs .draft-row").length;
  const row = document.createElement("div");
  row.className = "draft-row";
  row.innerHTML = `<span class="n">${String(rows + 1).padStart(2, "0")}</span>
    <textarea rows="2" placeholder="varie uma coisa por vez: lente, luz, ritmo…">${escapeHtml(value)}</textarea>
    <button class="btn-mini" type="button" data-remove-variation>remover</button>`;
  $("#draft-inputs").append(row);
}

async function generateDrafts() {
  const error = $("#draft-error");
  error.textContent = "";
  try {
    const prompts = $$("#draft-inputs textarea").map((t) => t.value).filter(Boolean);
    await api.post(`/api/projects/${state.projectId}/draft-batches`, {
      prompts,
      aspect_ratio: $("#draft-aspect").value,
      duration_seconds: Number($("#draft-duration").value),
    });
    await refreshProject();
  } catch (err) {
    error.textContent = err.message;
  }
}

async function promoteDraft(id) {
  const draft = state.generations.find((g) => g.id === id);
  const resolution = window.prompt("Promover para qual resolução? (720p, 1080p, 4k)", "720p");
  if (!resolution) return;
  await api.post(`/api/projects/${state.projectId}/generations`, {
    prompt: draft.prompt,
    mode: "text_to_video",
    resolution,
    aspect_ratio: draft.aspect_ratio,
    duration_seconds: draft.duration_seconds,
  });
  await refreshProject();
}

/* ------------------------------------------------------------------ boot */

function bindEvents() {
  $$(".nav-link").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("#hero-start").addEventListener("click", () => $("#step-1").scrollIntoView({ behavior: "smooth" }));
  $("#project-select").addEventListener("change", (e) => loadProjects(e.target.value));
  $("#new-project").addEventListener("click", async () => {
    const name = window.prompt("Nome do projeto");
    if (!name) return;
    const project = await api.post("/api/projects", { name });
    state.pipeline = null;
    await loadProjects(project.id);
  });

  $("#context-form").addEventListener("submit", submitContext);

  $("#story-save").addEventListener("click", async () => {
    state.pipeline = await api.patch(`/api/pipelines/${state.pipeline.id}`, { story: collectStory() });
    renderPipeline();
    $("#story-note").textContent = "Roteiro salvo.";
  });
  $("#story-next").addEventListener("click", async () => {
    state.pipeline = await api.patch(`/api/pipelines/${state.pipeline.id}`, { story: collectStory() });
    state.pipeline = await api.post(`/api/pipelines/${state.pipeline.id}/prompts`);
    renderPipeline();
    $("#step-3").scrollIntoView({ behavior: "smooth" });
  });

  $("#board-recompile").addEventListener("click", async () => {
    await api.patch(`/api/pipelines/${state.pipeline.id}`, { storyboard: collectBoard() });
    state.pipeline = await api.post(`/api/pipelines/${state.pipeline.id}/prompts`);
    renderPipeline();
  });
  $("#board-next").addEventListener("click", () => $("#step-4").scrollIntoView({ behavior: "smooth" }));
  $("#segments").addEventListener("click", (event) => {
    const index = event.target.dataset.copy;
    if (index === undefined) return;
    navigator.clipboard?.writeText(state.pipeline.storyboard.segments[Number(index)].prompt || "");
    event.target.textContent = "copiado";
    setTimeout(() => (event.target.textContent = "copiar prompt"), 1500);
  });

  $("#render-start").addEventListener("click", async () => {
    try {
      await api.post(`/api/pipelines/${state.pipeline.id}/render`, { resolution: $("#render-resolution").value });
      await loadPipeline(state.pipeline.id);
    } catch (err) {
      $("#render-note").textContent = err.message;
      $("#render-note").className = "note error";
    }
  });
  $("#render-list").addEventListener("click", (event) => {
    const item = event.target.closest("li[data-id]");
    if (!item) return;
    state.selected = item.dataset.id;
    renderRender();
  });

  $("#mode").addEventListener("change", renderMediaSlots);
  $("#resolution").addEventListener("change", updateCost);
  $("#duration").addEventListener("input", updateCost);
  $("#composer").addEventListener("submit", submitComposer);
  $("#scene-actions").addEventListener("click", (event) => {
    if (event.target.dataset.extend) extendScene(event.target.dataset.extend);
    if (event.target.dataset.upscale) upscale(event.target.dataset.upscale);
  });
  $("#chain").addEventListener("click", (event) => {
    const item = event.target.closest("li[data-id]");
    if (!item) return;
    state.selected = item.dataset.id;
    renderStudioScene();
  });

  $("#add-variation").addEventListener("click", () => addVariation());
  $("#draft-generate").addEventListener("click", generateDrafts);
  $("#draft-inputs").addEventListener("click", (event) => {
    if (event.target.hasAttribute("data-remove-variation")) event.target.closest(".draft-row").remove();
  });

  document.body.addEventListener("click", (event) => {
    const openId = event.target.dataset?.open;
    const promoteId = event.target.dataset?.promote;
    if (openId) {
      state.selected = openId;
      showTab("studio");
      renderStudioScene();
    }
    if (promoteId) promoteDraft(promoteId);
  });
}

function startPolling() {
  clearInterval(state.poll);
  state.poll = setInterval(async () => {
    const busy =
      state.pipeline?.status === "rendering" ||
      state.generations.some((g) => g.status === "queued" || g.status === "running");
    if (!busy || document.hidden) return;
    if (state.pipeline) await loadPipeline(state.pipeline.id);
    await refreshProject();
  }, 3000);
}

async function boot() {
  state.config = await api.get("/api/config");
  const { resolutions, aspect_ratios, segment_seconds, max_cumulative_seconds } = state.config;

  fillSelect($("#resolution"), resolutions, "720p");
  fillSelect($("#aspect"), aspect_ratios);
  fillSelect($("#draft-aspect"), aspect_ratios);
  fillSelect($("#ctx-aspect"), aspect_ratios);
  fillSelect($("#ctx-resolution"), resolutions, "720p");
  fillSelect($("#render-resolution"), resolutions, "720p");
  const durations = [];
  for (let s = segment_seconds; s <= max_cumulative_seconds; s += segment_seconds) durations.push(s);
  fillSelect($("#ctx-duration"), durations, 30);

  $("#runtime-badge").textContent =
    `${state.config.model} · provider ${state.config.provider}` +
    (state.config.text_available ? ` · texto ${state.config.text_model}` : " · texto local");
  $("#foot-model").textContent = state.config.has_api_key ? "gemini api conectada" : "modo mock — sem api key";

  renderMediaSlots();
  updateCost();
  ["", "", ""].forEach(() => addVariation());
  bindEvents();
  await loadProjects();
  renderPipeline();
  startPolling();
}

boot().catch((err) => {
  document.body.insertAdjacentHTML(
    "afterbegin",
    `<p class="note error" style="padding:20px 40px">Falha ao iniciar: ${escapeHtml(err.message)}</p>`
  );
});
