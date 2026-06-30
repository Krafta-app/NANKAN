const {
  compactRace,
  handleOptions,
  requireSupabase,
  sendError,
  sendJson,
  supabaseFetch,
} = require("./_supabase");

module.exports = async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "GET") return sendError(res, 405, "GETだけ対応しています");
  if (!requireSupabase(res)) return;

  try {
    const query = {
      select:
        "race_key,date,place_code,place_name,race_num,race_id,course,dist,post_time,race_name,grades_json,uma_ids_json,eval_list_text,generated_at,has_result",
      order: "date.desc,place_code.asc,race_num.asc",
      limit: "500",
    };
    if (req.query.date) query.date = `eq.${req.query.date}`;
    if (req.query.place_code) query.place_code = `eq.${req.query.place_code}`;
    if (req.query.only_with_result === "1") query.has_result = "eq.1";

    const rows = await supabaseFetch("races", { query });
    const races = (rows || []).map(compactRace);
    const dates = [...new Set(races.map((race) => race.date).filter(Boolean))];
    const places = [
      ...new Map(
        races
          .filter((race) => race.place_code)
          .map((race) => [race.place_code, race.place_name]),
      ).entries(),
    ].map(([place_code, place_name]) => ({ place_code, place_name }));

    sendJson(res, 200, {
      ok: true,
      supabaseProjectRef: require("./_supabase").getConfig().projectRef,
      races,
      dates,
      places,
      latestDate: dates[0] || null,
    });
  } catch (err) {
    sendError(res, err.status || 500, "レース一覧の読み込みに失敗しました", err.message);
  }
};
