"""/public 重算节流(作者 0712)设计门。

为什么要节流: 群友在持续上传 -> samples.jsonl 几乎一直在变 -> 若"数据变了就重建", 每次有人打开
公开页都可能触发一次 3 万行全量聚合。而 collector 和【激活服务是同一个进程】—— 白烧的 CPU 会
拖慢别人激活。公共统计是长期数据(掉率), 晚半小时毫无影响。

⭐两条容易写错的规则:
  1. 数据【变了】但没到间隔 -> 仍然吃缓存(这条最容易漏, 一漏节流就完全失效)
  2. 数据【没变】-> 永远吃缓存(哪怕过了间隔, 也没必要重算)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import sample_collector_server as scs  # noqa: E402


def _fresh_cache(monkeypatch):
    monkeypatch.setattr(scs, "_PUB_CACHE",
                        {"key": None, "plain": None, "gz": None, "etag": None,
                         "built_mono": 0.0, "built_wall": 0.0})


def _write(out: Path, n: int) -> None:
    """写 n 条样本(每次内容不同 -> mtime/size 变 -> 模拟群友持续上传)。"""
    import json
    lines = []
    for i in range(n):
        lines.append(json.dumps({
            "map_id": 2401, "final_quality_counts": "q1=1;q6=1",
            "known_value_sum": 1000 + i, "session_hash": f"s{i}",
        }, ensure_ascii=False))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _count_builds(monkeypatch) -> list:
    """记录 aggregate_public 被真正调用了几次(= 真重建了几次)。"""
    import public_stats
    calls = []
    real = public_stats.aggregate_public

    def spy(samples):
        calls.append(1)
        return real(samples)

    monkeypatch.setattr(public_stats, "aggregate_public", spy)
    return calls


def test_first_request_builds(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    _write(out, 5)
    monkeypatch.setattr(scs, "_OUT", out)
    _fresh_cache(monkeypatch)
    calls = _count_builds(monkeypatch)

    scs._public_bodies()
    assert len(calls) == 1


def test_unchanged_data_never_rebuilds(tmp_path, monkeypatch):
    """规则 2: 数据没变 -> 永远吃缓存(哪怕过了 30 分钟也不必重算)。"""
    out = tmp_path / "samples.jsonl"
    _write(out, 5)
    monkeypatch.setattr(scs, "_OUT", out)
    _fresh_cache(monkeypatch)
    calls = _count_builds(monkeypatch)

    scs._public_bodies()
    assert len(calls) == 1
    # 把上次重建时间推到很久以前(模拟过了 30 分钟), 但数据没变
    scs._PUB_CACHE["built_mono"] = time.monotonic() - 10 * scs.PUBLIC_REBUILD_INTERVAL
    for _ in range(5):
        scs._public_bodies()
    assert len(calls) == 1, "数据没变却重算了"


def test_changed_data_within_interval_still_uses_cache(tmp_path, monkeypatch):
    """⭐规则 1(最容易漏): 数据变了, 但距上次重建不足 30 分钟 -> 仍然吃缓存, 不重算。"""
    out = tmp_path / "samples.jsonl"
    _write(out, 5)
    monkeypatch.setattr(scs, "_OUT", out)
    _fresh_cache(monkeypatch)
    calls = _count_builds(monkeypatch)

    scs._public_bodies()
    assert len(calls) == 1

    for i in range(6, 12):          # 群友持续上传 -> 文件一直在变
        _write(out, i)
        scs._public_bodies()
    assert len(calls) == 1, f"节流失效: 数据一变就重算了({len(calls)} 次)"


def test_changed_data_after_interval_rebuilds(tmp_path, monkeypatch):
    """过了间隔且数据确实变了 -> 才重建(不然页面永远不更新)。"""
    out = tmp_path / "samples.jsonl"
    _write(out, 5)
    monkeypatch.setattr(scs, "_OUT", out)
    _fresh_cache(monkeypatch)
    calls = _count_builds(monkeypatch)

    scs._public_bodies()
    assert len(calls) == 1

    _write(out, 9)                                     # 数据变了
    scs._PUB_CACHE["built_mono"] = time.monotonic() - scs.PUBLIC_REBUILD_INTERVAL - 1  # 过了间隔
    scs._public_bodies()
    assert len(calls) == 2, "过了间隔且数据变了, 却没重建 -> 页面会永远停在旧数据"


def test_interval_is_30_minutes():
    assert scs.PUBLIC_REBUILD_INTERVAL == 1800.0


def test_page_states_update_time_and_frequency(tmp_path, monkeypatch):
    """页面必须明说非实时，并区分最新游玩日与页面生成时间。"""
    import public_stats
    html = public_stats.build_public_html(
        public_stats.aggregate_public([]),
        generated_at=time.time(), rebuild_interval=1800.0,
    )
    assert "30 分钟更新一次" in html
    assert "最新游玩日" in html
    assert "页面生成于" in html


def test_missing_samples_file_does_not_crash(tmp_path, monkeypatch):
    """样本文件还不存在(全新部署)时公开页也要能出来, 不能 500。"""
    monkeypatch.setattr(scs, "_OUT", tmp_path / "nope.jsonl")
    _fresh_cache(monkeypatch)
    plain, gz, etag = scs._public_bodies()
    assert b"<html" in plain.lower() or b"<!doctype" in plain.lower()


# --------------------------------------------------------------------------- #
# 启动预热: 重启后第一个访客不该替所有人跑一次全量聚合
# --------------------------------------------------------------------------- #
def test_warm_public_cache_fills_cache_without_blocking(tmp_path, monkeypatch):
    """⭐collector 每次部署都重启 -> _PUB_CACHE 清空 -> 首个访客同步重建(实测线上 >10s 超时)。
    启动预热必须: ① 真的把缓存填上 ② 在【后台线程】里跑, 绝不阻塞 serve_forever
    (阻塞 = 端口迟迟不 listen = server_update.sh 健康检查误判成启动失败 -> 假回滚)。"""
    import threading
    import time as _t

    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps({
        "map_id": 2501, "hero": "ahmed", "final_quality_counts": "q1=2;q5=1;q6=2",
        "known_value_sum": 500000, "received_ts": _t.time(),
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(scs, "_OUT", out)
    _fresh_cache(monkeypatch)
    assert scs._PUB_CACHE["plain"] is None          # 起始: 冷缓存

    before = threading.active_count()
    scs._warm_public_cache()                        # 必须【立即】返回
    assert threading.active_count() >= before       # 起了后台线程

    for _ in range(50):                             # 等后台线程填好(<5s)
        if scs._PUB_CACHE["plain"] is not None:
            break
        _t.sleep(0.1)
    assert scs._PUB_CACHE["plain"] is not None, "预热没把缓存填上"


def test_warm_public_cache_never_crashes_the_server(tmp_path, monkeypatch, capsys):
    """⭐预热只是优化 —— 它失败绝不能拖垮服务(退回"首个访客重建"的旧行为即可)。"""
    import time as _t

    def _boom():
        raise RuntimeError("模拟聚合炸了")

    monkeypatch.setattr(scs, "_public_bodies", _boom)
    scs._warm_public_cache()                        # 不许抛
    _t.sleep(0.3)
    assert "预热失败" in capsys.readouterr().out    # 只打一行, 服务继续
