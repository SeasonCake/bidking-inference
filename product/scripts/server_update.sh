#!/usr/bin/env bash
# BidKing 收集服务器一键更新: 把 ~ 里新传的 .py 覆盖进 ~/bidking/scripts/ 并重启服务,
# 健康检查(/healthz；激活组另加 /activation-readiness)失败则【自动回滚】到更新前版本。实时有人上传也安全
# (上传是 best-effort, 重启 ~1-2s 期间失败的客户端会下一局自动重传, 磁盘样本一条不丢)。
#
# 用法: 用文件管理器把新 .py 传到服务器 ~ (home) 目录, 然后显式列出本次候选:
#   bash ~/server_update.sh sample_dashboard.py
# 激活运行时必须整组部署:
#   bash ~/server_update.sh activation_core.py activation_server.py \
#     sample_collector_server.py activation_deploy_gate.py pet_usage_db_admin.py \
#     upload_lease.py upload_identity_store.py upload_identity_db_admin.py \
#     activation_device_admin.py my_records.py
set -euo pipefail

REPO="$HOME/bidking"
DST="$REPO/scripts"
SVC="bidking-collector"
PORT=18900
# ⚠️ 这是【白名单】: 不在表里的 .py 会被【静默跳过】, 而脚本照样打印"✓ 更新成功"。
# 0712 实翻车: public_stats.py / web_ui.py 是 0.2.7 新增的, 当时不在表里 —— 传上去也不会生效,
# 而运维看到的是一条绿色的成功信息。所以下面第 0 步加了"发现表外 .py 就中止"的硬门。
# 新增服务端模块时【必须同时加进这张表】。
FILES=(sample_dashboard.py sample_collector_server.py build_match_records.py
       activation_core.py activation_server.py mint_token.py batch_mint.py
       membership_card_store.py
       sample_history_index.py
       activation_http_e2ee.py quote_preferences.py
       activation_deploy_gate.py pet_usage_db_admin.py upload_lease.py
       upload_identity_store.py upload_identity_db_admin.py activation_device_admin.py
       public_stats.py web_ui.py
       my_records.py)
ACTIVATION_GROUP=(sample_dashboard.py build_match_records.py public_stats.py
                  activation_core.py activation_server.py sample_collector_server.py
                  membership_card_store.py
                  sample_history_index.py
                  activation_http_e2ee.py quote_preferences.py
                  activation_deploy_gate.py pet_usage_db_admin.py upload_lease.py
                  upload_identity_store.py upload_identity_db_admin.py
                  activation_device_admin.py my_records.py)
PET_STATE_DIRECTORY_NAME="bidking-activation-anchor"
PET_STATE_DIRECTORY="/var/lib/$PET_STATE_DIRECTORY_NAME"
UPLOAD_IDENTITY_STATE_DIRECTORY_NAME="bidking-upload-identity"
UPLOAD_IDENTITY_STATE_DIRECTORY="/var/lib/$UPLOAD_IDENTITY_STATE_DIRECTORY_NAME"
if [ "${BIDKING_SERVER_UPDATE_TEST_MODE:-0}" = "1" ]; then
  PET_STATE_DIRECTORY="${BIDKING_TEST_PET_STATE_DIRECTORY:?missing test Pet StateDirectory}"
  UPLOAD_IDENTITY_STATE_DIRECTORY="${BIDKING_TEST_UPLOAD_IDENTITY_STATE_DIRECTORY:?missing test upload identity StateDirectory}"
fi
PET_USAGE_DB="$PET_STATE_DIRECTORY/pet_usage.sqlite3"
PET_USAGE_HEAD="$PET_STATE_DIRECTORY/pet_usage.head.jsonl"
UPLOAD_IDENTITY_DB="$UPLOAD_IDENTITY_STATE_DIRECTORY/upload_identity.sqlite3"
# 【一次性运维脚本】: 手动跑过就留在 ~ 里, 本来就不该被部署。它们不是"忘记登记的新模块",
# 所以下面第 0 步的硬门要放行它们 —— 否则每次部署都被历史遗留卡住, 而人会开始习惯性无视这道门,
# 那这道门就白设了(一个总在误报的告警等于没有告警)。
IGNORE=(apply_rank_patch.py rescreen_collected_samples.py
        activation_state_aware_deploy.py trial_state_admin.py
        activation_http_e2ee_key_admin.py)
STAMP="$(date +%Y%m%d-%H%M%S)"
BAK="$REPO/.script_bak/$STAMP"
CAND="$REPO/.script_candidates/$STAMP"

cleanup_candidate() {
  case "$CAND" in
    "$REPO/.script_candidates/"*) rm -rf -- "$CAND" ;;
    *) echo "✗ 拒绝清理异常候选路径: $CAND" >&2; return 1 ;;
  esac
}

health() {  # /healthz 只证明 HTTP/看板存活；激活兼容性由 activation_health 独立验证。
  # 重试等待: collector 启动先全量加载样本库(库已数万行, VPS 上要好几秒), 固定 sleep 2 一查即断
  # 会把"还在加载"误判成"不健康"→假回滚(0711 实翻车: 新版+回滚版双双误报, 服务其实都起来了)。
  # 改为最多等 ~45s, 每 3s 探一次; 真崩(crash loop/500)则 15 次全失败, 照旧判不健康。
  local _health_err=""
  for _i in $(seq 1 15); do
    if _health_err="$(curl -fsS -m 3 "localhost:$PORT/healthz" 2>&1)"; then
      return 0
    fi
    sleep 3
  done
  # 重启/回滚的判据不能吞 stderr；保留最后一次 curl 原因供运维判断 500、拒绝连接或超时。
  echo "healthz 最后一次失败: ${_health_err:-无错误文本}" >&2
  return 1
}

activation_health() {
  local _ready_err=""
  local _ready_body=""
  for _i in $(seq 1 5); do
    if _ready_body="$(curl -fsS -m 3 "localhost:$PORT/activation-readiness" 2>&1)"; then
      if python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
raise SystemExit(
    0 if isinstance(payload, dict)
    and payload.get("ok") is True
    and payload.get("service") == "activation"
    else 1
)
' "$_ready_body"; then
        return 0
      fi
      _ready_err="HTTP 200 但 activation readiness 载荷不合格"
    else
      _ready_err="$_ready_body"
    fi
    sleep 2
  done
  echo "activation-readiness 最后一次失败: ${_ready_err:-无错误文本}" >&2
  return 1
}

# 0) 硬门: ~ 里出现【既不在 FILES、也不在 IGNORE】的 .py 就中止。
#    没有这道门, 那个文件会被静默跳过, 而脚本照样报"更新成功" —— 运维以为发布了, 线上其实是旧代码。
stray=()
shopt -s nullglob
for p in "$HOME"/*.py; do
  b="$(basename "$p")"
  known=0
  for f in "${FILES[@]}" "${IGNORE[@]}"; do [ "$b" = "$f" ] && known=1 && break; done
  [ "$known" -eq 0 ] && stray+=("$b")
done
shopt -u nullglob
if [ "${#stray[@]}" -gt 0 ]; then
  echo "✗ ~ 目录里有【认不出的】 .py: ${stray[*]}"
  echo "  它们【不会被部署】。二选一:"
  echo "    · 这是要上线的新模块 -> 加进本脚本的 FILES 表"
  echo "    · 这是一次性运维脚本 -> 加进 IGNORE 表, 或从 ~ 移走"
  echo "  (静默跳过 = 你以为发布了、线上还是旧代码 —— 所以这里直接中止。)"
  exit 1
fi

# 1) 只部署命令行显式列出的候选。HOME 中其他已知 .py 可能是旧候选，绝不顺带吸收。
if [ "$#" -eq 0 ]; then
  echo "✗ 必须显式列出本次更新文件，禁止扫描 HOME 后自动混批。"
  echo "  示例: bash ~/server_update.sh sample_dashboard.py"
  echo "  支持的文件: ${FILES[*]}"
  exit 1
fi
found=()
for requested in "$@"; do
  known=0
  for f in "${FILES[@]}"; do [ "$requested" = "$f" ] && known=1 && break; done
  if [ "$known" -ne 1 ]; then
    echo "✗ 不允许部署的文件: $requested" >&2
    exit 1
  fi
  for f in "${found[@]}"; do
    if [ "$requested" = "$f" ]; then
      echo "✗ 重复的部署参数: $requested" >&2
      exit 1
    fi
  done
  if [ ! -f "$HOME/$requested" ]; then
    echo "✗ HOME 中缺少显式候选: $requested" >&2
    exit 1
  fi
  found+=("$requested")
done

activation_touched=0
for f in "${found[@]}"; do
  for member in "${ACTIVATION_GROUP[@]}"; do
    [ "$f" = "$member" ] && activation_touched=1
  done
done
if [ "$activation_touched" -eq 1 ]; then
  missing_group=()
  for member in "${ACTIVATION_GROUP[@]}"; do
    present=0
    for f in "${found[@]}"; do [ "$f" = "$member" ] && present=1 && break; done
    [ "$present" -eq 0 ] && missing_group+=("$member")
  done
  if [ "${#missing_group[@]}" -gt 0 ]; then
    echo "✗ 激活运行时是不可拆分部署组；缺少: ${missing_group[*]}" >&2
    exit 1
  fi

  state_directories="$(systemctl show "$SVC" -p StateDirectory --value 2>/dev/null || true)"
  case " $state_directories " in
    *" $PET_STATE_DIRECTORY_NAME "*) ;;
    *)
      echo "✗ systemd unit 未声明 StateDirectory=$PET_STATE_DIRECTORY_NAME。" >&2
      echo "  禁止把Pet权威账本放回应用工作目录；先按handoff离线配置并初始化。" >&2
      exit 1
      ;;
  esac
  case " $state_directories " in
    *" $UPLOAD_IDENTITY_STATE_DIRECTORY_NAME "*) ;;
    *)
      echo "✗ systemd unit 未声明 StateDirectory=$UPLOAD_IDENTITY_STATE_DIRECTORY_NAME。" >&2
      echo "  禁止把私有上传身份/replay数据库放进公开样本目录。" >&2
      exit 1
      ;;
  esac
  if [ ! -d "$PET_STATE_DIRECTORY" ] || [ -L "$PET_STATE_DIRECTORY" ]; then
    echo "✗ Pet StateDirectory 缺失或为符号链接: $PET_STATE_DIRECTORY" >&2
    exit 1
  fi
  if [ ! -f "$PET_USAGE_DB" ] || [ -L "$PET_USAGE_DB" ]; then
    echo "✗ Pet SQLite authority 未离线初始化: $PET_USAGE_DB" >&2
    echo "  server_update.sh 不会自动创建、迁移或重置额度。" >&2
    exit 1
  fi
  if [ ! -f "$PET_USAGE_HEAD" ] || [ -L "$PET_USAGE_HEAD" ]; then
    echo "✗ Pet external head 未离线初始化: $PET_USAGE_HEAD" >&2
    echo "  server_update.sh 不会自动创建、迁移或重置额度。" >&2
    exit 1
  fi
  if [ ! -d "$UPLOAD_IDENTITY_STATE_DIRECTORY" ] || [ -L "$UPLOAD_IDENTITY_STATE_DIRECTORY" ]; then
    echo "✗ 上传身份 StateDirectory 缺失或为符号链接: $UPLOAD_IDENTITY_STATE_DIRECTORY" >&2
    exit 1
  fi
  if [ ! -f "$UPLOAD_IDENTITY_DB" ] || [ -L "$UPLOAD_IDENTITY_DB" ]; then
    echo "✗ 上传身份数据库未离线初始化: $UPLOAD_IDENTITY_DB" >&2
    exit 1
  fi
  if [ -e "$REPO/pet_usage.json" ] || [ -e "$REPO/pet_usage.anchor.jsonl" ]; then
    echo "✗ 发现旧Pet JSON状态；禁止自动迁移或与SQLite并存授权。" >&2
    exit 1
  fi
fi

leftovers=()
for p in "$HOME"/*.py; do
  [ -e "$p" ] || continue
  b="$(basename "$p")"
  requested=0
  for f in "${found[@]}"; do [ "$b" = "$f" ] && requested=1 && break; done
  known=0
  for f in "${FILES[@]}"; do [ "$b" = "$f" ] && known=1 && break; done
  [ "$known" -eq 1 ] && [ "$requested" -eq 0 ] && leftovers+=("$b")
done
echo "将更新(显式候选): ${found[*]}"
if [ "${#leftovers[@]}" -gt 0 ]; then
  echo "将保留且不部署的 HOME 候选: ${leftovers[*]}"
fi

# 2) 在临时目录验证【最终将部署的字节组合】，不从 Git 工作树偷换文件。
mkdir -p "$CAND"
candidate_paths=()
for f in "${found[@]}"; do
  cp "$HOME/$f" "$CAND/$f"
  candidate_paths+=("$CAND/$f")
done
python3 -m py_compile "${candidate_paths[@]}"
if [ "$activation_touched" -eq 1 ]; then
  gate="$DST/activation_deploy_gate.py"
  [ -f "$CAND/activation_deploy_gate.py" ] && gate="$CAND/activation_deploy_gate.py"
  if [ ! -f "$gate" ]; then
    echo "✗ 缺少 activation_deploy_gate.py；首次启用时必须把它加入显式候选" >&2
    exit 1
  fi
  python3 "$gate" --candidate-dir "$CAND"
  upload_identity_admin="$CAND/upload_identity_db_admin.py"
  if [ ! -f "$upload_identity_admin" ]; then
    echo "✗ 缺少 upload_identity_db_admin.py；无法验证私有身份数据库协议。" >&2
    exit 1
  fi
  if ! upload_identity_receipt="$(python3 "$upload_identity_admin" --db "$UPLOAD_IDENTITY_DB" status)"; then
    echo "✗ 上传身份数据库 schema/quick_check 未通过。" >&2
    exit 1
  fi
  if ! python3 -c '
import json, sys
try:
    value = json.loads(sys.argv[1])
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if type(value) is dict and value == {"ok": True, "state": "ready"} else 1)
' "$upload_identity_receipt"; then
    echo "✗ 上传身份数据库状态回执不合格。" >&2
    exit 1
  fi
fi
echo "✓ 候选组合预检通过。"
if [ "${BIDKING_SERVER_UPDATE_PREFLIGHT_ONLY:-0}" = "1" ]; then
  echo "PREFLIGHT ONLY: 未覆盖、未重启。"
  cleanup_candidate
  exit 0
fi

# 3) 备份当前线上版本
mkdir -p "$BAK"
for f in "${found[@]}"; do
  if [ -f "$DST/$f" ]; then
    cp -p "$DST/$f" "$BAK/$f"
  else
    : > "$BAK/.absent-$f"
  fi
done
echo "已备份当前版 -> $BAK"

restore_backup() {
  local has_backup=0
  local has_absent=0
  for f in "${found[@]}"; do
    has_backup=0
    has_absent=0
    [ -f "$BAK/$f" ] && has_backup=1
    [ -f "$BAK/.absent-$f" ] && has_absent=1
    if [ $((has_backup + has_absent)) -ne 1 ]; then
      echo "✗ 回滚元数据不完整或冲突: $f backup=$has_backup absent=$has_absent" >&2
      return 1
    fi
    if [ "$has_backup" -eq 1 ]; then
      if ! cp -p "$BAK/$f" "$DST/$f"; then
        echo "✗ 恢复文件失败: $f" >&2
        return 1
      fi
      if ! cmp -s "$BAK/$f" "$DST/$f"; then
        echo "✗ 恢复后字节校验失败: $f" >&2
        return 1
      fi
    else
      if ! rm -f "$DST/$f"; then
        echo "✗ 恢复缺失状态失败: $f" >&2
        return 1
      fi
      if [ -e "$DST/$f" ]; then
        echo "✗ 恢复缺失状态后文件仍存在: $f" >&2
        return 1
      fi
    fi
  done
  return 0
}

deployment_health() {
  health && { [ "$activation_touched" -eq 0 ] || activation_health; }
}

rollback_and_verify() {
  local reason="$1"
  echo "回滚原因: $reason" >&2
  if ! restore_backup; then
    echo "✗✗ 回滚文件恢复或字节校验失败，禁止重启并禁止报告恢复成功。" >&2
    return 1
  fi
  if ! sudo systemctl restart "$SVC"; then
    echo "✗✗ 回滚文件已恢复，但服务重启失败。" >&2
    return 1
  fi
  sleep 2
  if deployment_health; then
    echo "✓ 已回滚，并通过普通健康与适用的激活 readiness 验收。"
    return 0
  fi
  echo "✗✗ 回滚后未通过普通健康/激活 readiness 双门。" >&2
  return 1
}

# 4) 所有候选预检后再覆盖。运行中的旧进程不会导入半组文件；全部成功后才重启。
install_failed=0
for f in "${found[@]}"; do
  if install -m 0644 "$CAND/$f" "$DST/$f"; then
    echo "  ✓ 覆盖 $f"
  else
    install_failed=1
    break
  fi
done
if [ "$install_failed" -ne 0 ]; then
  echo "✗ 覆盖中断，恢复并验证完整部署组。" >&2
  if ! rollback_and_verify "candidate_install_failed"; then
    echo "✗✗ 自动恢复未通过，请立即人工排查。" >&2
  fi
  exit 1
fi

# 5) 重启 + 普通健康和激活 readiness 双门
echo "重启 $SVC ..."
if ! sudo systemctl restart "$SVC"; then
  echo "✗ 服务重启失败，恢复并验证更新前文件。" >&2
  if ! rollback_and_verify "candidate_restart_failed"; then
    echo "✗✗ 自动恢复未通过，请立即人工排查。" >&2
  fi
  exit 1
fi
sleep 2
if deployment_health; then
  echo "✓ 更新成功, 服务健康。"
  # /stats 有 dashboard Basic Auth，旧脚本匿名 curl 实际只拿到 401，运维却会误以为已核对样本量。
  # 直接读本机权威文件，只打印行数/字节数，不触碰样本内容，也不把 dashboard 口令放进命令行。
  sample_file="$REPO/data/collected/samples.jsonl"
  if [ -f "$sample_file" ]; then
    sample_lines="$(wc -l < "$sample_file")"
    sample_bytes="$(wc -c < "$sample_file")"
    echo "本机样本快照: lines=$sample_lines bytes=$sample_bytes path=$sample_file"
  else
    echo "⚠ 未找到默认样本文件: $sample_file（若 systemd 使用了自定义 --out，请按 ExecStart 核对）"
  fi
  trials_file="$REPO/trials.json"
  if [ -f "$trials_file" ]; then
    python3 - "$trials_file" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
try:
    data = json.loads(p.read_text(encoding="utf-8"))
    print(f"本机试用状态: devices={len(data) if isinstance(data, dict) else 'INVALID'} path={p}")
except Exception as exc:
    print(f"⚠ 试用状态不可解析: {type(exc).__name__} path={p}")
PY
  fi
  echo "激活监测: sudo journalctl -u $SVC --since '10 min ago' --no-pager | grep -E '\[activation-(alert|summary)\]'"
  for f in "${found[@]}"; do rm -f "$HOME/$f"; done   # 清掉 ~ 待更新文件, 防下次误用旧文件
  cleanup_candidate
  echo "(已清理 ~ 里的待更新文件; 回滚备份保留在 $BAK)"
else
  echo "✗ 健康/readiness 检查失败! 自动回滚到更新前完整版本 ..."
  if rollback_and_verify "candidate_health_or_readiness_failed"; then
    echo "✓ 回滚验收完成。请检查新文件后重试。"
  else
    echo "✗✗ 回滚后仍未恢复! 手动排查: sudo journalctl -u $SVC -n 50 --no-pager" >&2
  fi
  exit 1
fi
