// 伊森估值全面完善 workflow — 下版本运行(起草于 2026-06-28, hero-tuning-0625)。
// 权威背景: docs/HANDOFF_ethan_no_red_damp_value_phantom_2026-06-28.zh-CN.md (§6 Phase A/B/C)。
//
// 怎么跑: Workflow({ scriptPath: "scripts/workflows/ethan_completion_workflow.js" })
// 跑前先核对/微调下方 CONFIG 的样本路径与脚本名(它们随版本可能变)。
//
// ⚠️ CPU 纪律 (memory: core-tuning-verification-discipline / use-workflow-for-deep-multiangle-work):
//   引擎回放是 CPU-bound 纯 Python DP。workflow 真正的并发上限 = min(16, 核数-2),但 CPU-bound 任务
//   N 并发≈M/N 速度(thrash)。所以本脚本: Phase A 用「少而大」的族系分片(3-4个), Phase B 的 approach
//   agent 各在 worktree 改引擎+跑 bounded measure(≤3-4 并发)。每个 measure 都要带样本上限/wall-clock
//   timeout, 别 blind 跑全库。

export const meta = {
  name: 'ethan-completion',
  description: '下版本伊森估值全面完善: 全样本基线 → 三方案judge对照 → 红单格价标定。强隔离ethan, 双向不回归gate。',
  phases: [
    { title: 'Baseline', detail: '全样本measure ethan bias (族系×是否锚定×轮次)' },
    { title: 'Approaches', detail: '三方案各在worktree实现+bounded measure (damp / cell-residual守恒 / 高位保守收缩)' },
    { title: 'Judge', detail: '裁判打分三方案 (红格bias主, 双向不回归), 合成推荐' },
    { title: 'RedValue', detail: '红单格价标定分析 (锚定56xx红值+42%过)' },
  ],
}

// ----------------------------------------------------------------------------
// CONFIG — 跑前核对 (这些随版本可能变)
// ----------------------------------------------------------------------------
const CONFIG = {
  engine: 'hero_ref/src/ahmad_ref_engine.py',
  handoff: 'docs/HANDOFF_ethan_no_red_damp_value_phantom_2026-06-28.zh-CN.md',
  // 现成校准工具 (Phase A 复用, 别重造):
  evalTools: [
    'scripts/calibration/eval_ethan_blocks.py',   // 分块 per-quality count + grid/value/red bias
    'scripts/calibration/hero_new_health.py',     // per-hero NEW 健康, 活动图分离
    'scripts/calibration/compare_synth_vs_real_ethan.py',
  ],
  realSamplesNote: '真实 ethan NEW 在 data/samples/group_* (按 06-25 边界分 NEW/OLD; 见 samples-version-era-partition)',
  synthPool: 'data/samples/synthetic_draw/ethan/ (⚠️现全R5锚定; Phase A 若需无锚验证须先补无锚R1-R4合成局)',
  families: ['56xx', '25xx', '24xx'],
  // 强隔离铁律: 所有引擎改动必须 ethan-gated + env回退; aisha/ahmed/draw 必 byte-identical。
  noRegressionGate: 'tests/test_aisha_no_red_damp.py + tests/test_ahmad_ref_engine_public_info.py (3预存fail不算新增)',
}

const BIAS_SCHEMA = {
  type: 'object',
  required: ['family', 'cells'],
  properties: {
    family: { type: 'string' },
    n_samples: { type: 'number' },
    // 每个分组 (anchored_R5 / unanchored_byround) 的 bias 中位与均值, %; red_cells/red_value 绝对+%
    cells: {
      type: 'array',
      items: {
        type: 'object',
        required: ['group', 'grid_bias_med', 'value_bias_med', 'red_value_bias_med'],
        properties: {
          group: { type: 'string' },          // e.g. "unanchored_R3" / "anchored_R5"
          n: { type: 'number' },
          grid_bias_med: { type: 'number' },
          grid_bias_mean: { type: 'number' },
          value_bias_med: { type: 'number' },
          red_cells_bias_med: { type: 'number' },
          red_value_bias_med: { type: 'number' },
          count_bias_mean: { type: 'number' },
        },
      },
    },
    notes: { type: 'string' },                 // 系统性 vs 方差的判断 + 样本是否够
  },
}

const APPROACH_SCHEMA = {
  type: 'object',
  required: ['approach', 'grid_bias_med', 'value_bias_med', 'regression_ok', 'summary'],
  properties: {
    approach: { type: 'string' },              // damp / cell_residual / conservative_shrink
    env_flag: { type: 'string' },              // 用于启用的 env 旗标
    // 在基线相同分组上的 after-fix bias:
    grid_bias_med_25xx: { type: 'number' },
    grid_bias_med_56xx: { type: 'number' },
    value_bias_med_25xx: { type: 'number' },
    value_bias_med_56xx: { type: 'number' },
    grid_bias_med: { type: 'number' },
    value_bias_med: { type: 'number' },
    red_cells_bias_med: { type: 'number' },
    regression_ok: { type: 'boolean' },        // aisha/ahmed/draw byte-identical + gate零新增fail?
    worktree_diff_stat: { type: 'string' },
    summary: { type: 'string' },               // 机制 + 优缺点 + 残留
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['ranking', 'recommended', 'rationale'],
  properties: {
    ranking: { type: 'array', items: { type: 'string' } },   // 从优到劣
    recommended: { type: 'string' },
    rationale: { type: 'string' },
    open_questions_for_author: { type: 'array', items: { type: 'string' } }, // 需作者拍板的取舍(如方案3欠估)
  },
}

// ----------------------------------------------------------------------------
// Phase A — 全样本基线 (族系分片, 少而大, bounded)
// ----------------------------------------------------------------------------
phase('Baseline')
log('Phase A: measure ethan bias by family × anchored × round (run_reference_engine = overlay 路径)')

const baseline = (await parallel(CONFIG.families.map(fam => () =>
  agent(
    `你是引擎校准 agent。任务: 测量英雄 ethan 在地图族系 ${fam} 上的估值偏差 (bias)。\n` +
    `背景必读: ${CONFIG.handoff} (尤其 §1/§3 实战证据与族系依赖)。\n` +
    `做法: 复用 ${CONFIG.evalTools.join(' 或 ')}; 真实样本 ${CONFIG.realSamplesNote}。\n` +
    `用 run_reference_engine (overlay 路径, 非离线 _snapshot_at_batch — 后者对轮廓英雄欠估) 跑该族系的真实 ethan NEW 样本,\n` +
    `按 (是否锚定 R5全揭示 / 未锚定且按轮次 R1-R4) 分组, 对每组算: 总格 / 总价值 / 红格 / 红值 / 件数 的 bias 中位与均值。\n` +
    `关键判断: 该族系无锚组的总格过估是【系统性】还是【方差】(看中位是否显著偏离0 + 散布)。${fam}=25xx/24xx 时尤其注意低档仓(0红/小金)。\n` +
    `⚠️ bounded: 样本若 >40 个先抽 40 个代表性(含深轮/大仓), 给每次 run_reference_engine 加 wall-clock timeout, 别 blind 跑全部。\n` +
    `⚠️ 若该族系无锚样本不足以判系统/方差, 在 notes 里明确指出"需补无锚合成局" (现合成池全R5锚定=不可用于无锚验证)。\n` +
    `返回结构化 bias。`,
    { schema: BIAS_SCHEMA, phase: 'Baseline', label: `baseline:${fam}` }
  )
))).filter(Boolean)

log(`Phase A done: ${baseline.length} families measured`)

// ----------------------------------------------------------------------------
// Phase B — 三方案各在 worktree 实现 + bounded measure (≤3 并发, CPU 纪律)
// ----------------------------------------------------------------------------
phase('Approaches')
log('Phase B: implement+measure 3 approaches in isolated worktrees (强隔离 ethan, env回退)')

const baselineJson = JSON.stringify(baseline)
const APPROACHES = [
  {
    key: 'damp',
    brief: '复用已实现的 ethan cell-based no-red damp (引擎已有, ETHAN_ENABLE_NO_RED_DAMP=1, frac-scaling)。\n' +
      '任务: 全样本标定 ETHAN_NO_RED_DAMP_STRENGTH / frac-power / MIN_FRAC, 让 56xx 真红不被杀(价值安全侧不翻欠太多)、25xx 幻影红→0。\n' +
      '已知: 这条只修价值不修总格(压红后件数reallocate给金紫)。如实报告总格残留。',
  },
  {
    key: 'cell_residual',
    brief: '实现【总格守恒 / cell-residual】(deferred A计划, docs/HANDOFF_ethan_cell_centric_plan)。\n' +
      '思路: ethan无锚时给软总格锚(如先验总格期望) + 高位做显式残差(红格=总格−已知低中−紫金估计), 直接修总格+价值。\n' +
      '⚠️ 已验证陷阱: 单注入软 total_grid_target 只硬钳总数、不自动重分配 per-quality(red 仍冻结)→ 必须写【显式残差逻辑】绕开 combo 污染。\n' +
      '红单格价偏高(见方案C), 残差红格×过高单价仍会过 → 注意与C的耦合。',
  },
  {
    key: 'conservative_shrink',
    brief: '实现【高位格刻意保守收缩】: ethan无锚时把 q4/q5/q6 cells 一起往保守中心收(map-aware: 低档族系收更多)。\n' +
      '⚠️ 这是"宁可欠估"的更激进版, 会系统性欠估正常/高档仓的总格与价值(安全侧但牺牲准度)。\n' +
      '在 open_questions 里明确标出"此方案需作者拍板取舍(欠估换安全)", 别擅自当最优。',
  },
]

const approaches = (await parallel(APPROACHES.map(a => () =>
  agent(
    `你是引擎修复+校准 agent (在隔离 worktree 工作, 可改 ${CONFIG.engine})。\n` +
    `方案 ${a.key}: ${a.brief}\n\n` +
    `背景必读: ${CONFIG.handoff}。基线(Phase A)bias = ${baselineJson}\n` +
    `铁律: ① 强隔离 ethan (hero=='ethan' gated) + env 旗标回退; aisha/ahmed/draw 必 byte-identical。\n` +
    `② 实现后, 在与基线【相同的样本分组】上重测 25xx/56xx 的 总格/价值/红格 bias (run_reference_engine, bounded+timeout)。\n` +
    `③ 跑无回归 gate: ${CONFIG.noRegressionGate} (3预存fail不算新增; 任何新增fail = regression_ok:false)。\n` +
    `④ 报 worktree git diff --stat。gate没过/不隔离 = 不算成功, regression_ok 据实填 false。\n` +
    `返回结构化结果。`,
    { schema: APPROACH_SCHEMA, phase: 'Approaches', label: `approach:${a.key}`, isolation: 'worktree' }
  )
))).filter(Boolean)

log(`Phase B done: ${approaches.length} approaches measured`)

// ----------------------------------------------------------------------------
// Phase C — 红单格价标定 (并行于 judge, 共享线谨慎)
// ----------------------------------------------------------------------------
phase('RedValue')
const redValue = await agent(
  `你是红价标定 agent。背景: ${CONFIG.handoff} §6 Phase C + memory aisha-red-knowability-shape-maps。\n` +
  `事实: 锚定 56xx ethan 红值 +42% 过 (格子精确时仍过 → 是红【每格价】偏高, 不是格子)。\n` +
  `任务: 用真实 ethan NEW 锚定局(格子精确)分析红单格价应降多少 → 红值 bias 趋 0。\n` +
  `⚠️ 红单格价是【共享 aisha 红价值线】, 动它影响 aisha → 必须 ethan 隔离 或 提供全样本 balanced A/B + 回退方案。\n` +
  `返回: 建议降幅 + 是否能 ethan 隔离 + 对 aisha 的影响评估 + 与方案 cell_residual 的耦合提示。`,
  { phase: 'RedValue', label: 'red-per-cell-value' }
)

// ----------------------------------------------------------------------------
// Judge — 裁判打分 + 合成
// ----------------------------------------------------------------------------
phase('Judge')
const verdict = await agent(
  `你是裁判。基于以下材料给三方案 (damp / cell_residual / conservative_shrink) 排名 + 推荐:\n` +
  `基线 bias: ${baselineJson}\n` +
  `三方案结果: ${JSON.stringify(approaches)}\n` +
  `红价分析: ${redValue}\n\n` +
  `裁判口径 (主→次): ① 红格 bias (主裁判, 趋0) ② 价值 bias 安全侧(宁欠不超) ③ 总格 bias 双向(25xx改善 且 56xx不回归) ④ regression_ok 必为 true ⑤ 改动复杂度/可维护。\n` +
  `任何 regression_ok:false 的方案直接降到末位。把"需作者拍板的取舍"(如 conservative_shrink 的欠估、cell_residual+红价耦合)放进 open_questions_for_author。\n` +
  `返回结构化裁决。`,
  { schema: VERDICT_SCHEMA, phase: 'Judge', label: 'judge' }
)

return {
  baseline,
  approaches,
  red_value_analysis: redValue,
  verdict,
  next_step: '把 verdict.recommended 在主循环里落地实现 + 全样本measure + 设计用例gate; verdict.open_questions_for_author 先问作者拍板。',
}
