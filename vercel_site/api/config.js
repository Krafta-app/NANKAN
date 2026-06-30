const {
  getConfig,
  handleOptions,
  PATTERN_DIMS,
  PATTERN_MARKS,
  PLACE_BY_CODE,
  sendJson,
} = require("./_supabase");

module.exports = async function handler(req, res) {
  if (handleOptions(req, res)) return;
  const cfg = getConfig();
  sendJson(res, 200, {
    ok: true,
    configured: cfg.configured,
    supabaseProjectRef: cfg.projectRef,
    supabaseKeyRole: cfg.keyInfo.role,
    supabaseKeyRef: cfg.keyInfo.ref,
    memoEnabled: cfg.configured && cfg.usingServiceRole,
    memoAuthRequired: Boolean(cfg.pin),
    placeByCode: PLACE_BY_CODE,
    patternDims: PATTERN_DIMS,
    patternMarks: PATTERN_MARKS,
  });
};
