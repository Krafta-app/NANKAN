const state = {
  config: null,
  currentDate: "",
  currentPlace: "",
  currentRaceKey: "",
  races: [],
  dates: [],
  places: [],
  raceDetail: null,
  parsed: null,
  notedUmaIds: new Set(),
  stats: null,
  activeTab: "pace",
  demo: new URLSearchParams(location.search).has("demo"),
};

const els = {};
const PIN_KEY = "nankan_site_pin";

const PLACE_FALLBACK = { "10": "大井", "11": "川崎", "12": "船橋", "13": "浦和" };
const TAB_PANELS = {
  pace: "panelPace",
  index: "panelIndex",
  ai: "panelAi",
  match: "panelMatch",
  memo: "panelMemo",
};

document.addEventListener("DOMContentLoaded", () => {
  cacheElements();
  bindEvents();
  ensureMarkMenu();
  void boot();
});

function cacheElements() {
  for (const id of [
    "statusLine", "pinBox", "pinInput", "refreshButton",
    "dateSelect", "placeSelect", "raceRail", "raceSummary",
    "panelPace", "panelIndex", "panelAi", "panelMatch", "panelMemo",
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

  document.querySelectorAll("#raceTabs .tab").forEach((button) => {
    button.addEventListener("click", () => showTab(button.dataset.tab));
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
  await loadAllNotes();
  await loadRaces();
}

async function refreshAll() {
  await loadAllNotes();
  await loadRaces(true);
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

// 全メモを一度だけ取得し、レース一覧のメモ印・評価一覧の太字に使う uma_id 集合を作る。
async function loadAllNotes() {
  try {
    const data = state.demo ? demoNotes("") : await apiGet("/api/notes?query=");
    const ids = new Set();
    for (const note of data.notes || []) {
      const hasText = (note.note_text || "").trim();
      const hasPattern = Object.values(note.pattern || parseJson(note.pattern_json) || {}).some(Boolean);
      if (note.uma_id && (hasText || hasPattern)) ids.add(String(note.uma_id));
    }
    state.notedUmaIds = ids;
  } catch {
    state.notedUmaIds = new Set();
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
    if (state.currentDate && state.dates.length && !state.dates.includes(state.currentDate)) {
      state.currentDate = data.latestDate || state.dates[0] || "";
      state.currentRaceKey = "";
      return loadRaces(false);
    }
    if (!state.currentDate && data.latestDate) {
      state.currentDate = data.latestDate;
      return loadRaces(preserveRace);
    }

    state.races = data.races || [];
    state.places = data.places || [];
    renderFilters();
    renderRaceRail();

    if (!preserveRace || !state.races.some((race) => race.race_key === state.currentRaceKey)) {
      state.currentRaceKey = state.races[0]?.race_key || "";
    }
    if (state.currentRaceKey) {
      await loadRace(state.currentRaceKey);
    } else {
      state.raceDetail = null;
      state.parsed = null;
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
  renderLoading(els.raceSummary, "予想を読み込み中");

  try {
    state.raceDetail = state.demo ? demoRace(raceKey) : await apiGet(`/api/race?race_key=${encodeURIComponent(raceKey)}`);
    state.parsed = parsePrediction(state.raceDetail);
    renderAllPanels();
    setStatus(summaryLine());
  } catch (err) {
    state.raceDetail = null;
    state.parsed = null;
    renderAllPanels();
    setStatus("読込エラー");
    renderEmpty(els.raceSummary, "レース詳細を取得できません", err.message);
  }
}

// ---- data_html から各セクションを取り出し、馬番↔uma_id↔メモ の対応表を作る ----
function parsePrediction(detail) {
  const html = detail?.page?.data_html || "";
  const race = detail?.race || {};
  const notes = detail?.notes || {};
  const patterns = detail?.patterns || {};

  const result = {
    evalText: race.eval_list_text || "",
    paceEl: null, indexEl: null, aiEl: null, matchEl: null,
    umaMap: {},          // umaban -> { name, uma_id, noteText }
    notedUmaban: new Set(),
    markCtx: { markPrefix: `${race.date || ""}_${race.place_name || ""}_`, raceNum: String(race.race_num || "") },
  };
  if (!html) return result;

  const doc = new DOMParser().parseFromString(html, "text/html");
  result.paceEl = doc.querySelector("#tab-pace");
  result.indexEl = doc.querySelector("#tab-index");
  result.aiEl = doc.querySelector("#tab-ai");
  result.matchEl = doc.querySelector("#tab-match");

  // 名前 -> uma_id の正規化辞書（出馬表の uma_ids から）
  const byNorm = {};
  for (const [name, umaId] of Object.entries(race.uma_ids || {})) {
    byNorm[normName(name)] = umaId;
  }

  // 出走馬分析の各見出しから 馬番→馬名 を取り、uma_id・メモへ橋渡し。
  if (result.aiEl) {
    for (const header of result.aiEl.querySelectorAll(".horse-header")) {
      const umaban = header.querySelector(".mark-btn")?.dataset.uma;
      if (!umaban) continue;
      const span = header.querySelector("span:not(.mark-btn)") || header;
      const raw = (span.textContent || "").trim();
      const name = raw.replace(/^[①-⑳]/, "").replace(/^\[\d{1,2}\]/, "").split(/[\s　]/)[0];
      const umaId = byNorm[normName(name)] || "";
      const noteText = (notes[umaId]?.note_text || "").trim();
      const hasPattern = Object.values(patterns[umaId] || {}).some(Boolean);
      result.umaMap[umaban] = { name, uma_id: umaId, noteText };
      if (noteText || hasPattern) result.notedUmaban.add(String(umaban));
    }
  }
  return result;
}

function renderFilters() {
  fillSelect(
    els.dateSelect,
    state.dates.map((date) => ({ value: date, label: formatDate(date) })),
    state.currentDate,
  );
  const allPlaces = Object.entries(state.config?.placeByCode || PLACE_FALLBACK).map(([value, label]) => ({ value, label }));
  fillSelect(els.placeSelect, [{ value: "", label: "すべて" }, ...allPlaces], state.currentPlace);
}

function raceHasMemo(race) {
  const ids = Object.values(race.uma_ids || {});
  return ids.some((id) => state.notedUmaIds.has(String(id)));
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
      const active = race.race_key === state.currentRaceKey;
      const memo = raceHasMemo(race);
      button.className = `race-button${active ? " active" : ""}${memo ? " has-memo" : ""}`;
      // 競馬場の頭文字＋距離（例: 大井1200m → 大1200）。「予想」文字は出さず横長・縦スリムに。
      const placeInitial = (race.place_name || "").slice(0, 1);
      const label = `${placeInitial}${race.dist || ""}`;
      button.innerHTML = `
        <strong>${escapeHtml(race.race_num)}R${memo ? '<i class="memo-dot" title="メモ有り"></i>' : ""}</strong>
        <span class="rb-meta">${escapeHtml(label || "—")}</span>
        ${race.has_result ? '<span class="rb-state done">結果</span>' : ""}
      `;
      button.addEventListener("click", () => void loadRace(race.race_key));
      return button;
    }),
  );
}

function renderAllPanels() {
  renderEval();
  renderSection(els.panelPace, state.parsed?.paceEl, "展開");
  renderSection(els.panelIndex, state.parsed?.indexEl, "相対評価");
  renderAi();
  renderSection(els.panelMatch, state.parsed?.matchEl, "対戦表");
  renderMemo();
}

// 評価一覧を左カラムに常設表示。各馬番に予想印（◎○▲…）ボタンを付ける。
function renderEval() {
  const parsed = state.parsed;
  const race = state.raceDetail?.race;
  if (!race) {
    renderEmpty(els.raceSummary, "レース未選択", "日付と競馬場を選んでください。");
    return;
  }
  const text = parsed?.evalText || "";
  if (!text) {
    renderEmpty(els.raceSummary, "評価一覧なし", "予想の評価一覧が保存されていません。");
    return;
  }
  const noted = parsed.notedUmaban;
  const wrap = document.createElement("div");
  wrap.className = "eval-panel";

  const lines = text.split("\n").map((line) => line.replace(/^【(?:総合評価|評価一覧)】\s*/, "")).filter((line) => line.trim());
  for (const line of lines) {
    const row = document.createElement("div");
    row.className = "eval-line";
    row.innerHTML = evalLineHtml(line, noted);
    wrap.appendChild(row);
  }

  if (noted.size) {
    const legend = document.createElement("div");
    legend.className = "eval-legend";
    legend.innerHTML = `<strong>太字の馬番</strong>＝メモ登録あり（${noted.size}頭）`;
    wrap.appendChild(legend);
  }

  els.raceSummary.replaceChildren(wrap);
  bindMarks(wrap);
}

function evalLineHtml(line, noted) {
  let h = escapeHtml(line);
  // 先にランク文字を着色（馬番[N]の直前のS/A/B…を拾う）。
  h = h.replace(/(^|[\s　])([SABCDEFG])(?=\[|[\s　]|$)/g, (m, pre, r) => `${pre}<span class="rank-${r}">${r}</span>`);
  // 各馬番[N]の前に印ボタンを差し込み、メモ有りなら太字化。
  h = h.replace(/\[(\d{1,2})\]/g, (m, n) => {
    const num = noted.has(String(n)) ? `<strong class="memo-num">[${n}]</strong>` : `[${n}]`;
    return `<span class="eval-chip"><span class="mark-btn" data-uma="${n}"></span>${num}</span>`;
  });
  // 当日横断の調教上位は赤字（🕰️注目欄）。エンジンが [[R]]…[[/R]] で囲む。
  h = h.replace(/\[\[R\]\]/g, '<span class="chokyo-red">').replace(/\[\[\/R\]\]/g, "</span>");
  // 実タイム等の括弧は半分くらいの小さい字（[[SMALL]]…[[/SMALL]]）。
  h = h.replace(/\[\[SMALL\]\]/g, '<small class="chokyo-detail">').replace(/\[\[\/SMALL\]\]/g, "</small>");
  return h;
}

// 展開・相対評価・対戦表: data_html のセクションをそのまま流し込み、印ボタンを有効化。
function renderSection(panel, sourceEl, label) {
  if (!state.raceDetail?.race) {
    renderEmpty(panel, `${label}なし`, "レースを選んでください。");
    return;
  }
  if (!sourceEl) {
    renderEmpty(panel, `${label}なし`, "このレースには該当データがありません。");
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "prediction-inline";
  for (const node of [...sourceEl.children]) {
    wrap.appendChild(document.importNode(node, true));
  }
  cleanupInline(wrap);
  bindMarks(wrap);
  panel.replaceChildren(wrap);
}

function renderAi() {
  const panel = els.panelAi;
  const parsed = state.parsed;
  if (!state.raceDetail?.race) {
    renderEmpty(panel, "出走馬分析なし", "レースを選んでください。");
    return;
  }
  if (!parsed?.aiEl) {
    renderEmpty(panel, "出走馬分析なし", "このレースには該当データがありません。");
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "prediction-inline";
  for (const node of [...parsed.aiEl.children]) {
    wrap.appendChild(document.importNode(node, true));
  }
  cleanupInline(wrap);
  injectMemos(wrap, parsed);
  bindMarks(wrap);
  panel.replaceChildren(wrap);
}

// 各馬の厩舎コメント（「…」行）の直下にメモ内容を差し込む。
function injectMemos(root, parsed) {
  const container = root.querySelector(".content-box > div") || root.querySelector(".content-box") || root;
  let umaban = null;
  for (const node of [...container.childNodes]) {
    if (node.nodeType === 1 && node.classList?.contains("horse-header")) {
      umaban = node.querySelector(".mark-btn")?.dataset.uma || null;
      continue;
    }
    // 厩舎コメント（chokyo-label）の直前で差し込む = 「厩舎の話の下」
    if (node.nodeType === 1 && node.classList?.contains("chokyo-label") && umaban) {
      const info = parsed.umaMap[umaban];
      if (info?.noteText) {
        const box = document.createElement("div");
        box.className = "memo-inline";
        box.innerHTML = `<span class="memo-inline-tag">📝 メモ</span><span class="memo-inline-text">${escapeHtml(info.noteText).replace(/\n/g, "<br>")}</span>`;
        container.insertBefore(box, node);
      }
      umaban = null;
    }
  }
}

function cleanupInline(root) {
  root.querySelectorAll("style, script").forEach((node) => node.remove());
  root.querySelectorAll("[onclick]").forEach((node) => {
    node.dataset.originalOnclick = node.getAttribute("onclick") || "";
    node.removeAttribute("onclick");
  });
  root.querySelectorAll("a[href]").forEach((link) => {
    link.target = "_blank";
    link.rel = "noopener";
  });
}

// ---------- 印（◎○▲…）ボタン: 単体予想HTMLと同じ localStorage キーで保存 ----------
function ensureMarkMenu() {
  if (els.markMenu) return;
  const menu = document.createElement("div");
  menu.className = "app-mark-menu";
  menu.id = "appMarkMenu";
  for (const mark of ["◎", "○", "▲", "△", "☆", "✓", "消", ""]) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "mm-item";
    item.textContent = mark || "✕";
    item.dataset.markValue = mark;
    item.addEventListener("click", (event) => {
      event.stopPropagation();
      applyMark(mark);
    });
    menu.appendChild(item);
  }
  document.body.appendChild(menu);
  els.markMenu = menu;
  document.addEventListener("click", hideMarkMenu);
}

function markKey(uma) {
  const ctx = state.parsed?.markCtx || { markPrefix: "", raceNum: "" };
  return `${ctx.markPrefix}keiba_mark_${ctx.raceNum}_${uma}`;
}

function bindMarks(root) {
  root.querySelectorAll(".mark-btn").forEach((btn) => {
    const uma = btn.dataset.uma;
    const saved = uma ? localStorage.getItem(markKey(uma)) : "";
    btn.textContent = saved || "";
    btn.dataset.mark = saved || "";
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      openMarkMenu(btn);
    });
  });
}

function openMarkMenu(btn) {
  ensureMarkMenu();
  state.markTarget = btn;
  const menu = els.markMenu;
  const rect = btn.getBoundingClientRect();
  menu.style.display = "flex";
  const width = 280;
  let left = window.scrollX + rect.left;
  if (left + width > window.scrollX + window.innerWidth) left = window.scrollX + window.innerWidth - width - 8;
  menu.style.top = `${window.scrollY + rect.bottom + 6}px`;
  menu.style.left = `${Math.max(8, left)}px`;
}

function hideMarkMenu() {
  if (els.markMenu) els.markMenu.style.display = "none";
}

function applyMark(mark) {
  const btn = state.markTarget;
  if (!btn) return;
  const uma = btn.dataset.uma;
  if (mark) localStorage.setItem(markKey(uma), mark);
  else localStorage.removeItem(markKey(uma));
  syncMarks(uma, mark);
  hideMarkMenu();
}

function syncMarks(uma, mark) {
  document.querySelectorAll(`.mark-btn[data-uma="${cssEscape(uma)}"]`).forEach((btn) => {
    btn.textContent = mark || "";
    btn.dataset.mark = mark || "";
  });
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function renderMemo() {
  const detail = state.raceDetail;
  if (!detail?.race) {
    renderEmpty(els.panelMemo, "メモなし", "レースを選んでください。");
    return;
  }
  const container = document.createElement("div");

  const grid = document.createElement("div");
  grid.className = "memo-grid";
  for (const horse of detail.horses || []) {
    grid.appendChild(createMemoCard(horse));
  }
  container.appendChild(grid);

  const search = document.createElement("div");
  search.className = "notes-search";
  search.innerHTML = `
    <div class="search-row">
      <input id="noteSearchInput" type="search" placeholder="馬名でメモ検索" />
      <button class="search-button" type="button">検索</button>
    </div>
    <div id="noteSearchResult"></div>
  `;
  const input = search.querySelector("input");
  search.querySelector("button").addEventListener("click", () => void searchNotes(input.value));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") void searchNotes(input.value);
  });
  container.appendChild(search);

  els.panelMemo.replaceChildren(container);
}

function createMemoCard(horse) {
  const note = state.raceDetail?.notes?.[horse.uma_id]?.note_text || "";
  const pattern = { ...(state.raceDetail?.patterns?.[horse.uma_id] || {}) };
  const hasMemo = note.trim() || Object.values(pattern).some(Boolean);
  const card = document.createElement("details");
  card.className = "memo-card";
  card.innerHTML = `
    <summary class="memo-head">
      <div>
        <strong>${escapeHtml(horse.umaban ? `${horse.umaban} ${horse.name}` : horse.name)}</strong>
        <span>${escapeHtml([horse.tier, horse.finish_rank ? `${horse.finish_rank}着` : "", horse.popularity ? `${horse.popularity}人気` : ""].filter(Boolean).join(" / "))}</span>
      </div>
      <div class="memo-head-right">
        ${hasMemo ? '<span class="memo-status">メモ有</span>' : ""}
        ${tierBadge(horse.tier)}
      </div>
    </summary>
  `;

  const body = document.createElement("div");
  body.className = "memo-body";

  const textarea = document.createElement("textarea");
  textarea.value = note;
  textarea.placeholder = "メモ（次走時に厩舎コメント下へ表示）";
  body.appendChild(textarea);

  const patternBox = document.createElement("div");
  patternBox.className = "pattern-box";
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
  body.appendChild(patternBox);

  const save = document.createElement("button");
  save.type = "button";
  save.className = "save-button";
  save.textContent = state.config?.memoEnabled ? "保存" : "保存不可";
  save.disabled = !state.config?.memoEnabled || !horse.uma_id;
  save.addEventListener("click", () => void saveMemo(horse, textarea, card, save));
  body.appendChild(save);
  card.appendChild(body);

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
        body: JSON.stringify({ uma_id: horse.uma_id, horse_name: horse.name, note_text: textarea.value, pattern }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        // 原因(detail)も表示する。例: Supabaseの列不足やRLSエラーを見えるように。
        const detail = payload.detail ? `：${payload.detail}` : "";
        throw new Error((payload.error || "保存に失敗しました") + detail);
      }
    }

    if (!state.raceDetail.notes) state.raceDetail.notes = {};
    if (!state.raceDetail.patterns) state.raceDetail.patterns = {};
    const trimmed = textarea.value.trim();
    state.raceDetail.notes[horse.uma_id] = {
      uma_id: horse.uma_id, horse_name: horse.name, note_text: trimmed, updated_at: new Date().toISOString(),
    };
    state.raceDetail.patterns[horse.uma_id] = pattern;

    // メモ状態の即時反映（太字・厩舎コメント下メモ・一覧の印）
    const hasMemo = trimmed || Object.values(pattern).some(Boolean);
    if (hasMemo) state.notedUmaIds.add(String(horse.uma_id));
    else state.notedUmaIds.delete(String(horse.uma_id));
    state.parsed = parsePrediction(state.raceDetail);
    renderEval();
    renderAi();
    renderRaceRail();

    saveButton.classList.add("saved");
    saveButton.textContent = "保存済";
    showToast(`${horse.name} を保存しました`);
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

function showTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll("#raceTabs .tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
  document.getElementById(TAB_PANELS[tab])?.classList.add("active");
}

function renderPinState() {
  els.pinBox.hidden = !state.config?.memoAuthRequired;
}

function renderSetupState() {
  setStatus("Vercel環境変数待ち");
  els.raceRail.replaceChildren();
  renderEmpty(els.raceSummary, "未設定", "SUPABASE_URL と SUPABASE_SERVICE_ROLE_KEY を入れてください。");
  renderEmpty(els.panelPace, "接続待ち", "VercelのEnvironment Variables設定後、再デプロイすると表示されます。");
  for (const id of ["panelIndex", "panelAi", "panelMatch", "panelMemo"]) {
    renderEmpty(els[id], "接続待ち", "Supabase接続後に表示されます。");
  }
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
  if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
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
  const dist = race.course || (race.dist ? `${race.dist}m` : "");
  const parts = [
    `${race.place_name}${race.race_num}R`,
    race.race_name || "",
    dist,
    race.post_time ? `${race.post_time}発走` : "",
  ].filter(Boolean);
  return parts.join("  ");
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

function normName(value) {
  return String(value ?? "").replace(/[\s　]/g, "");
}

function patternSummary(pattern) {
  return Object.entries(pattern || {})
    .filter(([, value]) => value)
    .map(([key, value]) => `<span class="badge">${escapeHtml(key)} ${escapeHtml(value)}</span>`)
    .join("");
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

// ----------------------------- デモデータ -----------------------------
function demoConfig() {
  return {
    ok: true, configured: true, memoEnabled: true, memoAuthRequired: false,
    placeByCode: PLACE_FALLBACK,
    patternDims: ["逃げ", "番手", "内枠", "中枠", "外枠"],
    patternMarks: ["◯", "△", "✕"],
  };
}

function demoRaces(date, place) {
  const races = [
    {
      race_key: "20260629_10_10", date: "20260629", place_code: "10", place_name: "大井",
      race_num: 10, race_name: "サンプル特別", dist: "1400", course: "ダ1400m", post_time: "20:10",
      eval_list_text: "【総合評価】  S[1]  A[2]  B[3]  C[4]\n🕰️： [[R]][1](大井36.3 1位/35頭)[[/R]] [[R]][3](船橋37.1 3位/22頭)[[/R]]",
      has_result: true, generated_at: "2026-06-29T18:30:00",
      uma_ids: { サンプルスター: "demo-1", ミナミノライト: "demo-2", カワサキロード: "demo-3", ウラワノカゼ: "demo-4" },
    },
    {
      race_key: "20260629_10_11", date: "20260629", place_code: "10", place_name: "大井",
      race_num: 11, race_name: "メインレース", dist: "1600", course: "ダ1600m", post_time: "20:50",
      eval_list_text: "【総合評価】  S[1]  A[2]",
      has_result: false, generated_at: "2026-06-29T18:42:00",
      uma_ids: { サンプルスター: "demo-1", ミナミノライト: "demo-2" },
    },
  ].filter((race) => (!date || race.date === date) && (!place || race.place_code === place));
  return { ok: true, races, dates: ["20260629"], places: [{ place_code: "10", place_name: "大井" }], latestDate: "20260629" };
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
    ok: true, race,
    page: { data_html: demoHtml(race), data_text: "" },
    horses, results: horses,
    notes: { "demo-1": { note_text: "内枠で揉まれなければ安定。前走は不利あり。" } },
    patterns: { "demo-1": { 内枠: "◯", 番手: "△" } },
  };
}

function demoHtml(race) {
  return `<!doctype html><html><body>
    <div class="sticky-top">
      <div class="eval-bar">【総合評価】  S[1]  A[2]  B[3]  C[4]</div>
      <div class="tabs"></div>
    </div>
    <div id="tab-pace" class="tab-content"><div class="content-box"><pre>【展開予想】\nハナは[1]サンプルスター。番手に[3]。\nMペース想定。</pre></div></div>
    <div id="tab-index" class="tab-content"><div class="content-box">相対評価テーブル（デモ）</div></div>
    <div id="tab-ai" class="tab-content"><div class="content-box"><div>
      <div class="horse-header"><span class="mark-btn" data-uma="1"></span><span>①サンプルスター　S</span></div>
      騎:Ｄｅｍｏ騎手
      【結論:計88点】
      「気配良く順調。」
      <span class="chokyo-label">【調教】</span><div class="chokyo-text">坂路47.0</div>
      <hr>
      <div class="horse-header"><span class="mark-btn" data-uma="2"></span><span>②ミナミノライト　A</span></div>
      騎:Ｄｅｍｏ騎手
      【結論:計80点】
      「叩き2走目で上昇。」
      <span class="chokyo-label">【調教】</span><div class="chokyo-text">坂路48.5</div>
      <hr>
    </div></div></div>
    <div id="tab-match" class="tab-content"><div class="content-box"><pre>【対戦表】\n[1]サンプルスター > [2]ミナミノライト</pre></div></div>
  </body></html>`;
}

function demoNotes(query) {
  const notes = [
    {
      uma_id: "demo-1", horse_name: "サンプルスター",
      note_text: "内枠で揉まれなければ安定。前走は不利あり。",
      pattern: { 内枠: "◯", 番手: "△" }, updated_at: "2026-06-29T19:00:00",
    },
  ].filter((note) => !query || note.horse_name.includes(query));
  return { ok: true, notes };
}
