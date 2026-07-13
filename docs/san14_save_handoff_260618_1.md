# 三国志14存档修改器交接分析文档（260618-1 基线）

## 结论摘要

本文档只保留 `260618-1` 样本的可信分析结论。`260618-2` 及之后样本的数据被确认不可靠，基于这些样本做出的差分分析、候选字段判断和生成测试文件已删除，不应作为后续开发依据。

当前已经可靠完成的是存档格式识别、LWC 解压、LWC 无字段修改重编码，以及页面/API 基础工作台。尚未可靠完成的是资金、军粮、士兵、武将属性等具体业务字段的定位和写入。

## 样本范围

可信基线样本目录：

```text
/Users/chengyu/Downloads/三国志14/his/260618-1/remote
```

样本文件统计：

| 类型 | 数量 | 文件特征 |
| --- | ---: | --- |
| 手动存档 | 7 | `svdex*.s14`，可作为后续可编辑目标 |
| 自动存档 | 10 | `autosdex*.s14`，格式同进度存档，也可解析 |
| 设置文件 | 2 | `configS_SC.s14`、`configS_TC.s14`，纯 LWC 文件 |
| 玩家记录 | 1 | `prdataN.s14`，纯 LWC 文件 |

全量 20 个 `.s14` 文件均已验证可完成 LWC 解压，错误数为 0。

## 260618-1 关键样本

当前扫描排序推荐的基线文件：

```text
/Users/chengyu/Downloads/三国志14/his/260618-1/remote/svdexTC01.s14
```

该文件与 `svdexTC00.s14` 的解压 SHA 前缀一致，疑似同一存档内容的两个槽位副本。

| 项目 | 值 |
| --- | --- |
| 文件名 | `svdexTC01.s14` |
| 文件类型 | 手动存档 |
| 语言 | 繁体 |
| 槽位 | `01` |
| 存档时间 | `2026-06-01 12:50` |
| 原文件大小 | `162585` bytes |
| 进度存档魔数 | `SN14SVEXVER0000`，位于文件偏移 `0x04` |
| LWC 偏移 | `278` (`0x116`) |
| LWC payload 偏移 | `546` (`0x222`) |
| LWC 解压后大小 | `1964931` bytes |
| LWC 压缩流大小 | `162039` bytes |
| LWC consumed payload | `162039` bytes |
| LWC remaining bits | `6` |
| 解压 SHA256 | `44781f05ca9fb520d745767b6dccdfccef9095eb8595c0b4ff1a4febbf475ea5` |

重编码验证：

| 项目 | 值 |
| --- | --- |
| 重编码策略 | literal-only LWC，复用原 256 字节替换表 |
| 重编码后原始文件大小 | `610515` bytes |
| 重编码后 LWC 压缩流大小 | `609969` bytes |
| 解压回校验 | 通过 |
| 回校验 SHA256 | `44781f05ca9fb520d745767b6dccdfccef9095eb8595c0b4ff1a4febbf475ea5` |

说明：literal-only 重编码不做 LZ 回溯压缩，因此文件会明显变大，但当前验证说明解压内容可无损还原。是否能被游戏实际读取，仍需要在游戏内加载测试。

## LWC 格式结论

SAN14 `.s14` 文件中有两类结构：

1. 进度存档：前面有游戏自定义头部，`LWC\x1a` 压缩块从文件中部开始。
2. 配置/玩家记录：文件本身从 `LWC\x1a` 开始。

已确认 LWC 块结构：

```text
offset + 0x00: 4 bytes  magic = 4c 57 43 1a
offset + 0x04: u32_le   uncompressed_size
offset + 0x08: u32_le   compressed_size
offset + 0x0c: 256 bytes substitution table
offset + 0x10c: compressed bitstream payload
```

解压逻辑：

1. payload 是 MSB-first 位流。
2. 先读取一个变长整数 `symbol`。
3. `symbol < 256` 表示字面量，真实字节为 `table[symbol]`。
4. `symbol >= 256` 表示 LZ 回溯：
   - `distance = symbol - 256`
   - `length = read_value() + 3`
5. 重复直到输出长度等于 `uncompressed_size`。

当前编码器只实现 literal-only：

1. 复用原 LWC 256 字节 table。
2. 将解压后的每个字节映射回 table 下标。
3. 逐个写入变长整数，不生成 LZ 回溯。
4. 生成新文件时使用 `raw[:lwc_offset] + rebuilt_lwc`。

## 进度存档偏移规律

在 `260618-1` 样本中：

| 文件类型 | LWC 偏移 |
| --- | ---: |
| `svdexTC00/01.s14` | `278` (`0x116`) |
| `svdexSC*.s14` | `294` (`0x126`) |
| `autosdexSC*.s14` | `294` (`0x126`) |
| `configS_*.s14` | `0` |
| `prdataN.s14` | `0` |

后续不要写死偏移，继续用 `_san14_lwc_info()` 搜索 `LWC\x1a`。

## 当前已实现代码

主要文件：

```text
main.py
templates/san14_save.html
```

已实现页面：

```text
GET /san14-save
```

已实现 API：

| API | 作用 |
| --- | --- |
| `GET /api/san14/files?path=...` | 扫描 remote 目录，识别手动存档、自动存档、配置文件、玩家记录 |
| `GET /api/san14/analyze?file=...` | 解压单个 `.s14`，返回 LWC 元信息和轻量探测结果 |
| `POST /api/san14/rebuild-test` | 不修改字段，仅重编码生成同名测试存档到原目录 `new/` 下 |

核心函数：

| 函数 | 作用 |
| --- | --- |
| `_san14_default_remote_dir()` | 从 Downloads 下自动寻找最近的三国志14 remote 目录 |
| `_san14_file_info()` | 识别文件类型、语言、槽位、存档时间、LWC 基本信息 |
| `_san14_lwc_info()` | 搜索并读取 LWC 块头 |
| `_san14_lwc_decompress()` | 完整 LWC 解码 |
| `_san14_lwc_compress_literals()` | literal-only LWC 重编码 |
| `_san14_probe_decompressed()` | 轻量探测人名和数值，不能作为写入依据 |

页面当前只保留三个动作：

1. 扫描目录。
2. 解析推荐存档或指定存档。
3. 生成格式测试存档。

已删除内容：

1. `260618-2/3` 相关的上一样本对比 API。
2. 页面上的“对比上一样本”按钮。
3. 基于不可靠样本生成的候选字段分析函数。
4. `260618-2`、`260618-3` 及 `his/260618-2` 下生成的 `remote/new/svdexTC01.s14` 测试文件。

## 探测结果边界

`260618-1` 目录下目前没有可信备注 `.txt` 文件，因此没有可靠的资金、军粮、士兵、武将属性真值。

在 `svdexTC01.s14` 的解压数据中，能够直接搜索到：

| 内容 | 编码 | 偏移 |
| --- | --- | --- |
| `呂布` | UTF-16LE | `0xebfc5`, `0x1d7f10`, `0x1ddf54` |

历史代码中还保留了默认数值探测项 `35000`、`201000`、`90000`，这是调试辅助，不是可信字段结论。当前在 `260618-1` 中观察到：

| 调试项 | 存储形式 | 偏移 |
| --- | --- | --- |
| `35000` | 原值 `u32_le/u16_le` | `0x2f86` |
| `201000` | `/10 = 20100`, `u16_le` | `0x43dd7` |
| `90000` | `/10 = 9000`, `u16_le` | `0x54222`, `0x56989`, `0x7e493`, `0x8625f`, `0x8f25b` |

这些只能说明字节序列存在，不能说明就是可修改字段。后续不能直接基于这些偏移写入。

## 后续建议

Claude Code 接手后，建议按以下顺序继续：

1. 先保留现有格式层能力，不要动 LWC 解码/重编码主逻辑。
2. 准备新的可信样本，每次只改变一个变量：
   - 同一剧本、同一势力、同一日期。
   - 单独改变资金。
   - 单独改变军粮。
   - 单独改变一个城市兵力。
   - 单独改变一个武将属性。
3. 每份样本旁边放一份明确备注文件，记录：
   - 玩家势力。
   - 城市名或武将名。
   - 修改前后 UI 显示值。
   - 游戏内保存时间。
4. 在可信样本到位后，再重新实现差分分析工具。
5. 字段写入必须满足：
   - 至少两个独立样本一致。
   - 修改后可通过 `_san14_lwc_decompress()` 回校验。
   - 生成到原目录 `new/<原文件名>`。
   - 游戏内实际加载成功。

## 验证命令

本次交接前已执行：

```bash
.venv/bin/python -m py_compile main.py
node --check /tmp/san14_save.js
```

并用 `.venv/bin/python` 直接验证了：

1. `260618-1` 全量 20 个 `.s14` 均可解压。
2. `svdexTC01.s14` literal-only 重编码后再次解压，SHA 与原始解压数据一致。

