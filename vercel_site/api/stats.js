const {
  compactRace,
  handleOptions,
  postgrestIn,
  requireSupabase,
  sendError,
  sendJson,
  supabaseFetch,
} = require("./_supabase");

const TIER_ORDER = ["S", "A", "B", "C", "D", "E", "F", "G", "主力", "一軍", "二軍", "三軍", "不明"];

function bucket(stats, tier) {
  if (!stats[tier]) stats[tier] = { n: 0, win: 0, ren: 0, fuku: 0 };
  return stats[tier];
}

module.exports = async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "GET") return sendError(res, 405, "GETだけ対応しています");
  if (!requireSupabase(res)) return;

  try {
    const query = {
      select: "*",
      has_result: "eq.1",
      grades_json: "not.is.null",
      order: "date.desc,place_code.asc,race_num.asc",
      limit: "500",
    };
    if (req.query.date) query.date = `eq.${req.query.date}`;
    if (req.query.place_code) query.place_code = `eq.${req.query.place_code}`;

    const races = (await supabaseFetch("races", { query })).map(compactRace);
    const raceKeys = races.map((race) => race.race_key);
    const results = raceKeys.length
      ? await supabaseFetch("race_results", {
          query: {
            select: "race_key,horse_name,finish_rank,popularity",
            race_key: postgrestIn(raceKeys),
          },
        })
      : [];

    const resultByRace = new Map();
    for (const row of results || []) {
      if (!resultByRace.has(row.race_key)) resultByRace.set(row.race_key, []);
      resultByRace.get(row.race_key).push(row);
    }

    const stats = {};
    const rows = [];

    for (const race of races) {
      const rankByName = new Map();
      for (const result of resultByRace.get(race.race_key) || []) {
        if (result.horse_name) rankByName.set(result.horse_name.replace(/\s/g, ""), result.finish_rank);
      }

      let winner = "";
      let saHit = false;
      let saWin = false;
      for (const [horseName, tier] of Object.entries(race.grades || {})) {
        const rank = rankByName.get(String(horseName).replace(/\s/g, ""));
        if (!rank) continue;
        if (rank === 1) winner = horseName;
        const item = bucket(stats, tier || "不明");
        item.n += 1;
        if (rank === 1) item.win += 1;
        if (rank <= 2) item.ren += 1;
        if (rank <= 3) item.fuku += 1;
        if (["S", "A", "主力", "一軍"].includes(tier) && rank <= 3) saHit = true;
        if (["S", "A", "主力", "一軍"].includes(tier) && rank === 1) saWin = true;
      }

      rows.push({
        race_key: race.race_key,
        date: race.date,
        place_name: race.place_name,
        race_num: race.race_num,
        race_name: race.race_name,
        winner,
        hit_label: saWin ? "勝ち" : saHit ? "複勝圏" : "外れ",
      });
    }

    const orderedStats = {};
    for (const tier of TIER_ORDER) {
      if (stats[tier]) orderedStats[tier] = stats[tier];
    }
    for (const [tier, value] of Object.entries(stats)) {
      if (!orderedStats[tier]) orderedStats[tier] = value;
    }

    sendJson(res, 200, { ok: true, stats: orderedStats, races: rows });
  } catch (err) {
    sendError(res, err.status || 500, "成績の読み込みに失敗しました", err.message);
  }
};
