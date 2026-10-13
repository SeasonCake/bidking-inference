export const meta = {
  name: 'ethan-residual-fix-design',
  description: 'Adversarially confirm the generous-q4 residual root cause and design+vet the fix for ethan 3+red under-estimate',
  phases: [
    { title: 'Confirm', detail: '3 independent skeptics try to refute generous-q4 root cause' },
    { title: 'SynthFeas', detail: 'feasibility of new-standard ethan synthetic-sample route (concurrent)' },
    { title: 'Design', detail: '3 distinct fix variants (mid / family-cap / red-floor)' },
    { title: 'Adversarial', detail: 'break each design on 1-2red + 2401/2407 phantom-red' },
    { title: 'Synthesize', detail: 'pick/hybridize winning spec + implementation plan' },
  ],
}

// ---- shared verified context (offline, corrected-round, 99 clean_eval ethan; CELL-level facts vs settlement truth) ----
const CTX = `
REPO: c:/example/bidking-workspace/bidking-lab  (engine: hero_ref/src/ahmad_ref_engine.py ~11k lines)
TASK CONTEXT — ethan (hero_id 208) auction price-estimation. Main line = fix ethan 3+red value UNDER-estimate.

VERIFIED ROOT-CAUSE FINDING (to CONFIRM or REFUTE):
The engine's red-residual LIFT block (ahmad_ref_engine.py lines 10700-10787) estimates red cells as a
RESIDUAL: resid_red = max(0, total_grid_target - known_nonred - q4_generous), where
known_nonred = q1_cells + q3_cells + q5_resolved. The LIFT then prices resid_red at a family per-cell
price and lifts the balanced value IF the residual-implied red value exceeds the combo's red p50
(LIFT-ONLY, never cuts; gated red_lift_target > rv50*value_scale).
- q4 (purple) cells, when only an appraisal VALUE is known (no exact scan), is read GENEROUSLY as
  q4_generous = quality_values["q4"] / _ethan_quality_percell("q4", map)[0]  <-- [0] is the p25 (LOWEST)
  per-cell price => value/p25 = MAX plausible purple cells. Line 10763. Comment (10695) says this makes the
  residual a "conservative LOWER story for red" (more purple -> less red residual).
- MEASURED on 15 gated-non-firing 3+red samples (they PASS the full current gate: q13==2, q4&q5 resolved,
  yet LIFT declines because resid computes ~0): q4 over-claims purple by MEDIAN +4.4 cells (engine q4g vs
  settlement truth q4 cells); q5 (gold) over-claim MEDIAN 0.0 (accurate); RED under-claim MEDIAN -5.1 cells.
  => the intentional "conservatism" OVER-corrected: value/p25 systematically over-estimates purple, which
  directly starves the red residual, causing 3+red under-estimate.
SMOKING GUN rows (map, round, err%, truth_red_cells, resid_red, tg(truth_tot), known_nonred, q4g(truth_q4), q5r(truth_q5)):
  5607 r5  -36%  t_red=10 resid=0   tg=79(79)   known=41  q4g=38.6(t_q4=28)  q5r=16(16)   [q4=value]
  5606 r5  -53%  t_red=5  resid=0   tg=72(72)   known=58.6 q4g=16.8(t_q4=12)  q5r=19.6(16)  [q4=value]
  5606 r4  -33%  t_red=10 resid=0   tg=102(114) known=85  q4g=29.4(t_q4=19)  q5r=18(18)   [q4=value, tg also under-anchored]
  5603 r5  -31%  t_red=16 resid=7.2 tg=84(84)   known=46  q4g=30.8(t_q4=22)  q5r=7(7)     [q4=value]
  5605 r5  -26%  t_red=13 resid=13  tg=108(108) known=56  q4g=39(t_q4=39)    q5r=33(33)   [q4=EXACT -> q4 accurate, resid OK]
  5611 r5  +21%  t_red=10 resid=10  tg=110(110) known=74  q4g=26(t_q4=26)    q5r=21(21)   [q4=EXACT -> accurate]
  => rows with q4=EXACT have ZERO over-claim; ONLY q4=value (generous p25) over-claims. Strong causal signal.

CONSTRAINTS THE FIX MUST RESPECT (both are hard):
1) 1-2red band is ALREADY +34.7% OVER-estimate, and it is NOT caused by LIFT (only 1/24 fired). So a fix
   that raises the red residual must NOT spill into 1-2red (would worsen the +34.7%). The two bands need
   OPPOSITE corrections.
2) memory warns: cutting red / over-lifting red on low-red maps regressed the author's live games 2401 &
   2407 (villa family, phantom-red). Any change that increases red residual broadly risks re-introducing
   phantom red on low-red villa/hidden maps.

RELEVANT ENGINE CONSTANTS (ahmad_ref_engine.py ~1575-1632):
- ETHAN_QUALITY_PERCELL_BY_FAMILY["q4"]["map_56xx"] = (2092 p25, 2330 mid, 2601 p75)  # per-cell purple PRICE
    value/p25 = MAX cells (current), value/mid = central, value/p75 = MIN cells
- ETHAN_RED_CELL_FRAC_BY_FAMILY = {hidden:0.161, map_56xx:0.115, shipwreck:0.125, villa:0.083}  # red cells / total
- Calibrated (this session, scripts/_tmp_ethan_q45_prior_calib.py): 56xx purple(q4) occupies ~0.291 of total
  grid, gold(q5) ~0.145 (settlement truth medians; villa/hidden data MISSING).
- ETHAN_RESID_RED_PERCELL_BY_FAMILY (red $/cell): {hidden:46791, map_56xx:46864, shipwreck:43513, villa:46627}

DATA FILE (per-sample, 99 rows): scripts/_tmp_ethan_lift_coverage.json  fields include:
  map, round, tred (truth red COUNT), tred_cells, tq4_cells, tq5_cells, ttot_cells, tval, bal, err,
  fired (LIFT fired?), gate_pre, have_q13, q4/q5 (resolution source: exact|value|avg_cells|avg_values_only|none),
  ri_tg, ri_known, ri_q4g, ri_q5r, ri_resid, ri_lift_target, ri_fam.
CAVEAT: this is OFFLINE reconstruction (round-corrected 747f1bb). It UNDER-reconstructs low-scan q1/q3 for
some samples (q13<2=29 is partly an offline artifact; live players scanned). But CELL-level q4/q5/red
truth comparisons above are vs settlement ground truth and are NOT confounded. Value err% MAGNITUDES may be
offline-inflated; trust the cell-level mechanism, treat value err% as directional.
Read the engine block (10690-10790) and the constants before answering. Ground every claim in the data file
and code, not assumption. You are producing raw analysis for the orchestrator, not a human-facing message.
`

const DIAG = {
  type: 'object', additionalProperties: false,
  required: ['root_cause_confirmed', 'confidence', 'strongest_supporting_evidence', 'strongest_competing_cause', 'competing_cause_share_estimate'],
  properties: {
    root_cause_confirmed: { type: 'boolean', description: 'Is "generous-q4 value/p25 over-claims purple, starving red residual" the correct dominant root cause of ethan 3+red under-estimate?' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    strongest_supporting_evidence: { type: 'string' },
    strongest_competing_cause: { type: 'string', description: 'Best alternative explanation you can construct (e.g. total-grid under-anchor, red per-cell price too low, q1/q3 over-claim, offline artifact). Name the single strongest.' },
    competing_cause_share_estimate: { type: 'string', description: 'Rough share of the 15-sample under-estimate attributable to the competing cause vs generous-q4' },
  },
}

const DESIGN = {
  type: 'object', additionalProperties: false,
  required: ['variant', 'mechanism', 'exact_code_change', 'protects_1_2_red', 'phantom_red_villa_risk', 'predicted_effect_3plus_red', 'predicted_effect_1_2_red', 'env_name', 'grounding'],
  properties: {
    variant: { type: 'string' },
    mechanism: { type: 'string', description: 'How the fix changes the residual computation' },
    exact_code_change: { type: 'string', description: 'Concrete code diff sketch at ahmad_ref_engine.py, with line anchors' },
    protects_1_2_red: { type: 'string', description: 'Precise reason this cannot worsen the 1-2red +34.7% over-estimate' },
    phantom_red_villa_risk: { type: 'string', description: 'Precise reason this will not regress 2401/2407 villa phantom-red' },
    predicted_effect_3plus_red: { type: 'string', description: 'Quantified using the data file where possible (e.g. resid_red on 5607 r5 goes 0->X)' },
    predicted_effect_1_2_red: { type: 'string' },
    env_name: { type: 'string', description: 'kill/enable env var name' },
    grounding: { type: 'string', description: 'Which specific data-file rows / code lines you used' },
  },
}

const VERDICT = {
  type: 'object', additionalProperties: false,
  required: ['breaks', 'severity', 'failure_scenario', 'concrete_sample_or_case', 'fix_suggestion'],
  properties: {
    breaks: { type: 'boolean', description: 'Does this design worsen 1-2red or regress villa phantom-red (or otherwise break)?' },
    severity: { type: 'string', enum: ['fatal', 'major', 'minor', 'none'] },
    failure_scenario: { type: 'string' },
    concrete_sample_or_case: { type: 'string', description: 'A specific data-file row / map / regime where it fails, or a constructed case' },
    fix_suggestion: { type: 'string', description: 'Minimal change that would neutralize the failure' },
  },
}

const SYNTH = {
  type: 'object', additionalProperties: false,
  required: ['root_cause_verdict', 'winning_design', 'rationale', 'hybrid_notes', 'implementation_steps', 'code_location', 'env', 'sweep_gates', 'residual_risks', 'recommend_default'],
  properties: {
    root_cause_verdict: { type: 'string', description: 'Confirmed / partially confirmed / refuted, with the panel consensus and any dissent' },
    winning_design: { type: 'string' },
    rationale: { type: 'string' },
    hybrid_notes: { type: 'string', description: 'Any grafts from runner-up designs' },
    implementation_steps: { type: 'array', items: { type: 'string' } },
    code_location: { type: 'string' },
    env: { type: 'string' },
    sweep_gates: { type: 'array', items: { type: 'string' }, description: 'Exact pass/fail gates the sweep must check before default-ON' },
    residual_risks: { type: 'array', items: { type: 'string' } },
    recommend_default: { type: 'string', enum: ['ON', 'OFF'], description: 'Recommended initial env default before sweep confirms' },
  },
}

// ---- Synthetic-sample feasibility (author add-on, INDEPENDENT track — runs concurrent with Confirm) ----
const SYNTH_CTX = `
SEPARATE TASK — feasibility of a NEW-STANDARD ethan synthetic-sample generator (the author is sample-starved:
only ~99 clean_eval ethan, mostly 56xx; villa/hidden family priors MISSING). ALL prior synthetic ethan
samples were BUGGY and must not be trusted (round drift now fixed 747f1bb; old reveal reconstruction was
unfaithful). Assess whether the following NEW route is feasible and faithful.

NEW STANDARD (author, authoritative):
- Base = ONLY AHMED (艾哈迈德) real settled vaults — the author fixed this: ahmed is the most STABLE and
  easiest to re-skin (full readable settlement truth: every tier's count/cells/value known). Do NOT use
  aisha/other heroes as base. Recommend the specific clean ahmed pool (build_clean_eval_set / clean_eval_ahmed).
- KEEP public info as-is. Do NOT drift rounds (use corrected action_round semantics, docs/ROUND_SEMANTICS.zh-CN.md).
- ONLY replace ethan's SKILL + TOOL usage. Everything else (grid, items, settlement truth, public info) stays.
- The ethan tool/skill chain is EXACTLY 5 tools, applied BY ROUND:
    1. 普品扫描 (common scan  -> q1 white exact cells)      \\ these two are RANDOM across the
    2. 良品扫描 (good scan    -> q3 blue exact cells)       // first two rounds
    3. 优品均格 (premium avg-cells, tool id 100112 -> q4 purple avg_cells, NOT exact)
    4. 极品扫描 (66%: gold exact cells)  OR  极品估价 (33%: gold VALUE sum)   -> q5
    5. 抽检 x2 (random sample inspection -> random_value_floors, 2 draws)
  plus ethan's outline-reveal skills (1002081..1002085) as the live monitor emits them per round.

ASSESS:
- Do the tool IDs / result row formats exist and are they emittable via the live snapshot contract the
  engine consumes (extract_evidence: action_result_rows, public_info, skill_reveals)? Find them in the repo.
- What is the correct base pool (ahmed clean full-data vaults? which set)? Can settlement truth be mapped to
  the ethan reveal chain faithfully (e.g. 优品均格 requires a real per-item purple avg-cells derivable from truth)?
- Why were the OLD synthetic ethan samples buggy, and does the new route actually avoid those failure modes?
- Concrete implementation sketch (which existing script to adapt: scripts/synthesize_draw_hero_samples.py,
  scripts/emit_ethan_synth.py; what to change), estimated sample yield, and the fidelity gate to prove one
  generated sample round-trips (engine output on synth == engine output on a real ethan of same structure).
Read: scripts/synthesize_draw_hero_samples.py, scripts/emit_ethan_synth.py, docs/ROUND_SEMANTICS.zh-CN.md,
the ethan reveal mechanics, and the extract_evidence / action_result_rows path in the engine. Ground claims
in code. You produce raw analysis for the orchestrator.
`
const SYNTH_FEAS = {
  type: 'object', additionalProperties: false,
  required: ['feasible', 'tool_ids_found', 'base_pool_recommendation', 'old_bug_root_causes', 'new_route_avoids_them', 'implementation_sketch', 'fidelity_gate', 'sample_yield_estimate', 'blockers'],
  properties: {
    feasible: { type: 'string', enum: ['yes', 'yes-with-caveats', 'no'] },
    tool_ids_found: { type: 'string', description: 'The concrete tool/skill IDs + result-row formats found in the repo for the 5 tools + outline skills, with file:line' },
    base_pool_recommendation: { type: 'string', description: 'The specific clean AHMED pool to re-skin (base is fixed to ahmed) + how to select fully-readable-truth ahmed vaults' },
    old_bug_root_causes: { type: 'string', description: 'Why prior synthetic ethan samples were unfaithful/buggy' },
    new_route_avoids_them: { type: 'string', description: 'Whether/how the new standard avoids each old failure mode' },
    implementation_sketch: { type: 'string', description: 'Which script to adapt and the concrete changes' },
    fidelity_gate: { type: 'string', description: 'How to prove one generated sample is faithful (round-trip test)' },
    sample_yield_estimate: { type: 'string' },
    blockers: { type: 'array', items: { type: 'string' } },
  },
}

// ---- Phase 1: Confirm (3 skeptics) + SynthFeas (2 agents), run CONCURRENTLY ----
phase('Confirm')
const [confirms, synthFeas] = await Promise.all([
  parallel([0, 1, 2].map((i) => () =>
    agent(
      `${CTX}\n\nYOU ARE SKEPTIC #${i + 1}. Your job is to TRY TO REFUTE the verified root cause, not confirm it. ` +
      `Open scripts/_tmp_ethan_lift_coverage.json and the engine block. Test the competing hypotheses: ` +
      `(a) total-grid under-anchoring (compare ri_tg vs ttot_cells across the 15), ` +
      `(b) red per-cell price too low (would under-value even with correct resid), ` +
      `(c) q1/q3 over-claim rather than q4, ` +
      `(d) it's an offline artifact. ` +
      `Only report root_cause_confirmed=true if you could NOT refute it. Default to skepticism.`,
      { label: `confirm:${i + 1}`, phase: 'Confirm', schema: DIAG, effort: 'high' }
    )
  )),
  parallel([
    () => agent(`${SYNTH_CTX}\n\nYou are the FEASIBILITY analyst. Produce the full assessment.`,
      { label: 'synth:feasibility', phase: 'SynthFeas', schema: SYNTH_FEAS, effort: 'high' }),
    () => agent(`${SYNTH_CTX}\n\nYou are the FIDELITY SKEPTIC. Assume the new route will STILL produce ` +
      `subtly-wrong samples. Hunt for the failure modes that would make generated samples diverge from real ` +
      `ethan (e.g. 优品均格 avg-cells not derivable from truth without per-item purple layout; outline reveal ` +
      `coverage; random_avg_red_anchor / 抽检 interaction; public-info vs private-scan double counting). ` +
      `Report feasible='no' or 'yes-with-caveats' with the specific blockers.`,
      { label: 'synth:fidelity-skeptic', phase: 'SynthFeas', schema: SYNTH_FEAS, effort: 'high' }),
  ]),
])

// ---- Phases 2+3: Design each variant, then adversarially break it (pipeline, no barrier) ----
const VARIANTS = [
  { key: 'q4-mid', brief: 'For the RESIDUAL ONLY, read q4 purple cells with the MID per-cell price (value/mid) instead of value/p25. Fewer purple cells -> larger red residual. Keep display/quote paths using their current readings.' },
  { key: 'q4-family-cap', brief: 'Cap q4_generous at an upper bound derived from a family purple cell-fraction (e.g. total_grid_target * family_q4_frac, using the calibrated 0.291 for 56xx), so an implausibly large value/p25 reading cannot exceed the family-typical purple footprint. Needs villa/hidden priors (currently missing) — address that gap.' },
  { key: 'red-floor-prior', brief: 'Blend the arithmetic residual with a family red-cell-fraction FLOOR (total_grid_target * ETHAN_RED_CELL_FRAC_BY_FAMILY) so red is never starved below the family-typical red footprint when the outline total is locked. Guard so it only floors when the total grid is anchored and does not fire on low-red villa maps.' },
]

const designed = await pipeline(
  VARIANTS,
  (v) => agent(
    `${CTX}\n\nDESIGN VARIANT "${v.key}": ${v.brief}\n\n` +
    `Produce a concrete, implementable spec. Quantify the predicted effect using ACTUAL rows from ` +
    `scripts/_tmp_ethan_lift_coverage.json (e.g. recompute resid_red for 5607 r5, 5606 r5, and a 1-2red row). ` +
    `Be explicit about how it protects the 1-2red +34.7% over-estimate and avoids 2401/2407 villa phantom-red. ` +
    `Read the engine block 10690-10790 and constants 1575-1632 for exact line anchors.`,
    { label: `design:${v.key}`, phase: 'Design', schema: DESIGN, effort: 'high' }
  ),
  (design, v) => parallel([0, 1].map((j) => () =>
    agent(
      `${CTX}\n\nYou are an ADVERSARIAL BREAKER. Here is a proposed fix design for ethan red residual:\n` +
      `${JSON.stringify(design)}\n\n` +
      `Try HARD to break it. Find a concrete data-file row (scripts/_tmp_ethan_lift_coverage.json) or a ` +
      `realistic regime (esp. 1-2red samples, villa/hidden low-red maps, exact-q4 samples that are already ` +
      `accurate) where this design over-lifts red, worsens the 1-2red +34.7% over-estimate, or re-introduces ` +
      `2401/2407 phantom red. Default breaks=true if you find any plausible regression. Breaker #${j + 1}.`,
      { label: `break:${v.key}:${j + 1}`, phase: 'Adversarial', schema: VERDICT, effort: 'high' }
    )
  )).then((verdicts) => ({ variant: v.key, design, verdicts: verdicts.filter(Boolean) }))
)

// ---- Phase 4: Synthesize ----
phase('Synthesize')
const synthesis = await agent(
  `${CTX}\n\nPANEL RESULTS.\n\nROOT-CAUSE SKEPTICS:\n${JSON.stringify(confirms.filter(Boolean))}\n\n` +
  `DESIGNS + ADVERSARIAL VERDICTS:\n${JSON.stringify(designed.filter(Boolean))}\n\n` +
  `Synthesize the final recommendation. Pick the winning design (or a hybrid) that best fixes the 3+red ` +
  `under-estimate WHILE surviving the adversarial breakers on 1-2red and villa phantom-red. Prefer the ` +
  `narrowest, best-gated change. Give exact implementation steps with code anchors, the env var + recommended ` +
  `default (OFF unless a breaker-proof case exists), and the precise sweep gates the orchestrator must pass ` +
  `before flipping default-ON. Be concrete and honest about residual risks and any villa/hidden data gaps.`,
  { label: 'synthesize', phase: 'Synthesize', schema: SYNTH, effort: 'high' }
)

return {
  confirms: confirms.filter(Boolean),
  synth_feasibility: synthFeas.filter(Boolean),
  designed: designed.filter(Boolean),
  synthesis,
}
