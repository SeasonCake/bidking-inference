# -*- coding: utf-8 -*-
"""验证给定包内模块能 Cython 编成 .pyd，并执行必要的包内运行时冒烟。

用途: L1 .pyd 扩展前的【逐模块体检】—— build_live_portable.ps1 的 flat-temp 编法只适合 hero_ref/src
的扁平模块; src/bidking_lab/** 的模块有包内相对导入(from .x import y), 必须【在包内原地编译】才忠实。
本脚本把 src/ 拷到 temp, 对目标模块原地 cythonize(去 docstring, 同 build), 删 .py 只留 .pyd,
再在子进程里按完整包路径 import + 取关键属性, 报每模块 PASS/FAIL。绿了才往 build 的 $mods 加。

退休: 当 build 的 .pyd 覆盖面定稿、打包冒烟纳入发布 gate 后可删本脚本(一次性体检工具)。

用法: python scripts/verify_pyd_compile.py            # 默认体检 secure/ + 3 个 live 模块
      python scripts/verify_pyd_compile.py bidking_lab/secure/activation.py ...   # 指定
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

# 默认体检集: secure 层(激活编排, 高价值非引擎) + 3 个已初判可编的 live 模块。
# inference/ 23 个(估值数学=引擎域 + 重相对导入)【本轮不在内】, 留下一轮专项 + 打包冒烟。
DEFAULT_TARGETS = [
    "bidking_lab/secure/activation.py",
    "bidking_lab/secure/data_crypt.py",
    "bidking_lab/secure/integrity.py",
    "bidking_lab/secure/antidebug.py",
    "bidking_lab/secure/rust_provider.py",
    "bidking_lab/secure/client_activate.py",
    "bidking_lab/secure/enforce.py",
    "bidking_lab/live/sample_upload.py",
    "bidking_lab/live/monitor.py",
]

# 编译用 python: 同 build 的 BuildPython(CPython3.13, ABI 与内嵌 python 一致)
BUILD_PY = r"C:\Python313\python.exe"

_SETUP = '''\
import sys
from setuptools import Extension, setup
from Cython.Build import cythonize
import Cython.Compiler.Options as _cyopt
_cyopt.docstrings = False  # 同 build: 去掉编进 .pyd 的算法 docstring(反 AI 逆向 L1)
mods = sys.argv[1:]
sys.argv = [sys.argv[0], "build_ext", "--inplace"]
compile_flags = ["/O2", "/GL", "/guard:cf"]
link_flags = ["/LTCG", "/OPT:REF", "/OPT:ICF", "/GUARD:CF",
              "/DYNAMICBASE", "/NXCOMPAT", "/HIGHENTROPYVA"]
exts = [Extension(m[:-3].replace("/", "."), [m],
                  extra_compile_args=compile_flags,
                  extra_link_args=link_flags) for m in mods]
setup(ext_modules=cythonize(exts, language_level=3, quiet=True,
                            compiler_directives={"embedsignature": False,
                                                 "emit_code_comments": False}))
'''


def _mod_name(rel: str) -> str:
    return rel[:-3].replace("/", ".")  # bidking_lab/secure/x.py -> bidking_lab.secure.x


def main(argv: list[str]) -> int:
    targets = argv or DEFAULT_TARGETS
    tmp = Path(tempfile.mkdtemp(prefix="verify_pyd_"))
    tsrc = tmp / "src"
    print(f"[verify] 拷 src -> {tsrc}", flush=True)
    shutil.copytree(SRC, tsrc, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 1) 原地编译(包内, 保相对导入)
    (tsrc / "_cyt_verify_setup.py").write_text(_SETUP, encoding="utf-8")
    print(f"[verify] cythonize {len(targets)} 模块(原地)...", flush=True)
    proc = subprocess.run(
        [BUILD_PY, "_cyt_verify_setup.py", *targets],
        cwd=tsrc, capture_output=True, text=True,
        encoding="utf-8", errors="replace",  # 子进程输出可能含非GBK字节(中文注释/箭头)->别用locale默认
    )
    if proc.returncode != 0:
        print("[verify] 编译失败:\n" + (proc.stderr or proc.stdout)[-3000:], flush=True)
        return 1

    # 2) 删 .py/.c, 只留 .pyd -> 强制 import .pyd
    results: list[tuple[str, str]] = []
    for rel in targets:
        py = tsrc / rel
        pyds = list(py.parent.glob(py.stem + "*.pyd"))
        if not pyds:
            results.append((rel, "NO_PYD"))
            continue
        py.unlink(missing_ok=True)
        (py.with_suffix(".c")).unlink(missing_ok=True)

    # 3) 子进程逐模块 import(完整包路径, 验相对导入解析)
    importer = (
        "import importlib, sys\n"
        "ok=[]; bad=[]\n"
        "for m in sys.argv[1:]:\n"
        "    try:\n"
        "        mod=importlib.import_module(m)\n"
        "        f=getattr(mod,'__file__','')\n"
        "        if m == 'bidking_lab.secure.client_activate':\n"
        "            assert mod._result_from_cache(None, device='pyd-smoke', ingest_url=None, now=0) is None\n"
        "            import os, tempfile\n"
        "            os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='pyd_activation_')\n"
        "            mod._activate_urls = lambda _ingest: ['http://127.0.0.1:1/activate']\n"
        "            r = mod.activate(ui_token='pyd-smoke-token', timeout=0.05, force_server=True)\n"
        "            assert not r.activated and r.reason == 'offline'\n"
        "        ok.append(m+('  <.pyd>' if f.endswith('.pyd') else '  <NOT pyd:'+f+'>'))\n"
        "    except Exception as e:\n"
        "        bad.append(m+'  -> '+type(e).__name__+': '+str(e)[:200])\n"
        "print('PYD_OK:'); [print('  ',x) for x in ok]\n"
        "print('PYD_FAIL:'); [print('  ',x) for x in bad]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    mod_names = [_mod_name(r) for r in targets]
    test = subprocess.run(
        [BUILD_PY, "-c", importer, *mod_names],
        cwd=tsrc, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**_env_with_src(tsrc)},
    )
    print(test.stdout, flush=True)
    if test.stderr.strip():
        print("[stderr]\n" + test.stderr[-1500:], flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"[verify] {'全部 PASS' if test.returncode == 0 else '有 FAIL(见上)'}", flush=True)
    return test.returncode


def _env_with_src(tsrc: Path) -> dict:
    import os
    e = dict(os.environ)
    e["PYTHONPATH"] = str(tsrc) + os.pathsep + e.get("PYTHONPATH", "")
    e["PYTHONIOENCODING"] = "utf-8"
    return e


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
