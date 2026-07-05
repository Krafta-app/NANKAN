const {
  handleOptions,
  parseJsonField,
  postgrestIn,
  requireSupabase,
  sendError,
  sendJson,
  supabaseFetch,
} = require("./_supabase");

// 「狙」＝力関係がはっきりしていて予想しやすいレース。対戦表(data_html #tab-match)を読み、
// 次のいずれかに該当する「今回の出走馬」を数え、延べ頭数(重複除く)が出走頭数の
// NERAI_RATIO 以上なら狙いのレースとする。
//  (1) 今回と同じ競馬場での対戦成績がある馬（同距離を含む。過去レース見出しに今回の場名）
//  (2) 評価ランク S/A 同士の直接対決に絡む馬（同一過去レースに現 S/A が2頭以上／場所は不問）
// 閾値は調整可能な定数。JRAは固定6頭だったが、南関は頭数が読める一覧なので割合で判定する。
const NERAI_RATIO = 0.7;
const NERAI_MIN_HORSES = 2; // 極端に少頭数のレースで割合だけで付かないようにする最低頭数

module.exports = async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "GET") return sendError(res, 405, "GETだけ対応しています");
  if (!requireSupabase(res)) return;

  const date = String(req.query.date || "");
  if (!/^\d{8}$/.test(date)) return sendError(res, 400, "date(YYYYMMDD)が必要です");

  try {
    const raceQuery = {
      select: "race_key,place_name,place_code,grades_json,uma_ids_json",
      date: `eq.${date}`,
      order: "place_code.asc,race_num.asc",
      limit: "500",
    };
    if (req.query.place_code) raceQuery.place_code = `eq.${req.query.place_code}`;
    const raceRows = (await supabaseFetch("races", { query: raceQuery })) || [];
    if (!raceRows.length) return sendJson(res, 200, { ok: true, nerai: {} });

    const raceKeys = raceRows.map((row) => row.race_key).filter(Boolean);
    const pageRows = raceKeys.length
      ? await supabaseFetch("race_pages", {
          query: {
            select: "race_key,data_html",
            race_key: postgrestIn(raceKeys),
            limit: "500",
          },
        }).catch(() => [])
      : [];
    const htmlByKey = new Map((pageRows || []).map((row) => [row.race_key, row.data_html || ""]));

    const nerai = {};
    for (const row of raceRows) {
      const grades = parseJsonField(row.grades_json, {});
      const umaIds = parseJsonField(row.uma_ids_json, {});
      const field = Object.keys(umaIds).length || Object.keys(grades).length;
      const html = htmlByKey.get(row.race_key) || "";
      nerai[row.race_key] = computeNerai(html, row.place_name || "", field);
    }
    sendJson(res, 200, { ok: true, nerai });
  } catch (err) {
    sendError(res, err.status || 500, "狙い判定の計算に失敗しました", err.message);
  }
};

// data_html の #tab-match セクションを解析し、狙い判定に必要な情報を返す。
function computeNerai(dataHtml, placeName, field) {
  const base = { isTarget: false, count: 0, field: field || 0, threshold: 0 };
  if (!field) return base;
  const threshold = Math.max(NERAI_MIN_HORSES, Math.ceil(field * NERAI_RATIO));
  base.threshold = threshold;

  const start = dataHtml.indexOf('id="tab-match"');
  if (start < 0) return base;
  let seg = dataHtml.slice(start);
  const next = seg.indexOf('id="tab-', 8);
  if (next > 0) seg = seg.slice(0, next);

  const qualified = new Set();
  // ブロックは ❗️(同場同距離) または ◆(それ以外) で始まる。次のマーカー/終端まで1ブロック。
  const blockRe = /(?:❗️|◆)([\s\S]*?)(?=❗️|◆|<\/pre>|$)/g;
  let m;
  while ((m = blockRe.exec(seg))) {
    const block = m[1] || "";
    const nl = block.indexOf("\n");
    const header = (nl >= 0 ? block.slice(0, nl) : block).replace(/<[^>]+>/g, " ");
    const samePlace = placeName && header.includes(placeName);

    // 1エントリ = " / " 区切り。data-uma を持つ(=今回の出走馬)エントリのみ拾う。
    const entries = [];
    for (const part of block.split(/\s\/\s/)) {
      const um = part.match(/data-uma="(\d+)"/);
      if (!um) continue;
      const rk = part.match(/rank-([SABCDEFG])/);
      entries.push({ uma: um[1], rank: rk ? rk[1] : "" });
    }
    if (!entries.length) continue;
    // (1) 今回と同じ競馬場での対戦
    if (samePlace) for (const en of entries) qualified.add(en.uma);
    // (2) S/A同士の直接対決
    const sa = entries.filter((en) => en.rank === "S" || en.rank === "A");
    if (sa.length >= 2) for (const en of sa) qualified.add(en.uma);
  }

  const count = qualified.size;
  return { isTarget: count >= threshold && count > 0, count, field, threshold };
}
