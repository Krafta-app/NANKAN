const state = {
  config: null,
  currentDate: "",
  currentPlace: "",
  currentRaceKey: "",
  races: [],
  dates: [],
  places: [],
  raceDetail: null,
  stats: null,
  activeTab: "prediction",
  demo: new URLSearchParams(location.search).has("demo"),
};

const els = {};
const PIN_KEY = "nankan_site_pin";
let predictionControlsAbort = null;

document.addEventListener("DOMContentLoaded", () => {
  cacheElements();
  bindEvents();
  void boot();
});

function cacheElements() {
  for (const id of [
    "statusLine",
    "pinBox",
    "pinInput",
    "refreshButton",
    "dateSelect",
    "placeSelect",
    "metricStrip",
    "raceRail",
    "raceSummary",
    "panelPrediction",
    "panelResult",
    "panelMemo",
    "panelStats",
  ]) {
    els[id] = document.getElementById(id);
  }
}

function bindEvents() {
  els.refreshButton.addEventListener("click", () => void refreshAll());
  els.dateSelect.addEventListener("change", () => {
    state.currentDate = els.dateSelect.value;
    state.currentRaceKey = "";
    void loadRaces();
  });
  els.placeSelect.addEventListener("change", () => {
    state.currentPlace = els.placeSelect.value;
    state.currentRaceKey = "";
    void loadRaces();
  });
  els.pinInput.value = localStorage.getItem(PIN_KEY) || "";
  els.pinInput.addEventListener("input", () => {
    localStorage.setItem(PIN_KEY, els.pinInput.value.trim());
  });

  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      showTab(button.dataset.tab);
    });
  });
}

async function boot() {
  setStatus("設定確認中");
  await loadConfig();
  renderPinState();

  if (!state.config?.configured && !state.demo) {
    renderSetupState();
    return;
  }

  await loadRaces();
}

async function refreshAll() {
  state.stats = null;
  await loadRaces(true);
  if (state.activeTab === "stats") await loadStats();
}

async function loadConfig() {
  if (state.demo) {
    state.config = demoConfig();
    return;
  }
  try {
    state.config = await apiGet("/api/config");
  } catch (err) {
    state.config = { configured: false, memoEnabled: false, memoAuthRequired: false };
    showToast(`設定確認に失敗しました: ${err.message}`);
  }
}

async function loadRaces(preserveRace = false) {
  setStatus("レース読込中");
  renderLoading(els.raceRail, "レースを読み込み中");

  try {
    let data;
    if (state.demo) {
      data = demoRaces(state.currentDate, state.currentPlace);
    } else {
      const params = new URLSearchParams();
      if (state.currentDate) params.set("date", state.currentDate);
      if (state.currentPlace) params.set("place_code", state.currentPlace);
      data = await apiGet(`/api/races?${params.toString()}`);
    }

    state.dates = data.dates || [];
    if (!state.currentDate && data.latestDate) {
      state.currentDate = data.latestDate;
      return loadRaces(preserveRace);
    }

    state.races = data.races || [];
    state.places = data.places || [];
    renderFilters();
    renderMetrics();
    renderRaceRail();

    if (!preserveRace || !state.races.some((race) => race.race_key === state.currentRaceKey)) {
      state.currentRaceKey = state.races[0]?.race_key || "";
    }
    if (state.currentRaceKey) {
      await loadRace(state.currentRaceKey);
    } else {
      state.raceDetail = null;
      renderAllPanels();
      setStatus("対象レースなし");
    }
  } catch (err) {
    setStatus("読込エラー");
    renderEmpty(els.raceRail, "レース一覧を取得できません", err.message);
  }
}

async function loadRace(raceKey) {
  state.currentRaceKey = raceKey;
  renderRaceRail();
  setStatus("レース詳細読込中");
  renderLoading(els.panelPrediction, "予想を読み込み中");

  try {
    state.raceDetail = state.demo ? demoRace(raceKey) : await apiGet(`/api/race?race_key=${encodeURIComponent(raceKey)}`);
    renderMetrics();
    renderAllPanels();
    setStatus(summaryLine());
  } catch (err) {
    state.raceDetail = null;
    renderAllPanels();
    setStatus("読込エラー");
    renderEmpty(els.panelPrediction, "レース詳細を取得できません", err.message);
  }
}

async function loadStats() {
  if (!state.config?.configured && !state.demo) return;
  renderLoading(els.panelStats, "成績を読み込み中");
  try {
    if (state.demo) {
      state.stats = demoStats();
    } else {
      const params = new URLSearchParams();
      if (state.currentDate) params.set("date", state.currentDate);
      if (state.currentPlace) params.set("place_code", state.currentPlace);
      state.stats = await apiGet(`/api/stats?${params.toString()}`);
    }
    renderStats();
  } catch (err) {
    renderEmpty(els.panelStats, "成績を取得できません", err.message);
  }
}

function renderFilters() {
  fillSelect(
    els.dateSelect,
    state.dates.map((date) => ({ value: date, label: formatDate(date) })),
    state.currentDate,
  );

  const allPlaces = Object.entries(state.config?.placeByCode || {
    "10": "大井",
    "11": "川崎",
    "12": "船橋",
    "13": "浦和",
  }).map(([value, label]) => ({ value, label }));
  fillSelect(els.placeSelect, [{ value: "", label: "すべて" }, ...allPlaces], state.currentPlace);
}

function renderMetrics() {
  const resultCount = state.races.filter((race) => race.has_result).length;
  const horses = state.raceDetail?.horses || [];
  const memoCount = horses.filter((horse) => state.raceDetail?.notes?.[horse.uma_id]?.note_text).length;
  const items = [
    ["レース", state.races.length],
    ["結果済", resultCount],
    ["出走馬", horses.length || "-"],
    ["メモ", memoCount || 0],
  ];
  els.metricStrip.replaceChildren(
    ...items.map(([label, value]) => {
      const box = document.createElement("div");
      box.className = "metric";
      box.innerHTML = `<b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span>`;
      return box;
    }),
  );
}

function renderRaceRail() {
  if (!state.races.length) {
    renderEmpty(els.raceRail, "該当レースなし", "Macで予想生成後、Supabaseへ同期されると表示されます。");
    return;
  }

  els.raceRail.replaceChildren(
    ...state.races.map((race) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `race-button${race.race_key === state.currentRaceKey ? " active" : ""}`;
      button.innerHTML = `<strong>${race.race_num}R</strong><span>${race.has_result ? "結果済" : "予想"}</span>`;
      button.addEventListener("click", () => void loadRace(race.race_key));
      return button;
    }),
  );
}

function renderAllPanels() {
  renderSummary();
  renderPrediction();
  renderResult();
  renderMemo();
  if (state.activeTab === "stats") void loadStats();
}

function renderSummary() {
  const detail = state.raceDetail;
  if (!detail?.race) {
    renderEmpty(els.raceSummary, "レース未選択", "日付と競馬場を選んでください。");
    return;
  }
  const race = detail.race;
  const horses = detail.horses || [];
  const tierCounts = horses.reduce((acc, horse) => {
    const tier = horse.tier || "不明";
    acc[tier] = (acc[tier] || 0) + 1;
    return acc;
  }, {});

  els.raceSummary.replaceChildren();
  const wrapper = document.createElement("div");
  wrapper.innerHTML = `
    <div class="summary-kicker">${escapeHtml(formatDate(race.date))} ${escapeHtml(race.place_name || "")}</div>
    <div class="summary-title">${escapeHtml(race.race_num)}R ${escapeHtml(race.race_name || "")}</div>
    <div class="summary-meta">
      <div><span>距離</span><b>${escapeHtml(race.course || (race.dist ? `${race.dist}m` : "-"))}</b></div>
      <div><span>状態</span><b>${race.has_result ? "結果取得済" : "予想保存済"}</b></div>
      <div><span>生成</span><b>${escapeHtml(shortDateTime(race.generated_at) || "-")}</b></div>
    </div>
    <div class="tier-cloud"></div>
  `;
  const cloud = wrapper.querySelector(".tier-cloud");
  for (const [tier, count] of Object.entries(tierCounts)) {
    const badge = document.createElement("span");
    badge.className = `badge${["S", "A", "主力", "一軍"].includes(tier) ? " good" : ""}`;
    badge.textContent = `${tier} ${count}`;
    cloud.appendChild(badge);
  }
  els.raceSummary.appendChild(wrapper);
}

function renderPrediction() {
  const detail = state.raceDetail;
  if (!detail?.race) {
    renderEmpty(els.panelPrediction, "予想なし", "レースを選んでください。");
    return;
  }
  const html = detail.page?.data_html || "";
  const text = detail.page?.data_text || "";
  els.panelPrediction.replaceChildren();

  if (html) {
    const wrap = document.createElement("div");
    wrap.className = "prediction-viewer";

    const toolbar = document.createElement("div");
    toolbar.className = "prediction-toolbar";
    toolbar.innerHTML = `
      <div>
        <strong>${escapeHtml(detail.race.place_name || "")}${escapeHtml(detail.race.race_num)}R 予想</strong>
        <span>${escapeHtml(detail.race.race_name || "")}</span>
      </div>
      <div class="prediction-actions"></div>
    `;

    const actions = toolbar.querySelector(".prediction-actions");
    const focusButton = makeToolButton("集中", "予想だけ大きく見る");
    const openButton = makeToolButton("別窓", "別ウィンドウで開く");
    const downloadButton = makeToolButton("HTML保存", "HTMLを保存する");
    actions.append(focusButton, openButton, downloadButton);

    const inline = document.createElement("div");
    inline.className = "prediction-inline";
    renderInlinePredictionHtml(inline, html, detail.race);

    focusButton.addEventListener("click", () => {
      wrap.classList.toggle("is-focus");
      document.body.classList.toggle("prediction-focus-open", wrap.classList.contains("is-focus"));
      focusButton.textContent = wrap.classList.contains("is-focus") ? "戻る" : "集中";
    });
    openButton.addEventListener("click", () => openPredictionHtml(html));
    downloadButton.addEventListener("click", () => downloadPredictionHtml(detail.race, html));

    wrap.append(toolbar, inline);
    els.panelPrediction.appendChild(wrap);
  } else if (text) {
    const pre = document.createElement("pre");
    pre.className = "text-fallback";
    pre.textContent = text;
    els.panelPrediction.appendChild(pre);
  } else {
    renderEmpty(els.panelPrediction, "予想HTMLなし", "race_pages に data_html が保存されていません。");
  }
}

function renderInlinePredictionHtml(target, html, race) {
  if (predictionControlsAbort) predictionControlsAbort.abort();
  predictionControlsAbort = new AbortController();

  const doc = new DOMParser().parseFromString(html, "text/html");
  const scriptText = [...doc.querySelectorAll("script")].map((script) => script.textContent || "").join("\n");
  const markPrefix = parseScriptValue(scriptText, /MARK_PREFIX\s*=\s*'([^']*)'/) || "";
  const singleRaceNum = parseScriptValue(scriptText, /keiba_mark_([^'"+]+)_/) || race?.race_num || "";

  target.replaceChildren();
  for (const node of [...doc.body.childNodes]) {
    if (node.nodeName.toLowerCase() === "script" || node.nodeName.toLowerCase() === "style") continue;
    target.appendChild(document.importNode(node, true));
  }
  target.querySelectorAll("style,script").forEach((node) => node.remove());
  target.querySelectorAll("[onclick]").forEach((node) => {
    node.dataset.originalOnclick = node.getAttribute("onclick") || "";
    node.removeAttribute("onclick");
  });
  target.querySelectorAll("a[href]").forEach((link) => {
    link.target = "_blank";
    link.rel = "noopener";
  });

  bindInlinePredictionControls(target, {
    markPrefix,
    singleRaceNum: String(singleRaceNum),
    raceNum: String(race?.race_num || singleRaceNum || ""),
    signal: predictionControlsAbort.signal,
  });
}

function bindInlinePredictionControls(root, context) {
  root.querySelectorAll(".tab-button").forEach((button) => {
    const tabName = inlineCallArg(button, /openTab\([^,]+,\s*'([^']+)'/) || tabIdFromButton(button);
    if (!tabName) return;
    button.addEventListener("click", (event) => openInlineTab(root, event.currentTarget, tabName, context.raceNum), {
      signal: context.signal,
    });
  });

  root.querySelectorAll(".race-tab").forEach((button) => {
    const raceId = inlineCallArg(button, /openRace\([^,]+,\s*'([^']+)'/) || button.id?.replace(/^btn-/, "");
    if (!raceId) return;
    button.addEventListener("click", (event) => openInlineRace(root, event.currentTarget, raceId), {
      signal: context.signal,
    });
  });

  root.querySelectorAll(".inner-tab").forEach((button) => {
    const tabId = inlineCallArg(button, /openInnerTab\([^,]+,\s*'([^']+)'/) || tabIdFromButton(button);
    if (!tabId) return;
    button.addEventListener("click", (event) => openInlineInnerTab(event.currentTarget, tabId), {
      signal: context.signal,
    });
  });

  root.querySelectorAll(".mark-btn").forEach((button) => {
    button.addEventListener(
      "click",
      (event) => {
        event.stopPropagation();
        openInlineMarkMenu(root, event.currentTarget);
      },
      { signal: context.signal },
    );
  });

  root.querySelectorAll("#mark-menu .menu-item").forEach((item) => {
    const mark = item.textContent.trim() === "--" ? "" : item.textContent.trim().replace("✕", "");
    item.addEventListener(
      "click",
      (event) => {
        event.stopPropagation();
        selectInlineMark(root, mark, context);
      },
      { signal: context.signal },
    );
  });

  root.addEventListener("click", () => closeInlineMarkMenu(root), { signal: context.signal });
  restoreInlineTabs(root, context);
  syncInlineMarks(root, context);
}

function openInlineTab(root, button, tabName, raceNum) {
  root.querySelectorAll(".tab-content").forEach((content) => content.classList.remove("active"));
  root.querySelectorAll(".tab-button").forEach((tabButton) => tabButton.classList.remove("active"));
  root.querySelector(`#${cssEscape(tabName)}`)?.classList.add("active");
  button.classList.add("active");
  if (raceNum) localStorage.setItem(`keiba_active_tab_${raceNum}`, tabName);
}

function openInlineRace(root, button, raceId) {
  root.querySelectorAll(".race-content").forEach((content) => content.classList.remove("active"));
  root.querySelectorAll(".race-tab").forEach((tabButton) => tabButton.classList.remove("active"));
  root.querySelector(`#${cssEscape(raceId)}`)?.classList.add("active");
  button.classList.add("active");
  localStorage.setItem("keiba_combined_race", raceId);
}

function openInlineInnerTab(button, tabId) {
  const parent = button.closest(".race-content");
  if (!parent) return;
  parent.querySelectorAll(".inner-content").forEach((content) => content.classList.remove("active"));
  parent.querySelectorAll(".inner-tab").forEach((tabButton) => tabButton.classList.remove("active"));
  parent.querySelector(`#${cssEscape(tabId)}`)?.classList.add("active");
  button.classList.add("active");
  localStorage.setItem(`keiba_inner_${parent.id}`, tabId);
}

function openInlineMarkMenu(root, button) {
  const menu = root.querySelector("#mark-menu");
  if (!menu) return;
  root.querySelectorAll(".mark-btn.current").forEach((item) => item.classList.remove("current"));
  button.classList.add("current");
  const rect = button.getBoundingClientRect();
  const maxLeft = Math.max(8, window.innerWidth - 292);
  menu.style.display = "flex";
  menu.style.top = `${rect.bottom + 6}px`;
  menu.style.left = `${Math.max(8, Math.min(maxLeft, rect.left))}px`;
}

function selectInlineMark(root, mark, context) {
  const button = root.querySelector(".mark-btn.current");
  if (!button) return;
  const raceNum = button.dataset.race || context.singleRaceNum || context.raceNum;
  const uma = button.dataset.uma;
  if (!raceNum || !uma) return;
  const key = `${context.markPrefix}keiba_mark_${raceNum}_${uma}`;
  if (mark) localStorage.setItem(key, mark);
  else localStorage.removeItem(key);
  syncInlineMarks(root, context);
  closeInlineMarkMenu(root);
}

function closeInlineMarkMenu(root) {
  root.querySelectorAll(".mark-btn.current").forEach((item) => item.classList.remove("current"));
  const menu = root.querySelector("#mark-menu");
  if (menu) menu.style.display = "none";
}

function syncInlineMarks(root, context) {
  root.querySelectorAll(".mark-btn").forEach((button) => {
    const raceNum = button.dataset.race || context.singleRaceNum || context.raceNum;
    const uma = button.dataset.uma;
    const saved = raceNum && uma ? localStorage.getItem(`${context.markPrefix}keiba_mark_${raceNum}_${uma}`) : "";
    button.dataset.mark = saved || "";
    button.textContent = saved || "";
  });
}

function restoreInlineTabs(root, context) {
  const savedTab = context.raceNum ? localStorage.getItem(`keiba_active_tab_${context.raceNum}`) : "";
  const tabButton = savedTab ? findInlineButton(root, ".tab-button", savedTab) : root.querySelector(".tab-button.active");
  if (tabButton) tabButton.click();

  const savedRace = localStorage.getItem("keiba_combined_race");
  const raceButton = savedRace ? findInlineButton(root, ".race-tab", savedRace) : root.querySelector(".race-tab.active, .race-tab");
  if (raceButton) raceButton.click();

  root.querySelectorAll(".race-content").forEach((raceContent) => {
    const savedInner = localStorage.getItem(`keiba_inner_${raceContent.id}`);
    const innerButton = savedInner
      ? findInlineButton(raceContent, ".inner-tab", savedInner)
      : raceContent.querySelector(".inner-tab.active, .inner-tab");
    if (innerButton) innerButton.click();
  });
}

function findInlineButton(root, selector, targetId) {
  return [...root.querySelectorAll(selector)].find((button) => button.dataset.targetId === targetId || buttonMatchesArg(button, targetId));
}

function tabIdFromButton(button) {
  return button.dataset.targetId || "";
}

function buttonMatchesArg(button, targetId) {
  const text = button.getAttribute("data-original-onclick") || button.outerHTML;
  return text.includes(`'${targetId}'`);
}

function inlineCallArg(node, pattern) {
  const source = node.dataset.originalOnclick || node.getAttribute("onclick") || "";
  const value = parseScriptValue(source, pattern);
  if (value) node.dataset.targetId = value;
  node.dataset.originalOnclick = source;
  return value;
}

function parseScriptValue(text, pattern) {
  return String(text || "").match(pattern)?.[1] || "";
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function makeToolButton(label, title) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tool-button";
  button.textContent = label;
  button.title = title;
  return button;
}

function renderResult() {
  const detail = state.raceDetail;
  if (!detail?.race) {
    renderEmpty(els.panelResult, "結果なし", "レースを選んでください。");
    return;
  }
  if (!detail.race.has_result) {
    renderEmpty(els.panelResult, "結果待ち", "レース後に結果取得が完了すると表示されます。");
    return;
  }

  const table = document.createElement("div");
  table.className = "table-wrap";
  table.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>着</th><th>馬番</th><th>馬名</th><th>予想</th><th>人気</th><th>着差</th><th>判定</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  `;
  const tbody = table.querySelector("tbody");
  for (const horse of detail.horses || []) {
    const hit = isTopHit(horse) ? "的中" : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(horse.finish_rank || "-")}</td>
      <td>${escapeHtml(horse.umaban || "-")}</td>
      <td><span class="horse-name">${escapeHtml(horse.name || "")}</span></td>
      <td>${tierBadge(horse.tier)}</td>
      <td>${escapeHtml(horse.popularity || "-")}</td>
      <td>${escapeHtml(horse.time_diff ?? "-")}</td>
      <td>${hit ? '<span class="badge good">的中</span>' : ""}</td>
    `;
    tbody.appendChild(tr);
  }
  els.panelResult.replaceChildren(table);
}

function renderMemo() {
  const detail = state.raceDetail;
  if (!detail?.race) {
    renderEmpty(els.panelMemo, "メモなし", "レースを選んでください。");
    return;
  }

  const container = document.createElement("div");
  const current = document.createElement("div");
  current.className = "memo-grid";
  for (const horse of detail.horses || []) {
    current.appendChild(createMemoCard(horse));
  }
  container.appendChild(current);

  const search = document.createElement("div");
  search.className = "notes-search";
  search.innerHTML = `
    <div class="search-row">
      <input id="noteSearchInput" type="search" placeholder="馬名検索" />
      <button class="search-button" type="button">検索</button>
    </div>
    <div id="noteSearchResult"></div>
  `;
  const input = search.querySelector("input");
  const button = search.querySelector("button");
  button.addEventListener("click", () => void searchNotes(input.value));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") void searchNotes(input.value);
  });
  container.appendChild(search);

  els.panelMemo.replaceChildren(container);
}

function createMemoCard(horse) {
  const note = state.raceDetail?.notes?.[horse.uma_id]?.note_text || "";
  const pattern = { ...(state.raceDetail?.patterns?.[horse.uma_id] || {}) };
  const card = document.createElement("article");
  card.className = "memo-card";
  card.innerHTML = `
    <div class="memo-head">
      <div>
        <strong>${escapeHtml(horse.umaban ? `${horse.umaban} ${horse.name}` : horse.name)}</strong>
        <span>${escapeHtml([horse.tier, horse.finish_rank ? `${horse.finish_rank}着` : "", horse.popularity ? `${horse.popularity}人気` : ""].filter(Boolean).join(" / "))}</span>
      </div>
      ${tierBadge(horse.tier)}
    </div>
  `;

  const textarea = document.createElement("textarea");
  textarea.value = note;
  textarea.placeholder = "メモ";
  card.appendChild(textarea);

  const patternBox = document.createElement("div");
  for (const dim of state.config?.patternDims || ["逃げ", "番手", "内枠", "中枠", "外枠"]) {
    const row = document.createElement("div");
    row.className = "pattern-row";
    const label = document.createElement("b");
    label.textContent = dim;
    const group = document.createElement("div");
    group.className = "mark-group";
    for (const mark of ["◯", "△", "✕", "×"]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `mark-button${pattern[dim] === mark ? " active" : ""}`;
      button.textContent = mark;
      button.dataset.dim = dim;
      button.dataset.mark = mark === "×" ? "" : mark;
      button.addEventListener("click", () => {
        group.querySelectorAll(".mark-button").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
      });
      group.appendChild(button);
    }
    row.append(label, group);
    patternBox.appendChild(row);
  }
  card.appendChild(patternBox);

  const save = document.createElement("button");
  save.type = "button";
  save.className = "save-button";
  save.textContent = state.config?.memoEnabled ? "保存" : "保存不可";
  save.disabled = !state.config?.memoEnabled || !horse.uma_id;
  save.addEventListener("click", () => void saveMemo(horse, textarea, card, save));
  card.appendChild(save);

  return card;
}

async function saveMemo(horse, textarea, card, saveButton) {
  const pattern = {};
  card.querySelectorAll(".mark-button.active").forEach((button) => {
    if (button.dataset.mark) pattern[button.dataset.dim] = button.dataset.mark;
  });

  saveButton.disabled = true;
  saveButton.textContent = "保存中";
  try {
    if (state.demo) {
      await sleep(180);
    } else {
      const headers = { "Content-Type": "application/json" };
      const pin = els.pinInput.value.trim();
      if (pin) headers["x-nankan-pin"] = pin;
      const response = await fetch("/api/notes", {
        method: "POST",
        headers,
        body: JSON.stringify({
          uma_id: horse.uma_id,
          horse_name: horse.name,
          note_text: textarea.value,
          pattern,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "保存に失敗しました");
    }

    if (!state.raceDetail.notes) state.raceDetail.notes = {};
    if (!state.raceDetail.patterns) state.raceDetail.patterns = {};
    state.raceDetail.notes[horse.uma_id] = {
      uma_id: horse.uma_id,
      horse_name: horse.name,
      note_text: textarea.value.trim(),
      updated_at: new Date().toISOString(),
    };
    state.raceDetail.patterns[horse.uma_id] = pattern;
    saveButton.classList.add("saved");
    saveButton.textContent = "保存済";
    showToast(`${horse.name} を保存しました`);
    renderMetrics();
  } catch (err) {
    saveButton.textContent = "再保存";
    showToast(err.message);
  } finally {
    saveButton.disabled = false;
    setTimeout(() => {
      saveButton.classList.remove("saved");
      saveButton.textContent = "保存";
    }, 1400);
  }
}

async function searchNotes(query) {
  const box = document.getElementById("noteSearchResult");
  if (!box) return;
  renderLoading(box, "検索中");
  try {
    const data = state.demo ? demoNotes(query) : await apiGet(`/api/notes?query=${encodeURIComponent(query || "")}`);
    const notes = data.notes || [];
    if (!notes.length) {
      renderEmpty(box, "該当なし", "メモはまだありません。");
      return;
    }
    const list = document.createElement("div");
    list.className = "memo-grid";
    for (const note of notes) {
      const card = document.createElement("article");
      card.className = "memo-card";
      card.innerHTML = `
        <div class="memo-head">
          <div>
            <strong>${escapeHtml(note.horse_name || "")}</strong>
            <span>${escapeHtml(shortDateTime(note.updated_at) || "")}</span>
          </div>
        </div>
        <p>${escapeHtml(note.note_text || "").replace(/\n/g, "<br>")}</p>
        <div class="tier-cloud">${patternSummary(note.pattern || parseJson(note.pattern_json))}</div>
      `;
      list.appendChild(card);
    }
    box.replaceChildren(list);
  } catch (err) {
    renderEmpty(box, "検索できません", err.message);
  }
}

function renderStats() {
  if (!state.stats) {
    renderLoading(els.panelStats, "成績を読み込み中");
    return;
  }
  const stats = state.stats.stats || {};
  const races = state.stats.races || [];
  if (!Object.keys(stats).length) {
    renderEmpty(els.panelStats, "成績なし", "結果取得済みレースがまだありません。");
    return;
  }

  const wrap = document.createElement("div");
  wrap.className = "stat-grid";

  const bars = document.createElement("div");
  bars.className = "bars";
  for (const [tier, item] of Object.entries(stats)) {
    const n = item.n || 1;
    const pct = Math.round((item.fuku / n) * 1000) / 10;
    const line = document.createElement("div");
    line.className = "bar-line";
    line.innerHTML = `
      <b>${escapeHtml(tier)}</b>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.max(0, Math.min(100, pct))}%"></span></span>
      <span>${pct}%</span>
    `;
    bars.appendChild(line);
  }

  const table = document.createElement("div");
  table.className = "table-wrap";
  table.innerHTML = `
    <table>
      <thead><tr><th>日付</th><th>場</th><th>R</th><th>1着</th><th>S/A</th></tr></thead>
      <tbody></tbody>
    </table>
  `;
  const tbody = table.querySelector("tbody");
  for (const race of races) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(formatDate(race.date))}</td>
      <td>${escapeHtml(race.place_name || "")}</td>
      <td>${escapeHtml(race.race_num)}</td>
      <td>${escapeHtml(race.winner || "")}</td>
      <td>${escapeHtml(race.hit_label || "")}</td>
    `;
    tbody.appendChild(tr);
  }

  wrap.append(bars, table);
  els.panelStats.replaceChildren(wrap);
}

function showTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
  const map = {
    prediction: els.panelPrediction,
    result: els.panelResult,
    memo: els.panelMemo,
    stats: els.panelStats,
  };
  map[tab]?.classList.add("active");
  if (tab === "stats") void loadStats();
}

function renderPinState() {
  els.pinBox.hidden = !state.config?.memoAuthRequired;
}

function renderSetupState() {
  setStatus("Vercel環境変数待ち");
  els.metricStrip.replaceChildren();
  els.raceRail.replaceChildren();
  renderEmpty(els.raceSummary, "未設定", "SUPABASE_URL と SUPABASE_SERVICE_ROLE_KEY を入れてください。");
  renderEmpty(els.panelPrediction, "接続待ち", "VercelのEnvironment Variables設定後、再デプロイすると表示されます。");
  renderEmpty(els.panelResult, "結果なし", "接続後に表示されます。");
  renderEmpty(els.panelMemo, "メモなし", "接続後に保存できます。");
  renderEmpty(els.panelStats, "成績なし", "接続後に表示されます。");
}

function renderLoading(target, message) {
  const div = document.createElement("div");
  div.className = "empty-state";
  div.innerHTML = `<strong>${escapeHtml(message)}</strong><p>少し待ってください。</p>`;
  target.replaceChildren(div);
}

function renderEmpty(target, title, message) {
  const template = document.getElementById("emptyTemplate");
  const node = template.content.cloneNode(true);
  node.querySelector("strong").textContent = title;
  node.querySelector("p").textContent = message || "";
  target.replaceChildren(node);
}

async function apiGet(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function fillSelect(select, options, value) {
  select.replaceChildren(
    ...options.map((option) => {
      const el = document.createElement("option");
      el.value = option.value;
      el.textContent = option.label;
      el.selected = option.value === value;
      return el;
    }),
  );
}

function setStatus(text) {
  els.statusLine.textContent = text;
}

function summaryLine() {
  const race = state.raceDetail?.race;
  if (!race) return "待機中";
  return `${formatDate(race.date)} ${race.place_name}${race.race_num}R`;
}

function formatDate(value) {
  const text = String(value || "");
  if (!/^\d{8}$/.test(text)) return text || "-";
  return `${text.slice(0, 4)}/${text.slice(4, 6)}/${text.slice(6, 8)}`;
}

function shortDateTime(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}

function tierBadge(tier) {
  if (!tier) return "";
  const cls = ["S", "A", "主力", "一軍"].includes(tier) ? " good" : ["B", "二軍"].includes(tier) ? " warn" : "";
  return `<span class="badge${cls}">${escapeHtml(tier)}</span>`;
}

function isTopHit(horse) {
  return ["S", "A", "主力", "一軍"].includes(horse.tier) && Number(horse.finish_rank || 99) <= 3;
}

function enhancePredictionHtml(html) {
  const style = `
    <style>
      html,body{margin:0;background:#fffdfa;color:#20221f;}
      body{font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Yu Gothic",sans-serif;line-height:1.55;}
      table{max-width:100%;}
      .container,.wrapper,main{max-width:100%!important;}
      .tabs,.race-tabs,.inner-tabs{position:sticky!important;top:0;z-index:20;}
      button,.tab,.race-tab,.inner-tab{min-height:34px;}
      @media (max-width: 720px){
        body{font-size:14px;}
        table{font-size:13px;}
        .tabs,.race-tabs,.inner-tabs{overflow-x:auto;white-space:nowrap;}
      }
    </style>
  `;
  if (/<\/head>/i.test(html)) return html.replace(/<\/head>/i, `${style}</head>`);
  return `<!doctype html><html><head><meta charset="utf-8">${style}</head><body>${html}</body></html>`;
}

function openPredictionHtml(html) {
  const blob = new Blob([enhancePredictionHtml(html)], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener,noreferrer");
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function downloadPredictionHtml(race, html) {
  const date = race?.date || "race";
  const place = race?.place_name || "nankan";
  const raceNum = race?.race_num || "";
  const filename = `${date}_${place}${raceNum}R.html`;
  const blob = new Blob([enhancePredictionHtml(html)], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function patternSummary(pattern) {
  const parts = Object.entries(pattern || {})
    .filter(([, value]) => value)
    .map(([key, value]) => `<span class="badge">${escapeHtml(key)} ${escapeHtml(value)}</span>`);
  return parts.join("");
}

function parseJson(value) {
  if (!value) return {};
  if (typeof value === "object") return value;
  try {
    return JSON.parse(value) || {};
  } catch {
    return {};
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showToast(message) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2600);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function demoConfig() {
  return {
    ok: true,
    configured: true,
    memoEnabled: true,
    memoAuthRequired: false,
    placeByCode: { "10": "大井", "11": "川崎", "12": "船橋", "13": "浦和" },
    patternDims: ["逃げ", "番手", "内枠", "中枠", "外枠"],
    patternMarks: ["◯", "△", "✕"],
  };
}

function demoRaces(date, place) {
  const races = [
    {
      race_key: "20260629_10_10",
      date: "20260629",
      place_code: "10",
      place_name: "大井",
      race_num: 10,
      race_name: "サンプル特別",
      dist: "1400",
      course: "ダ1400m",
      has_result: true,
      generated_at: "2026-06-29T18:30:00",
    },
    {
      race_key: "20260629_10_11",
      date: "20260629",
      place_code: "10",
      place_name: "大井",
      race_num: 11,
      race_name: "メインレース",
      dist: "1600",
      course: "ダ1600m",
      has_result: false,
      generated_at: "2026-06-29T18:42:00",
    },
  ].filter((race) => (!date || race.date === date) && (!place || race.place_code === place));
  return {
    ok: true,
    races,
    dates: ["20260629"],
    places: [{ place_code: "10", place_name: "大井" }],
    latestDate: "20260629",
  };
}

function demoRace(raceKey) {
  const race = demoRaces("", "").races.find((item) => item.race_key === raceKey) || demoRaces("", "").races[0];
  const horses = [
    { umaban: 1, name: "サンプルスター", uma_id: "demo-1", finish_rank: 1, popularity: 2, time_diff: 0, tier: "S" },
    { umaban: 2, name: "ミナミノライト", uma_id: "demo-2", finish_rank: 4, popularity: 1, time_diff: 0.5, tier: "A" },
    { umaban: 3, name: "カワサキロード", uma_id: "demo-3", finish_rank: 2, popularity: 5, time_diff: 0.2, tier: "B" },
    { umaban: 4, name: "ウラワノカゼ", uma_id: "demo-4", finish_rank: 7, popularity: 8, time_diff: 1.4, tier: "C" },
  ];
  return {
    ok: true,
    race,
    page: {
      data_html: `
        <section style="padding:18px">
          <h2 style="margin:0 0 12px">大井${race.race_num}R 予想ビュー</h2>
          <table border="1" cellspacing="0" cellpadding="8" style="border-collapse:collapse;width:100%">
            <tr><th>評価</th><th>馬</th><th>見立て</th></tr>
            <tr><td>S</td><td>サンプルスター</td><td>相対比較で軸候補</td></tr>
            <tr><td>A</td><td>ミナミノライト</td><td>展開次第で上位</td></tr>
          </table>
        </section>
      `,
      data_text: "",
    },
    horses,
    results: horses,
    notes: { "demo-1": { note_text: "内枠で揉まれなければ安定。" } },
    patterns: { "demo-1": { 内枠: "◯", 番手: "△" } },
  };
}

function demoStats() {
  return {
    ok: true,
    stats: {
      S: { n: 8, win: 3, ren: 5, fuku: 6 },
      A: { n: 11, win: 2, ren: 4, fuku: 6 },
      B: { n: 14, win: 1, ren: 3, fuku: 5 },
      C: { n: 18, win: 1, ren: 2, fuku: 3 },
    },
    races: [
      { date: "20260629", place_name: "大井", race_num: 10, winner: "サンプルスター", hit_label: "勝ち" },
    ],
  };
}

function demoNotes(query) {
  const notes = [
    {
      uma_id: "demo-1",
      horse_name: "サンプルスター",
      note_text: "内枠で揉まれなければ安定。",
      pattern: { 内枠: "◯", 番手: "△" },
      updated_at: "2026-06-29T19:00:00",
    },
  ].filter((note) => !query || note.horse_name.includes(query));
  return { ok: true, notes };
}
