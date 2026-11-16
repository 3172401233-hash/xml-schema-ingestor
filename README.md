# xml-schema-ingestor

供应商订单 XML 的流式接入库。每天的 XML 文件根节点下包含多个 `order`，每个
订单下有若干商品行和零到多个可选支付节点；旧版（v1）与新版（v2）文件使用不同
命名空间，且供应商偶尔会在文件尾部截断。本库把 XML 拆成可通过稳定主键互相关联
的 staging 记录，全程基于 SAX 事件流解析，不构造 DOM，可处理远大于内存的文件。

## 目录结构

```
xml_ingester/
  models.py    # StagingRecord / IngestResult 数据模型与目标表名
  keys.py      # 订单、商品、支付的确定性稳定主键
  mapping.py   # 字段映射：XML 路径 -> staging 列
  presets.py   # v1 / v2 两个 schema 版本的预置映射
  handler.py   # SAX ContentHandler，记录边界与取值核心
  parser.py    # ingest_orders() 公共入口、流适配与坏尾识别
tests/
  samples/     # 多版本、重复订单、截断、坏尾等 XML 样本
  test_ingester.py
```

## 快速开始

```python
from xml_ingester import ingest_orders

with open("orders_20261116.xml", "rb") as stream:
    result = ingest_orders(stream, schema_version="v1")

for record in result.records:
    load_into_staging(record.table, record.record_key, record.parent_key, record.fields)

if result.truncated:
    alert_ops(f"文件尾部缺失，已保留 {len(result.records)} 条完整记录: {result.error}")
```

`ingest_orders` 接受二进制流（`open(..., "rb")`、`BytesIO`）或文本流
（`StringIO`、`open(..., "r")`），逐块喂给 SAX 解析器；也可以传入自定义
`FieldMapping` 覆盖预置版本。

## XML 路径到表字段的映射规则

### 1. 路径语法（与命名空间无关）

映射路径只使用元素的 **local name**，命名空间由 schema 版本单独声明，因此同一套
映射规则可以跨命名空间工作：

| 路径写法 | 含义 |
| --- | --- |
| `order/customer/name` | 相对记录根元素嵌套元素的文本内容 |
| `order/@id` | 记录根元素 `order` 的 `id` 属性 |
| `order/buyer/@company` | 嵌套元素 `buyer` 的 `company` 属性 |

路径始终相对于记录根元素（`order` / `item` / `payment`），开头的 `/` 和空白会被
归一化。属性段以 `@` 前缀标识，其余段按元素文本处理。

### 2. 记录与目标表

每个 `RecordMapping` 声明一个记录根标签与列映射，解析器在该标签开始时开记录、
闭合时落一条 staging 行：

| 记录根 | 目标表 | 关联方式 |
| --- | --- | --- |
| `order` | `stg_order` | 顶层记录，`parent_key` 为空 |
| `item`（v2 为 `line`） | `stg_order_item` | `parent_key` = 所属订单的 `record_key` |
| `payment` | `stg_order_payment` | `parent_key` = 所属订单的 `record_key`，节点可选 |

### 3. 多候选路径与首匹配优先

每个列可以配置多个候选路径，按顺序匹配，第一个取到值的路径生效，后续路径不再
覆盖（空元素和纯空白元素不写值），用于兼容同一版本内的结构差异：

```python
RecordMapping(
    table="stg_order",
    root_tag="order",
    columns={"money": ["order/amount", "order/fallbackAmount"]},
)
```

### 4. v1 与 v2 的实际映射

同一批 canonical 列，在两个版本中来源完全不同（v1 靠元素文本，v2 靠属性）：

| staging 列 | v1 路径（命名空间 `urn:supplier:orders:v1`） | v2 路径（命名空间 `urn:supplier:orders:v2`） |
| --- | --- | --- |
| `stg_order.order_no` | `order/orderNo` | `order/@id` |
| `stg_order.customer_name` | `order/customer/name` | `order/buyer/@company` |
| `stg_order.total_amount` | `order/totalAmount` | `order/total` |
| `stg_order.currency` | `order/currency` | `order/@currency` |
| `stg_order_item.line_no` | `item/lineNo` | `line/@seq` |
| `stg_order_item.sku` | `item/sku` | `line/@sku` |
| `stg_order_item.item_name` | `item/name` | `line/desc` |
| `stg_order_item.qty` | `item/qty` | `line/quantity` |
| `stg_order_item.unit_price` | `item/unitPrice` | `line/price` |
| `stg_order_payment.method` | `payment/method` | `payment/@channel` |
| `stg_order_payment.amount` | `payment/amount` | `payment/paidAmount` |

### 5. 字符转义

`&amp;`、`&lt;`、`&gt;`、数字字符引用等由 SAX 解析器自动还原为原字符，映射层
拿到的始终是解码后的文本（例如 `Widget &lt;Pro&gt;` 入库为 `Widget <Pro>`），
不需要调用方自行 unescape。

### 6. 可选字段与缺失节点

映射列没有取到值时不会出现在 `record.fields` 中；订单没有 `payment` 节点时不产生
支付记录，商品行同理。下游可按“列缺失即 NULL”处理。

## 稳定主键规则

主键是业务身份的纯函数，重复解析同一订单（同一文件内重复节点、跨天文件重复
投递）都得到相同 key，staging 装载端可直接 upsert 幂等处理：

- 订单：`ord_<sha1(version, order_no) 前16位>`；订单号缺失时退化为对元素结构
  身份的摘要，保证同样输入仍然确定。
- 商品：`itm_<sha1(订单key, line_no)>`；行号缺失时使用商品在订单内的结构路径。
- 支付：`pay_<sha1(订单key, 支付序号)>`，同一订单多个支付节点按顺序区分。
- 商品与支付记录同时带 `parent_key` 指回订单 key，形成可关联的父子关系。

## 命名空间处理

- `FieldMapping.namespaces` 声明该版本允许的命名空间 URI；解析按 local name 匹配，
  v1/v2 切换只换映射预置，不需要改解析代码。
- 实际命名空间不在声明集合内时，解析继续（仍按 local name 取值），同时在
  `result.warnings` 中为每个异常命名空间记录一条告警，便于发现版本错配。
- 自定义映射可把 `namespaces` 设为空 `frozenset()`，进入接受任意命名空间的
  宽松模式。

## 截断与坏尾处理

解析器在记录根元素 **闭合时** 才发出该订单及其子记录，因此文件中途被切断时：

- 已闭合的订单、商品、支付全部保留在 `result.records` 中；
- 未闭合的部分订单整体丢弃，不会写入半成品记录；
- `result.complete=False`，并根据 expat 错误信息区分：
  `truncated=True` 表示文件尾部缺失/未闭合 token（`no element found`、
  `unclosed token` 等），其他 well-formedness 错误（如根节点后的垃圾内容
  `junk after document element`）标记为致命错误但同样保留此前的完整记录，
  错误文本在 `result.error`。

## 运行测试

```bash
python -m unittest
```

测试覆盖：v1/v2 多版本样本、父子关联与稳定主键、重复订单号、可选字段缺失、
字符转义、文件截断与坏尾、空流、自定义映射，以及 7 字节分块流和逐字节流等
分块读取场景（见 `tests/support.py` 的 `ChunkedReader` / `OneByteReader`）。
