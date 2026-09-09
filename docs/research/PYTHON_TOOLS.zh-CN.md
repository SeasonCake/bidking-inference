# 离线 Python 小工具：重组、表差分与 OCR 文本纠错

这些实验用 Python 3.10+ 标准库和人工输入即可运行。TCP 与文本工具来自 0.3.0
历史辅助源码；表差分 CLI 是新写的独立实验，共用摘取的表解析函数。它们不进入
`auction_inference` 稳定 API，也不要求游戏、抓包驱动、OCR 模型或真实表文件。

English summary: runnable, standard-library-only research tools for TCP sequence
reassembly, explicit-schema table comparisons and historical Chinese OCR text
normalization. All bundled examples are synthetic. These are experimental APIs,
not a current game client or a semantic compatibility certification.

从仓库根目录运行：

```sh
python -B -m research.transport
python -B -m research.tables --demo
python -B -m research.tables --demo --details
python -X utf8 -B -m research.text
python -B -m unittest discover -s tests -p 'test_research_transport.py' -v
python -B -m unittest discover -s tests -p 'test_research_tables.py' -v
python -B -m unittest discover -s tests -p 'test_research_ocr.py' -v
```

## 1. TCP：恢复连续字节，观察缺口与回收

源码：[tcp_reassembly.py](../../research/transport/tcp_reassembly.py)。历史源为
2026-07-24 的 0.3.0 辅助层。公开适配去除了产品环境开关及采集器介绍，保留
`_Segment`、`_FlowState`、`TcpSeqReassembler` 的完整类体和方法行为。
2026-09-09 来源核对时，这些辅助类仍被后续私有产品复用；历史并不代表已停用。

```python
from research.transport import TcpSeqReassembler

flow = ("sender", 1, "receiver", 2)  # 两个方向使用不同四元组
gate = TcpSeqReassembler(event_limit=0)
assert gate.feed(flow, 100, b"AAAA", now=0) == b"AAAA"
assert gate.feed(flow, 108, b"CCCC", now=1) == b""
assert gate.feed(flow, 104, b"BBBB", now=2) == b"BBBBCCCC"
assert gate.feed(flow, 108, b"CCCC", now=3) == b""
```

| API | 合同与边界 |
| --- | --- |
| `feed(key, seq, payload, now=None)` | 返回新连续 bytes；`now` 默认单调时钟，手工输入须使用同一单调时间基准 |
| `sweep(now=None)` | 丢弃达到 `stale_seconds` 的流，返回数量；不伪造缺失字节或释放待发数据 |
| `reset(key=None)` | 丢弃一个或全部流；累计统计不会清零；新连接重用同 key 时由调用者 reset |
| `as_dict()` | 当前缓冲/缺口和累计计数；最多 `event_limit` 条摘要，不含 payload 或原始端点 |

示例输出为 `AAAABBBBCCCC`，各次发出长度 `[4, 0, 8, 0]`。缺口填补后只发出一次。
测试保留了历史的 14 个合成用例，并新增 5 个边界用例；没有带出真实结算样本或采集接线。

需要主动理解的限制：

- **首个非空片段直接建立锚点并发出。** 如果最初看到 seq=104，稍后 seq=100 到达，
  工具无法恢复已经错过的流头。乱序保证只针对锚点之后。
- 已消费区域不重发；pending 冲突重叠采用先到的字节。它不验证重传内容一致，也不检测恶意修改。
- 序号按 32 位空间比较；与期望序号相距达到半空间时没有唯一的前后判断，本实现按负方向解释。
  调用者须保证段长度/可判定距离小于 `2**31`。
- 默认最多 64 个方向流、每流 2 MiB、合计 8 MiB pending payload；不包括 Python 对象、临时复制或输出。
  容量在排空前检查，所以过大的**连续**片段也会整次丢弃，补洞时短暂超限同样会拒绝。
- 新建流先 sweep，满流数时淘汰 `last_seen` 最小者。对已有流 feed 不会自动 sweep 其它流；
  空 payload 不更新计数或时效。空闲期间应显式调用 sweep。
- 默认事件摘要有随机加盐流摘要，跨实例不可当稳定身份；它不是密码学匿名性证明。
- 无 SYN/FIN/RST、拥塞控制、ACK、抓包、socket、TLS/应用解密或线程同步。传入 TCP payload 的
  序号与连接生命周期由调用者负责；给到密文仍只会输出重组后的密文。

有效 Issue 例题：`pending 冲突重叠是否应该返回额外诊断`、`补洞时先容量检查的最小反例`。
请附人工 `(seq, bytes, now)` 列表、参数、预期输出、实际输出及 Python 版本，不附真实流量。

## 2. 表函数：保留宽容历史入口，另设严格入口

源码：[codec.py](../../research/tables/codec.py)。从同一 0.3.0 源中只摘取
`decode_table_text`、`iter_table_rows`、`assert_uniform_columns`，保留三个函数体。
没有复制原模块的透明文件加载、解密依赖或目录发现入口。

```python
from research.tables import decode_table_text, decode_table_text_strict, iter_table_rows, assert_uniform_columns

assert decode_table_text("YQ==!") == "a"  # 旧入口允许被 Base64 解码器忽略的字符
try:
    decode_table_text_strict("YQ==!")     # 新入口明确拒绝同一输入
except ValueError:
    pass
else:
    raise AssertionError("strict decoder accepted punctuation")
rows = list(iter_table_rows("id\tname\n1\tToy\n"))
assert assert_uniform_columns(rows) == 2
```

Base64 是编码，不是加密。两个入口都先移除空白，随后解码为 UTF-8；旧入口保留
`validate=False`，新入口使用 `validate=True`。错误 padding、非 UTF-8 payload 仍可报错，
“宽容”不是接受任意内容。旧函数不剥离 BOM。`iter_table_rows` 使用 `splitlines()`，保留行内
空单元格，空文本得到零行；文本末尾一个换行不增加行，额外空行会生成单列空行。
`assert_uniform_columns([]) == 0`；矩形只是形状，不解释标题、ID、类型、单位或业务列。

有效 Issue 例题：`Base64 空白与非法字符的容错差异`、`空行是否应由新调用层显式拒绝`。
变更严格性应新增/选用入口，不能静默改变历史函数合同。

## 3. 多表差分：原字节、规范文本、结构分别回答

源码：[diff.py](../../research/tables/diff.py) 与 [CLI](../../research/tables/__main__.py)。
这是新写的离线研究工具，背景来自旧多表版本核对方法；不包含真实表提取器、游戏 bundle adapter
或真实 62 表内容。默认演示只读取 [fixtures](../../research/tables/fixtures/schema.json) 中的五个人工名称。

```sh
python -B -m research.tables --before ./my-before --after ./my-after --schema ./my-schema.json
```

`--demo` 和显式路径不能混用；不传参数会给用法错误，不会搜索用户/游戏目录。
工具只输出 stdout，错误写 stderr；没有保存或覆盖文件选项。需要保存时由用户自行明确重定向
到选定的新文件，并避免覆盖输入。

Schema v1 示例：

```json
{
  "version": 1,
  "tables": {
    "toy.tsv": {"encoding": "tsv", "columns": ["id", "name"], "id_column": "id"},
    "notes.txt": {"encoding": "text"}
  }
}
```

表模式要求 UTF-8（可有 BOM）、首行标题、至少两列。`base64-tsv` 表示先使用新增严格 Base64
入口，再按 TSV 检查；`text` 明确表示非结构文本。schema 必须提供唯一列名和一个 ID 列。
列全按字符串处理，不推断数值、单位、枚举或表之间关系。未知表仍列入字节差异，并标记
`unknown_table`；声明但两侧都不存在的名称单独计数，不伪造为 added 或 removed。

| 输出 | 判断范围 |
| --- | --- |
| `added/removed/changed/unchanged` | 文件名集合及原始 bytes；BOM 或换行变化也算 changed |
| `canonical_changed` | 解码后去一个 BOM、按 `splitlines()` 统一分隔符及尾部单个换行；不 trim 单元格，不排序 |
| `structure_changed` | 标题/列序/列数/数据行数；单元格修改可为 false，重复 ID 另外标问题 |
| `null` | 该比较不存在或无法得到结构/文本，不是通过 |
| `issue_counts` | 两侧快照的问题次数；同一问题在两侧出现会计两次 |
| `semantic_compatibility` | 始终 `not_assessed`；解析通过不能推出消费者或游戏已兼容 |

人工 demo 的可手工核对结果：

| 文件 | 分类及重点 |
| --- | --- |
| `items.tsv` | changed；新增 `tag` 列且 ID `1` 重复；结构和规范文本都变 |
| `labels.tsv` | changed；仅 after 有 BOM，规范文本/结构未变 |
| `notes.txt` | unchanged；显式非结构文本，结构比较为 null |
| `retired.tsv` | removed |
| `new.tsv` | added |

因此摘要固定为 added=1、removed=1、changed=2、unchanged=1，并报告 duplicate_ids=1、
unknown_columns=1。额外测试使用 CRLF、加列、缺列、错编码、不等列、同名不同内容、未知表和故意错误 schema。
同名表仅改 `A→B` 会使 canonical_changed=true、structure_changed=false，这不代表业务兼容。

默认限制为每个目录 64 个条目、单文件 1 MiB、schema 加两侧输入共 8 MiB。
用 `--max-files`、`--max-file-bytes`、`--max-total-bytes` 显式调整；值须为正整数。
单层枚举，不跟随 symlink/reparse point，发现子目录也报错；不会递归、跳过报错后生成假完整结果。
schema 中重复键/不支持键会报错。不可访问路径、预算超限、读取期间发现身份/大小/时间变化返回 exit 2；
正常比较（即使有差异或结构问题）返回 exit 0，消费者应检查 `issue_counts`。
输入应为已冻结的本地副本；这些检查不是抵御并发恶意替换的原子文件系统快照，也不是硬实时/内存峰值保证。

默认仅输出摘要。`--details` 额外输出文件名、hash、标题与结构计数，仍不输出数据行或 ID 值；
标题和文件名也可能敏感，使用自己的输入时只分享经过自己确认的摘要。

有效 Issue 例题：`canonical 相同但 raw 改变的完整最小对照`、`新增列但消费者未变化该怎样验证`、
`重复 ID 对计数和关联分析的影响`。附自造 before/after、schema、命令、预期分类及版本。
业务兼容结论需要使用该表的消费者测试，本实验没有提供这种结论。

## 4. OCR：看得见的确定规则，也看得见误改

源码：[ocr_normalize.py](../../research/text/ocr_normalize.py)。0.3.0 的词库、正则和函数行为
完整保留，只调整模块介绍；2026-09-09 仍有现行复用。这是特定历史中文面板的文本后处理，
不是通用中文纠错器或 OCR 精度评测。

```python
from research.text import normalize_ocr_text
assert normalize_ocr_text("自色意品") == "白色藏品"
assert normalize_ocr_text("这件货色不错") == "这件金色不错"  # 明确的误改反例
```

函数先将 CRLF 改 LF，再依词库顺序做全局字符串替换，最后合并特定重复标签。
它不判断上下文或置信度，不读取截图，不改变坐标布局，不做数值校验；裸 CR、全角英数、未知词保留。
示例中的“货色”可能是完全正确的词，规则仍会改成“金色”。词库顺序是合同的一部分，增加规则可能
产生串联替换；不要将少量人工用例当作当前 OCR/游戏版本的准确率。

有效 Issue 例题：`某个有上下文的正确词被替换`、`规则顺序产生二次替换`。提供人工文本和明确
预期即可，无需提交私人截图。若尝试通用纠错，另设适用场景与入口并量化误改率。
