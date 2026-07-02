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
  oddsSort: "umaban",
  raceTypeForecast: null,
  demo: new URLSearchParams(location.search).has("demo"),
};

const els = {};
const PIN_KEY = "nankan_site_pin";

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
    "panelPace", "panelOdds", "panelIndex", "panelAi", "panelMatch", "panelMemo",
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

  state.odds = null;
  state.raceTypeForecast = null;
  try {
    state.raceDetail = state.demo ? demoRace(raceKey) : await apiGet(`/api/race?race_key=${encodeURIComponent(raceKey)}`);
    state.parsed = parsePrediction(state.raceDetail);
    renderAllPanels();
    setStatus(summaryLine());
    void loadOdds();
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
    updateRaceTypeForecast();
    renderOdds();
    setStatus(summaryLine());
    return;
  }
  state.oddsLoading = true;
  state.raceTypeForecast = null;
  setStatus(summaryLine());
  renderOdds();
  try {
    const data = await apiGet(`/api/odds?race_id=${encodeURIComponent(raceId)}`);
    state.odds = data;
  } catch (err) {
    state.odds = { error: err.message };
    state.raceTypeForecast = null;
  } finally {
    state.oddsLoading = false;
    updateRaceTypeForecast();
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
      const hot = h.ninki && h.ninki <= 3 ? " odds-fav" : "";
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
  const forecast = araBannerHtml(horses);
  panel.innerHTML = `
    <div class="odds-wrap">
      ${forecast}
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
      <p class="odds-note">南関公式の暫定オッズ（発売中は変動）。見出しの馬番/単勝で並び替え。↻またはレース再選択で更新。</p>
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
    ...resultContextForHorse(horse),
  }));
}

function updateRaceTypeForecast() {
  state.raceTypeForecast = computeAraForecast(oddsRowsWithContext());
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

function resultContextForHorse(horse) {
  const byUma = new Map();
  const byName = new Map();
  for (const row of [...(state.raceDetail?.horses || []), ...(state.raceDetail?.results || [])]) {
    const ctx = {
      finish_rank: toNumber(row.finish_rank),
      result_popularity: toNumber(row.popularity),
      time_diff: row.time_diff,
    };
    if (row.umaban != null) byUma.set(Number(row.umaban), ctx);
    if (row.name || row.horse_name) byName.set(normName(row.name || row.horse_name), ctx);
  }
  const umaKey = Number(horse?.umaban);
  if (Number.isFinite(umaKey) && byUma.has(umaKey)) return byUma.get(umaKey);
  return byName.get(normName(horse?.name)) || {};
}

function tierClass(tier) {
  if (["S", "A"].includes(tier)) return "sa";
  if (tier === "B") return "b";
  if (tier === "C") return "c";
  return "low";
}

// ===== レース堅さ/荒れ具合 10パターン判定 =====
// 総合評価のランク構成、1人気単勝、10倍以内頭数、市場エントロピー、AIと市場のズレを同じ0-100指標へ圧縮する。
const ARA_TIERS = ["S", "A", "B", "C", "D", "E", "F", "G", "無"];
const ARA_STRONG_TIERS = new Set(["S", "A", "主力", "一軍"]);
const ARA_PATTERN_TONES = {
  "軸不動": "secure",
  "人気決着": "cool",
  "順当": "cool",
  "軸信頼・紐荒れ": "flat",
  "上位拮抗": "flat",
  "混戦": "flat",
  "波乱含み": "warn",
  "穴警戒": "hot",
  "軸危険": "hot",
  "大荒れ": "danger",
};

function toNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function clamp(value, min = 0, max = 100) {
  return Math.max(min, Math.min(max, value));
}

function entropy(values) {
  const sum = values.reduce((a, b) => a + b, 0);
  if (!sum) return 0;
  return values.reduce((acc, value) => {
    if (!value) return acc;
    const p = value / sum;
    return acc - p * Math.log2(p);
  }, 0);
}

function normalizedEntropy(values) {
  const active = values.filter((value) => value > 0).length;
  if (active <= 1) return 0;
  return entropy(values) / Math.log2(active);
}

function canonicalTier(tier) {
  if (ARA_STRONG_TIERS.has(tier)) return tier === "S" || tier === "主力" ? "S" : "A";
  if (["B", "二軍"].includes(tier)) return "B";
  if (["C", "三軍"].includes(tier)) return "C";
  if (["D", "E", "F", "G"].includes(tier)) return tier;
  return "無";
}

function tierPower(tier) {
  const t = canonicalTier(tier);
  if (t === "S") return 5;
  if (t === "A") return 4;
  if (t === "B") return 3;
  if (t === "C") return 2;
  if (["D", "E", "F", "G"].includes(t)) return 1;
  return 0;
}

function tierCounts(rows) {
  const counts = Object.fromEntries(ARA_TIERS.map((tier) => [tier, 0]));
  for (const row of rows) counts[canonicalTier(row.tier)] += 1;
  return counts;
}

function tierCountText(counts) {
  return ARA_TIERS
    .filter((tier) => counts[tier])
    .map((tier) => `${tier}${counts[tier]}`)
    .join(" ");
}

function computeAraForecast(rows) {
  const withOdds = rows.filter((r) => r.tanshou != null && r.tanshou > 0);
  const odds = withOdds.map((r) => r.tanshou).slice().sort((a, b) => a - b);
  const field = rows.length;
  if (odds.length < 4 || field < 4) return null;

  const counts = tierCounts(rows);
  const nS = counts.S || 0;
  const nA = counts.A || 0;
  const nSA = nS + nA;
  const nTop = nSA + (counts.B || 0);
  const nLow = (counts.D || 0) + (counts.E || 0) + (counts.F || 0) + (counts.G || 0) + (counts["無"] || 0);
  const tierEntropy = normalizedEntropy(ARA_TIERS.map((tier) => counts[tier] || 0));

  const inv = odds.map((o) => 1 / o);
  const tt = inv.reduce((a, b) => a + b, 0);
  const marketEntropy = normalizedEntropy(inv.map((x) => x / tt));
  const favOdds = odds[0];
  const favRow = withOdds.reduce((a, b) => (a.tanshou <= b.tanshou ? a : b));
  const favUnder2 = favOdds < 2.0;
  const favPower = tierPower(favRow.tier);
  const favAxis = favPower >= 4;
  const favWeak = favPower > 0 && favPower < 4;
  const nSub10 = odds.filter((o) => o <= 10).length;
  const topMarketSA = withOdds.filter((r) => r.ninki && r.ninki <= 3 && tierPower(r.tier) >= 4).length;
  const saUnpopular = withOdds.filter((r) => tierPower(r.tier) >= 4 && (!r.ninki || r.ninki >= 5 || r.tanshou > 10)).length;
  const marketSaBoost = topMarketSA >= 2 ? 16 : topMarketSA === 1 && favAxis ? 8 : 0;
  const roughDamp = topMarketSA >= 2 ? 20 : topMarketSA === 1 && favAxis ? 9 : 0;

  const aiConcentration = clamp(
    (nSA === 0 ? 0 : nSA === 1 ? 78 : nSA === 2 ? 64 : nSA === 3 ? 52 : topMarketSA >= 2 ? 44 : 32)
      + (nS === 1 ? 14 : 0)
      - tierEntropy * 14
      - Math.max(0, nTop - 5) * 3
      + marketSaBoost,
  );
  const marketClarity = clamp(
    100
      - marketEntropy * 48
      - Math.max(0, nSub10 - 2) * 8
      - Math.max(0, favOdds - 1.6) * 12
      + (favUnder2 ? 12 : 0)
      + marketSaBoost * 0.35,
  );
  const favAlign = favAxis ? 100 : favPower === 3 ? 64 : favPower === 2 ? 42 : favPower === 1 ? 25 : 48;
  const conflictScore = clamp(
    (favWeak ? 35 : 0)
      + (nSA > 0 && topMarketSA === 0 ? 25 : 0)
      + Math.min(28, saUnpopular * 12)
      + (favUnder2 && !favAxis ? 15 : 0)
      - (topMarketSA >= 2 ? 18 : 0),
  );
  const axisScore = clamp(
    0.42 * aiConcentration
      + 0.34 * marketClarity
      + 0.24 * favAlign
      - (nSA === 0 ? 18 : 0)
      - (conflictScore >= 45 ? 8 : 0),
  );
  const roughScore = clamp(
    marketEntropy * 34
      + (nSub10 / withOdds.length) * 24
      + Math.max(0, favOdds - 1.8) * 10
      + tierEntropy * 14
      + (nLow / field) * 12
      + conflictScore * 0.42
      + Math.max(0, field - 10) * 2
      - (axisScore >= 78 ? 16 : 0)
      - (favUnder2 && favAxis ? 8 : 0)
      - roughDamp,
  );

  const label = classifyAraPattern({
    axisScore, roughScore, favOdds, favUnder2, favAxis, favWeak, nSA, nTop, nSub10, conflictScore, topMarketSA,
  });
  const tone = ARA_PATTERN_TONES[label] || "flat";
  const reasons = [];
  reasons.push(tierCountText(counts));
  if (favUnder2) reasons.push(`1人気${favOdds.toFixed(1)}倍(<2.0)`);
  else if (favOdds >= 3.0) reasons.push(`1人気${favOdds.toFixed(1)}倍と混戦`);
  else reasons.push(`1人気${favOdds.toFixed(1)}倍`);
  reasons.push(`単勝10倍以内${nSub10}頭`);
  reasons.push(favAxis ? "1人気がS/A" : "1人気とAI上位にズレ");
  if (topMarketSA >= 2) reasons.push(`人気上位S/A${topMarketSA}頭`);
  if (saUnpopular) reasons.push(`S/A人気薄${saUnpopular}頭`);

  return {
    label,
    tone,
    axisScore: Math.round(axisScore),
    roughScore: Math.round(roughScore),
    counts,
    nSub10,
    favOdds,
    favUnder2,
    reason: reasons.filter(Boolean).join(" / "),
    actual: computeActualAra(rows, label),
  };
}

function classifyAraPattern(f) {
  if (f.topMarketSA >= 2 && f.axisScore >= 64 && f.nSub10 <= 4) return f.favUnder2 ? "軸不動" : "人気決着";
  if (f.topMarketSA >= 2 && f.axisScore >= 56 && f.nSub10 <= 5) return "順当";
  if (f.roughScore >= 82 && f.axisScore < 42 && f.topMarketSA === 0) return "大荒れ";
  if (f.axisScore >= 82 && f.favUnder2 && f.favAxis && f.nSub10 <= 3) return "軸不動";
  if (f.conflictScore >= 58 && f.topMarketSA === 0 && (f.favUnder2 || f.nSA >= 1)) return "軸危険";
  if (f.axisScore >= 72 && f.favUnder2 && f.nSub10 <= 4) return "人気決着";
  if (f.axisScore >= 66 && f.nSA <= 2 && f.nSub10 >= 5) return "軸信頼・紐荒れ";
  if (f.roughScore >= 76 && f.topMarketSA <= 1) return "穴警戒";
  if (f.nSA >= 3 && f.roughScore >= 48 && f.favOdds < 3.0) return "上位拮抗";
  if (f.favOdds >= 3.0 || f.nSub10 >= 6) return "混戦";
  if (f.roughScore >= 58) return "波乱含み";
  return "順当";
}

function computeActualAra(rows, predictedLabel) {
  const finished = rows
    .filter((r) => r.finish_rank != null)
    .slice()
    .sort((a, b) => a.finish_rank - b.finish_rank);
  if (finished.length < 3) return null;

  const top3 = finished.slice(0, 3);
  const winner = top3[0];
  const popOf = (row) => row.result_popularity || row.ninki || null;
  const top3Pops = top3.map(popOf).filter((v) => v != null);
  const winnerPop = popOf(winner) || 99;
  const maxTop3Pop = top3Pops.length ? Math.max(...top3Pops) : 99;
  const top3FavCount = top3Pops.filter((pop) => pop <= 3).length;
  const top3MidCount = top3Pops.filter((pop) => pop <= 5).length;
  const saRows = rows.filter((row) => tierPower(row.tier) >= 4);
  const saWin = saRows.some((row) => row.finish_rank === 1);
  const saHit = saRows.some((row) => row.finish_rank != null && row.finish_rank <= 3);
  const favorite = rows
    .filter((row) => row.ninki || row.tanshou)
    .slice()
    .sort((a, b) => (a.ninki || 99) - (b.ninki || 99) || (a.tanshou || 99) - (b.tanshou || 99))[0];
  const favoriteRank = favorite?.finish_rank || null;

  let label;
  if (winnerPop >= 7 && maxTop3Pop >= 8) label = "大荒れ";
  else if (saWin && favoriteRank && favoriteRank <= 3 && winnerPop <= 3 && top3FavCount >= 2) label = "軸不動";
  else if (top3FavCount === 3 || (winnerPop <= 2 && top3MidCount === 3)) label = "人気決着";
  else if (saHit && maxTop3Pop >= 7) label = "軸信頼・紐荒れ";
  else if (saRows.length && !saHit) label = "軸危険";
  else if (winnerPop >= 5 || maxTop3Pop >= 8) label = "穴警戒";
  else if (top3MidCount >= 2 && saHit) label = "順当";
  else if (winnerPop >= 4 || maxTop3Pop >= 6) label = "波乱含み";
  else label = "上位拮抗";

  const axisLabel = !saRows.length ? "S/A軸なし" : saWin ? "S/A勝利" : saHit ? "S/A複勝圏" : "S/A圏外";
  const predictedBand = araBand(predictedLabel);
  const actualBand = araBand(label);
  const diff = Math.abs(predictedBand - actualBand);
  const fit = diff === 0 ? "堅さ一致" : diff === 1 ? "方向は近い" : "要見直し";
  const top3Text = top3
    .map((row) => `[${row.umaban}]${popOf(row) || "-"}人気${row.tier ? ` ${canonicalTier(row.tier)}` : ""}`)
    .join(" / ");
  return { label, axisLabel, fit, top3Text };
}

function araBand(label) {
  if (["軸不動", "人気決着", "順当"].includes(label)) return 0;
  if (["軸信頼・紐荒れ", "上位拮抗", "混戦", "波乱含み"].includes(label)) return 1;
  return 2;
}

function araBannerHtml(rows) {
  const f = computeAraForecast(rows);
  if (!f) return "";
  const actual = f.actual
    ? `<div class="ara-result"><b>結果検証</b><span>実際：${escapeHtml(f.actual.label)}</span><span>${escapeHtml(f.actual.axisLabel)}</span><span>${escapeHtml(f.actual.fit)}</span><small>${escapeHtml(f.actual.top3Text)}</small></div>`
    : "";
  return `<div class="ara-banner ${f.tone}">
    <div class="ara-main"><span class="ara-label">レース型予報：${escapeHtml(f.label)}</span></div>
    <div class="ara-metrics">
      <span class="ara-pill">荒れ指数 <b>${f.roughScore}</b></span>
      <span class="ara-pill">軸信頼 <b>${f.axisScore}</b></span>
    </div>
    <div class="ara-sub"><span class="ara-reason">${escapeHtml(f.reason)}</span></div>
    ${actual}
  </div>`;
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
  const forecast = state.raceTypeForecast;
  if (forecast) parts.push(`レース型:${forecast.label} 荒${forecast.roughScore} 軸${forecast.axisScore}`);
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
