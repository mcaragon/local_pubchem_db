/* PubChem Local web UI - plain JavaScript, no build step. */
(function () {
  "use strict";

  const view = document.getElementById("view");
  const qInput = document.getElementById("q");
  const sugList = document.getElementById("suggestions");
  const banner = document.getElementById("banner");
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

  const PROP_LABELS = {
    boiling_point: "Boiling point", melting_point: "Melting point", density: "Density",
    flash_point: "Flash point", autoignition_temperature: "Autoignition temp.", refractive_index: "Refractive index",
  };
  const COMPUTED_LABELS = {
    molecular_formula: "Molecular formula", molecular_weight: "Molecular weight (g/mol)", exact_mass: "Exact mass",
    monoisotopic_mass: "Monoisotopic mass", xlogp3: "XLogP3", tpsa: "TPSA (Å²)", complexity: "Complexity",
    hbond_donors: "H-bond donors", hbond_acceptors: "H-bond acceptors", rotatable_bonds: "Rotatable bonds",
    heavy_atoms: "Heavy atoms", formal_charge: "Formal charge", components: "Covalent units",
  };
  const ID_LABELS = {
    iupac_name: "IUPAC name", cas: "CAS", inchi: "InChI", inchikey: "InChIKey", smiles: "SMILES",
    connectivity_smiles: "Connectivity SMILES",
  };

  // ------------------------------------------------------------------ helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      err.status = res.status;
      throw err;
    }
    return res.json();
  }
  function fmtNum(n) { return n == null ? "–" : Number(n).toLocaleString(); }
  function fmtBytes(n) {
    if (n == null) return "–";
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return n.toFixed(i ? 1 : 0) + " " + u[i];
  }
  function fmtVal(v, unit) {
    if (v == null) return "–";
    return esc(v) + (unit ? " " + esc(unit) : "");
  }
  function svgUrl(cid, w, h) {
    return `/api/compound/${cid}/structure.svg?width=${w}&height=${h}&theme=light`;
  }
  function loading() { view.innerHTML = '<div class="spinner" role="status" aria-label="Loading"></div>'; }
  function copyBtn(text) { return `<button class="copy" data-copy="${esc(text)}" title="Copy">copy</button>`; }
  function setActiveNav(route) {
    document.getElementById("navSetup").classList.toggle("active", route === "setup");
  }

  view.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-copy]");
    if (btn) {
      navigator.clipboard.writeText(btn.dataset.copy).then(() => { btn.textContent = "copied"; setTimeout(() => (btn.textContent = "copy"), 1200); });
    }
  });

  // ------------------------------------------------------------------ routing
  function parseHash() {
    const h = location.hash.replace(/^#\/?/, "");
    const [path, query] = h.split("?");
    const params = new URLSearchParams(query || "");
    const parts = path.split("/").filter(Boolean);
    return { parts, params };
  }
  let pollTimer = null;
  async function route() {
    clearTimeout(pollTimer);
    hideSuggestions();
    const { parts, params } = parseHash();
    setActiveNav(parts[0] || "");
    window.scrollTo(0, 0);
    try {
      if (parts[0] === "compound" && parts[1]) return await showCompound(parseInt(parts[1], 10));
      if (parts[0] === "search") return await showSearch(params.get("q") || "");
      if (parts[0] === "setup") return await showSetup();
      return await showHome();
    } catch (err) {
      view.innerHTML = `<div class="card empty"><h2>Something went wrong</h2><p>${esc(err.message)}</p></div>`;
    }
  }
  window.addEventListener("hashchange", route);

  // ------------------------------------------------------------------ search + autocomplete
  document.getElementById("searchForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = qInput.value.trim();
    if (!q) return;
    const active = sugList.querySelector("li.active");
    if (active && !sugList.hidden) { location.hash = "#/compound/" + active.dataset.cid; }
    else { location.hash = "#/search?q=" + encodeURIComponent(q); }
    hideSuggestions();
    qInput.blur();
  });
  let sugTimer = null, sugSeq = 0;
  qInput.addEventListener("input", () => {
    clearTimeout(sugTimer);
    const q = qInput.value.trim();
    if (q.length < 2) return hideSuggestions();
    sugTimer = setTimeout(async () => {
      const seq = ++sugSeq;
      try {
        const data = await api("/api/autocomplete?limit=8&q=" + encodeURIComponent(q));
        if (seq !== sugSeq) return;
        if (!data.suggestions.length) return hideSuggestions();
        sugList.innerHTML = data.suggestions.map((s) =>
          `<li role="option" data-cid="${s.cid}"><span>${esc(s.name)}</span><span class="cid">CID ${s.cid}</span></li>`).join("");
        sugList.hidden = false;
      } catch (e) { hideSuggestions(); }
    }, 150);
  });
  qInput.addEventListener("keydown", (e) => {
    if (sugList.hidden) return;
    const items = [...sugList.querySelectorAll("li")];
    let i = items.findIndex((li) => li.classList.contains("active"));
    if (e.key === "ArrowDown") { i = Math.min(items.length - 1, i + 1); }
    else if (e.key === "ArrowUp") { i = Math.max(-1, i - 1); }
    else if (e.key === "Escape") { hideSuggestions(); return; }
    else return;
    e.preventDefault();
    items.forEach((li, j) => li.classList.toggle("active", j === i));
  });
  sugList.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li");
    if (!li) return;
    e.preventDefault();
    location.hash = "#/compound/" + li.dataset.cid;
    hideSuggestions();
  });
  qInput.addEventListener("blur", () => setTimeout(hideSuggestions, 150));
  function hideSuggestions() { sugList.hidden = true; sugList.innerHTML = ""; }

  // ------------------------------------------------------------------ views
  async function showHome() {
    qInput.value = "";
    const [health, build] = await Promise.all([api("/api/health"), api("/api/build/status")]);
    const any = Object.values(health.datasets).some(Boolean);
    const c = health.counts;
    const examples = ["acetone", "64-17-5", "benzaldehyde", "C7H8", "CC(=O)OC1=CC=CC=C1C(=O)O", "sodium borohydride", "2244"];
    view.innerHTML = `
      <section class="hero">
        <h1>Offline PubChem</h1>
        <p>Search compounds by name, CAS number, formula, SMILES, InChIKey or CID. Lab safety data, physical
           properties and structures are served entirely from your own server.</p>
        ${any ? `<div class="examples">${examples.map((e) => `<a href="#/search?q=${encodeURIComponent(e)}">${esc(e)}</a>`).join("")}</div>`
              : `<p><a class="btn primary" href="#/setup">Set up the databases</a></p>`}
      </section>
      <section class="stats">
        ${stat(c.compounds, "compounds (full PubChem)", health.datasets.compounds)}
        ${stat(c.synonyms, "searchable names & synonyms", health.datasets.names)}
        ${stat(c.lab_safety_records, "compounds with lab safety data", health.datasets.lcss)}
      </section>`;
    updateBanner(build);
  }
  function stat(n, label, available) {
    return `<div class="stat"><div class="num">${available ? fmtNum(n) : "–"}</div><div class="lbl">${esc(label)}${available ? "" : " · not built"}</div></div>`;
  }

  async function showSearch(q) {
    qInput.value = q;
    loading();
    const data = await api("/api/search?limit=50&q=" + encodeURIComponent(q));
    if (data.results.length === 1 && ["cid", "inchikey", "cas"].includes(data.results[0].match_type)) {
      location.replace("#/compound/" + data.results[0].cid);
      return;
    }
    if (!data.results.length) {
      view.innerHTML = `<div class="card empty"><h2>No results for “${esc(q)}”</h2>
        <p>Try another name, a CAS number (e.g. 67-64-1), a formula (C3H6O) or a SMILES string.</p></div>`;
      return;
    }
    const typeLabel = { cid: "CID", inchikey: "InChIKey", inchi: "InChI", name: "Name", cas: "CAS", formula: "Formula", smiles: "Structure", name_prefix: "Name starts with" };
    view.innerHTML = `<p class="muted small">${data.results.length} result${data.results.length > 1 ? "s" : ""} for “${esc(q)}”</p>
      <div class="results">${data.results.map((r) => `
        <a class="result" href="#/compound/${r.cid}">
          <div class="thumb"><img loading="lazy" alt="" src="${svgUrl(r.cid, 192, 144)}" onerror="this.remove()"></div>
          <div><div class="name">${esc(r.title || "CID " + r.cid)}</div>
            <div class="meta"><span>CID ${r.cid}</span>${r.molecular_formula ? `<span>${esc(r.molecular_formula)}</span>` : ""}
              ${r.molecular_weight ? `<span>${esc(r.molecular_weight)} g/mol</span>` : ""}${r.cas ? `<span>CAS ${esc(r.cas)}</span>` : ""}</div></div>
          <div class="tags"><span class="pill">${esc(typeLabel[r.match_type] || r.match_type)}${r.match_type === "name_prefix" ? ": " + esc(r.matched) : ""}</span>
            ${r.has_lab_data ? '<span class="pill lab">Lab data</span>' : ""}</div>
        </a>`).join("")}</div>`;
  }

  async function showCompound(cid) {
    loading();
    let c;
    try { c = await api("/api/compound/" + cid); }
    catch (err) {
      if (err.status === 404) { view.innerHTML = `<div class="card empty"><h2>CID ${cid} is not in the local database</h2><p><a href="https://pubchem.ncbi.nlm.nih.gov/compound/${cid}" target="_blank" rel="noopener">Open on pubchem.ncbi.nlm.nih.gov</a></p></div>`; return; }
      throw err;
    }
    document.title = (c.title || "CID " + cid) + " · PubChem Local";
    const ids = Object.entries(c.identifiers).filter(([, v]) => v);
    const comp = Object.entries(c.computed_properties).filter(([, v]) => v != null);
    const phys = Object.entries(c.physical_properties);
    const other = Object.entries(c.other_properties || {});
    const safety = c.safety;

    view.innerHTML = `
      <section class="compound-head card">
        <div class="structure"><img alt="Structure of ${esc(c.title)}" src="${svgUrl(cid, 300, 240)}" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'muted small',textContent:'No structure available'}))"></div>
        <div>
          <div class="row"><span class="pill">CID ${cid}</span>${c.has_lab_data ? '<span class="pill lab">Lab safety data</span>' : ""}
            ${safety && safety.signal_word ? `<span class="pill ${safety.signal_word === "Danger" ? "danger" : "warn"}">${esc(safety.signal_word)}</span>` : ""}</div>
          <h1>${esc(c.title || "CID " + cid)}</h1>
          <dl class="keyvals">
            ${c.computed_properties.molecular_formula ? `<dt>Formula</dt><dd>${formula(c.computed_properties.molecular_formula)}</dd>` : ""}
            ${c.computed_properties.molecular_weight ? `<dt>Molar mass</dt><dd>${esc(c.computed_properties.molecular_weight)} g/mol</dd>` : ""}
            ${ids.map(([k, v]) => `<dt>${esc(ID_LABELS[k] || k)}</dt><dd><span class="${k === "iupac_name" || k === "cas" ? "" : "mono"}">${esc(v)}</span>${copyBtn(v)}</dd>`).join("")}
          </dl>
          <p class="small" style="margin-top:12px"><a href="${esc(c.pubchem_url)}" target="_blank" rel="noopener">View on PubChem ↗</a> ·
            <a href="/api/compound/${cid}" target="_blank" rel="noopener">JSON</a> · <a href="/api/compound/${cid}/lab" target="_blank" rel="noopener">Lab summary JSON</a></p>
        </div>
      </section>

      <div class="grid2">
        <section class="card">
          <h2>Physical properties</h2>
          ${phys.length ? `<table class="props">${phys.map(([k, p]) => `
            <tr><th>${esc(PROP_LABELS[k] || k)}</th><td class="v">${p.value != null ? `${fmtVal(p.value, p.unit)}${p.n > 1 ? ` <span class="muted small">(median of ${p.n})</span>` : ""}` : '<span class="muted">see sources</span>'}
              <details class="sources"><summary>${p.sources.length} source value${p.sources.length > 1 ? "s" : ""}</summary><ul>
              ${p.sources.map((s) => `<li class="${s.standard ? "" : "nonstd"}">${esc(s.raw)}${s.source ? ` <span class="muted">— ${esc(s.source)}</span>` : ""}${s.standard ? "" : " <em>(not at standard conditions, excluded)</em>"}</li>`).join("")}
              </ul></details></td></tr>`).join("")}</table>`
            : `<p class="muted small">${c.has_lab_data ? "No parsed experimental values." : "No experimental data in the laboratory safety dataset for this compound."}</p>`}
          ${other.length ? `<h3>Other experimental data</h3><table class="props">${other.map(([k, vals]) => `
            <tr><th>${esc(k)}</th><td>${esc(vals[0].value)}${vals.length > 1 ? `<details class="sources"><summary>${vals.length - 1} more</summary><ul>${vals.slice(1).map((s) => `<li>${esc(s.value)}${s.source ? ` <span class="muted">— ${esc(s.source)}</span>` : ""}</li>`).join("")}</ul></details>` : ""}</td></tr>`).join("")}</table>` : ""}
        </section>

        <section class="card">
          <h2>Safety (GHS)</h2>
          ${safety ? `
            ${safety.signal_word ? `<div class="signal ${esc(safety.signal_word)}">${esc(safety.signal_word)}</div>` : ""}
            ${safety.pictograms.length ? `<div class="ghs">${safety.pictograms.map((p) => `<figure><img src="/static/ghs/${esc(p.code)}.svg" alt="${esc(p.name)}"><figcaption>${esc(p.name)}</figcaption></figure>`).join("")}</div>` : ""}
            ${safety.hazard_statements.length ? `<h3>Hazard statements</h3><ul class="statements">${safety.hazard_statements.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : '<p class="muted small">No GHS hazard statements reported.</p>'}
            ${safety.precautionary_statement_codes ? `<h3>Precautionary statements</h3><p class="small">${esc(safety.precautionary_statement_codes)}</p>` : ""}
            ${safety.nfpa.length ? `<h3>NFPA 704</h3><p class="small">${safety.nfpa.map((n) => `${esc(n.name || "")} ${esc(n.value)}`).join(" · ")}</p>` : ""}`
            : '<p class="muted small">No laboratory chemical safety summary is available for this compound.</p>'}
        </section>
      </div>

      ${comp.length ? `<section class="card"><h2>Computed properties</h2><table class="props">${comp.map(([k, v]) => `<tr><th>${esc(COMPUTED_LABELS[k] || k)}</th><td>${esc(v)}</td></tr>`).join("")}</table></section>` : ""}

      ${c.has_lab_data ? `<section class="card lcss" id="lcss"><h2>Laboratory Chemical Safety Summary</h2><div class="muted small" id="lcssBody">Loading…</div></section>` : ""}

      ${c.synonyms.length ? `<section class="card"><h2>Synonyms</h2><div class="synonyms">${c.synonyms.map((s) => `<span>${esc(s)}</span>`).join("")}</div></section>` : ""}`;

    if (c.has_lab_data) loadLcss(cid);
  }

  // Merge identical values reported by several sources into one entry
  function dedupe(entries) {
    const map = new Map();
    for (const e of entries) {
      const key = (e.name || "") + "\u0000" + e.value.trim().toLowerCase();
      if (!map.has(key)) map.set(key, { name: e.name, value: e.value, sources: [] });
      const item = map.get(key);
      if (e.source && !item.sources.includes(e.source)) item.sources.push(e.source);
    }
    return [...map.values()];
  }

  function formula(f) { return esc(f).replace(/(\d+)/g, "<sub>$1</sub>"); }

  async function loadLcss(cid) {
    const body = document.getElementById("lcssBody");
    try {
      const data = await api(`/api/compound/${cid}/safety-summary`);
      const skip = new Set(["2D Structure", "3D Conformer"]);
      body.className = "";
      body.innerHTML = data.sections.filter((s) => !skip.has(s.heading)).map((top) => `
        <details><summary>${esc(top.heading)}</summary>
          ${top.sections.map((sub) => { const entries = dedupe(sub.entries); return `<div class="sub">${sub.heading !== top.heading ? `<h4>${esc(sub.heading)}</h4>` : ""}
            ${entries.slice(0, 40).map((e) => `<div class="entry">${e.name ? `<strong>${esc(e.name)}:</strong> ` : ""}${esc(e.value)}${e.sources.length ? ` <span class="src">— ${esc(e.sources.slice(0, 3).join("; "))}${e.sources.length > 3 ? ` +${e.sources.length - 3} more` : ""}</span>` : ""}</div>`).join("")}
            ${entries.length > 40 ? `<div class="muted small">… ${entries.length - 40} more</div>` : ""}</div>`; }).join("")}
        </details>`).join("");
    } catch (e) { body.textContent = "Could not load the safety summary: " + e.message; }
  }

  // ------------------------------------------------------------------ setup / build
  const DATASET_INFO = {
    lcss: { title: "Lab safety & experimental data", desc: "PubChem's Laboratory Chemical Safety Summaries: boiling/melting point, density, flash point, GHS hazards, handling, first aid, disposal. Download ≈ 0.5 GB, builds in minutes. Most useful for lab work — start here." },
    names: { title: "Names & synonyms", desc: "Titles and synonyms for all compounds; enables name, trade name and CAS search across all of PubChem. Download ≈ 3 GB, ≈ 22 GB on disk, builds in under an hour on most systems." },
    compounds: { title: "All compounds (full PubChem)", desc: "Structures and computed properties (formula, mass, SMILES, InChI, XLogP, TPSA…) of ~120 million compounds. Download ≈ 110 GB (deleted as it is imported), database ≈ 70–100 GB plus ≈ 15 GB of temporary space while it builds; takes many hours. Resumable." },
  };

  async function showSetup() {
    qInput.value = "";
    const st = await api("/api/build/status");
    renderSetup(st);
  }

  function renderSetup(st) {
    const b = st.build || {};
    const running = b.state === "running";
    const p = b.progress;
    let pct = null;
    if (p && p.total) pct = Math.min(100, (100 * p.done) / p.total);
    const progressText = p ? (p.unit === "bytes" ? `${fmtBytes(p.done)}${p.total ? " / " + fmtBytes(p.total) : ""}` : `${fmtNum(p.done)}${p.total ? " / " + fmtNum(p.total) : ""} ${esc(p.unit)}`) : "";
    view.innerHTML = `
      <section class="card">
        <h2>Databases</h2>
        <div class="datasets">${st.datasets.map((d) => {
          const info = DATASET_INFO[d.name];
          const meta = d.meta || {};
          return `<div class="dataset">
            <label><input type="checkbox" name="ds" value="${d.name}" ${d.built ? "" : "checked"} ${running ? "disabled" : ""}> ${esc(info.title)}</label>
            <p>${esc(info.desc)}</p>
            <div class="row small">${d.built ? `<span class="pill ok">Built ${esc((meta.built_at || "").slice(0, 10))}</span><span class="muted">${fmtBytes(d.size_bytes)}</span>` : '<span class="pill">Not built</span>'}
              ${d.building ? '<span class="pill warn">Partial build present</span>' : ""}</div>
          </div>`; }).join("")}</div>
        <div class="row" style="margin-top:14px">
          ${st.admin_token_required ? `<input id="adminToken" type="password" placeholder="Admin token" style="padding:7px 10px;border-radius:8px;border:1px solid var(--border);background:var(--surface-2);color:var(--text)">` : ""}
          <button class="btn primary" id="startBuild" ${running ? "disabled" : ""}>Download &amp; build selected</button>
          ${running ? '<button class="btn danger" id="cancelBuild">Cancel build</button>' : ""}
        </div>
        <p class="muted small">Rebuilding a dataset keeps the current version online until the new one is complete. PubChem publishes updates weekly.</p>
      </section>
      <section class="card">
        <h2>Build status</h2>
        <div class="row"><strong>${esc(stateLabel(b.state))}</strong>
          ${b.current ? `<span class="muted">· ${esc(b.current.dataset)}: ${esc(b.current.message)}</span>` : ""}</div>
        ${running ? `<div class="progress ${pct == null ? "indeterminate" : ""}" style="margin:10px 0 4px"><div style="width:${pct == null ? 30 : pct}%"></div></div>
          <div class="muted small">${progressText}${p && p.detail ? " · " + esc(p.detail) : ""}</div>` : ""}
        ${b.error ? `<p style="color:var(--danger)">${esc(b.error)}</p>` : ""}
        ${b.log && b.log.length ? `<h3>Log</h3><div class="log" id="buildLog">${esc(b.log.slice(-80).join("\n"))}</div>` : ""}
      </section>`;
    const logEl = document.getElementById("buildLog");
    if (logEl) logEl.scrollTop = logEl.scrollHeight;

    const start = document.getElementById("startBuild");
    start && start.addEventListener("click", async () => {
      const datasets = [...view.querySelectorAll("input[name=ds]:checked")].map((i) => i.value);
      if (!datasets.length) return alert("Select at least one dataset.");
      if (datasets.includes("compounds") && !confirm("The full compound database needs about 110 GB of downloads and 70–100 GB for the database (plus ≈ 15 GB temporary space), and takes many hours to build. Continue?")) return;
      try {
        await api("/api/build/start", { method: "POST", headers: authHeaders(), body: JSON.stringify({ datasets }) });
        showSetup();
      } catch (e) { alert("Could not start the build: " + e.message); }
    });
    const cancel = document.getElementById("cancelBuild");
    cancel && cancel.addEventListener("click", async () => {
      if (!confirm("Cancel the running build? Downloads and imported compound files are kept and the build can be resumed.")) return;
      try { await api("/api/build/cancel", { method: "POST", headers: authHeaders() }); } catch (e) { alert(e.message); }
      showSetup();
    });
    updateBanner(st);
    if (running) pollTimer = setTimeout(async () => { if (parseHash().parts[0] === "setup") renderSetup(await api("/api/build/status")); }, 2000);
  }

  function authHeaders() {
    const h = { "Content-Type": "application/json" };
    const t = document.getElementById("adminToken");
    if (t && t.value) h["X-Admin-Token"] = t.value;
    return h;
  }
  function stateLabel(s) {
    return { running: "Building…", done: "Last build finished", failed: "Last build failed", cancelled: "Last build cancelled", interrupted: "Last build was interrupted", idle: "Idle" }[s || "idle"] || s;
  }
  function updateBanner(st) {
    const b = st.build || {};
    const built = (st.datasets || []).some((d) => d.built);
    if (b.state === "running") {
      banner.innerHTML = `Database build in progress (${esc(b.current ? b.current.dataset : "")}). <a href="#/setup">View progress</a>`;
      banner.hidden = false;
    } else if (!built && st.datasets) {
      banner.innerHTML = `No databases have been built yet. <a href="#/setup">Set them up</a>`;
      banner.hidden = false;
    } else {
      banner.hidden = true;
    }
  }

  route();
})();
