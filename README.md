# paper-reading-suite

**一条命令，把 Zotero 里的一篇 PDF 论文变成 5 条结构化笔记，并自动提取全部图表页、附上一份可核对的完整性报告。**

给 Claude Code / DeepSeek Harness 等支持 `SKILL.md` 约定的 agent 用的技能包。

---

## 它解决什么问题

读一篇论文，不同阶段需要不同的东西：

- 刚拿到，想知道**这篇讲什么** → 需要一个速览
- 准备引用，想知道**凭什么这么说** → 需要证据链
- 要挑刺，想知道**每节论证站不站得住** → 需要逐节精读
- 刚入学不懂这个领域，想知道**为什么这么写** → 需要零基础拆解
- 想知道**作者当时放弃了什么** → 需要决策地图

这五件事以前要分别做。这个脚本一次做完，**并且告诉你它漏了什么**。

---

## 核心设计：完整性可核对

大多数「AI 读论文」工具的问题是：你无法知道它漏了哪张图、哪张表。

这个脚本每一步都产出审计结果，并在**每条笔记开头嵌入一份完整性报告**：

```markdown
### 完整性报告（自动生成，请核对）

**章节来源**：PDF 书签（outline）

| 项目 | 应有 | 已覆盖 | 缺失 |
|---|---|---|---|
| 表 | 9 | 9 | 无 |
| 图 | 8 | 8 | 无 |
| 章节 | 11 | 9 | 空节 2（父节无独立内容，属论文结构） |

**编号连续性检查**（这才是「有没有漏识别」的证据）

- ✅ 表、图的编号都连续，没有缺号

**图表清单**（可信度：高=标题形态正常 / 低=可能错位，需人工核对）

| 编号 | 页码 | 已渲染 | 标题可信度 | 标题 |
|---|---|---|---|---|
| Table 1 | p4 | ✅ | 高 | Hospital A monthly statistical data. |
...
```

### 为什么需要两个维度

**⚠️ 上面的「表 9/9」只证明「识别到的都渲染了」，不能证明「没有漏识别」。** 这是大多数工具不说的坑。

**实测踩到过：** Li 2021（*Energies* 14(13) 3864）有一张 Figure 4，它的 caption 和表格挤在同一行：

```
p9: ΔURE(EI) | ΔURE(A) | ... | Overall change | Figure 4. Decomposition results of urban residential energy consumption...
```

**只要 caption 不在行首就认不出来 —— 报告会显示「图 4/4 全齐」，实际漏了一张。**

所以脚本加了第二层：**扫全文所有 `Table N` / `Figure N` 的编号，和识别结果做差集**。有缺口就报出来，并对缺失的编号放宽规则再搜一次。

```
编号有缺口，用宽松规则补：['Figure 4']
✅ 兜底后编号已连续
表 4 个 / 图 5 个
```

**三级可信度，分开写：**

| 级别 | 含义 | 怎么验 |
|---|---|---|
| **硬** | 图表编号计数 vs 渲染页数，**且编号连续无缺号** | 对不上，报告就是红的 |
| **中** | 章节用 PDF 书签，标题清单可逐条看 | 报告列出全部章节名 |
| **软** | 5 条笔记的文字 | 需人抽读，或另做独立复检 |

**「空节」不是缺失** —— 父节（如 `2 Materials and Methods`）在 PDF 里只有标题、内容全在子节（`2.1` `2.2`），这是论文结构。

---

## 产出的 5 条笔记

| # | 前缀 | 读者 | 回答什么 |
|---|---|---|---|
| ① | 【① 飞书模版 · 速览】 | 有基础 | 这篇讲什么（19 项） |
| ② | 【② 证据链】 | 审稿人 | 凭什么这么说（claims / evidence / links + caveat） |
| ③ | 【③ 精读笔记 · 逐节 N 节】 | 同行 | 每一节怎么论证、哪里站不住 |
| ④ | 【④ 零基础拆解 · 作者视角】 | 硕士新生 | 从头到尾讲一遍（8 章，结构固定） |
| ⑤ | 【⑤ 决策地图】 | 想投这个刊的人 | 这篇靠哪几个决策撑起来（**结构随论文变**） |

### ⑤ 决策地图是最特别的一条

**它不套固定模板。** 先做一次「决策侦查」，从论文里提取「作者本可以选别的、但他选了这个」的决策点，再按这些决策组织全文。

识别标准（两条必须同时满足）：

1. 存在**真实的备选方案**（不是「不这么做会死」的伪选择）
2. 换了备选，**结论 / 贡献 / 可信度会实质变化**

排除项：「按惯例就该这么做」/「数据只能这么拿」/ 纯写作风格 / 插值补缺失 / 软件版本。

**实测效果**（Dai 2025, *Sustainability* 17(20) 9175）提取出 10 个决策点，其中几条直接指出了作者自己没意识到的问题：

> 「84% 是**清洁能源产量**的占比，不是**绿色产业产值**的占比，两个分母不是一回事」——作者用这个数字为自己的口径辩护，但分母偷换了

> 「这个选择不是为了更严谨，**而是为了制造新意**——他要的贡献点就在『跨区域中介』这四个字上」

> 「分组回归系数 0.382 / 0.049 / −0.255 说明有非线性，但假设 H1–H3 全设定为正，作者自己综述了负向机制却在假设里正向化」

---

## 四次实测（四种不同的研究范式）

| 论文 | 类型 | 页 / blocks | 表 / 图 | 章节来源 | 结果 |
|---|---|---|---|---|---|
| Dai 2025 *Sustainability*<br/>17(20) 9175 | 空间计量 | 25 / 314 | **12 / 4** | PDF 书签 35 条 | 编号全部连续 |
| Jia 2022 *Sustainability*<br/>14(19) 12837 | 成本效益 | 17 / 305 | **9 / 8** | PDF 书签 11 条 | 表 9/9、图 8/8，缺 0 页 |
| Cheng 2023 *Water*<br/>15(17) 3060 | DPSIRM 评价 | 14 / 196 | **4 / 3** | PDF 书签 19 条 → 20 节 | 143.6 秒，5 条笔记，假 key = 0 |
| Li 2021 *Energies*<br/>14(13) 3864 | 时空格局 + GDIM + 脱钩 | 17 / 311 | **4 / 5** | PDF 书签 18 条 → 19 节 | **Figure 4 靠兜底补回**；5 条笔记，假 key = 0 |

**⭐ 四篇跨了四种研究范式：空间计量、成本效益、DPSIRM 评价、时空分解。** 都能跑通，且图表编号全部连续。

**⚠️ 更正：Dai 2025 之前记的「表 8 / 图 5」是错的。** 全文实际是 Table 1–12、Figure 1–4，严格规则全部识别到。

**一篇约 3 分钟、约 9 万 tokens。**

---

## 四个非显然的设计决定

这些都是踩过坑才定的，也是这个脚本和「随手写个 prompt」的区别。

### 1. 章节必须用 PDF 书签，不能用正则

按页切 → 17 页切出 17 节（「第1页」…「第17页」），和真实的 7 章毫无关系。

用 `doc.get_toc()` → 11 条书签对应 11 节，标题全对。

**正则还有个隐藏坑：PDF 连字 `ﬁ`（U+FB01）。** `Cost–Beneﬁt Analysis` 里的 `fi` 是连字，普通正则 `[A-Za-z]` 匹配不到，整个第 3 章会消失。正则里必须显式包含 `\uFB00-\uFB06`。

**结论：书签优先，正则只作无书签时的兜底。**

### 2. 图表识别必须「严格优先 + 只补缺口」，不是单靠行首锚定

**阶段 1（严格）**：caption 必须在行首。准确率高，能避开正文引用 —— `as shown in Table 2. The results indicate...` 不是 caption。

**两个阶段都必须保证「标题在同一行」**（正则用 `[^\n]` 而不是 `\s`）。这才防住跨行抢标题：

```
p11 有：shown in Figure 7 and Table 7.
下一行：Figure 6. The Sankey diagrams for scenario 2.
```

允许跨行时，正则从 `Table 7` 开始匹配会把下一行 `Figure 6` 的标题抢走 —— **结果 Figure 6 整条从清单里消失**。

**阶段 2（兜底）**：**只对「编号有缺口」的编号**放宽行首要求。因为 caption 可能和表格挤在同一行（见上面 Li 2021 的 Figure 4）。

**⚠️ 踩过的坑：一度以为「干脆去掉行首锚定就行」—— 结果 Cheng 2023 的表从 4 个掉到 2 个、Jia 2022 从 9 个掉到 6 个**，正文引用全被误当成 caption。所以必须「严格优先，只补缺口」，不能整体放宽。

### 3. 渲染不设页数上限

早期版本为省时间只渲染前 6 页。后果：Jia 2022 命中 12 页，**9 个表只提到 3 个、8 张图只提到 4 张**。

这不是「内容差异」，是**真缺失**。现在全部渲染。

### 4. 附件 key ≠ 父条目 key

Zotero 里，论文条目（parent）和它的 PDF 附件（attachment）是两行记录，各有自己的 key。拼 PDF 路径必须 `JOIN itemAttachments` 拿**附件 key**，用父条目 key 会拼出一个不存在的路径。

---

## 依赖：三种用法要的东西不一样

**先看清你要跑哪一种，再决定装什么。**

| 用法 | 需要什么 | 不需要什么 |
|---|---|---|
| `--skip-notes --skip-write`<br/>切块 + 章节 + 图表 + 渲染 + 完整性报告 | Python 3.11+<br/>PyMuPDF<br/>**Zotero 7+**（要读 `zotero.sqlite`） | ❌ DeepSeek key<br/>❌ zotero-mcp |
| `--skip-write`<br/>上一行全部 + 5 条笔记文字 | 上面全部 +<br/>**DeepSeek API key** | ❌ zotero-mcp |
| 不带参数<br/>全流程，写回 Zotero | 上面全部 +<br/>**zotero-mcp**（且必须提供 `write_note` / `write_item`） | — |

### 完整依赖表

| 组件 | 作用 | 必需性 | 怎么装 |
|---|---|---|---|
| **Python 3.11+** | 运行脚本 | 全流程 | [python.org](https://www.python.org/downloads/) |
| **PyMuPDF** | 切块 + 渲染页面 | 全流程 | `pip install pymupdf` |
| **Zotero 7+** | 论文库（脚本读它的 sqlite） | 全流程 | [zotero.org](https://www.zotero.org/download/)，**至少打开一次** |
| **DeepSeek API key** | 5 条笔记的文字 | 要生成笔记时 | 见下面「第 3 步」 |
| **zotero-mcp** | 写笔记 / 导图片回 Zotero | 要写库时 | 见下面「第 2 步」 |
| ~~norman_reader.py~~ | 切块 + 渲染（替代 PyMuPDF 实现） | **可选** | 不用装，脚本有内置等价实现 |

### ⚠️ 关于 zotero-mcp：接口有两套，目前不通用

**本脚本的写库路径写死了两个工具名：**

```
write_note    创建笔记
write_item    导入图片附件
```

**这来自 [cookjohn/zotero-mcp](https://github.com/cookjohn/zotero-mcp) 这个 Zotero 插件（1.2k stars，MIT，作者 cookjohn）。**

**⭐ 具体来源已实测确认（解包 xpi 查 manifest.json）：**

```
插件名:    Zotero MCP Plugin
版本:      1.5.0（本机装的）
插件 ID:   zotero-mcp-plugin@autoagent.my
作者:      cookjohn
更新源:    https://github.com/cookjohn/zotero-mcp/releases/latest/download/update.json
write_note 出现在: content/scripts/zotero-mcp-plugin.js
```

### ⚠️ 别装错项目：`cookjohn/zotero-mcp` ≠ `54yyyu/zotero-mcp`

**这是两个完全不同的项目，接口不通用。**

| | **cookjohn/zotero-mcp** ✅ 本脚本用这个 | **54yyyu/zotero-mcp** ❌ 不兼容 |
|---|---|---|
| 形态 | **Zotero 插件**（.xpi 装进 Zotero） | Python MCP 服务端 |
| 端口 | Zotero 进程自己监听 **23120** | 自己起服务 |
| 工具数 | 27 | 44 |
| 写笔记 | `write_note` ✅ | `zotero_manage_note` |
| 导附件 | `write_item` ✅ | `zotero_attach_file` |
| 写权限 | 直接可用 | 多一道 `zotero_authorize_local_writes`（Zotero 里弹窗点授权） |

**装错的表现 —— 脚本会明确告诉你：**

```
❌ zotero-mcp 连上了，但缺少本脚本需要的工具：write_note（创建笔记）、write_item（导入图片附件）
   服务端 http://127.0.0.1:23120/mcp 共提供 44 个工具
   诊断：你连的像是官方 54yyyu/zotero-mcp —— 它把写笔记叫 zotero_manage_note，
         把附件叫 zotero_attach_file。本脚本只认 write_note / write_item。
   → 需要带 write_note + write_item 的 zotero-mcp 变体
   → 或者跳过写库：加 --skip-write（笔记只生成不写回 Zotero）
   → 或者只跑本地部分：加 --skip-notes（完全不需要 MCP 和 key）
```

**⭐⭐ 不想折腾的话，加 `--skip-write` 就好 —— 前面几步全都正常，笔记生成在本地，你自己复制进 Zotero。**

### 那个 npm 包不是必须的

`~/.dsh/mcp-servers/zotero-mcp/` 下的 npm 包（也是 `cookjohn/zotero-mcp` 这个仓库的 server 部分）**实际没被用到**。实测端口 23120 和 23119 的监听进程都是 `zotero.exe` 本身 —— MCP 端点是**插件内部起的 HTTP 服务**，不是那个 node 进程。

**所以只用装插件，不用 `npm install zotero-mcp`。**

### norman_reader.py 为什么是可选的

没有它也能完整跑。脚本内置了等价的 PyMuPDF 实现，实测同一篇 PDF 切出 **196/196 blocks、text 100% 一致**，渲染出的 PNG 字节数也完全相同。

有它就用它（多一层源映射校验），没有就用内置的。用 `NORMAN_READER` 环境变量可以显式指向它。

---

## 安装

### 第 1 步：装 Python 依赖

```bash
pip install pymupdf
```

只要这一个。其余都是标准库。

### 第 2 步：装 Zotero

1. 装 [Zotero 7](https://www.zotero.org/download/)，**至少打开一次**（它会生成 `zotero.sqlite`）
2. 把你准备精读的 PDF 拖进 Zotero

**⭐ 到这一步，你就可以跑 `--skip-notes` 了**（切块 + 图表 + 完整性报告）。

### 第 2.5 步：装 zotero-mcp 插件（只有要写回 Zotero 才需要）

**装 [cookjohn/zotero-mcp](https://github.com/cookjohn/zotero-mcp) 这个 Zotero 插件**（不是 54yyyu 那个 Python 项目）。

1. 去 [Releases](https://github.com/cookjohn/zotero-mcp/releases/latest) 下载 `zotero-mcp-plugin-x.y.z.xpi`
2. Zotero → 工具 → 插件 → 齿轮 → **Install Plugin From File** → 选那个 .xpi
3. 重启 Zotero
4. 确认 23120 端口已开（Zotero 进程自己在听）

**验证方式：** 跑 `python bin/paper_reading_suite.py --check`，应该看到
`✅ zotero-mcp 有两个必需工具（write_note / write_item）`。

**⭐ 只用装插件，不用 `npm install zotero-mcp`** —— 实测 23120 的监听进程就是 `zotero.exe` 本身。

**没有或装错，就用 `--skip-write` 跳过这一步。**

### 第 3 步：配 DeepSeek key（只有要生成笔记文字才需要）

建一个文件，里面**只写一行** key：

```bash
# Windows
mkdir %USERPROFILE%\.secrets
echo sk-你的key > %USERPROFILE%\.secrets\deepseek.key

# macOS / Linux
mkdir -p ~/.secrets
echo "sk-你的key" > ~/.secrets/deepseek.key
chmod 600 ~/.secrets/deepseek.key
```

**⚠️ 这个文件绝对不要提交进任何仓库。** 仓库的 `.gitignore` 已经挡了 `*.key` 和 `.secrets/`。

### 第 4 步：自检

```bash
python bin/paper_reading_suite.py --check
```

**这一步会检查 5 件事并明确告诉你缺什么：**

```
STEP -1：环境预检
  ✅ pymupdf
  ✅ Zotero 数据库可读（185 个条目）
  ✅ DeepSeek key 已读到（35 字符）
  ✅ zotero-mcp 可达（http://127.0.0.1:23120/mcp）
  ℹ️  没有 norman_reader，用内置 pymupdf 切块/渲染（功能等价）

  ✅ 预检通过
```

**如果缺东西，它会一次列全，而不是跑到一半崩在 traceback 里：**

```
  ❌ 有 3 个问题必须先解决：
    1. 找不到 Zotero 数据库：C:\no\such\zotero\zotero.sqlite
     → 确认 Zotero 已安装并至少打开过一次
     → 或设环境变量 ZOTERO_HOME 指向你的 Zotero 数据目录
    2. 找不到 DeepSeek API key：C:\no\such\key
     → 建这个文件，里面只写一行 key
     ...
```

---

## 配置

全部用环境变量，不写死在代码里：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PAPER_SUITE_PYTHON` | 当前解释器 | 跑 norman_reader 子进程用（可选） |
| `NORMAN_READER` | `~/.dsh/skills/norman-paper-reading/bin/norman_reader.py` | norman_reader 位置（可选） |
| `DEEPSEEK_KEY_FILE` | `~/.secrets/deepseek.key` | 一行纯文本 API key |
| `ZOTERO_HOME` | `~/Zotero` | Zotero 数据目录（含 `zotero.sqlite`） |
| `PAPER_SUITE_TMP` | `%TEMP%/paper-reading-suite` | 临时/工作目录（C 盘紧张时改到别的盘） |
| `ZOTERO_MCP_URL` | `http://127.0.0.1:23120/mcp` | zotero-mcp 端点 |

---

## 用法

```bash
# 环境自检（先跑这个）
python bin/paper_reading_suite.py --check

# 先只看完整性报告，确认图表都抓到了（不花模型钱）
python bin/paper_reading_suite.py <Zotero父条目key> --skip-notes --skip-write

# 生成笔记但不写库
python bin/paper_reading_suite.py <Zotero父条目key> --skip-write

# 完整跑：生成 5 条笔记并写进 Zotero
python bin/paper_reading_suite.py <Zotero父条目key>
```

**父条目 key 怎么找：** Zotero 里右键条目 → 显示条目信息，或看 `zotero.sqlite` 的 `items.key`（8 位大写字母数字）。

```
--check           只做环境预检，然后退出
--work DIR        工作目录（默认 %TEMP%/paper-reading-suite/_suite_<key>）
--skip-notes      只做检测 + 渲染 + 完整性报告，不调模型
--skip-write      生成笔记但不写进 Zotero
```

---

## 已知限制

**必须知道这些，否则会被误导：**

1. **图表标题的「文字」不保证准确，但「有没有漏」不靠人猜。**
   报告里有 `score` 标注，`低` 的需人工核对；但**编号连续性检查是硬的** —— 有缺号一定报出来，兜底补过也会说明补了哪个。
2. **没有独立复检。** 5 条笔记的文字内容是模型生成的，脚本**没有**第二次调用去核对「这些内容是否都能在原文找到」。这一层是「软」的。
3. **一次一篇。** 不做多篇横向对比。
4. **强依赖 Zotero + zotero-mcp。** 不是通用 PDF 工具，是「Zotero 生态内的论文精读流水线」。**用不了裸 PDF 文件，必须先进 Zotero。**
5. **笔记写入用 HTML 直传。** Zotero 的 `markdownToNoteHtml` 对 Markdown 表格支持不好（会把 `|` 当纯文本），所以脚本先把整篇转成 HTML 再写。

---

## 图片写入 Zotero 的唯一可行格式

这一条踩了 6 轮坑，单独写出来：

```html
<img alt="说明文字" width="原图像素宽" height="原图像素高" data-attachment-key="真实附件KEY">
```

**三条硬规则：**

1. **不要 `src`**（不要相对路径、不要 `file://`、不要 base64 data URI）
2. **`data-attachment-key` 必须是数据库里真实存在的 image 附件 key**
3. **`width` / `height` 写原图像素尺寸**（不缩放），否则图会显示得很小

**踩过的坑：**

| 写法 | 结果 |
|---|---|
| `<img src="page-08.png">` 相对路径 | ❌ 坏图（Zotero 笔记没有「当前目录」概念） |
| `<img src="data:image/jpeg;base64,...">` | ⚠️ 能显示，但 Zotero 会自动压缩并**伪造** `data-attachment-key`（key 不存在） |
| `<img data-attachment-key="假key">` | ❌ 坏图（key 在库里不存在） |
| `<img src="file:///C:/...">` | 未验证通过，不要用 |
| `<img alt="..." width="..." height="..." data-attachment-key="真key">` | ✅ **唯一可行** |

**⚠️ 最关键的一条：`write_note` 返回 `success: true` 不代表 key 有效。** 它会接受一个不存在的 key 并返回成功。所以脚本写完必须回查 `itemAttachments` 表核对：

```python
used = re.findall(r'data-attachment-key="([A-Z0-9]+)"', note_from_db)
real = set(所有 image/png 附件的 key)
assert set(used) <= real, "有假 key，图会显示不出来"
```

---

## 作者视角：用户需求怎么变成这个设计的

这个技能源于一句话需求：

> 「作者视角我只是随便提出来，给你提了 4 点。但是他这篇论文可能只有 4 点。所以他作者不可能只有这 4 点东西。而且甚至每一篇论文都不一样。所以他应该是一篇论文作者视角，然后产出一份笔记，但是它里面的东西它是不一样的，它这个可能不是规则的字段。」

**核心约束：结构不能是固定字段，必须从论文里长出来。**

所以 ⑤ 分成两步：

1. **侦查**：只出决策清单，不写笔记。要求模型自己说明「为什么是这么多个，不是更多或更少」。
2. **组织**：按清单写。每个决策的**标题用它自己的内容**（「把绿色产业界定成水电+风电+光伏」），**不许用类名**（「变量定义」）。

**诚实说明：** 每个决策内部仍有 5 个固定子问题（面对什么局面 / 选了什么放弃什么 / 为什么这么选 / 换成备选会怎样 / 最易被追问什么）。**决策点是长出来的，决策内部的讲法仍是模板。** 要做到「每个决策连讲法都不一样」，需要 N 次模型调用，成本翻倍——这一步还没做。

---

## 许可

MIT
