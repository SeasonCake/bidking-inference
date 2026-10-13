# -*- coding: utf-8 -*-
"""打包前后核对【会被装进包的引擎数据文件】有没有被改动。

为什么需要它
------------
`scripts/package_data_manifest.json` 是构建复制集合与本检测器冻结集合的共同真源。
清单中的文件会进入便携包；缺少、变动或源码树混入 BKENC1/BKENC2 密文都会 fail-closed。
`data/processed/upload_config.enc` 是每次构建生成物，不属于源码清单。

而本仓库是**多窗口共享**的,并且:
- 这些文件全部 **gitignored**(各机本地生成)⇒ `git status` **看不到它们被改**。
- 2026-07-12 实况: 调参窗口为跑 workflow 建了 4 个 git worktree,把 `data/` 用 **junction 挂到主仓
  同一份物理目录**,4 个 agent 并发跑 sweep。它们被口头警告"只读",但**警告不是隔离**。

⇒ 任何一个 agent 往 `data/` 写一个字节,打出去的包就带着**被污染的引擎数据**,而且是**加密的** ——
  没有任何人会发现,直到群友的估价开始出错。**git 在这里保护不了你。**

用法
----
    python scripts/check_package_data_integrity.py --snapshot   # 生成待审候选，不覆盖批准基线
    python scripts/check_package_data_integrity.py --verify     # 打包【之前】核对
    python scripts/check_package_data_integrity.py --list-json  # 构建脚本读取复制清单

批准基线是 tracked 文件 `scripts/package_data_fingerprints.json`，其自身和统一复制清单的
SHA-256 都会写入 BUILD_MANIFEST。这样 SourceCommit + manifest 中的两个哈希即可追溯数据门。
⚠️ 合法的重新生成(`scripts/calibration/regenerate_drop_weights.py`)也会让 hash 变 ——
   那种情况下 **--verify 报红是对的**: `--snapshot` 只能生成 gitignored 候选文件；必须独立
   审查差异后，才可显式更新并提交批准基线。构建过程绝不自动接受未知变化。
   这道门只回答"内容变了吗", 不回答"变得对不对"; 后者由 pytest gate
   `tests/test_red_longtail_value_norm.py`(陈旧性)守。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
# ⚠️ 基线【必须放在 data/ 外面】。0712 实翻车: 它原来在 data/.package_data_fingerprints.json,
#    而事故正是【整个 data/ 被删】—— 护栏和它要保护的东西一起没了, 事后只能靠临时目录里的一份副本救场。
#    **护栏不能住在被保护物内部。**
_MANIFEST = _ROOT / "scripts" / "package_data_manifest.json"
_BASELINE = _ROOT / "scripts" / "package_data_fingerprints.json"
_CANDIDATE = _ROOT / ".package_data_fingerprints.candidate.json"
_ENGINE_SOURCE = _ROOT / "hero_ref" / "src" / "ahmad_ref_engine.py"
_ENGINE_BASELINE_NAME = "AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID"
_ENGINE_RANKMAP_GENERATION_NAME = "CURRENT_RANKMAP_PACKAGE_GENERATION"
_ENGINE_RANKMAP_FILES_NAME = "_CURRENT_RANKMAP_GENERATION_FILES"
_ENGINE_AUCTION_HALL_RELATIVE_NAME = "AUCTION_HALL_6001_SHADOW_RELATIVE"
_ENGINE_AUCTION_HALL_BYTES_NAME = "AUCTION_HALL_6001_SHADOW_BYTES"
_ENGINE_AUCTION_HALL_SHA256_NAME = "AUCTION_HALL_6001_SHADOW_SHA256"
_ENGINE_RANKMAP_EXPECTED_FILES = (
    "data/raw/tables/Item.txt",
    "data/raw/tables/BidMap.txt",
    "data/raw/tables/RankMap.txt",
    "data/processed/rankmap_drop_weights_current.json",
    "data/processed/rankmap_drop_priors_current.json",
    "data/processed/rankmap_distribution_authority_current.json",
)
_CURRENT_RANKMAP_AUTHORITY = (
    _ROOT / "data" / "processed" / "rankmap_distribution_authority_current.json"
)
_MANIFEST_SCHEMA = "bidking_package_data_manifest_v1"
_BASELINE_SCHEMA = "bidking_package_data_fingerprints_v1"
_CANDIDATE_SCHEMA = "bidking_package_data_fingerprints_candidate_v1"
_MANIFEST_REL = "scripts/package_data_manifest.json"
_GENERATED_UPLOAD_CONFIG = "data/processed/upload_config.enc"


class ManifestError(ValueError):
    pass


def _load_manifest() -> tuple[str, ...]:
    if not _MANIFEST.is_file():
        raise ManifestError(f"缺统一发包数据清单: {_MANIFEST}")
    try:
        value = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"统一发包数据清单不可读: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != _MANIFEST_SCHEMA:
        raise ManifestError(f"统一发包数据清单 schema 必须为 {_MANIFEST_SCHEMA}")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise ManifestError("统一发包数据清单 files 必须是非空列表")

    normalized: list[str] = []
    for raw in files:
        if not isinstance(raw, str) or not raw.strip():
            raise ManifestError("统一发包数据清单包含空路径或非字符串路径")
        rel = raw.strip().replace("\\", "/")
        path = Path(rel)
        if (
            path.is_absolute()
            or rel.startswith("/")
            or ".." in path.parts
            or rel != path.as_posix()
            or not rel.startswith(("data/processed/", "data/raw/tables/"))
        ):
            raise ManifestError(f"统一发包数据清单路径越界或未规范化: {raw!r}")
        if rel == _GENERATED_UPLOAD_CONFIG:
            raise ManifestError(
                f"{_GENERATED_UPLOAD_CONFIG} 是构建生成物，不得进入源码复制清单"
            )
        normalized.append(rel)
    if len(normalized) != len(set(normalized)):
        raise ManifestError("统一发包数据清单包含重复路径")
    return tuple(normalized)


try:
    SHIPPED = _load_manifest()
    _MANIFEST_ERROR = None
except ManifestError as exc:
    SHIPPED = ()
    _MANIFEST_ERROR = str(exc)

_ENC_MAGICS = (b"BKENC1", b"BKENC2")


def _configure_cli_stdio() -> None:
    """Keep diagnostics fail-loud even on legacy Windows console encodings."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def check_plaintext() -> list[str]:
    """🔴 这些文件在【源码树里必须是明文】。返回是密文的那些。

    为什么单独查这个(hash 比对不够)
    ------------------------------
    0712 实翻车: `data/` 被误删后, 有人从 **加密包**(`dist/bidking-live-*-encrypted/`)里恢复 ——
    包里的 `quality_combo_presolve_q456.json` 是 BKENC 密文，被原样拷回了源码树。
    源码模式下引擎读不了它, 于是【静默退回默认值】—— 而所有跑在那棵树上的 sweep / A-B / 标定
    全部作废, 且没有任何报错。

    这道门【不需要基线】就能抓到它 —— 而事故当时基线恰好也被删了。所以它是最后一道防线。
    BKENC1 是旧版原字节加密；BKENC2 会先压缩再加密。两者每次都使用新 nonce，因此同一
    明文每次打包的密文 hash 都不同，不能用两个包的密文 hash 判断数据版本。
    """
    bad = []
    for rel in SHIPPED:
        p = _ROOT / rel
        if p.exists() and p.read_bytes()[:6] in _ENC_MAGICS:
            bad.append(rel)
    return bad


def fingerprint() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rel in SHIPPED:
        p = _ROOT / rel
        if not p.exists():
            out[rel] = {"missing": True}
            continue
        b = p.read_bytes()
        out[rel] = {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}
    return out


def _fingerprint_records(files: dict[str, dict]) -> str:
    """Hash one exact path/bytes/SHA identity set without reading file bodies."""

    payload = [
        {
            "path": rel,
            "bytes": record["size"],
            "sha256": record["sha256"],
        }
        for rel, record in sorted(files.items())
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_sha256() -> str:
    return hashlib.sha256(_MANIFEST.read_bytes()).hexdigest()


def _static_engine_value(node: ast.AST) -> object:
    """Return a direct AST literal/container value, never an alias or expression."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Tuple):
        return tuple(_static_engine_value(item) for item in node.elts)
    if isinstance(node, ast.List):
        return [_static_engine_value(item) for item in node.elts]
    if isinstance(node, ast.Dict):
        result: dict[object, object] = {}
        for key_node, value_node in zip(node.keys, node.values, strict=True):
            if key_node is None:
                raise ManifestError("引擎 package authority 不能使用字典展开")
            key = _static_engine_value(key_node)
            try:
                duplicate = key in result
            except TypeError as exc:
                raise ManifestError("引擎 package authority 包含不可哈希键") from exc
            if duplicate:
                raise ManifestError("引擎 package authority 包含重复键")
            result[key] = _static_engine_value(value_node)
        return result
    raise ManifestError("引擎 package authority 必须只使用静态字面量")


def _target_names(target: ast.AST) -> set[str]:
    """Names bound by an assignment target, including destructuring targets."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(item) for item in target.elts))
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return set()


def _pattern_names(pattern: ast.pattern) -> set[str]:
    """Names a match pattern writes when evaluated in module execution scope."""
    if isinstance(pattern, ast.MatchAs):
        names = _pattern_names(pattern.pattern) if pattern.pattern is not None else set()
        if pattern.name is not None:
            names.add(pattern.name)
        return names
    if isinstance(pattern, ast.MatchStar):
        return {pattern.name} if pattern.name is not None else set()
    if isinstance(pattern, ast.MatchMapping):
        names = set().union(*(_pattern_names(item) for item in pattern.patterns))
        if pattern.rest is not None:
            names.add(pattern.rest)
        return names
    if isinstance(pattern, ast.MatchSequence):
        return set().union(*(_pattern_names(item) for item in pattern.patterns))
    if isinstance(pattern, ast.MatchClass):
        return set().union(
            *(_pattern_names(item) for item in (*pattern.patterns, *pattern.kwd_patterns))
        )
    if isinstance(pattern, ast.MatchOr):
        return set().union(*(_pattern_names(item) for item in pattern.patterns))
    return set()


def _module_bindings(module: ast.Module) -> dict[str, list[tuple[str, ast.AST]]]:
    """Census every binding that can execute in module scope.

    Function/class bodies establish a local/class namespace and are intentionally not
    traversed. Their definition names, decorators, bases and defaults execute in the
    enclosing module scope and are recorded accordingly.
    """
    bindings: dict[str, list[tuple[str, ast.AST]]] = {}

    star_import_marker = "<module-import-star>"

    def record(names: set[str], kind: str, node: ast.AST) -> None:
        for name in names:
            bindings.setdefault(name, []).append((kind, node))

    def record_context(
        names: set[str],
        kind: str,
        node: ast.AST,
        class_globals: set[str] | None,
    ) -> None:
        if class_globals is None:
            record(names, kind, node)
        else:
            record(names & class_globals, f"class-global-{kind}", node)

    def class_global_names(nodes: list[ast.stmt]) -> set[str]:
        names: set[str] = set()

        def visit(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return
            if isinstance(node, ast.Global):
                names.update(node.names)
                return
            for child in ast.iter_child_nodes(node):
                visit(child)

        for node in nodes:
            visit(node)
        return names

    def expression(node: ast.AST | None, class_globals: set[str] | None) -> None:
        if node is None:
            return

        class NamedExpressionVisitor(ast.NodeVisitor):
            def visit_Lambda(self, _node: ast.Lambda) -> None:
                # A lambda's assignment-expression target is local to that lambda.
                return

            def visit_NamedExpr(self, named: ast.NamedExpr) -> None:
                record_context(
                    _target_names(named.target),
                    "namedexpr",
                    named,
                    class_globals,
                )
                self.generic_visit(named)

        NamedExpressionVisitor().visit(node)

    def statements(
        nodes: list[ast.stmt],
        *,
        module_body: bool = False,
        class_globals: set[str] | None = None,
    ) -> None:
        for node in nodes:
            statement(
                node,
                module_body=module_body,
                class_globals=class_globals,
            )

    def except_handler(
        handler: ast.ExceptHandler, class_globals: set[str] | None
    ) -> None:
        expression(handler.type, class_globals)
        if handler.name is not None:
            record_context({handler.name}, "except", handler, class_globals)
        statements(handler.body, class_globals=class_globals)

    def statement(
        node: ast.stmt,
        *,
        module_body: bool = False,
        class_globals: set[str] | None = None,
    ) -> None:
        if isinstance(node, ast.Assign):
            names = set().union(*(_target_names(target) for target in node.targets))
            kind = (
                "assign"
                if module_body
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                else "assign-suite"
            )
            record_context(names, kind, node, class_globals)
            expression(node.value, class_globals)
        elif isinstance(node, ast.AnnAssign):
            record_context(
                _target_names(node.target), "annassign", node, class_globals
            )
            expression(node.annotation, class_globals)
            expression(node.value, class_globals)
        elif isinstance(node, ast.AugAssign):
            record_context(
                _target_names(node.target), "augassign", node, class_globals
            )
            expression(node.value, class_globals)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            record_context(_target_names(node.target), "for", node, class_globals)
            expression(node.iter, class_globals)
            statements(node.body, class_globals=class_globals)
            statements(node.orelse, class_globals=class_globals)
        elif isinstance(node, ast.While):
            expression(node.test, class_globals)
            statements(node.body, class_globals=class_globals)
            statements(node.orelse, class_globals=class_globals)
        elif isinstance(node, ast.If):
            expression(node.test, class_globals)
            statements(node.body, class_globals=class_globals)
            statements(node.orelse, class_globals=class_globals)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                expression(item.context_expr, class_globals)
                if item.optional_vars is not None:
                    record_context(
                        _target_names(item.optional_vars),
                        "with",
                        node,
                        class_globals,
                    )
            statements(node.body, class_globals=class_globals)
        elif isinstance(
            node,
            (ast.Try,)
            + ((ast.TryStar,) if hasattr(ast, "TryStar") else ()),
        ):
            statements(node.body, class_globals=class_globals)
            for handler in node.handlers:
                except_handler(handler, class_globals)
            statements(node.orelse, class_globals=class_globals)
            statements(node.finalbody, class_globals=class_globals)
        elif isinstance(node, ast.Match):
            expression(node.subject, class_globals)
            for case in node.cases:
                record_context(
                    _pattern_names(case.pattern), "match", case.pattern, class_globals
                )
                expression(case.guard, class_globals)
                statements(case.body, class_globals=class_globals)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            record_context({node.name}, "function", node, class_globals)
            for decorator in node.decorator_list:
                expression(decorator, class_globals)
            expression(node.returns, class_globals)
            for default in (*node.args.defaults, *node.args.kw_defaults):
                expression(default, class_globals)
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                expression(argument.annotation, class_globals)
            if node.args.vararg is not None:
                expression(node.args.vararg.annotation, class_globals)
            if node.args.kwarg is not None:
                expression(node.args.kwarg.annotation, class_globals)
        elif isinstance(node, ast.ClassDef):
            record_context({node.name}, "class", node, class_globals)
            for decorator in node.decorator_list:
                expression(decorator, class_globals)
            for base in node.bases:
                expression(base, class_globals)
            for keyword in node.keywords:
                expression(keyword.value, class_globals)
            statements(node.body, class_globals=class_global_names(node.body))
        elif node.__class__.__name__ == "TypeAlias":
            # TypeAlias is a Python 3.12+ module binding. Never treat it as authority.
            record(_target_names(node.name), "typealias", node)
            expression(getattr(node, "value", None), class_globals)
        elif isinstance(node, ast.Delete):
            record_context(
                set().union(*(_target_names(target) for target in node.targets)),
                "delete",
                node,
                class_globals,
            )
        elif isinstance(node, ast.Import):
            record_context(
                {
                    alias.asname or alias.name.split(".", 1)[0]
                    for alias in node.names
                },
                "import",
                node,
                class_globals,
            )
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                record({star_import_marker}, "import-star", node)
            else:
                record_context(
                    {alias.asname or alias.name for alias in node.names},
                    "import",
                    node,
                    class_globals,
                )
        elif isinstance(node, ast.Global):
            if class_globals is None:
                record(set(node.names), "global", node)
        elif isinstance(node, ast.Nonlocal):
            if class_globals is None:
                record(set(node.names), "nonlocal", node)
        else:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    expression(child, class_globals)

    statements(module.body, module_body=True)
    return bindings


def _exact_module_assignment(
    bindings: dict[str, list[tuple[str, ast.AST]]], name: str
) -> ast.Assign:
    if "<module-import-star>" in bindings:
        raise ManifestError("引擎 package authority 不允许 from ... import *")
    entries = bindings.get(name, [])
    if len(entries) != 1 or entries[0][0] != "assign":
        raise ManifestError(f"引擎 package authority {name} 必须且只能有一次普通模块赋值")
    assignment = entries[0][1]
    assert isinstance(assignment, ast.Assign)
    return assignment


def _member_literal_name(
    node: ast.Name, bindings: dict[str, list[tuple[str, ast.AST]]]
) -> object:
    """Permit the frozen engine's one-hop direct size/SHA constants only."""
    if not (
        node.id.startswith("AISHA_Q5_TOTAL_CELLS_")
        and node.id.endswith(("_BYTES", "_SHA256"))
    ):
        raise ManifestError("引擎 package authority 成员不得使用别名")
    assignment = _exact_module_assignment(bindings, node.id)
    value = _static_engine_value(assignment.value)
    if type(value) is int and not isinstance(value, bool):
        return value
    if type(value) is str:
        return value
    raise ManifestError("引擎 package authority 成员别名必须指向直接字符串或非布尔整数")


def _member_spec_value(
    node: ast.AST, bindings: dict[str, list[tuple[str, ast.AST]]]
) -> object:
    if isinstance(node, ast.Name):
        return _member_literal_name(node, bindings)
    return _static_engine_value(node)


def _direct_rankmap_members(
    node: ast.AST, bindings: dict[str, list[tuple[str, ast.AST]]]
) -> dict[object, object]:
    if not isinstance(node, ast.Dict):
        raise ManifestError("引擎 current-rankmap authority 必须是静态对象")
    result: dict[object, object] = {}
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        if key_node is None:
            raise ManifestError("引擎 current-rankmap authority 不能使用字典展开")
        key = _static_engine_value(key_node)
        if key in result:
            raise ManifestError("引擎 package authority 包含重复键")
        if not isinstance(value_node, ast.Tuple):
            raise ManifestError("引擎 current-rankmap authority 记录必须是直接元组")
        result[key] = tuple(
            _member_spec_value(item, bindings) for item in value_node.elts
        )
    return result


def _load_engine_package_contract(
    path: Path = _ENGINE_SOURCE,
) -> tuple[str, str, dict[str, tuple[int, str]]]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ManifestError("引擎 package baseline 常量不可读取或解析") from exc
    bindings = _module_bindings(module)
    baseline = _static_engine_value(
        _exact_module_assignment(bindings, _ENGINE_BASELINE_NAME).value
    )
    generation = _static_engine_value(
        _exact_module_assignment(bindings, _ENGINE_RANKMAP_GENERATION_NAME).value
    )
    rankmap_values = _direct_rankmap_members(
        _exact_module_assignment(bindings, _ENGINE_RANKMAP_FILES_NAME).value,
        bindings,
    )
    if type(baseline) is not str or not baseline.strip():
        raise ManifestError("引擎 package baseline 常量必须唯一且非空")
    if not rankmap_values:
        raise ManifestError("引擎 current-rankmap authority 必须唯一且非空")
    if type(generation) is not str or not generation.strip():
        raise ManifestError(
            "引擎 current-rankmap generation 必须唯一且非空"
        )
    normalized: dict[str, tuple[int, str]] = {}
    for rel, record in rankmap_values.items():
        if (
            type(rel) is not str
            or type(record) is not tuple
            or len(record) != 2
            or type(record[0]) is not int
            or record[0] < 0
            or type(record[1]) is not str
            or len(record[1]) != 64
            or any(char not in "0123456789abcdef" for char in record[1])
        ):
            raise ManifestError("引擎 current-rankmap authority 记录格式非法")
        normalized[rel] = record
    expected_files = set(_ENGINE_RANKMAP_EXPECTED_FILES)
    if generation == "76b7ca-2026-10-08-723":
        expected_files.update(("data/processed/drop_weights_all_maps_0627.json",
                               "data/processed/drop_table_priors_0627.json"))
    if set(normalized) != expected_files:
        raise ManifestError("引擎 current-rankmap authority 成员集合不完整")
    return baseline, generation, normalized


def _load_engine_package_baseline_id(path: Path = _ENGINE_SOURCE) -> str:
    baseline_id, _rankmap_generation, _rankmap_files = (
        _load_engine_package_contract(path)
    )
    return baseline_id


def _load_engine_auction_hall_6001_contract(
    path: Path = _ENGINE_SOURCE,
) -> tuple[str, int, str]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ManifestError("引擎 6001 provisional 常量不可读取或解析") from exc
    bindings = _module_bindings(module)
    relative = _static_engine_value(
        _exact_module_assignment(bindings, _ENGINE_AUCTION_HALL_RELATIVE_NAME).value
    )
    size = _static_engine_value(
        _exact_module_assignment(bindings, _ENGINE_AUCTION_HALL_BYTES_NAME).value
    )
    digest = _static_engine_value(
        _exact_module_assignment(bindings, _ENGINE_AUCTION_HALL_SHA256_NAME).value
    )
    if (
        relative != "data/processed/auction_hall_6001_shadow_distribution.json"
        or type(size) is not int
        or isinstance(size, bool)
        or size <= 0
        or type(digest) is not str
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ManifestError("引擎 6001 provisional 成员身份格式非法")
    return relative, size, digest


def _load_engine_shipwreck_calibrated_contract(path: Path = _ENGINE_SOURCE) -> tuple[str, int, str]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ManifestError("引擎沉船校准常量不可读取或解析") from exc
    bindings = _module_bindings(module)
    relative, size, digest = tuple(_static_engine_value(_exact_module_assignment(
        bindings, "SHIPWRECK_CALIBRATED_" + suffix).value)
        for suffix in ("RELATIVE", "BYTES", "SHA256"))
    if (relative != "data/processed/shipwreck_calibrated_b049.json"
        or type(size) is not int or size <= 0 or type(digest) is not str
        or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
        raise ManifestError("引擎沉船校准成员身份格式非法")
    return relative, size, digest


def _load_approved_baseline() -> tuple[dict[str, dict], str]:
    try:
        value = json.loads(_BASELINE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"批准基线不可读: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != _BASELINE_SCHEMA:
        raise ManifestError(f"批准基线 schema 必须为 {_BASELINE_SCHEMA}")
    if value.get("manifest") != _MANIFEST_REL:
        raise ManifestError(f"批准基线 manifest 必须为 {_MANIFEST_REL}")
    baseline_id = value.get("baseline_id")
    if not isinstance(baseline_id, str) or not baseline_id.strip():
        raise ManifestError("批准基线 baseline_id 必须是非空字符串")
    (
        engine_baseline_id,
        engine_rankmap_generation,
        engine_rankmap_files,
    ) = _load_engine_package_contract()
    if baseline_id != engine_baseline_id:
        raise ManifestError(
            "批准基线 baseline_id 与引擎固定 package authority 不一致"
        )
    if value.get("manifest_sha256") != _manifest_sha256():
        raise ManifestError("批准基线记录的 manifest_sha256 与当前统一清单不一致")
    files = value.get("files")
    if not isinstance(files, dict):
        raise ManifestError("批准基线 files 必须是对象")
    for rel, record in files.items():
        if not isinstance(rel, str) or not isinstance(record, dict):
            raise ManifestError("批准基线包含非法路径或记录")
        digest = record.get("sha256")
        size = record.get("size")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise ManifestError(f"批准基线记录格式非法: {rel}")
    source_validation = value.get("source_validation")
    if (
        type(source_validation) is not dict  # noqa: E721
        or type(source_validation.get("package_fingerprint")) is not str  # noqa: E721
        or source_validation["package_fingerprint"]
        != _fingerprint_records(files)
    ):
        raise ManifestError(
            "批准基线 source_validation package_fingerprint 与 files 不一致"
        )
    for rel, (expected_size, expected_sha) in engine_rankmap_files.items():
        record = files.get(rel)
        if (
            type(record) is not dict
            or record.get("size") != expected_size
            or record.get("sha256") != expected_sha
        ):
            raise ManifestError(
                "批准基线 current-rankmap 成员与引擎固定 authority 不一致"
            )
    sidecar_rel, sidecar_size, sidecar_sha = (
        _load_engine_auction_hall_6001_contract()
    )
    sidecar_record = files.get(sidecar_rel)
    if (
        type(sidecar_record) is not dict
        or sidecar_record.get("size") != sidecar_size
        or sidecar_record.get("sha256") != sidecar_sha
    ):
        raise ManifestError(
            "批准基线 6001 provisional 成员与引擎固定 identity 不一致"
        )
    calibrated_rel, calibrated_size, calibrated_sha = _load_engine_shipwreck_calibrated_contract()
    if files.get(calibrated_rel) != {"size": calibrated_size, "sha256": calibrated_sha}:
        raise ManifestError("批准基线沉船校准成员与引擎固定 identity 不一致")
    return files, engine_rankmap_generation


def _verify_current_rankmap_generation(expected_generation: str) -> None:
    try:
        rankmap_authority = json.loads(
            _CURRENT_RANKMAP_AUTHORITY.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("current-rankmap authority 不可读") from exc
    if (
        type(rankmap_authority) is not dict
        or rankmap_authority.get("package_generation")
        != expected_generation
    ):
        raise ManifestError(
            "current-rankmap generation 与引擎固定 authority 不一致"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot", action="store_true", help="生成待审候选，不覆盖批准基线")
    g.add_argument("--verify", action="store_true", help="与基线核对(打包前跑)")
    g.add_argument("--list-json", action="store_true", help="机器可读输出统一复制清单")
    args = ap.parse_args()

    if _MANIFEST_ERROR:
        print(f"✗ {_MANIFEST_ERROR}")
        return 2
    if args.list_json:
        print(json.dumps(list(SHIPPED), ensure_ascii=True, separators=(",", ":")))
        return 0

    now = fingerprint()
    missing = [k for k, v in now.items() if v.get("missing")]

    # 🔴 第 0 道门: 密文检查。【不依赖基线】—— 而 0712 那次事故, 基线自己也被删了。
    enc = check_plaintext()
    if enc:
        print("🔴 这些文件在源码树里是【BKENC1/BKENC2 密文】, 不是明文:")
        for rel in enc:
            print(f"     {rel}")
        print("\n   几乎可以肯定: 有人从【加密包】(dist/bidking-live-*-encrypted/)里恢复了 data/。")
        print("   源码模式下引擎读不了密文 -> 【静默退回默认值】-> 跑在这棵树上的 sweep / A-B / 标定全部作废,")
        print("   而且没有任何报错。**停手, 先把它们换回明文。**")
        print("   明文来源: dist 里的 *-portable 包(未加密), 或用【打包时那把 key】解密 *-encrypted 包里的同名文件。")
        print("   ⚠️ 别用 build_quality_combo_presolve.py 重新生成 —— 默认参数产出 18.0 MB, 与真本(15.7 MB)不符。")
        return 1

    if args.snapshot:
        if missing:
            print(f"✗ 这些文件不存在, 无法存基线: {missing}")
            print("  打包脚本会因此 throw。先跑 scripts/copy_game_tables.ps1 / regenerate_drop_weights.py。")
            return 2
        candidate = {
            "schema": _CANDIDATE_SCHEMA,
            "approved_baseline": _BASELINE.relative_to(_ROOT).as_posix(),
            "manifest": _MANIFEST_REL,
            "manifest_sha256": _manifest_sha256(),
            "generated_at": time.time(),
            "files": now,
        }
        _CANDIDATE.write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"✓ 待审候选已生成（未修改批准基线）: {_CANDIDATE}")
        print("  独立审查差异后，才可显式更新 scripts/package_data_fingerprints.json 并提交。")
        for rel, v in now.items():
            print(f"    {v['sha256'][:16]}  {v['size']:>9,} B  {rel}")
        return 0

    # --verify
    if not _BASELINE.exists():
        print(f"✗ 缺 tracked 批准基线({_BASELINE})。构建不得用 --snapshot 自动替代。")
        return 2
    try:
        base, engine_rankmap_generation = _load_approved_baseline()
    except ManifestError as exc:
        print(f"✗ 基线文件不可读或格式错误: {exc}")
        return 2

    manifest_set = set(SHIPPED)
    baseline_set = set(base)
    if manifest_set != baseline_set:
        print("🔴 冻结基线与统一发包数据清单不一致，拒绝打包。")
        for rel in sorted(manifest_set - baseline_set):
            print(f"     基线缺少: {rel}")
        for rel in sorted(baseline_set - manifest_set):
            print(f"     基线多出: {rel}")
        print("   先生成待审候选并独立审查；构建过程不得自动刷新批准基线。")
        return 1

    bad: list[str] = []
    for rel in SHIPPED:
        b, n = base.get(rel, {}), now.get(rel, {})
        if n.get("missing"):
            print(f"  🔴 {rel}: 文件不见了")
            bad.append(rel)
        elif b.get("sha256") != n.get("sha256"):
            print(f"  🔴 {rel}: 内容变了")
            print(f"       基线 {b.get('sha256','?')[:16]}  {b.get('size',0):>9,} B")
            print(f"       现在 {n.get('sha256','?')[:16]}  {n.get('size',0):>9,} B")
            bad.append(rel)
        else:
            print(f"  ✓  {rel}")

    if bad:
        print(f"\n🔴 {len(bad)} 个【会进包并被加密】的引擎数据文件被改动了。**停手,别打包。**")
        print("   排查方向:")
        print("     · 别的窗口的 workflow agent 是否往 data/ 写了东西?(worktree 的 data/ 可能是 junction)")
        print("     · 是不是你自己有意重跑了 regenerate_drop_weights.py? 是的话先生成候选并独立审查。")
        print("   ⚠️ 这些文件 gitignored ⇒ git status 看不到它们被改 ⇒ 只有这道门能发现。")
        return 1

    try:
        _verify_current_rankmap_generation(engine_rankmap_generation)
    except ManifestError as exc:
        print(f"✗ 引擎 package authority 不一致: {exc}")
        return 2

    print("\n✓ 全部一致 —— 会进包的引擎数据没被动过, 可以打包。")
    return 0


if __name__ == "__main__":
    _configure_cli_stdio()
    sys.exit(main())
