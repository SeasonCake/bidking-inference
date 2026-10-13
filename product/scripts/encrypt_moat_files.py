# -*- coding: utf-8 -*-
"""打包用: 就地压缩并加密包内核心 moat 文件(BKENC2-gzip/AES-256-GCM)。

key = env BIDKING_DATA_KEY(base64 32B = 出厂主密钥, 打包脚本从 -MasterKeyFile 设)。
运行时 data_crypt.load_json/load_text 用【Rust 内核内嵌的同一把 K】解密 —— 启动即可用、无激活竞态。
仅 build_live_portable.ps1 -Encrypt + -MasterKeyFile 时调用; 只加密【已接 data_crypt 读取点】的文件
(否则运行时读不到)。已加密的跳过(幂等)。

⚠️ 直接用 env(不走 resolve_key): resolve_key 现在内核优先, 打包机若装了内核会误取内核 key -> 与
   -MasterKeyFile 不符。故这里显式 base64 解 env, 保证用的就是 -MasterKeyFile 那把 K。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bidking_lab.secure.data_crypt import (  # noqa: E402
    BKENC2_GZIP_FORMAT,
    BKENC_AUTHENTICATION_AAD_SCHEME,
    encrypt_compressed_bytes,
    encryption_format,
)

# 已接 data_crypt 读取点或已冻结为最终组合前置门的 moat 文件(相对包根)。
# authority 的 audited Lane A loader 仍需在最终组合中切到 BKENC2-aware duplicate-safe
# 读取并独审；本 package-only source 只提前冻结必备包成员，不能冒充 runtime 已接线。
_MOAT_RELS = [
    "data/processed/drop_table_priors_0627.json",
    "data/processed/red_longtail_normalized_0627.json",
    "data/processed/items.json",
    "data/processed/items_droppable.json",
    "data/processed/item_category_authority_0_3_2.json",
    "data/processed/drop_weights_all_maps_0627.json",
    "data/processed/rankmap_drop_weights_current.json",
    "data/processed/rankmap_drop_priors_current.json",
    "data/processed/rankmap_distribution_authority_current.json",
    "data/processed/auction_hall_6001_shadow_distribution.json",
    "data/processed/shipwreck_calibrated_b049.json",
    "data/processed/maps.json",
    "data/processed/quality_combo_presolve_q456.json",
    "data/processed/battle_items.json",       # 道具名(monitor+sample_upload 读, 已接线)
    "data/processed/map_fragment_fixes.json",  # 地图名修正(capture/map_resolve 读, 已接线)
    "data/raw/tables/Item.txt",
    "data/raw/tables/Drop.txt",
    "data/raw/tables/BidMap.txt",
    "data/raw/tables/RankMap.txt",
    "data/raw/tables/Hero.txt",
    "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs",
]
_UPLOAD_CONFIG_REL = "data/processed/upload_config.enc"


def _contract_moat_members(path: Path) -> list[str]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    authentication = contract.get("bkenc_authentication")
    protection = contract.get("member_protection")
    if (
        type(authentication) is not dict
        or authentication.get("envelope_aad")
        != BKENC_AUTHENTICATION_AAD_SCHEME
        or type(protection) is not dict
    ):
        raise ValueError("release contract缺member_protection")
    protected: dict[str, tuple[str, bool]] = {}
    for member, rule in protection.items():
        if (
            type(member) is not str
            or type(rule) is not dict
            or set(rule) != {"mode", "required"}
            or rule.get("mode") not in {
                "bkenc1",
                BKENC2_GZIP_FORMAT,
                "plaintext",
            }
            or type(rule.get("required")) is not bool
        ):
            raise ValueError("release contract member_protection非法")
        protected[member] = (rule["mode"], rule["required"])
    if protected.get(_UPLOAD_CONFIG_REL) != ("bkenc1", True):
        raise ValueError("upload config必须保持required/BKENC1")
    moat = sorted(
        member
        for member, value in protected.items()
        if value == (BKENC2_GZIP_FORMAT, True)
    )
    if moat != sorted(_MOAT_RELS):
        raise ValueError("加密写入集合与release contract不一致")
    return moat


def main() -> int:
    ap = argparse.ArgumentParser(description="就地加密包内核心 moat 文件")
    ap.add_argument("--stage", required=True, help="打包 stage 目录(包根)")
    ap.add_argument(
        "--release-contract",
        required=True,
        help="固定member_protection集合的发布合同",
    )
    args = ap.parse_args()

    b64 = os.environ.get("BIDKING_DATA_KEY", "").strip()
    if not b64:
        print("[error] 未设 BIDKING_DATA_KEY(应由打包脚本从 -MasterKeyFile 设)", file=sys.stderr)
        return 2
    try:
        key = base64.b64decode(b64)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] BIDKING_DATA_KEY base64 解码失败: {exc}", file=sys.stderr)
        return 2
    if len(key) != 32:
        print(f"[error] BIDKING_DATA_KEY 应为 32 字节, 实为 {len(key)}", file=sys.stderr)
        return 2

    try:
        moat_rels = _contract_moat_members(Path(args.release_contract))
    except (OSError, TypeError, ValueError, json.JSONDecodeError, UnicodeError) as exc:
        print(f"[error] 发布合同加密集合无效: {type(exc).__name__}", file=sys.stderr)
        return 2

    stage = Path(args.stage)
    done = skipped = missing = 0
    for rel in moat_rels:
        p = stage / rel
        if not p.is_file():
            print(f"  skip(缺失): {rel}")
            missing += 1
            continue
        existing_format = encryption_format(p)
        if existing_format is not None:
            if existing_format != BKENC2_GZIP_FORMAT:
                print(f"  error(既有加密格式不匹配): {rel}", file=sys.stderr)
                return 3
            print(f"  skip(已压缩加密): {rel}")
            skipped += 1
            continue
        # read_bytes() 先完整读入内存，再覆盖单个 stage 文件；不触碰源码输入。
        p.write_bytes(
            encrypt_compressed_bytes(p.read_bytes(), key, member_path=rel)
        )
        done += 1
        print(f"  enc+gzip: {rel}")
    print(f"MOAT_BKENC2_GZIP done={done} skipped={skipped} missing={missing}")
    # 至少要加密到核心几个(否则打包脚本应 fail loud, 说明路径变了)
    if missing or done + skipped != len(moat_rels):
        print("[error] moat 加密集合未完整覆盖发布合同", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
