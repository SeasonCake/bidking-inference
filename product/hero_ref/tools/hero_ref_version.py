"""Display/build version for Hero Ref UI. Bump when cutting a release package."""

from __future__ import annotations

import re
from pathlib import Path

# Source of truth for dev/engineering runs; build_hero_ref_portable.ps1 -Version should match.
# 0.2.2 = 付费 token 激活 + 强加密首发版(地图名 56xx 排位+沉船活动 / 新藏品引用全验 / 对局记录赢家我方+删对手最高+排位slot8
# +老样本回填 / 看板拿仓率+玩家我方优先 / C2+HMAC+UAC 安全硬化 / 12h 试用横幅 / banner 4d89b84 / trim rank ff5f5de);
# distinct from the packaged "0.2.0-hotfix2" snapshot so a命令行(源码)run is never mistaken for the old pkg.
# 0.2.3-hotfix1 = 0.2.2 之后 hotfix 合集包：cross-match 0868536 / 结算 f3 合并 b3c9868(5604+2401 同根) /
# monitor 0x0077 心跳 4ee1960 / age-stale 末次报价 59755e4 / Aisha 早轮 a0aaeac / Ahmed q1 f4eeca0 /
# ref_v0 catalog e553913 / token swap+R1 hero 3afd40a / 打包加固 flow_no_sniff 8b40688 / 退役 activity_shadow+修 collection 3507d6d。
# 0.2.3-hotfix2 = hotfix1 之后：a9 draw_hero phantom-red 根因修复(_draw_hero_count_center informed-center guard
# + FIX-A q6 floor deduct + FIX-B revealed footprint per_item, 5605 大件红拆分过估 +39%→+21%/+25%→+8%) /
# 收集诊断.bat 群友"没反应"修复(纯 ASCII .bat + 无条件 pause + 输出落包根 + cmd 编码 bug)。
# 0.2.3-hotfix3 = hotfix2 之后(首包发布; git 上 1ace654/04526df 曾撤回工作区但未发版, 勿追旧包):
# (a) live 出价帧跳过 formal 推理(monitor 语义签名不含 bids + overlay 用 inference_signature 而非 mtime);
# (b) overlay ref-input memoize(ref_input_signature 含 minimap/手填; 同轮相同输入跳过 ~1s 重算);
# (c) 空 snapshot {} 不再误启 worker(修复 render_missing 回归)。
# 0.2.4-hotfix2 = hotfix1 之后的显示/结算修复合集包(英雄适配未完成不进, 临时急发满足群友):
# (a) overlay 快照读跨进程竞争→整局无信息 8d35a96(读失败短重试4次+FileNotFound即返回);
# (b) summary worker 卡死看门狗 963afc6（后续 hotfix1 已收窄为 process-only；线程无法取消，禁止超时后重复启动）;
# (c) 新版图结算帧尾部 desync 漏解藏品 3a63e60(item-wrapper 兜底扫描, 修结算价偏低, 默认ON);
# (d) 沉船活动红率特调 e55946a(时间+家族门控·自动过期·默认ON);
# (e) mini 悬浮窗底部留空修复 fb4341a(大 probe 消除 reqheight 震荡)。
# 0.2.4-hotfix3 = hotfix2 之后(源码侧先 bump 以便作者命令行实测区分版本; 打包窗口出包时对齐):
# (a) 跨对局卡死修 119f677(reset 不再删快照)+重启⟳按钮; (b) 详情展开小屏底部越界 clamp 882f5f8;
# (c) 调参线: 伊森两杠杆 default-ON(占用估计器+红残差重定价v2 8e67f23)/塔蒂总格DP上限 239f75d/
#     2401红格贴地 bc0ede6/5606拆红 fa1c254/raven solid-floor 74ea979/活动船红 2.0→1.5。
# 0.2.4-hotfix3 已上线(COS+manifest+collector, SHA256 B78D4C39); 冒烟又修: 重启真根因
#     DETACHED_PROCESS→CREATE_NO_WINDOW/重启静默/-Restart 跳过 Npcap 守卫/激活栏底部留白/三按钮等大/罐头文案还原。
# 0.2.5 = 已发布(整局不出数 VLAN 修 + 伊森四件套 + 艾莎 5608 红残差修)。
# 0.2.5-diag = 0.2.5 之后的【诊断专项】重打包(不 bump hotfix, 主要为特定情况诊断 + 少量引擎订正):
#   - 发布安全: 卡住bug诊断增强(reset归档近30min/ignored_samples 8→24)+ 加密包默认in-thread消pool挂起
#     (b76e8a6)+ overlay「剩余格」→「红/空格」行名+自适应字号(bb941b2)。
#   - 调参引擎: 沉船活动重分类(按图号X511-X520门控·factor 1.5→1.74·修_is_shipwreck_map漏35xx拓扑bug·
#     monitor镜像判定)(3b6b4e9/8990d60)。
#   (dashboard提速+活动tag 6b8104e 是服务端, 不进客户端包。)
# 0.2.6 = 0.2.5-diag 之后的下一【稳定发布】(数字bump·让全员收到自动更新提示, 不再是-diag被_vtuple忽略)。
#   发布安全: A2重启"关了没重开"竞态修(866abe2)+restart.log/overlay.stderr.log补进诊断导出(866abe2/aca408f)
#     +手填排位图56xx显示错配沉船名修(bab5314·5605→军械私拍)+[npcap]状态行加kernel_recv(b2505a7·A1卡住
#     诊断:flatline窗判丢帧vs服务器没推)。调参引擎: 待引擎窗口B系列就绪一起进(0707十任务backlog)。
# 0.2.6-hotfix1 = 0.2.6 上线当晚群友反馈的修复批([调参窗口]bump·后续改动以此版本辨认):
#   已进: H6金格件数未锁总格软化(39aed7f·四案2508/2501/5609/5611假锁)+玛丽亚100108低品合计修(同39aed7f)。
#   队列(BUGLEDGER_0.2.6-hotfix1): R1留上一局(0.2.6新增回归疑)·红件锁定仍浮动(5602/5603)·
#   结算红品质丢失(5603磁悬浮滑板→Q0)·H5金件族先验定中心·H1 B6守卫·H2红价lever。
# 0.2.6-hotfix1 已发布(0710·基线039a38e·SHA256 35083FBC...AB71·COS+manifest+collector已切):
#   终态: H6假锁软化+玛丽亚+手填只填总格(105f064)+BUG-A开局盲窗边界relock(09ce704)+BUG-C结算品质
#   恢复(6e2ea3a)+BUG-D金红配对夹回(22d66f3)+H7p1巨红尾显示hedge(eb88ca9+bb8f8aa豁免)+B1红格
#   room-band(9a21cf4); H1/H5/H7p2三个否定闭环默认OFF存档。
# 0.2.7 = hotfix1 之后的下一版本([发布安全]bump·后续改动以此版本辨认·目标=公版发布前最后大版):
#   三大项(作者0710定·记忆release-0.2.7-roadmap): ①公共大数据平台(公版uid-scope)
#   ②caddy HTTPS(P0·明文传输key修复·api.bidking.example.invalid) ③伊森适配(引擎主线H3/H4/H2后)。
#   另: 客户端自动更新完善·激活带client_version(主密钥可轮换)·VLAN真机验证·公版反逆向战略。
# 0.2.7-hotfix1 = 0.2.7 正式发布后的群测修复开发线；源码先 bump 以区分已发布包与当前工程运行。
#   本常量只定义源码产品版本；BUILD_MANIFEST、包名、服务器 /version 仍在正式冻结/打包时统一核对。
# 0.2.7-hotfix2 = hotfix1 发布后的窄修复：(a) Gabriela 三个不同红品 runtime 中，完整揭示项必须同时
#   进入件数、格数与价值证据，修复 2543 R4/R5 已明确三红却显示 2/2/3（核心引擎 f3c9d09）；
#   (b) HeroRefDiag 携带无密钥 upload_status 状态，现场可区分未派发、配置解密失败与 HTTP 鉴权失败。
# 0.2.7-hotfix3 = hotfix2 紧急重打：修复构建末段用仓库陈旧 upload_config.enc 覆盖本次密文，
#   导致试用/付费客户端均在 HTTP 前 InvalidTag、云端样本断流；最终成品增加生成物哈希一致性门。
# 0.2.8-hotfix1 = 0.2.8-R2 发布后的热修线；已完成内测候选与服务器第一阶段部署。
# 0.2.9 = hotfix1 内测发布后的下一开发线；未作为布局后正式候选发布。
# 0.2.9-r1 = 包布局收口后的第一条可追踪候选线；后续引擎临时修订与最终重建均以此版本辨认。
# 0.2.9-hotfix1 = r1 生产切换后首个正式热修边界；用于承载 WMI/CIM 启动与诊断降级、
# 公版连续报价日志收口及默认关闭的最低产品版本门，并让正式 hotfix 在版本比较中严格晚于
# 发布前 rN 候选，避免已拿到 r1 的用户被误判为最新版。
# 0.2.9-hotfix2 = 2026-07-22 已稳定发布到公群的官方更新前热修。
# 0.3.0 = 7 月 23 日官方更新兼容与玛丽亚适配开发线；已于 2026-07-24 完成正式发布闭环。
# 0.3.0-hotfix1 = 0.3.0 正式发布后的常规优化开发线；后续审计、调参、候选和成品继续
# 分阶段记录，不复用或覆盖已发布的 0.3.0 成品身份。
# 0.3.0-hotfix1-r1 = 针对两台 redirector 稳定缺失机器的兼容修复重打边界；只在来源、
# 独立审计与受影响实机全链证据闭合后形成新候选，不覆盖旧 hotfix1 诊断制品。
# 0.3.0-hotfix2 = hotfix1-r1 定向兼容候选之后的下一开发线；承载已排队的引擎、UI、
# 数据与服务端完善项。版本边界本身不代表这些工作已完成、已有候选或已经发布。
# 0.3.0-hotfix2-r1 = hotfix2 首个 encrypted standard ZIP 的私有构建路径成品门阻断后，
# 用于区分修复 runtime bytecode 隐私边界后的全新候选；不覆盖原 blocked ZIP。
# 0.3.1 = 2026-07-31 已完成候选构建与人工分发，激活/采集服务已切到对应合同；
# 生产 /version 清单是独立运维状态，不能由源码版本或分发状态代替证明。
# 0.3.1-hotfix1 = 2026-08-01 已完成正式发布与生产 /version 清单切换的维护版。
# 历史说明：曾建立的 0.3.2 开发身份已前向暂停；对应旧提交只作历史证据，不作为
# 当前开发线的 source、候选或发布身份复用。
# 0.3.1-hotfix2 = hotfix1 之后的完整收口线；统一承载尚未进入生产闭环的 activation/server
# P0 与已确认 core 缺口。生产 /version、最低支持版本和部署仍由独立发布门约束。
# 0.3.1-hotfix3 = hotfix2 已正式发布后的 UI/状态表达修订线；优先关闭品质组合冲突、
# 概率/锁定状态与可用红件信息的层级和一致性问题。该源码版本不代表已有候选或线上切换。
# 0.3.1-hotfix4 = 保留 hotfix3 历史候选，为作者实机确认的品质组合与布局
# 修复建立独立发布身份；不覆盖或重用已撤回的 hotfix3 制品。
# 0.3.1-hotfix5 = 已于 2026-08-06 正式发布并冻结生产字节；后续开发不得重建、覆盖
# 或重新切换该正式版本。
# 0.3.2 = hotfix5 之后的首个本地测试候选；源码、E07 与 artifact 审计曾通过，但作者
# 真实对局实测确认存在开局空窗、推理/结算卡住、部分锁状态误显示等问题，候选已废弃。
# 0.3.2-hotfix1 = 0.3.2 作者实测失败后的首轮修复候选；作者复测仍发现记录入口样式、
# Maria 状态收口、锁维度投影与格数联合约束问题，保留为历史实测失败候选。
# 0.3.2-hotfix2 = 第二轮作者实测修复线；作者复测达标并已完成全群正式发布，保留为
# 当前生产版本。完整 >5 求解、主客户端 Python 维护升级等事项未混入该正式包。
# 0.3.2-hotfix3 = hotfix2 正式发布后的下一开发线；本次只建立版本边界，具体计划按
# 当期 checkpoint 分车道推进，未经重新审计不得冒充候选、产物或生产版本。
# 0.3.2-hotfix4 = hotfix3 后续实战修复的独立版本边界；保留 hotfix2/hotfix3 客户端兼容，
# 版本 bump 本身不代表候选、产物、服务器切换或正式发布。
# 0.3.3 = hotfix4 内测发布后群友反馈的下一开发线；用于承载已冻结的计算、启动兼容与
# 延迟/件数调查。服务器、候选、产物与生产状态继续由独立门控制，不随源码版本自动切换。
# 0.3.3-hotfix2 = hotfix1 author-retest-failed 后的最窄版本边界；既有 fixed8/fixed9
# 字节已冻结为应急前代，不复用、覆盖或改名。
# 0.3.3-hotfix3 = 总件数公开事实触发红件/红格空窗的唯一后续修复边界；版本 bump
# 本身不代表候选、产物、实机、上传、部署或生产完成。
# 0.3.3-hotfix4 = hotfix3 公版后的最窄客户端启动兼容后继；只承载 bounded
# MOTW/ADS/WSS cache 修复，不覆盖或改名既有 hotfix3 字节，也不自动切服务器 manifest。
# 0.3.4 = 2026-08-27 官方 4ae819 更新适配开发线；承载新活动/竞拍大厅 6001、
# current-generation 数据与报价链。源码版本不代表候选、构建、服务器切换或正式发布。
# 0.3.4-hotfix1 = 0.3.4 正式发布后的最窄 Maria 首轮报价修复边界；只承载完整低品
# 件数在 reading 首帧的件锁与对应 cache 失效，不代表候选、作者验收、上传或生产切换。
# 0.3.5 = 原0.3.4-hotfix2在研批次提升为正式版筹备目标；保留已完成源码与历史证据。
# 源码版本同步不代表功能全闭合、已有候选、构建、上传、部署或正式发布。
# 0.3.5-hotfix1 = b049兼容、5215完整估值信息、沉船校准与启动/退出窄修候选线。
# 仅定义新的源码/包身份，不代表已构建、作者实测通过或服务器/公告已发布。
# 0.3.6 = 2026-10-08官方表适配、英雄分层估计、启动稳定性与兼容旧卡的真正续费开发线。
# 此处仅建立源码身份；候选、实测、服务器与商店文案均独立验证/发布。
HERO_REF_VERSION = "0.3.6"

_MANIFEST_PATTERN = re.compile(r"^PackageVersion:\s*v?(.+)\s*$", re.MULTILINE)


def resolve_hero_ref_version() -> str:
    """Prefer packaged BUILD_MANIFEST when present; else module constant."""
    here = Path(__file__).resolve()
    for parent in here.parents[:6]:
        manifest = parent / "BUILD_MANIFEST.txt"
        if not manifest.is_file():
            continue
        try:
            text = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _MANIFEST_PATTERN.search(text)
        if match:
            return match.group(1).strip()
    return HERO_REF_VERSION
