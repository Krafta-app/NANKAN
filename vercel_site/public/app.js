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
  odds: null,
  oddsLoading: false,
  oddsSort: "odds",
  fullRaceLoadingKey: "",
  view: "list",                 // "list"（レース一覧）| "race"（予想）
  sortMode: readStoredSortMode(), // "race"（レース順）| "conf"（信用度が高い順）
  nerai: {},                    // race_key -> { isTarget, count, ... }
  neraiReq: "",                 // 直近に投げた狙い判定リクエストの識別子（stale判定用）
  demo: new URLSearchParams(location.search).has("demo"),
};

const els = {};
const PIN_KEY = "nankan_site_pin";
const SORT_KEY = "nankan_race_sort";

const PLACE_FALLBACK = { "10": "大井", "11": "川崎", "12": "船橋", "13": "浦和" };
const TAB_PANELS = {
  pace: "panelPace",
  odds: "panelOdds",
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
    "listView", "raceView", "backToList", "raceViewTitle", "sortByRace", "sortByConf",
    "panelPace", "panelOdds", "panelIndex", "panelAi", "panelMatch", "panelMemo",
  ]) {
    els[id] = document.getElementById(id);
  }
}

function bindEvents() {
  els.refreshButton.addEventListener("click", () => void refreshAll());
  // 開催日/開催を変えたら一覧に留める（特定のレースは自動で開かない）。
  els.dateSelect.addEventListener("change", () => {
    state.currentDate = els.dateSelect.value;
    state.currentRaceKey = "";
    showListView();
    void loadRaces();
  });
  els.placeSelect.addEventListener("change", () => {
    state.currentPlace = els.placeSelect.value;
    state.currentRaceKey = "";
    showListView();
    void loadRaces();
  });
  els.pinInput.value = localStorage.getItem(PIN_KEY) || "";
  els.pinInput.addEventListener("input", () => {
    localStorage.setItem(PIN_KEY, els.pinInput.value.trim());
  });

  document.querySelectorAll("#raceTabs .tab").forEach((button) => {
    button.addEventListener("click", () => showTab(button.dataset.tab));
  });

  els.sortByRace?.addEventListener("click", () => setSortMode("race"));
  els.sortByConf?.addEventListener("click", () => setSortMode("conf"));
  els.backToList?.addEventListener("click", () => backToList());
  renderSortToggle();
}

/* ------------------------------- views ------------------------------- */
function showListView() {
  state.view = "list";
  if (els.listView) els.listView.hidden = false;
  if (els.raceView) els.raceView.hidden = true;
}

function showRaceView() {
  state.view = "race";
  if (els.listView) els.listView.hidden = true;
  if (els.raceView) els.raceView.hidden = false;
  window.scrollTo(0, 0);
}

// 予想ビューから一覧へ戻る。時刻連動の枠囲い・メモ印を更新し、直近の発走前レースへスクロール。
function backToList() {
  state.currentRaceKey = "";
  showListView();
  renderRaceBoard();
  setStatus(listStatusText());
  requestAnimationFrame(scrollBoardToUpcoming);
}

function setSortMode(mode) {
  const next = mode === "conf" ? "conf" : "race";
  if (state.sortMode === next) return;
  state.sortMode = next;
  writeStoredSortMode(next);
  renderSortToggle();
  renderRaceBoard();
}

function renderSortToggle() {
  const conf = state.sortMode === "conf";
  els.sortByRace?.classList.toggle("active", !conf);
  els.sortByConf?.classList.toggle("active", conf);
  els.sortByRace?.setAttribute("aria-pressed", String(!conf));
  els.sortByConf?.setAttribute("aria-pressed", String(conf));
}

function readStoredSortMode() {
  try { return localStorage.getItem(SORT_KEY) === "conf" ? "conf" : "race"; } catch { return "race"; }
}

function writeStoredSortMode(value) {
  try { localStorage.setItem(SORT_KEY, value === "conf" ? "conf" : "race"); } catch { /* private mode */ }
}

function listStatusText() {
  const places = new Set(state.races.map((r) => r.place_name || "")).size;
  const races = state.races.length;
  if (!races) return "対象レースなし";
  return `${places}場 ${races}R ／ レースを選ぶ`;
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
  if (state.raceDetail?.race) void loadOdds();
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
      state.currentDate = data.selectedDate || data.latestDate || state.dates[0] || "";
      state.currentRaceKey = "";
      return loadRaces(false);
    }
    if (!state.currentDate && (data.selectedDate || data.latestDate)) state.currentDate = data.selectedDate || data.latestDate;

    state.races = data.races || [];
    state.places = data.places || [];
    renderFilters();
    renderRaceBoard();
    void loadNerai();

    // 更新(refresh)時に予想ビューを見ていて、そのレースが残っていれば継続表示する。
    // それ以外（初回・開催変更）は一覧に留め、特定のレースは自動で開かない。
    const keepRace = preserveRace && state.currentRaceKey
      && state.races.some((race) => race.race_key === state.currentRaceKey);
    if (keepRace) {
      await loadRace(state.currentRaceKey);
    } else {
      state.currentRaceKey = "";
      state.raceDetail = null;
      state.parsed = null;
      showListView();
      setStatus(listStatusText());
      requestAnimationFrame(scrollBoardToUpcoming);
    }
  } catch (err) {
    setStatus("読込エラー");
    renderEmpty(els.raceRail, "レース一覧を取得できません", err.message);
  }
}

async function loadRace(raceKey) {
  state.currentRaceKey = raceKey;
  const listed = state.races.find((race) => race.race_key === raceKey);
  if (els.raceViewTitle && listed) {
    els.raceViewTitle.textContent = `${listed.place_name || ""}${listed.race_num ?? ""}R ${raceDisplayName(listed)}`.trim();
  }
  showRaceView();
  renderRaceBoard();
  setStatus("レース詳細読込中");
  renderLoading(els.raceSummary, "予想を読み込み中");

  state.odds = null;
  state.fullRaceLoadingKey = "";
  try {
    state.raceDetail = state.demo ? demoRace(raceKey) : await apiGet(`/api/race?race_key=${encodeURIComponent(raceKey)}&include_page=0`);
    state.parsed = parsePrediction(state.raceDetail);
    renderAllPanels();
    setStatus(summaryLine());
    void loadOdds();
    void ensureFullRaceForActiveTab();
  } catch (err) {
    state.raceDetail = null;
    state.parsed = null;
    renderAllPanels();
    setStatus("読込エラー");
    renderEmpty(els.raceSummary, "レース詳細を取得できません", err.message);
  }
}

async function ensureFullRaceForActiveTab() {
  if (!["pace", "index", "ai", "match"].includes(state.activeTab)) return;
  await ensureFullRace();
}

async function ensureFullRace() {
  const raceKey = state.currentRaceKey;
  if (!raceKey || state.demo || state.raceDetail?.pageLoaded || state.fullRaceLoadingKey === raceKey) return;
  state.fullRaceLoadingKey = raceKey;
  renderHeavyPanelsLoading();
  try {
    const fullDetail = await apiGet(`/api/race?race_key=${encodeURIComponent(raceKey)}`);
    if (state.currentRaceKey !== raceKey) return;
    state.raceDetail = fullDetail;
    state.parsed = parsePrediction(state.raceDetail);
    renderAllPanels();
    setStatus(summaryLine());
  } catch (err) {
    renderHeavyPanelsError(err.message);
  } finally {
    if (state.fullRaceLoadingKey === raceKey) state.fullRaceLoadingKey = "";
  }
}

function renderHeavyPanelsLoading() {
  for (const [tab, id] of Object.entries(TAB_PANELS)) {
    if (["pace", "index", "ai", "match"].includes(tab)) renderLoading(els[id], "詳細を読み込み中");
  }
}

function renderHeavyPanelsError(message) {
  renderEmpty(els.panelPace, "展開を取得できません", message);
  renderEmpty(els.panelIndex, "相対評価を取得できません", message);
  renderEmpty(els.panelAi, "出走馬分析を取得できません", message);
  renderEmpty(els.panelMatch, "対戦表を取得できません", message);
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

// レース一覧ボード。レース順＝競馬場ごとの列（横並び・横スクロール）、
// 信用度順＝全レースを信用度が高い順に並べた1列。
function renderRaceBoard() {
  if (!state.races.length) {
    els.raceRail.className = "race-board";
    renderEmpty(els.raceRail, "該当レースなし", "Macで予想生成後、Supabaseへ同期されると表示されます。");
    return;
  }
  els.raceRail.className = `race-board ${state.sortMode === "conf" ? "is-flat" : "is-grouped"}`;
  if (state.sortMode === "conf") renderFlatBoard(state.races);
  else renderGroupedBoard(state.races);
}

// 競馬場ごとに1列。列内は R 昇順。
function renderGroupedBoard(races) {
  const order = [];
  const byPlace = new Map();
  for (const race of races) {
    const place = race.place_name || "—";
    if (!byPlace.has(place)) {
      byPlace.set(place, []);
      order.push(place);
    }
    byPlace.get(place).push(race);
  }
  const cols = order.map((place) => {
    const list = [...byPlace.get(place)].sort((a, b) => numVal(a.race_num) - numVal(b.race_num));
    return buildTrackColumn(place, list);
  });
  // その日1場だけなら列を横幅いっぱいに広げる（2列想定の幅制限を外す）。
  els.raceRail.classList.toggle("is-single", order.length === 1);
  els.raceRail.replaceChildren(...cols);
}

// 信用度が高い順の1列。同点は競馬場名→R番号で安定させる。各行に競馬場も表示する。
function renderFlatBoard(races) {
  const list = [...races].sort((a, b) => {
    const d = confTierNum(b) - confTierNum(a);
    if (d) return d;
    const p = String(a.place_name || "").localeCompare(String(b.place_name || ""), "ja");
    if (p) return p;
    return numVal(a.race_num) - numVal(b.race_num);
  });
  const col = document.createElement("div");
  col.className = "track-col is-flatcol";
  const head = document.createElement("div");
  head.className = "track-col-head";
  head.innerHTML = `<div class="tch-place">信用度が高い順<span class="tch-kaisai">${list.length}R</span></div>`;
  col.appendChild(head);
  const body = document.createElement("div");
  body.className = "track-col-body";
  for (const race of list) body.appendChild(buildRaceRow(race, { showPlace: true }));
  col.appendChild(body);
  els.raceRail.replaceChildren(col);
}

function buildTrackColumn(place, races) {
  const col = document.createElement("div");
  col.className = "track-col";
  col.dataset.place = place;
  const head = document.createElement("div");
  head.className = "track-col-head";
  head.innerHTML = `<div class="tch-place">${escapeHtml(place)}<span class="tch-kaisai">${races.length}R</span></div>`;
  col.appendChild(head);
  const body = document.createElement("div");
  body.className = "track-col-body";
  for (const race of races) body.appendChild(buildRaceRow(race, { showPlace: false }));
  col.appendChild(body);
  return col;
}

// 1レース分の行（netkeiba一覧風）。クリックで予想ビューへ。
function buildRaceRow(race, { showPlace }) {
  const meta = parseRaceMeta(race);
  const active = race.race_key === state.currentRaceKey;
  const memo = raceHasMemo(race);
  const upcoming = isUpcomingRace(race);

  const button = document.createElement("button");
  button.type = "button";
  button.className = [
    "rl-row",
    meta.isSpecial ? "is-special" : "",
    upcoming ? "is-upcoming" : "",
    active ? "active" : "",
    memo ? "has-memo" : "",
  ].filter(Boolean).join(" ");

  const numLabel = showPlace
    ? `${escapeHtml(String(race.place_name || "").slice(0, 1))}${escapeHtml(String(race.race_num ?? "?"))}`
    : `${escapeHtml(String(race.race_num ?? "?"))}R`;

  // クラスは名前欄に入っているので重複を避け、グレード(重賞/OP/L)のときだけバッジを出す。
  const gradeBadge = (meta.grade && meta.isSpecial)
    ? `<span class="rl-grade grade-${meta.gradeCls}">${escapeHtml(meta.grade)}</span>`
    : "";

  const conf = raceConfidence(race);
  const confBadge = conf
    ? `<span class="rb-conf ${conf.cls}" title="信用度${conf.tier}（S/Aを軸として見る信用度）">信${escapeHtml(conf.tier)}</span>`
    : "";
  const info = state.nerai[race.race_key];
  const neraiBadge = info?.isTarget
    ? `<span class="rb-nerai" title="同競馬場での対戦やS/A同士の直接対決に該当する馬が${info.count}頭 → 狙いやすいレース">狙</span>`
    : "";

  const distTxt = `${meta.surface}${meta.dist || ""}${meta.dist ? "m" : ""}`;
  const headTxt = meta.headCount ? `${meta.headCount}頭` : "";
  const resultTxt = race.has_result ? '<span class="rl-done">結果</span>' : "";

  button.innerHTML = `
    <span class="rl-num">${numLabel}${memo ? '<i class="memo-dot" title="メモ有り"></i>' : ""}</span>
    <span class="rl-main">
      <span class="rl-line1">
        <span class="rl-name">${escapeHtml(meta.name || raceDisplayName(race))}</span>
        ${gradeBadge}
      </span>
      <span class="rl-line2">
        ${race.post_time ? `<span class="rl-time">${escapeHtml(race.post_time)}</span>` : ""}
        <span class="rl-dist">${escapeHtml(distTxt)}</span>
        ${headTxt ? `<span class="rl-head">${escapeHtml(headTxt)}</span>` : ""}
        ${resultTxt}
        <span class="rl-flex"></span>
        ${confBadge}${neraiBadge}
      </span>
    </span>
  `;
  button.addEventListener("click", () => void loadRace(race.race_key));
  return button;
}

// いちばん近い「これから」のレースの列が見えるよう横スクロールする。
function scrollBoardToUpcoming() {
  const board = els.raceRail;
  if (!board || state.view !== "list") return;
  const row = board.querySelector(".rl-row.is-upcoming");
  if (!row) { board.scrollLeft = 0; return; }
  const col = row.closest(".track-col");
  if (col) board.scrollLeft = Math.max(0, col.offsetLeft - 8);
}

// 一覧の表示に要る項目を race から取り出す。
function parseRaceMeta(race) {
  const name = String(race.race_name || "").trim();
  const course = String(race.course || "");
  const surface = /芝/.test(course) ? "芝" : "ダ";
  const dist = race.dist || (course.match(/\d{3,4}/) || [""])[0];
  const grade = parseRaceGrade(name);
  // 名前が付いた特別・重賞は背景色で強調。単なるクラス条件（Ｃ３(一)等）は平場扱い。
  const isSpecial = !!grade
    || /特別|賞|杯|カップ|記念|ステークス|ダービー|オークス|マイル|オープン|Jpn|Ｇ[ⅠⅡⅢ]/.test(name);
  const headCount = Object.keys(race.uma_ids || {}).length;
  return { name, surface, dist, grade, gradeCls: grade ? "g" : "", isSpecial, headCount };
}

function parseRaceGrade(name) {
  const norm = (s) => s.replace(/Ⅰ/g, "I").replace(/Ⅱ/g, "II").replace(/Ⅲ/g, "III");
  const s = norm(String(name || ""));
  let m = s.match(/Jpn\s?(III|II|I|[123])/);
  if (m) return `Jpn${m[1].replace("3", "III").replace("2", "II").replace("1", "I")}`;
  m = s.match(/(?:G|Ｇ)\s?(III|II|I|[123])/);
  if (m) return `G${m[1].replace("3", "III").replace("2", "II").replace("1", "I")}`;
  if (/リステッド|[(（]Ｌ[)）]|[(（]L[)）]/.test(s)) return "L";
  if (/オープン|[(（](?:OP|Ｏ?Ｐ)[)）]/.test(s)) return "OP";
  return "";
}

// 予想ビューの見出し等に使うレース名（無ければ距離条件で代替）。
function raceDisplayName(race) {
  const m = parseRaceMeta(race);
  if (m.name) return m.name;
  return `${m.surface}${m.dist || ""}${m.dist ? "m" : ""}`.trim() || "レース";
}

// スマホを開いた日本時間より後（＝これから発走）のレースか。
// 開催日が未来なら全て、過去なら無し、当日は発走時刻で判定する。
function isUpcomingRace(race) {
  const runDate = String(state.currentDate || "");
  const today = jstToday8();
  const pm = postMinutes(race.post_time);
  if (!runDate) return pm !== null && pm >= nowJstMinutes();
  if (runDate > today) return true;
  if (runDate < today) return false;
  return pm !== null && pm >= nowJstMinutes();
}

function postMinutes(text) {
  const m = String(text || "").match(/(\d{1,2})[:：](\d{2})/);
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

// 日本時間の今日を "YYYYMMDD" で返す（閲覧端末のTZに依存しない）。
function jstToday8() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date()).replace(/-/g, "");
}

function nowJstMinutes() {
  const parts = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date());
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
  const mi = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  return h * 60 + mi;
}

function numVal(value) {
  const n = parseInt(String(value ?? ""), 10);
  return Number.isFinite(n) ? n : 999;
}

/* --------------------------- 信用度・狙い判定 --------------------------- */
// 信用度は同一レースで何度も参照するので race にキャッシュする。
function raceConfidence(race) {
  if (!race) return null;
  if (!("__conf" in race)) race.__conf = computeConfidenceTier(race);
  return race.__conf;
}

function confTierNum(race) {
  const conf = raceConfidence(race);
  const n = conf ? Number(conf.tier) : 0;
  return Number.isFinite(n) ? n : 0;
}

// 評価ランク分布から信用度(1〜5)を作る。S/A(軸)が絞れているほど高く、
// 上位乱立・軸不在・低評価だらけは減点。中央競馬側と同じ「S/Aを軸として見る信用度」。
function computeConfidenceTier(race) {
  const counts = tierCounts(race);
  const field = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!field) return null;
  const S = counts.S || 0;
  const A = counts.A || 0;
  const B = counts.B || 0;
  const high = S + A;
  const lowTail = (counts.D || 0) + (counts.E || 0) + (counts.F || 0) + (counts.G || 0);

  let pts = 0;
  if (S === 1) pts += 2;                 // 単独S＝明確な軸
  else if (S === 0 && high === 1) pts += 1; // Sなしでも上位1頭に絞れる
  else if (S >= 3) pts -= 1;             // S乱立＝軸が絞れない
  if (high <= 2) pts += 1;               // 上位が絞れている
  else if (high >= 5) pts -= 1;          // 上位過多＝混戦
  if (high === 0) pts -= 1;              // 軸不在
  const core = high + B;
  if (core >= 4 && core <= Math.ceil(field * 0.65)) pts += 1; // 上位〜中位が程よい厚み
  if (lowTail >= Math.ceil(field * 0.5)) pts -= 1;            // 低評価だらけ＝紛れ

  const level = pts >= 3 ? 5 : pts >= 2 ? 4 : pts >= 0 ? 3 : pts >= -1 ? 2 : 1;
  return { tier: String(level), cls: `conf-${level}` };
}

// grades(馬名→tier) から S/A/B… の頭数を数える。無ければ eval_list_text から拾う。
function tierCounts(race) {
  const counts = {};
  const grades = race?.grades || {};
  const vals = Object.values(grades);
  if (vals.length) {
    for (const t of vals) {
      const r = String(t || "").trim().charAt(0).toUpperCase();
      if (/[SABCDEFG]/.test(r)) counts[r] = (counts[r] || 0) + 1;
    }
    if (Object.keys(counts).length) return counts;
  }
  const text = String(race?.eval_list_text || "");
  const re = /([SABCDEFG])((?:\s*\[\d{1,2}\])+)/g;
  let m;
  while ((m = re.exec(text))) {
    const n = (m[2].match(/\[\d{1,2}\]/g) || []).length;
    counts[m[1]] = (counts[m[1]] || 0) + n;
  }
  return counts;
}

// 「狙」判定を /api/nerai から取得し、一覧のバッジへ反映する。
async function loadNerai() {
  const date = state.currentDate;
  const place = state.currentPlace;
  const reqId = `${date}|${place}`;
  state.neraiReq = reqId;
  if (!date || state.demo) { state.nerai = {}; return; }
  try {
    const params = new URLSearchParams({ date });
    if (place) params.set("place_code", place);
    const data = await apiGet(`/api/nerai?${params.toString()}`);
    if (state.neraiReq !== reqId) return; // 開催が切り替わっていたら破棄
    state.nerai = data.nerai || {};
    if (state.view === "list") renderRaceBoard();
  } catch {
    if (state.neraiReq === reqId) state.nerai = {};
  }
}

function renderAllPanels() {
  renderEval();
  renderSection(els.panelPace, state.parsed?.paceEl, "展開");
  renderOdds();
  renderSection(els.panelIndex, state.parsed?.indexEl, "相対評価");
  renderAi();
  renderSection(els.panelMatch, state.parsed?.matchEl, "対戦表");
  renderMemo();
}

// 南関公式の単勝・複勝オッズをサーバー関数(/api/odds)経由で取得。人気は単勝昇順で導出。
async function loadOdds() {
  const race = state.raceDetail?.race;
  const raceId = race?.race_id ? String(race.race_id) : "";
  if (!/^\d{16}$/.test(raceId) || state.demo) {
    state.odds = state.demo ? { horses: demoOdds() } : null;
    renderOdds();
    setStatus(summaryLine());
    return;
  }
  state.oddsLoading = true;
  setStatus(summaryLine());
  renderOdds();
  try {
    const data = await apiGet(`/api/odds?race_id=${encodeURIComponent(raceId)}`);
    state.odds = data;
  } catch (err) {
    state.odds = { error: err.message };
  } finally {
    state.oddsLoading = false;
    renderOdds();
    setStatus(summaryLine());
  }
}

function demoOdds() {
  return [
    { umaban: 1, name: "サンプルスター", tanshou: 3.2, fukushou: "1.3-1.9", ninki: 2 },
    { umaban: 2, name: "ミナミノライト", tanshou: 2.1, fukushou: "1.1-1.4", ninki: 1 },
    { umaban: 3, name: "カワサキロード", tanshou: 8.4, fukushou: "2.1-3.6", ninki: 3 },
    { umaban: 4, name: "ウラワノカゼ", tanshou: 21.0, fukushou: "3.9-6.8", ninki: 4 },
  ];
}

function renderOdds() {
  const panel = els.panelOdds;
  if (!panel) return;
  if (!state.raceDetail?.race) {
    renderEmpty(panel, "オッズなし", "レースを選んでください。");
    return;
  }
  if (state.oddsLoading) {
    renderLoading(panel, "オッズを取得中");
    return;
  }
  const data = state.odds;
  if (!data || data.error) {
    renderEmpty(panel, "オッズ取得不可", data?.error || "発売前、または取得に失敗しました。↻で再取得できます。");
    return;
  }
  const horses = oddsRowsWithContext(data);
  if (!horses.length) {
    renderEmpty(panel, "オッズなし", "オッズ表を取得できませんでした。");
    return;
  }
  // 並び替え: 馬番=昇順 / 単勝=低い順(人気順、欠損は末尾)。
  const sortKey = state.oddsSort === "odds" ? "odds" : "umaban";
  horses.sort((a, b) => {
    if (sortKey === "odds") {
      const ao = a.tanshou == null ? Infinity : a.tanshou;
      const bo = b.tanshou == null ? Infinity : b.tanshou;
      if (ao !== bo) return ao - bo;
      return a.umaban - b.umaban;
    }
    return a.umaban - b.umaban;
  });
  const fetched = data.fetched_at ? shortDateTime(data.fetched_at) : "";
  const rows = horses
    .map((h) => {
      const tan = h.tanshou != null ? `${h.tanshou.toFixed(1)}` : "－";
      const nin = h.ninki ? `${h.ninki}` : "－";
      const hot = h.tanshou != null && h.tanshou < 10 ? " odds-fav" : "";
      const tierChip = h.tier
        ? `<span class="odds-tier tier-${tierClass(h.tier)}">${escapeHtml(h.tier)}</span>`
        : "";
      return `<tr class="${hot.trim()}">
        <td class="odds-uma">${escapeHtml(String(h.umaban))}</td>
        <td class="odds-name"><span class="odds-name-txt">${escapeHtml(h.name || "")}</span>${tierChip}</td>
        <td class="odds-tan">${escapeHtml(tan)}</td>
        <td class="odds-nin">${escapeHtml(nin)}<span class="odds-nin-suffix">人気</span></td>
        <td class="odds-fuku">${escapeHtml(h.fukushou || "－")}</td>
      </tr>`;
    })
    .join("");
  const umaSort = sortKey === "umaban" ? " is-sorted" : "";
  const oddsSort = sortKey === "odds" ? " is-sorted" : "";
  panel.innerHTML = `
    <div class="odds-wrap">
      <div class="odds-head">
        <strong>単勝・複勝オッズ</strong>
        <span class="odds-meta">${fetched ? `${escapeHtml(fetched)} 時点` : ""}<button class="odds-refresh" type="button" id="oddsRefreshBtn" title="オッズ再取得">↻</button></span>
      </div>
      <table class="odds-table">
        <thead><tr>
          <th class="odds-sortable${umaSort}" data-sort="umaban" role="button" tabindex="0" title="馬番順に並び替え">馬番${umaSort ? " ▲" : ""}</th>
          <th class="odds-name">馬名 / 評価</th>
          <th class="odds-sortable${oddsSort}" data-sort="odds" role="button" tabindex="0" title="単勝オッズ(人気)順に並び替え">単勝${oddsSort ? " ▲" : ""}</th>
          <th>人気</th>
          <th>複勝</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="odds-note">南関公式の暫定オッズ（発売中は変動）。単勝10倍未満を強調。見出しの馬番/単勝で並び替え。↻またはレース再選択で更新。</p>
    </div>`;
  const btn = panel.querySelector("#oddsRefreshBtn");
  if (btn) btn.addEventListener("click", () => void loadOdds());
  for (const th of panel.querySelectorAll(".odds-sortable")) {
    const apply = () => { state.oddsSort = th.dataset.sort; renderOdds(); };
    th.addEventListener("click", apply);
    th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); apply(); } });
  }
}

function oddsRowsWithContext(data = state.odds) {
  return ((data && !data.error ? data.horses : []) || []).map((horse) => ({
    ...horse,
    tier: oddsTierForHorse(horse),
  }));
}

// オッズ表示用: 馬番/馬名から総合評価tierを引く。
function oddsTierForHorse(horse) {
  const byUma = new Map();
  const byName = new Map();
  const horses = state.raceDetail?.horses || [];
  for (const h of horses) {
    if (h.umaban != null && h.tier) byUma.set(Number(h.umaban), h.tier);
    if (h.name && h.tier) byName.set(normName(h.name), h.tier);
  }
  for (const [name, tier] of Object.entries(state.raceDetail?.race?.grades || {})) {
    if (name && tier) byName.set(normName(name), tier);
  }
  const umaKey = Number(horse?.umaban);
  if (Number.isFinite(umaKey) && byUma.has(umaKey)) return byUma.get(umaKey);
  return byName.get(normName(horse?.name)) || "";
}

function tierClass(tier) {
  const rank = String(tier || "").trim().slice(0, 1).toUpperCase();
  if (["S", "A", "B", "C", "D", "E"].includes(rank)) return rank.toLowerCase();
  return "low";
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
  if (!state.raceDetail.pageLoaded) {
    renderEmpty(panel, `${label}読込待ち`, "評価一覧を先に表示しています。詳細はこのまま読み込みます。");
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
  if (!state.raceDetail.pageLoaded) {
    renderEmpty(panel, "出走馬分析読込待ち", "評価一覧を先に表示しています。詳細はこのまま読み込みます。");
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
    renderRaceBoard();

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
  void ensureFullRaceForActiveTab();
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
    pageLoaded: true,
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
