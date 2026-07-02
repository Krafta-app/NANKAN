// 南関公式の単勝・複勝オッズ（静的HTML /odds/{race_id}01.do）をサーバー側で取得して返す。
// ブラウザから直接叩くとCORSで弾かれるため、この関数を経由する。
// 人気はオッズ列に無いので単勝オッズ昇順で導出する。
const { handleOptions, sendJson, sendError } = require("./_supabase");

const ODDS_ROW = new RegExp(
  "<td>(\\d+)</td>\\s*" +                          // 馬番
  "<td[^>]*>\\s*<a href=\"/uma_info/(\\d+)\\.do\">([^<]+)</a>\\s*</td>\\s*" + // uma_id, 馬名
  "<td[^>]*>\\s*([0-9.]+|[-\\u2013]+)\\s*</td>\\s*" +   // 単勝
  "<td[^>]*>\\s*([0-9.]+-[0-9.]+|[-\\u2013]+)\\s*</td>", // 複勝(下-上)
  "g"
);

function parseOdds(rawHtml) {
  // 人気馬のオッズは <span class="...color..">3.3</span> で色付けされるので、
  // 数値抽出前にインラインのspanタグを剥がす。
  const html = rawHtml.replace(/<\/?span[^>]*>/g, "");
  const rows = [];
  let m;
  ODDS_ROW.lastIndex = 0;
  while ((m = ODDS_ROW.exec(html)) !== null) {
    const [, umaban, umaId, name, tanRaw, fukuRaw] = m;
    const tan = /^[0-9.]+$/.test(tanRaw) ? Number(tanRaw) : null;
    rows.push({
      umaban: Number(umaban),
      uma_id: umaId,
      name: name.trim(),
      tanshou: tan,
      fukushou: /[0-9]/.test(fukuRaw) ? fukuRaw.trim() : null,
      ninki: null,
    });
  }
  // 単勝オッズ昇順で人気を付与（同オッズは同順位、欠損は末尾）。
  const ranked = rows.filter((r) => r.tanshou != null).sort((a, b) => a.tanshou - b.tanshou);
  let prev = null, rank = 0;
  ranked.forEach((r, i) => {
    if (prev === null || r.tanshou !== prev) rank = i + 1;
    r.ninki = rank;
    prev = r.tanshou;
  });
  return rows;
}

module.exports = async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "GET") return sendError(res, 405, "GETだけ対応しています");

  const raceId = String(req.query.race_id || "").trim();
  if (!/^\d{16}$/.test(raceId)) return sendError(res, 400, "race_id(16桁)が必要です");

  const url = `https://www.nankankeiba.com/odds/${raceId}01.do`;
  try {
    const resp = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0 (compatible; nankan-ai-site)" },
    });
    if (!resp.ok) return sendError(res, 502, `オッズ取得失敗 (${resp.status})`);
    const buf = await resp.arrayBuffer();
    const html = new TextDecoder("shift_jis").decode(buf);
    const horses = parseOdds(html);
    if (!horses.length) return sendError(res, 404, "オッズ表を解析できません（発売前の可能性）");
    const hasOdds = horses.some((h) => h.tanshou != null);
    sendJson(
      res,
      200,
      { ok: true, race_id: raceId, source: url, has_odds: hasOdds, fetched_at: new Date().toISOString(), horses },
      { "Cache-Control": "s-maxage=20, stale-while-revalidate=40" }
    );
  } catch (err) {
    sendError(res, 500, "オッズの読み込みに失敗しました", err.message);
  }
};
