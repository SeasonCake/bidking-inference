"""🔒 /public 防搜索引擎索引(作者 0712 定:"群内自用,不要外传")的设计门。

为什么这道门值钱
----------------
`/public` 现在【免登录、公网可达】,"不要外传"只是口头请求、没有技术保障。
而搜索引擎的索引【收不回来】—— 就算之后加了 token 门,快照与摘要仍会留在结果页里。
所以必须在链接扩散【之前】挡住爬虫,而且**挡住这件事本身要有测试盯着**,
否则以后谁重构一下响应头/路由,防护静悄悄就没了,而没人会发现(爬虫不会来告诉你)。

三层,缺一不可
--------------
  1. `X-Robots-Tag` 响应头   -> 对 /export /stats 这类【非 HTML】响应也生效(<meta> 做不到)
  2. `/robots.txt`           -> 爬虫的第一站
  3. HTML `<meta robots>`    -> 页面被镜像/另存时仍然带着

⚠️ 本文件【真起一个 server 打真请求】,不是断言"源码里有那个字符串" ——
后者证明不了响应里真的带上了头(记忆 tk-rounded-card-winfo-ismapped-lies 是同一类教训:
只有真的把东西跑起来,才知道用户拿到的是什么)。
"""
from __future__ import annotations

import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import sample_collector_server as scs  # noqa: E402
import web_ui  # noqa: E402


@pytest.fixture()
def server():
    """真 server, 临时端口, loopback。"""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), scs.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310  loopback 自建 server
        return r.status, r.headers, r.read()


def test_every_response_carries_x_robots_tag(server):
    """第 1 层: X-Robots-Tag 在【每一个】响应上, 不只是 HTML 页。

    判据挂在 /version 而不是 /public: /version 是 JSON, 证明这个头【不依赖 HTML 渲染路径】
    —— 它是在 end_headers() 里发的, 所以任何路由(含以后新增的)都自动带上。
    """
    _, headers, _ = _get(f"{server}/version")
    tag = headers.get("X-Robots-Tag", "")
    assert "noindex" in tag, f"X-Robots-Tag 里没有 noindex: {tag!r}"
    assert "nofollow" in tag, f"X-Robots-Tag 里没有 nofollow: {tag!r}"


def test_robots_txt_disallows_everything(server):
    """第 2 层: /robots.txt 全站 Disallow。"""
    status, headers, body = _get(f"{server}/robots.txt")
    txt = body.decode("utf-8")
    assert status == 200
    assert "text/plain" in headers.get("Content-Type", "")
    assert "User-agent: *" in txt
    assert "Disallow: /" in txt


def test_robots_txt_is_served_before_the_rate_limiter(server, monkeypatch):
    """/robots.txt 必须在限流【之前】。

    被 429 挡住的爬虫【不会】把它理解成"禁止抓取" —— 它会当成临时故障, 过一阵回来重抓整站。
    所以哪怕限流已经全面触发, robots.txt 也必须给出确定的答案。
    """
    monkeypatch.setattr(scs, "_rate_ok", lambda ip: False)   # 限流全开: 其它路由一律 429
    status, _, body = _get(f"{server}/robots.txt")
    assert status == 200, "限流打开后 robots.txt 就拿不到了 —— 爬虫会当成临时故障回来重抓"
    assert "Disallow: /" in body.decode("utf-8")


def test_page_shell_carries_meta_robots():
    """第 3 层: HTML <meta robots>。页面被镜像/另存时, 响应头没了, 它还在。"""
    html = web_ui.page(title="t", active="public", body="<p>x</p>")
    assert '<meta name="robots"' in html
    assert "noindex" in html.split("<title>")[0], "meta robots 必须在 <head> 里(<title> 之前)"
