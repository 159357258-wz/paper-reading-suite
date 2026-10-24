"""论文阅读套件 · 单一入口
用法：python paper_reading_suite.py <Zotero父条目key> [--work 工作目录] [--skip-notes]

一条命令完成：
  1. 从 Zotero 取 PDF + 元数据
  2. norman-reader extract 切块
  3. 检测：章节（PDF书签优先）+ 图表（行首锚定）
  4. 渲染全部图表页
  5. 生成完整性报告
  6. 生成 5 条笔记（飞书速览 / 证据链 / 逐节精读 / 零基础拆解 / 决策地图）
  7. 写进 Zotero（图片作为附件，笔记里嵌完整性报告）
"""
import os, sys, json, io, re, time, sqlite3, shutil, subprocess, argparse, hashlib
from collections import defaultdict, Counter

# ─── 路径常量（都可用环境变量覆盖，便于换机器）───
# PY      : 跑 norman_reader 子进程用的解释器（默认用当前解释器）
# NORMAN  : norman_reader.py 的位置（来自 norman-paper-reading 技能）
# KEY_FILE: DeepSeek API key 文件（一行纯文本，不要提交进仓库）
# ZOTERO  : Zotero 数据目录
# TMP     : 临时/工作目录（C 盘紧张时改到别的盘）
# MCP     : zotero-mcp 插件的 Streamable HTTP 端点
PY = os.environ.get("PAPER_SUITE_PYTHON") or sys.executable
NORMAN = os.environ.get("NORMAN_READER") or os.path.expanduser(
    r"~/.dsh/skills/norman-paper-reading/bin/norman_reader.py")
KEY_FILE = os.environ.get("DEEPSEEK_KEY_FILE") or os.path.expanduser(r"~/.secrets/deepseek.key")
ZOTERO = os.environ.get("ZOTERO_HOME") or os.path.expanduser(r"~/Zotero")
TMP = os.environ.get("PAPER_SUITE_TMP") or os.path.join(
    os.environ.get("TEMP") or os.path.expanduser("~"), "paper-reading-suite")
MCP = os.environ.get("ZOTERO_MCP_URL") or "http://127.0.0.1:23120/mcp"

# 子进程（norman_reader）也用同一个临时目录：它提取 PDF 时会写中间文件
os.environ["TEMP"] = TMP
os.environ["TMP"] = TMP
os.makedirs(TMP, exist_ok=True)

LIG = {"\uFB00": "ff", "\uFB01": "fi", "\uFB02": "fl", "\uFB03": "ffi",
       "\uFB04": "ffl", "\uFB05": "ft", "\uFB06": "st"}


def norm(t):
    for k, v in LIG.items():
        t = t.replace(k, v)
    return re.sub(r"\s+", " ", t).strip()


def log(msg):
    print(msg, flush=True)


def sec(title):
    log("")
    log("=" * 78)
    log(title)
    log("=" * 78)


# ══════════════════════════════════════════
# STEP 0：读 Zotero
# ══════════════════════════════════════════
def load_zotero(parent_key, work):
    sec("STEP 0：从 Zotero 取 PDF 与元数据")
    tmp = os.path.join(TMP, "_suite_db")
    if os.path.isdir(tmp): shutil.rmtree(tmp)
    os.makedirs(tmp)
    for fn in ["zotero.sqlite", "zotero.sqlite-wal", "zotero.sqlite-shm"]:
        p = os.path.join(ZOTERO, fn)
        if os.path.exists(p): shutil.copy2(p, os.path.join(tmp, fn))
    conn = sqlite3.connect(os.path.join(tmp, "zotero.sqlite"))
    cur = conn.cursor()
    cur.execute("SELECT itemID, libraryID FROM items WHERE key=?", (parent_key,))
    r = cur.fetchone()
    if not r:
        raise SystemExit("❌ 找不到条目 %s" % parent_key)
    pid, lib = r

    def f(name):
        cur.execute("""SELECT v.value FROM itemData d
                       JOIN itemDataValues v ON v.valueID=d.valueID
                       JOIN fields fd ON fd.fieldID=d.fieldID
                       WHERE d.itemID=? AND fd.fieldName=?""", (pid, name))
        rr = cur.fetchone()
        return rr[0] if rr else ""

    cur.execute("""SELECT c.firstName, c.lastName FROM itemCreators ic
                   JOIN creators c ON c.creatorID=ic.creatorID
                   WHERE ic.itemID=? ORDER BY ic.orderIndex""", (pid,))
    creators = cur.fetchall()
    cur.execute("""SELECT a.key, ia.path FROM itemAttachments ia
                   JOIN items a ON a.itemID=ia.itemID
                   WHERE ia.parentItemID=? AND ia.contentType='application/pdf'
                   ORDER BY ia.itemID LIMIT 1""", (pid,))
    ar = cur.fetchone()

    # 先把所有字段查完（f() 依赖 cur，必须在 close 之前全部调用）
    v_title = f("title")
    v_date = f("date")
    v_venue = f("publicationTitle")
    v_doi = f("DOI")
    v_abstract = f("abstractNote")
    conn.close()

    if not ar:
        raise SystemExit("❌ 条目 %s 下没有 PDF 附件" % parent_key)

    att_key, att_path = ar
    pdf = os.path.join(ZOTERO, "storage", att_key, str(att_path).replace("storage:", ""))
    if not os.path.exists(pdf):
        raise SystemExit("❌ PDF 文件不存在：%s" % pdf)

    m = re.match(r"(\d{4})", v_date)
    meta = {
        "parent_key": parent_key, "parent_id": pid, "library_id": lib,
        "title": v_title,
        "authors": ", ".join((a + " " + b).strip() for a, b in creators[:5]),
        "year": m.group(1) if m else "",
        "venue": v_venue, "doi": v_doi,
        "abstract": v_abstract,
        "attachment_key": att_key, "pdf_path": pdf,
    }
    io.open(os.path.join(work, "meta.json"), "w", encoding="utf-8").write(
        json.dumps(meta, ensure_ascii=False, indent=1))
    log("  标题: %s" % meta["title"][:70])
    log("  作者: %s" % meta["authors"][:60])
    log("  年份: %s ｜ 期刊: %s" % (meta["year"], meta["venue"]))
    log("  DOI : %s" % meta["doi"])
    log("  PDF : %.1f MB" % (os.path.getsize(pdf)/1024/1024))
    return meta


# ══════════════════════════════════════════
# STEP 1：切块
# ══════════════════════════════════════════
def extract_builtin(pdf_path, work):
    """内置切块：不依赖 norman_reader，只用 pymupdf。

    产出的 source_map.json 与 norman-reader extract 的结构一致
    （实测同一 PDF：blocks 数相同、text 逐条相同），所以后面几步无需改。
    """
    import pymupdf
    doc = pymupdf.open(pdf_path)
    blocks = []
    for pg_idx, page in enumerate(doc, start=1):
        bi = 0
        for item in page.get_text("blocks"):
            x0, y0, x1, y1, text = item[0], item[1], item[2], item[3], item[4]
            text = (text or "").strip()
            if not text:
                continue
            bi += 1
            blocks.append({
                "id": "P%04d-B%04d" % (pg_idx, bi),
                "kind": "text",
                "page": pg_idx,
                "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                "text": text,
            })
    n_pages = doc.page_count
    doc.close()
    sm = {
        "schema_version": 1,
        "source": {"kind": "pdf", "path": pdf_path, "pages": n_pages,
                   "engine": "builtin-pymupdf"},
        "blocks": blocks,
    }
    io.open(os.path.join(work, "source_map.json"), "w", encoding="utf-8").write(
        json.dumps(sm, ensure_ascii=False, indent=1))
    log("  内置切块：%d 页 → %d blocks" % (n_pages, len(blocks)))
    return sm


def do_extract(meta, work):
    sec("STEP 1：切块")
    sm_path = os.path.join(work, "source_map.json")
    if os.path.exists(sm_path):
        log("  已存在 source_map.json，跳过")
    else:
        # 有 norman_reader 就用它（多一层校验），没有就用内置实现
        use_norman = os.path.exists(NORMAN)
        if use_norman:
            log("  用 norman_reader：%s" % NORMAN)
            t0 = time.time()
            r = subprocess.run([PY, "-W", "ignore", NORMAN, "extract", meta["pdf_path"], "--out", work],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=os.environ)
            log("  %.1f 秒 退出码 %s" % (time.time()-t0, r.returncode))
            for ln in (r.stdout or "").split("\n")[:5]:
                if ln.strip(): log("  " + ln)
            if r.returncode != 0 or not os.path.exists(sm_path):
                log("  ⚠️ norman_reader 失败，改用内置切块")
                use_norman = False
        if not use_norman:
            log("  用内置切块（无需 norman_reader）")
            return extract_builtin(meta["pdf_path"], work)
    sm = json.loads(io.open(sm_path, encoding="utf-8").read())
    log("  blocks: %d" % len(sm["blocks"]))
    return sm


# ══════════════════════════════════════════
# STEP 2：检测章节 + 图表
# ══════════════════════════════════════════
def do_detect(meta, sm, work):
    import pymupdf
    sec("STEP 2：检测章节与图表")
    blocks = sm["blocks"]
    doc = pymupdf.open(meta["pdf_path"])
    n_pages = doc.page_count

    # ── 章节：书签优先 ──
    toc = doc.get_toc()
    chapters = []
    if toc:
        src = "PDF 书签（outline）"
        for lvl, title, pg in toc:
            t = norm(title)
            if re.match(r"^(References|Bibliography|Appendix|Acknowledg|Contents)", t, re.I):
                continue
            chapters.append({"level": lvl + 1, "number": "", "heading": t, "page": pg, "from": "bookmark"})
    else:
        src = "正文正则（PDF 无书签）"
        TC = r"A-Za-z\uFB00-\uFB06\u2013\u2014\-\s,\(\)/&':’\""
        rx = re.compile(r"^(\d+(?:\.\d+){0,3})\.?\s+([" + TC + r"]{2,88})$")
        seen = set()
        for b in blocks:
            for ln in (b.get("text") or "").split("\n"):
                m = rx.match(ln.strip())
                if m:
                    num, title = m.group(1), norm(m.group(2))
                    if (num, title.lower()) in seen: continue
                    seen.add((num, title.lower()))
                    chapters.append({"level": num.count(".") + 2, "number": num,
                                     "heading": title, "page": b.get("page"), "from": "regex"})
    log("  章节来源：%s，共 %d 条" % (src, len(chapters)))

    # ── 按章节切文本 ──
    bs = sorted(blocks, key=lambda b: (b.get("page") or 0, b.get("id") or ""))
    def find_start(ch):
        target = norm(ch["heading"]).lower()[:30]
        for i, b in enumerate(bs):
            if b.get("page") == ch["page"] and target in norm(b.get("text") or "").lower():
                return i
        for i, b in enumerate(bs):
            if b.get("page") == ch["page"]:
                return i
        return 0
    starts = sorted([(find_start(c), c) for c in chapters], key=lambda x: x[0])
    sections = []
    for k, (si, c) in enumerate(starts):
        ei = starts[k+1][0] if k+1 < len(starts) else len(bs)
        txt = "\n".join((bs[j].get("text") or "") for j in range(si, ei)).strip()
        sections.append({"level": c["level"], "number": c["number"], "heading": c["heading"],
                         "page": c["page"], "text": txt, "blocks": ei - si, "from": c["from"]})
    if starts and starts[0][0] > 0:
        pre = "\n".join((bs[j].get("text") or "") for j in range(0, starts[0][0])).strip()
        if len(pre) > 80:
            sections.insert(0, {"level": 2, "number": "", "heading": "摘要与开头",
                                "page": 1, "text": pre, "blocks": starts[0][0], "from": "pre"})
    empty = [s for s in sections if len(s["text"]) < 80]
    log("  切出 %d 节（空节 %d 个）" % (len(sections), len(empty)))

    # ── 图表识别：两阶段 ──
    # 阶段 1（严格）：caption 必须在行首。准确率高，能避开正文引用
    #   （"as shown in Table 2. The results indicate..." 不是 caption）。
    # 阶段 2（兜底）：只在编号有缺口时，对缺失的那些编号放宽行首要求。
    #   原因：caption 常和表格挤在同一行，例如
    #   "| Overall change | Figure 4. Decomposition results of ..."（Li 2021 实测）。
    #
    # 两个阶段都必须保证「标题在同一行」（[^\n]），这才能防住跨行抢标题：
    #   p11 的 "shown in Figure 7 and Table 7." 行末后，下一行是
    #   "Figure 6. The Sankey..."，允许跨行就会把 Figure 6 的标题抢给 Table 7。
    cand = defaultdict(list)
    CAP_STRICT = re.compile(r"^\s*(Table|Figure)\s+(\d+)\s*[\.:]\s*([A-Z][^\n]{2,110})$", re.MULTILINE)
    CAP_LOOSE = re.compile(r"(Table|Figure)\s+(\d+)\s*[\.:]\s*([A-Z][^\n]{2,110})")

    def add_cand(kind, num, title, page):
        title = norm(title)
        if re.match(r"^(Table|Figure)\s+\d+\s*[\.:]", title):   # 第二道防线
            return
        cand[(kind, num)].append({"page": page, "title": title})

    for pgno in range(n_pages):
        for m in CAP_STRICT.finditer(doc[pgno].get_text()):
            add_cand(m.group(1), int(m.group(2)), m.group(3), pgno + 1)

    def score(kind, t):
        s = 0
        if 8 <= len(t) <= 130: s += 2
        if t.endswith("."): s += 2
        if re.match(r"^(This|There|It|We|The following|In this|As shown)", t, re.I): s -= 3
        if re.search(r"(Sustainability|Energies|Water|Processes)\s+\d{4}", t): s -= 4
        if re.match(r"^\d+ of \d+$", t): s -= 4
        if t and t[0].isupper(): s += 1
        return s

    def build_charts():
        out = {}
        for (kind, num), lst in cand.items():
            b = max(lst, key=lambda x: score(kind, x["title"]))
            out["%s %d" % (kind, num)] = {"kind": kind, "number": num, "page": b["page"],
                                          "title": b["title"], "score": score(kind, b["title"]),
                                          "candidates": len(lst)}
        return out

    charts = build_charts()

    # ── 编号连续性检查 ──
    # 报告里「表 N/N 已覆盖」只证明「识别到的都渲染了」，**不能证明没有漏识别**。
    # 所以再扫一遍全文所有 Table N / Figure N 的编号，和识别结果做差集。
    seen_nums = defaultdict(set)   # kind -> set(nums)
    loose = re.compile(r"(Table|Figure)\s+(\d+)")
    for pgno in range(n_pages):
        for m in loose.finditer(doc[pgno].get_text()):
            seen_nums[m.group(1)].add(int(m.group(2)))

    def compute_gaps(chs):
        g = {}
        for kind in ("Table", "Figure"):
            got = {v["number"] for v in chs.values() if v["kind"] == kind}
            allnums = seen_nums.get(kind, set())
            if got:
                top = max(got)
                miss = sorted(n for n in allnums if n < top and n not in got)
            else:
                miss = sorted(allnums)
            if miss:
                g[kind] = {"missing": miss, "all_numbers": sorted(allnums), "detected": sorted(got)}
        return g

    gaps = compute_gaps(charts)

    # 阶段 2：对缺口的编号用宽松规则补
    if gaps:
        need = {(k, n) for k, g in gaps.items() for n in g["missing"]}
        log("  编号有缺口，用宽松规则补：%s" % sorted("%s %d" % x for x in need))
        for pgno in range(n_pages):
            txt = doc[pgno].get_text()
            for m in CAP_LOOSE.finditer(txt):
                kind, num = m.group(1), int(m.group(2))
                if (kind, num) in need:
                    add_cand(kind, num, m.group(3), pgno + 1)
        charts = build_charts()
        gaps = compute_gaps(charts)
        for kind, g in gaps.items():
            log("  ⚠️ %s 仍缺号 %s（全文出现过 %s）" % (kind, g["missing"], g["all_numbers"]))
        if not gaps:
            log("  ✅ 兜底后编号已连续")

    tables = {k: v for k, v in charts.items() if v["kind"] == "Table"}
    figures = {k: v for k, v in charts.items() if v["kind"] == "Figure"}
    low = [k for k, v in charts.items() if v["score"] < 2]

    log("  表 %d 个 / 图 %d 个（标题可信度低：%s）" % (len(tables), len(figures), low or "无"))
    doc.close()
    return {"chapters_source": src, "sections": sections, "tables": tables,
            "figures": figures, "low_conf": low, "n_pages": n_pages, "gaps": gaps}


# ══════════════════════════════════════════
# STEP 3：渲染全部图表页
# ══════════════════════════════════════════
def do_render(meta, news, work):
    sec("STEP 3：渲染图表页（不设上限）")
    need = sorted({v["page"] for v in list(news["tables"].values()) + list(news["figures"].values())})
    have = sorted({int(re.search(r"pg-(\d+)", f).group(1))
                   for f in os.listdir(work) if re.search(r"pg-(\d+)", f)})
    todo = [p for p in need if p not in have]
    log("  需要 %d 页：%s" % (len(need), need))
    log("  已有 %d 页，待渲染 %d 页：%s" % (len(have), len(todo), todo))
    if todo:
        use_norman = os.path.exists(NORMAN)
        if use_norman:
            r = subprocess.run([PY, "-W", "ignore", NORMAN, "render", meta["pdf_path"],
                                "--pages", ",".join(map(str, todo)), "--dpi", "150",
                                "--name", "pg", "--out", work],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=os.environ)
            log("  norman_reader 渲染退出码 %s" % r.returncode)

        def _have():
            return {int(re.search(r"pg-(\d+)", f).group(1))
                    for f in os.listdir(work) if re.search(r"pg-(\d+)", f)}

        # 渲染后检查，缺的用内置实现补（也能在没有 norman_reader 时全用内置）
        still = [p for p in todo if p not in _have()]
        if still:
            import pymupdf
            if not os.path.exists(NORMAN):
                log("  用内置渲染（无需 norman_reader）")
            else:
                log("  norman_reader 漏了 %s，用内置补" % still)
            doc = pymupdf.open(meta["pdf_path"])
            for p in still:
                pix = doc[p - 1].get_pixmap(dpi=150)
                pix.save(os.path.join(work, "pg-%02d.png" % p))
            doc.close()
    have2 = sorted({int(re.search(r"pg-(\d+)", f).group(1))
                    for f in os.listdir(work) if re.search(r"pg-(\d+)", f)})
    missing = [p for p in need if p not in have2]
    log("  %s 最终 %d 页，缺 %d 页 %s" % ("✅" if not missing else "❌", len(have2), len(missing), missing))
    return {"pages_needed": need, "pages_rendered": have2, "pages_missing": missing}


# ══════════════════════════════════════════
# STEP 4：完整性报告
# ══════════════════════════════════════════
def build_report(meta, news, rend):
    t, f = news["tables"], news["figures"]
    pngs = set(rend["pages_rendered"])
    empty = [s for s in news["sections"] if len(s["text"]) < 80]

    L = []
    L.append("### 完整性报告（自动生成，请核对）")
    L.append("")
    L.append("**章节来源**：%s" % news["chapters_source"])
    L.append("")
    L.append("| 项目 | 应有 | 已覆盖 | 缺失 |")
    L.append("|---|---|---|---|")
    tm = [n for n, v in t.items() if v["page"] not in pngs]
    fm = [n for n, v in f.items() if v["page"] not in pngs]
    L.append("| 表 | %d | %d | %s |" % (len(t), len(t) - len(tm), ", ".join(sorted(tm)) or "无"))
    L.append("| 图 | %d | %d | %s |" % (len(f), len(f) - len(fm), ", ".join(sorted(fm)) or "无"))
    L.append("| 章节 | %d | %d | %s |" % (
        len(news["sections"]), len(news["sections"]) - len(empty),
        ("空节 %d（父节无独立内容，属论文结构）" % len(empty)) if empty else "无"))
    L.append("")

    # 编号连续性：这一节才能回答「有没有漏识别」。
    # 上面的表格只证明「识别到的都渲染了」，不能证明没有漏。
    gaps = news.get("gaps") or {}
    L.append("**编号连续性检查**（这才是「有没有漏识别」的证据）")
    L.append("")
    if gaps:
        for kind, g in gaps.items():
            L.append("- ⚠️ **%s 缺号**：识别到 %s，缺 **%s**（全文出现过的编号：%s）"
                     % (kind, g["detected"], g["missing"], g["all_numbers"]))
        L.append("")
        L.append("  缺号通常意味着 caption 排版形态异常（和表格挤在一行，或用了别的写法），脚本没认出来。**建议人工翻到对应页核对。**")
    else:
        L.append("- ✅ 表、图的编号都连续，没有缺号")
    L.append("")

    L.append("**图表清单**（可信度：高=标题形态正常 / 低=可能错位，需人工核对）")
    L.append("")
    L.append("| 编号 | 页码 | 已渲染 | 标题可信度 | 标题 |")
    L.append("|---|---|---|---|---|")
    for d, lab in [(t, "Table"), (f, "Figure")]:
        for k in sorted(d, key=lambda x: d[x]["number"]):
            v = d[k]
            L.append("| %s %d | p%d | %s | %s | %s |" % (
                lab, v["number"], v["page"], "✅" if v["page"] in pngs else "❌",
                "高" if v["score"] >= 2 else "低", v["title"][:52].replace("|", "\\|")))
    L.append("")
    L.append("**章节清单**")
    L.append("")
    for s in news["sections"]:
        L.append("- L%d %s%s（p%s，%d 字）" % (
            s["level"], (s["number"] + " ") if s["number"] else "",
            s["heading"], s["page"], len(s["text"])))
    return "\n".join(L)


# ══════════════════════════════════════════
# STEP 5：生成 5 条笔记
# ══════════════════════════════════════════
def ds(system, user, max_tokens=10000, temperature=0.4, json_mode=False):
    key = io.open(KEY_FILE, encoding="utf-8").read().strip()
    payload = {"model": "deepseek-chat",
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": temperature}
    if json_mode: payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.loads(r.read().decode())
    return d["choices"][0]["message"]["content"], d.get("usage", {})


def parse_json(raw):
    t = raw.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?", "", t); t = re.sub(r"\n?```$", "", t)
    return json.loads(t)


import urllib.request


def do_notes(meta, sm, news, work):
    sec("STEP 5：生成 5 条笔记")
    blocks = sm["blocks"]
    fulltext = "\n".join((b.get("text") or "") for b in blocks)
    BASE = """论文：%s
作者：%s
年份：%s　期刊：%s　DOI：%s

摘要：
%s

""" % (meta["title"], meta["authors"], meta["year"], meta["venue"], meta["doi"], meta["abstract"][:1200])
    res = {}

    def trunc(n):
        return fulltext[:n]

    # ①
    log("  ① 飞书速览 ...")
    S1 = """你是学术论文信息提取专家。用飞书「AI 文献批量速读模版」19 项结构填满。
规则：缺失填「未提及」，禁止编造；中文；禁解释性描述；数字要具体；只输出 Markdown。
标题原样保留：
# 飞书速览：{一句话中文结论}
## 一、基础信息
1、英文标题： 2、中文标题： 3、作者： 4、期刊名称： 5、发表年份：
6、摘要：（完整中文翻译） 7、关键词：
## 二、核心内容
8、核心论点： 9、研究目标： 10、研究问题：
11、研究设计：（数据来源/样本/时间跨度/识别策略/变量测度）
12、理论机制： 13、研究结论：（含具体系数与显著性） 14、理论贡献：
## 三、评判与延伸
15、局限性： 16、应用价值： 17、文献综述： 18、学术对话： 19、未来选题："""
    r, u = ds(S1, BASE + "正文：\n" + trunc(50000), 9000, 0.3)
    res["note1"] = r
    log("     %d 字" % len(r))

    # ②
    log("  ② 证据链 ...")
    K = re.compile(r"(Table\s*\d|Figure\s*\d|0\.\d{2,}|%|p\s*<\s*0|significant|robustness|hypothes)", re.I)
    kb = [b for b in blocks if K.search(b.get("text") or "") and len(b.get("text") or "") > 40]
    kb.sort(key=lambda b: (-len(re.findall(r"\d", b.get("text") or "")), b["id"]))
    picked = sorted(kb[:110], key=lambda b: b["id"])
    corpus = "\n".join("[%s] (p%s) %s" % (b["id"], b.get("page"), (b.get("text") or "").replace("\n", " ")) for b in picked)
    S2 = """你是严格的事实核查员。生成 claims/evidence/links/branches/actions。
硬约束：source_ids 必须从给定 block id 里选，不许编造；locator 写 "PDF page N" 或 "PDF page N, 表 M"；
claims.origin ∈ paper_fact/interpretation/my_critique；evidence.inspection ∈ verified/not_visual_checked/unavailable；
links.relation ∈ supports/limits/contradicts/mixed；每条 claim 必须有 caveat；每条 link 必须有 rationale。
输出 JSON：
{"claims":[{"id":"C01","statement":"","origin":"paper_fact","caveat":""}],
 "evidence":[{"id":"E01","kind":"table|text|figure","locator":"","source_ids":[""],"observation":"","inspection":"verified"}],
 "links":[{"claim_id":"C01","evidence_id":"E01","relation":"supports","strength":"strong|moderate|weak","rationale":""}],
 "branches":[{"id":"B01","question":"","why":"","difficulty":"high|medium|low","data_needed":""}],
 "actions":[{"id":"A01","action":"","rationale":"","evidence_ids":["E01"],"priority":"high|medium|low"}]}
目标：7-10 claims / 8-11 evidence / 9-13 links / 4-6 branches / 4-6 actions。"""
    r2, u2 = ds(S2, "论文：%s\n\n文本块：\n%s" % (meta["title"], corpus), 9000, 0.3, True)
    try:
        ev = parse_json(r2)
        valid = {b["id"] for b in blocks}
        for e in (ev.get("evidence") or []):
            good = [s for s in (e.get("source_ids") or []) if s in valid]
            e["source_ids"] = good
            if not good: e["inspection"] = "unavailable"
        res["evidence"] = ev
        log("     %d claims / %d evidence / %d links" % (
            len(ev.get("claims") or []), len(ev.get("evidence") or []), len(ev.get("links") or [])))
    except Exception as e:
        log("     ❌ %s" % str(e)[:100])
        res["evidence"] = {"claims": [], "evidence": [], "links": [], "branches": [], "actions": []}

    # ③
    log("  ③ 逐节精读 ...")
    S3 = """为论文每一节写「摘要 + 评析」。
summary 只复述（含数字/表号/方法名）；analysis 才评判（证据强度/假设/局限）。
禁止「不是…而是…」句式；禁止第一人称；见不到内容的写「该节提取到的文本不足」。
输出 JSON：{"sections":[{"heading":"","summary":"","analysis":""}]}
heading 必须与输入逐字一致、顺序一致。"""
    out = []
    B = 4
    sects = news["sections"]
    for bi in range(0, len(sects), B):
        batch = sects[bi:bi+B]
        items = "\n\n---\n\n".join("[%d] heading=%s | page=%s\n%s" % (
            bi+j, s["heading"], s["page"], s["text"][:2600]) for j, s in enumerate(batch))
        try:
            r3, _ = ds(S3, "论文：%s\n\n%s" % (meta["title"], items), 6000, 0.3, True)
            got = parse_json(r3).get("sections") or []
            for j, g in enumerate(got):
                if bi + j < len(sects):
                    s = sects[bi+j]
                    out.append({"number": s["number"], "heading": s["heading"], "page": s["page"],
                                "level": s["level"], "summary": g.get("summary", ""),
                                "analysis": g.get("analysis", "")})
            while len(out) < bi + len(batch):
                s = sects[len(out)]
                out.append({"number": s["number"], "heading": s["heading"], "page": s["page"],
                            "level": s["level"], "summary": "该节提取到的文本不足，无法判断。",
                            "analysis": "生成阶段未返回该节内容。"})
            log("     批次 %d/%d ✅" % (bi//B+1, (len(sects)+B-1)//B))
        except Exception as e:
            log("     批次 %d/%d ❌ %s" % (bi//B+1, (len(sects)+B-1)//B, str(e)[:60]))
            for j in range(len(batch)):
                s = sects[bi+j]
                out.append({"number": s["number"], "heading": s["heading"], "page": s["page"],
                            "level": s["level"], "summary": "该节生成失败。",
                            "analysis": "生成异常：%s" % str(e)[:60]})
    res["sections"] = out
    log("     共 %d 节" % len(out))

    # ④
    log("  ④ 零基础拆解 ...")
    S4 = """你擅长把论文讲给外行听，同时能还原作者决策过程。
读者：刚入学的硕士生，本科非本专业，第一次系统读文献。
铁律一：从生活经验出发；术语先大白话解释再给名字；禁止要求读者自行翻译的句子；
       不用「输入/组件/反馈/风险」代替具体对象和动作。
铁律二：每处回答「他为什么这么干」而不是只答「他干了什么」。
铁律三：区分「论文实测的」「作者说的」「我推测的」，推测必须标「推断」。
标题原样保留：
# 零基础拆解：{一句话结论}
## 0. 先用一个日常场景说明这篇论文在干什么
## 1. 作者当时面对什么
### 1.1 这个领域当时在做什么
### 1.2 卡在哪里（具体困难）
### 1.3 作者手里有什么
### 1.4 如果他不写这篇，会缺什么
### 1.5 为什么选这条路而不是别的（表格：备选方案/代价/作者为什么没选）
## 2. 论文的骨架：每小节为什么这么写
## 3. 核心机制讲透
## 4. 关键句子为什么这么写
## 5. 图表为什么画这个
## 6. 数字到底在说什么
## 7. 这篇论文的边界
## 8. 现在你能自己解释什么
## 作者意图推断声明"""
    r4, _ = ds(S4, BASE + "正文：\n" + trunc(50000), 12000, 0.5)
    res["note4"] = r4
    log("     %d 字" % len(r4))

    # ⑤
    log("  ⑤ 决策地图 ...")
    S5A = """你是「论文作者决策侦查员」。找出这篇论文里所有「作者本可以选别的，但他选了这个」的决策点。
不要套预设分类。什么算：有真实备选方案 且 换了备选结论/贡献/可信度会实质变化。
什么不算：「按惯例就该这么做」/「数据只能这么拿」/ 纯写作风格 / 插值补缺失 / 软件版本。
每条给出：id / decision（具体，不许写类名）/ alternatives / forgone / why_not_others /
evidence_strength(explicit|inferred|guess) / impact / importance(1-5)。
排序：按重要性降序。数量由论文决定。
输出 JSON：{"decisions":[...], "count_note":"为什么是这么多个，不是更多或更少"}"""
    r5a, _ = ds(S5A, "论文：%s\n\n正文：\n%s" % (meta["title"], trunc(48000)), 8000, 0.3, True)
    try:
        dec = parse_json(r5a)
        dl = dec.get("decisions") or []
        res["decisions"] = dec
        log("     %d 个决策点" % len(dl))
    except Exception as e:
        log("     ❌ %s" % str(e)[:80])
        res["decisions"] = {"decisions": []}
        dl = []

    if dl:
        dtxt = "\n\n".join("【%s｜重要度%s】%s\n  备选：%s\n  放弃：%s\n  依据(%s)：%s\n  影响：%s" % (
            d.get("id"), d.get("importance"), d.get("decision"),
            " / ".join(d.get("alternatives") or []), d.get("forgone"),
            d.get("evidence_strength"), d.get("why_not_others"), d.get("impact")) for d in dl)
        S5B = """你按「作者决策点清单」组织笔记（清单来自论文本身，不是预设分类）。
结构由清单决定，不要套固定章节名。每个决策标题用它自己的内容，不许用类名。
零基础能懂：术语先大白话解释再给名字；禁止要求读者自行翻译。
每个决策回答五件事：他当时面对的局面 → 选了什么放弃什么 → 为什么这么选 → 换成备选会怎样 → 最容易被审稿人追问什么。
严格区分：论文实测的 / 作者说的 / 你推测的（推测标「推断」）。不许编造作者心理。
标题原样保留：
# 作者决策地图：{一句话说清这篇论文靠哪几个选择撑起来}
## 先用一个日常场景说清这篇论文在干什么
## 这篇论文的决策地图
（表格：决策点/一句话/放弃什么/影响哪里/依据强度）
## 决策一：{用决策点内容命名}
### 他当时面对的局面
### 他选了什么，放弃了什么
### 为什么这么选（依据来自哪里）
### 如果换成备选方案，这篇论文会变成什么样
### 这里最容易被审稿人追问什么
（决策二到决策N，同样）
## 这些决策串起来，构成了什么样的论文
## 读者现在能自己回答什么
## 我的推断与依据强度
最后一行原样输出：以上拆解基于论文文本，作者真实动机只能从文本反推，无法完全确认。"""
        r5b, _ = ds(S5B, "论文：%s\n\n=== 决策点清单 ===\n%s\n\n=== 正文 ===\n%s" % (
            meta["title"], dtxt, trunc(38000)), 14000, 0.5)
        res["note5"] = r5b
        log("     %d 字" % len(r5b))

    io.open(os.path.join(work, "notes.json"), "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=1))
    return res



# ══════════════════════════════════════════
# Zotero storage 索引检查 / 修复
# ══════════════════════════════════════════
def _copy_zotero_db(tag):
    """把 Zotero 数据库（含 WAL）拷到临时目录，返回可读的 sqlite 路径。"""
    tmp = os.path.join(TMP, tag)
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp, exist_ok=True)
    for fn in ["zotero.sqlite", "zotero.sqlite-wal", "zotero.sqlite-shm"]:
        p = os.path.join(ZOTERO, fn)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(tmp, fn))
    return os.path.join(tmp, "zotero.sqlite")


def _query_storage_state(dbpath, keys=None):
    """返回 {key: (syncState, storageHash)}。keys=None 表示查全部 image/png。"""
    conn = sqlite3.connect(dbpath)
    cur = conn.cursor()
    sql = """SELECT a.key, ia.syncState, ia.storageHash
             FROM itemAttachments ia JOIN items a ON a.itemID = ia.itemID
             WHERE ia.contentType = 'image/png'"""
    params = ()
    if keys:
        sql += " AND a.key IN (%s)" % ",".join("?" * len(keys))
        params = tuple(keys)
    cur.execute(sql, params)
    out = {k: (ss, sh) for k, ss, sh in cur.fetchall()}
    conn.close()
    return out


def _check_storage_index(keys):
    """写库后检查：Zotero 能不能渲染这些图片。

    Zotero 渲染笔记里的 <img data-attachment-key> 时，会校验
    itemAttachments.syncState / storageHash / storageModTime。
    这三项没就绪时，即使 key 真实、文件也在，图在 Zotero 里就是不显示。
    """
    try:
        db = _copy_zotero_db("_suite_storage")
        state = _query_storage_state(db, list(keys))
    except Exception as e:
        log("  ⚠️ 查 storage 索引失败（不影响已写入的笔记）：%s" % str(e)[:100])
        return

    bad = [k for k, (ss, sh) in state.items() if ss == 0 or not sh]
    if not bad:
        log("  ✅ storage 索引就绪（%d 张图，Zotero 能直接渲染）" % len(state))
        return

    log("")
    log("  ⚠️ 有 %d 张图的 storage 索引没就绪：%s" % (len(bad), bad))
    log("     表现：Zotero 笔记里这些图位置是空的（key 和文件都没问题，是索引没建）。")
    log("     处理：")
    log("       1) 先试重启 Zotero（它会自己补索引）")
    log("       2) 还不行就关掉 Zotero，跑：")
    log("          python paper_reading_suite.py <任意key> --fix-storage")


def fix_storage():
    """补 image/png 附件的 storage 索引：syncState=2 + storageHash(MD5) + storageModTime。

    必须在 Zotero 关闭时跑，否则会被 Zotero 覆盖。
    """
    sec("修复 Zotero storage 索引")
    # 1. 确认 Zotero 没在跑
    running = False
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq zotero.exe"],
                                 capture_output=True, text=True, timeout=15)
            running = "zotero.exe" in (out.stdout or "").lower()
        else:
            out = subprocess.run(["pgrep", "-x", "zotero"],
                                 capture_output=True, text=True, timeout=15)
            running = bool((out.stdout or "").strip())
    except Exception:
        pass
    if running:
        log("  ❌ Zotero 还在运行。先完全退出 Zotero 再跑这个命令，")
        log("     否则你改的记录会被 Zotero 覆盖。")
        raise SystemExit(1)

    # 2. 找所有没就绪的
    db = _copy_zotero_db("_suite_fixscan")
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("""SELECT a.key, ia.path, ia.syncState, ia.storageHash
                   FROM itemAttachments ia JOIN items a ON a.itemID = ia.itemID
                   WHERE ia.contentType = 'image/png'
                     AND (ia.syncState = 0 OR ia.storageHash IS NULL)""")
    todo = cur.fetchall()
    conn.close()

    if not todo:
        log("  ✅ 所有图片附件的索引都已就绪，不用修")
        return

    log("  找到 %d 张需要修：" % len(todo))
    for k, path, ss, sh in todo:
        log("    %-11s syncState=%s hash=%s" % (k, ss, sh[:12] if sh else "空"))
    log("")

    # 3. 写在真库上（Zotero 已关闭）
    realdb = os.path.join(ZOTERO, "zotero.sqlite")
    conn = sqlite3.connect(realdb)
    cur = conn.cursor()
    ok = fail = 0
    for k, path, ss, sh in todo:
        rel = str(path).replace("storage:", "")
        full = os.path.join(ZOTERO, "storage", k, rel)
        if not os.path.exists(full):
            log("    ❌ %-11s 磁盘文件不存在：%s" % (k, full))
            fail += 1
            continue
        md5 = hashlib.md5(open(full, "rb").read()).hexdigest()
        mt = int(os.stat(full).st_mtime * 1000)
        cur.execute("""UPDATE itemAttachments
                       SET syncState = 2, storageHash = ?, storageModTime = ?
                       WHERE itemID = (SELECT itemID FROM items WHERE key = ?)""",
                    (md5, mt, k))
        if cur.rowcount == 1:
            log("    ✅ %-11s hash=%s" % (k, md5[:16] + "…"))
            ok += 1
        else:
            fail += 1
    conn.commit()
    conn.close()

    log("")
    log("  修好 %d 张，失败 %d 张" % (ok, fail))
    log("  现在启动 Zotero，笔记里的图应该能显示了。")


# ══════════════════════════════════════════
# STEP 6：写进 Zotero
# ══════════════════════════════════════════
def call(name, args, timeout=600):
    global _rid
    _rid += 1
    body = json.dumps({"jsonrpc": "2.0", "id": _rid, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request(MCP, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
    if raw.startswith("event:") or "data: " in raw[:100]:
        for line in raw.split("\n"):
            if line.startswith("data: "):
                raw = line[6:]; break
    j = json.loads(raw)
    if "error" in j: return {"error": j["error"]}
    content = j.get("result", {}).get("content") or []
    txt = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    try: return json.loads(txt)
    except Exception: return {"_text": txt}


_rid = 0


def md2html(md):
    import html as H
    def esc(s): return H.escape(str(s or ""), quote=False)
    def inl(s):
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s
    lines = md.split("\n"); out = []; i = 0
    inc = False; cbuf = []
    while i < len(lines):
        s = lines[i].rstrip()
        if s.strip().startswith("```"):
            if not inc: inc = True; cbuf = []
            else:
                inc = False; out.append("<pre><code>%s</code></pre>" % esc("\n".join(cbuf)))
            i += 1; continue
        if inc: cbuf.append(lines[i]); i += 1; continue
        if not s.strip(): i += 1; continue
        if s.strip().startswith("|") and s.strip().endswith("|"):
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i].strip()); i += 1
            rows = []
            for r in tbl:
                cs = [c.strip() for c in r.strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cs): continue
                rows.append(cs)
            if rows:
                out.append('<table border="1" cellspacing="0" cellpadding="6" style="border-collapse:collapse;width:100%">')
                for ri, r in enumerate(rows):
                    tg = "th" if ri == 0 else "td"
                    out.append("<tr>" + "".join("<%s>%s</%s>" % (tg, inl(esc(c)), tg) for c in r) + "</tr>")
                out.append("</table>")
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", s)
        if m:
            lv = len(m.group(1)); out.append("<h%d>%s</h%d>" % (lv, inl(esc(m.group(2))), lv)); i += 1; continue
        if s.strip().startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip()); i += 1
            out.append("<blockquote>%s</blockquote>" % inl(esc(" ".join(buf)))); continue
        if re.fullmatch(r"-{3,}", s.strip()):
            out.append("<hr/>"); i += 1; continue
        if re.match(r"^[-*+]\s+", s):
            it = []
            while i < len(lines) and re.match(r"^[-*+]\s+", lines[i]):
                it.append(re.sub(r"^[-*+]\s+", "", lines[i].strip())); i += 1
            out.append("<ul>" + "".join("<li>%s</li>" % inl(esc(x)) for x in it) + "</ul>"); continue
        if re.match(r"^\d+\.\s+", s):
            it = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                it.append(re.sub(r"^\d+\.\s+", "", lines[i].strip())); i += 1
            out.append("<ol>" + "".join("<li>%s</li>" % inl(esc(x)) for x in it) + "</ol>"); continue
        buf = []
        while i < len(lines) and lines[i].strip() and \
                not re.match(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|>|\||```)", lines[i].strip()):
            buf.append(lines[i].strip()); i += 1
        if buf: out.append("<p>%s</p>" % inl(esc(" ".join(buf))))
    return "\n".join(out)


def do_write(meta, notes, news, rend, report_md, work):
    import html as H
    sec("STEP 6：写进 Zotero")
    parent = meta["parent_key"]

    # 1. 图片导入
    pngs = sorted([f for f in os.listdir(work) if f.lower().endswith(".png")])
    log("  导入 %d 张图..." % len(pngs))
    for f in pngs:
        m = re.search(r"pg-(\d+)", f)
        pg = int(m.group(1)) if m else 0
        call("write_item", {"action": "import", "parentItemKey": parent,
                            "filePath": os.path.join(work, f),
                            "title": "PDF p%d" % pg})
        time.sleep(0.2)

    # 2. 回查真 key
    tmp = os.path.join(TMP, "_suite_verify")
    if os.path.isdir(tmp): shutil.rmtree(tmp)
    os.makedirs(tmp)
    for fn in ["zotero.sqlite", "zotero.sqlite-wal", "zotero.sqlite-shm"]:
        p = os.path.join(ZOTERO, fn)
        if os.path.exists(p): shutil.copy2(p, os.path.join(tmp, fn))
    conn = sqlite3.connect(os.path.join(tmp, "zotero.sqlite"))
    cur = conn.cursor()
    cur.execute("""SELECT a.key, ia.path FROM itemAttachments ia
                   JOIN items a ON a.itemID=ia.itemID
                   JOIN items p ON p.itemID=ia.parentItemID
                   WHERE p.key=? AND ia.contentType='image/png'""", (parent,))
    keymap = {}
    real = set()
    for k, path in cur.fetchall():
        rel = str(path).replace("storage:", "")
        mm = re.search(r"pg-(\d+)", rel)
        if mm:
            keymap[int(mm.group(1))] = k
            real.add(k)
    conn.close()
    log("  数据库真实图片 key: %d 个" % len(real))

    # 2.5 检查 Zotero 的 storage 索引（关键！）
    # ⚠️ 光有 key 和磁盘文件还不够。Zotero 渲染笔记图片时会校验
    #    itemAttachments.syncState / storageHash / storageModTime。
    #    syncState=0 且 storageHash 为空时，图在 Zotero 里显示不出来（2026-10-25 实测踩过）。
    #    Zotero 通常会异步补齐这三项，但通过 MCP import 的附件有时不会被处理到。
    _check_storage_index(real)

    def img_for(pages):
        ps = []
        for pg in pages:
            if pg in keymap:
                ps.append('<p><img alt="PDF page %d" width="1241" height="1754" data-attachment-key="%s"></p>'
                          % (pg, keymap[pg]))
        return "\n".join(ps)

    chart_pages = sorted(set(rend["pages_rendered"]))
    report_html = md2html(report_md)

    def hdr(badge, source, extra):
        return '<h1>%s</h1><p><strong>%s</strong></p><p>%s</p><hr/>' % (
            H.escape(badge, quote=False), H.escape(source, quote=False), H.escape(extra, quote=False))

    created = []
    # ①
    if notes.get("note1"):
        body = hdr("【① 飞书模版 · 速览】" + meta["title"],
                   "来源：飞书「AI 文献批量速读模版2.0」19 项结构",
                   "定位：快速过一遍。") + report_html + "<hr/>" + md2html(notes["note1"])
        body += "\n<hr/><h2>附：图表页原图</h2>\n" + img_for(chart_pages)
        r = call("write_note", {"action": "create", "parentKey": parent, "content": body,
                                "tags": ["飞书模版", "①速览", "19项"]})
        k = (r.get("data") or {}).get("noteKey")
        log("  %s ① %s" % ("✅" if k else "❌", k))
        if k: created.append(("①", k, len(body)))

    # ②
    ev = notes.get("evidence") or {}
    if ev.get("claims"):
        P = ["<h2>证据卡：论断与证据</h2>"]
        emap = {e["id"]: e for e in (ev.get("evidence") or [])}
        lmap = {}
        for l in (ev.get("links") or []):
            lmap.setdefault(l.get("claim_id"), []).append(l)
        for c in (ev.get("claims") or []):
            P.append("<h3>%s（%s）</h3>" % (H.escape(c.get("id", ""), quote=False), H.escape(c.get("origin", ""), quote=False)))
            P.append("<p><strong>陈述</strong>：%s</p>" % H.escape(c.get("statement", ""), quote=False))
            P.append("<p><strong>边界</strong>：%s</p>" % H.escape(c.get("caveat", ""), quote=False))
            for l in lmap.get(c.get("id"), []):
                e = emap.get(l.get("evidence_id")) or {}
                P.append("<p><strong>证据 %s</strong>（%s）｜%s（%s）：%s</p>" % (
                    H.escape(e.get("id", ""), quote=False), H.escape(e.get("locator", ""), quote=False),
                    H.escape(l.get("relation", ""), quote=False), H.escape(l.get("strength", ""), quote=False),
                    H.escape(l.get("rationale", ""), quote=False)))
        P.append("<h2>未来研究洞察</h2><ul>")
        for b in (ev.get("branches") or []):
            P.append("<li><strong>%s</strong>：%s</li>" % (
                H.escape(b.get("id", ""), quote=False), H.escape(b.get("question", ""), quote=False)))
        P.append("</ul><h2>下一步动作</h2><ul>")
        for a in (ev.get("actions") or []):
            P.append("<li><strong>%s</strong>：%s</li>" % (
                H.escape(a.get("id", ""), quote=False), H.escape(a.get("action", ""), quote=False)))
        P.append("</ul>")
        body = hdr("【② 证据链】" + meta["title"],
                   "来源：%d blocks → %d claims / %d evidence / %d links" % (
                       len(news["sections"]), len(ev.get("claims") or []),
                       len(ev.get("evidence") or []), len(ev.get("links") or [])),
                   "每条论断钉在具体 block 上。") + report_html + "<hr/>" + "\n".join(P)
        body += "\n<hr/><h2>附：图表页原图</h2>\n" + img_for(chart_pages)
        r = call("write_note", {"action": "create", "parentKey": parent, "content": body,
                                "tags": ["证据链", "②Norman", "可校验"]})
        k = (r.get("data") or {}).get("noteKey")
        log("  %s ② %s" % ("✅" if k else "❌", k))
        if k: created.append(("②", k, len(body)))

    # ③
    ss = notes.get("sections") or []
    if ss:
        P = ["<h2>逐节精读（%d 节）</h2>" % len(ss)]
        for s in ss:
            P.append("<h3>%s%s（p%s）</h3>" % (
                (H.escape(str(s["number"]) + " ", quote=False)) if s.get("number") else "",
                H.escape(s.get("heading", ""), quote=False), H.escape(str(s.get("page", "")), quote=False)))
            P.append("<p><strong>该节说什么</strong>：%s</p>" % H.escape(s.get("summary", ""), quote=False))
            P.append("<p><strong>该节是否站得住</strong>：%s</p>" % H.escape(s.get("analysis", ""), quote=False))
        body = hdr("【③ 精读笔记 · 逐节 %d 节】" % len(ss) + meta["title"],
                   "来源：%s" % news["chapters_source"],
                   "定位：看每一节怎么论证、哪里站不住。") + report_html + "<hr/>" + "\n".join(P)
        body += "\n<hr/><h2>附：图表页原图</h2>\n" + img_for(chart_pages)
        r = call("write_note", {"action": "create", "parentKey": parent, "content": body,
                                "tags": ["精读", "③逐节", "%d节" % len(ss)]})
        k = (r.get("data") or {}).get("noteKey")
        log("  %s ③ %s" % ("✅" if k else "❌", k))
        if k: created.append(("③", k, len(body)))

    # ④
    if notes.get("note4"):
        body = hdr("【④ 零基础拆解 · 作者视角】" + meta["title"],
                   "来源：ljg-paper 方法论（lijigang/ljg-skills, MIT）+ 作者决策重建层",
                   "读者：刚入学的硕士生，本科非本专业。") + report_html + "<hr/>" + md2html(notes["note4"])
        body += "\n<hr/><h2>附：图表页原图</h2>\n" + img_for(chart_pages)
        r = call("write_note", {"action": "create", "parentKey": parent, "content": body,
                                "tags": ["零基础拆解", "④作者视角", "硕士入门"]})
        k = (r.get("data") or {}).get("noteKey")
        log("  %s ④ %s" % ("✅" if k else "❌", k))
        if k: created.append(("④", k, len(body)))

    # ⑤
    dec = notes.get("decisions") or {}
    if notes.get("note5"):
        cnt = Counter(d.get("evidence_strength") for d in (dec.get("decisions") or []))
        ex = "<p><strong>做法：先从论文侦查出决策点（本文 %d 个），再按决策点组织。结构因论文而异。</strong></p>" % len(dec.get("decisions") or [])
        ex += "<p>依据强度：explicit %d / inferred %d / guess %d</p>" % (
            cnt.get("explicit", 0), cnt.get("inferred", 0), cnt.get("guess", 0))
        body = hdr("【⑤ 决策地图 · 按这篇论文自己的选择组织】" + meta["title"],
                   "来源：决策侦查 + 决策驱动组织（两步，非固定章节）",
                   "") + ex + report_html + "<hr/>" + md2html(notes["note5"])
        body += "\n<hr/><h2>附：图表页原图</h2>\n" + img_for(chart_pages)
        r = call("write_note", {"action": "create", "parentKey": parent, "content": body,
                                "tags": ["决策地图", "⑤作者决策", "%d个决策点" % len(dec.get("decisions") or [])]})
        k = (r.get("data") or {}).get("noteKey")
        log("  %s ⑤ %s" % ("✅" if k else "❌", k))
        if k: created.append(("⑤", k, len(body)))

    # 3. 验证假 key
    log("")
    log("  验证：回查每条笔记的图片 key")
    tmp2 = os.path.join(TMP, "_suite_final")
    if os.path.isdir(tmp2): shutil.rmtree(tmp2)
    os.makedirs(tmp2)
    for fn in ["zotero.sqlite", "zotero.sqlite-wal", "zotero.sqlite-shm"]:
        p = os.path.join(ZOTERO, fn)
        if os.path.exists(p): shutil.copy2(p, os.path.join(tmp2, fn))
    conn = sqlite3.connect(os.path.join(tmp2, "zotero.sqlite"))
    cur = conn.cursor()
    for label, k, ln in created:
        cur.execute("SELECT note FROM itemNotes n JOIN items i ON i.itemID=n.itemID WHERE i.key=?", (k,))
        r = cur.fetchone()
        if not r: continue
        note = r[0]
        used = set(re.findall(r'data-attachment-key="([A-Z0-9]+)"', note))
        fake = used - real
        log("    %s %-11s %7d 字符  img=%d 假key=%d %s" % (
            label, k, ln, note.count("<img"), len(fake), "✅" if not fake else "⚠️" + str(fake)))
    conn.close()
    return created


# ══════════════════════════════════════════
# STEP -1：环境预检
# ══════════════════════════════════════════
def preflight(need_notes, need_write):
    """开跑前把缺的东西一次说清，而不是跑到一半崩在 traceback 里。"""
    sec("STEP -1：环境预检")
    problems = []
    notes = []

    # 1. pymupdf
    try:
        import pymupdf
        v = getattr(pymupdf, "__version__", "")
        notes.append("✅ pymupdf %s" % v if v else "✅ pymupdf")
    except ImportError:
        problems.append("缺 pymupdf。装：pip install pymupdf")

    # 2. Zotero 数据目录
    sq = os.path.join(ZOTERO, "zotero.sqlite")
    if not os.path.exists(sq):
        problems.append(
            "找不到 Zotero 数据库：%s\n"
            "     → 确认 Zotero 已安装并至少打开过一次\n"
            "     → 或设环境变量 ZOTERO_HOME 指向你的 Zotero 数据目录" % sq)
    else:
        try:
            tmp = os.path.join(TMP, "_preflight")
            os.makedirs(tmp, exist_ok=True)
            for fn in ["zotero.sqlite", "zotero.sqlite-wal", "zotero.sqlite-shm"]:
                p = os.path.join(ZOTERO, fn)
                if os.path.exists(p):
                    shutil.copy2(p, os.path.join(tmp, fn))
            conn = sqlite3.connect(os.path.join(tmp, "zotero.sqlite"))
            n = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            conn.close()
            notes.append("✅ Zotero 数据库可读（%d 个条目）" % n)
        except Exception as e:
            problems.append("Zotero 数据库读不了：%s" % str(e)[:120])

    # 3. DeepSeek key（只在要生成笔记时需要）
    if need_notes:
        if not os.path.exists(KEY_FILE):
            problems.append(
                "找不到 DeepSeek API key：%s\n"
                "     → 建这个文件，里面只写一行 key（不要提交进任何仓库）\n"
                "     → 或设环境变量 DEEPSEEK_KEY_FILE 指向你的 key 文件" % KEY_FILE)
        else:
            k = io.open(KEY_FILE, encoding="utf-8").read().strip()
            if not k:
                problems.append("DeepSeek key 文件是空的：%s" % KEY_FILE)
            else:
                notes.append("✅ DeepSeek key 已读到（%d 字符）" % len(k))

    # 4. zotero-mcp（只在要写库时需要）——不光要连得上，还要有两个具体工具
    if need_write:
        import urllib.request as _u

        def _mcp(method, params=None, timeout=15):
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                               "params": params or {}}).encode()
            req = _u.Request(MCP, data=body,
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"}, method="POST")
            with _u.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", errors="replace")
            if raw.startswith("event:") or "data: " in raw[:100]:
                for line in raw.split("\n"):
                    if line.startswith("data: "):
                        raw = line[6:]
                        break
            return json.loads(raw)

        tools = None
        try:
            tl = _mcp("tools/list")
            tools = {t.get("name") for t in tl.get("result", {}).get("tools", [])}
        except Exception as e:
            problems.append(
                "连不上 zotero-mcp：%s\n"
                "     错误：%s\n"
                "     → 需要装 zotero-mcp 插件并在 Zotero 里启用\n"
                "     → 或设环境变量 ZOTERO_MCP_URL 指向你的端点" % (MCP, str(e)[:100]))

        if tools is not None:
            # 本脚本的写库路径写死了这两个工具名，必须存在
            REQ = {"write_note": "创建笔记", "write_item": "导入图片附件"}
            missing = [k for k, v in REQ.items() if k not in tools]
            if missing:
                # 给出「你装的是官方版」这种最常见情况的明确诊断
                hint = ""
                if "zotero_manage_note" in tools:
                    hint = ("\n     诊断：你连的像是官方 54yyyu/zotero-mcp —— 它把写笔记叫 "
                            "zotero_manage_note，把附件叫 zotero_attach_file。\n"
                            "     本脚本只认 write_note / write_item（自定义/集成版命名），"
                            "所以这两套接口目前不通用。")
                problems.append(
                    "zotero-mcp 连上了，但缺少本脚本需要的工具：%s\n"
                    "     服务端 %s 共提供 %d 个工具%s\n"
                    "     → 需要带 write_note + write_item 的 zotero-mcp 变体\n"
                    "     → 或者跳过写库：加 --skip-write（笔记只生成不写回 Zotero）\n"
                    "     → 或者只跑本地部分：加 --skip-notes（完全不需要 MCP 和 key）"
                    % ("、".join("%s（%s）" % (k, REQ[k]) for k in missing),
                       MCP, len(tools), hint))
            else:
                notes.append("✅ zotero-mcp 有两个必需工具（write_note / write_item），共 %d 个" % len(tools))

    # 5. norman_reader（可选）
    if os.path.exists(NORMAN):
        notes.append("✅ norman_reader 在位（会用它切块/渲染）")
    else:
        notes.append("ℹ️  没有 norman_reader，用内置 pymupdf 切块/渲染（功能等价）")

    for x in notes:
        log("  " + x)
    if problems:
        log("")
        log("  ❌ 有 %d 个问题必须先解决：" % len(problems))
        for i, p in enumerate(problems, 1):
            log("    %d. %s" % (i, p))
        log("")
        raise SystemExit("预检未通过，已停止（没有产生任何副作用）")
    log("")
    log("  ✅ 预检通过")


# ══════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(
        description="把 Zotero 里的一篇 PDF 论文变成 5 条结构化笔记，附完整性报告。",
        epilog="环境变量：PAPER_SUITE_TMP / NORMAN_READER / DEEPSEEK_KEY_FILE / "
               "ZOTERO_HOME / ZOTERO_MCP_URL / PAPER_SUITE_PYTHON")
    ap.add_argument("item_key", help="Zotero 父条目 key（8 位，如 TLCGH5DB）")
    ap.add_argument("--work", default=None, help="工作目录（默认在 TMP 下建）")
    ap.add_argument("--skip-notes", action="store_true", help="只做检测 + 渲染 + 完整性报告，不调模型")
    ap.add_argument("--skip-write", action="store_true", help="生成笔记但不写进 Zotero")
    ap.add_argument("--check", action="store_true", help="只做环境预检，然后退出")
    ap.add_argument("--fix-storage", action="store_true",
                    help="（需先关闭 Zotero）补图片附件的 storage 索引，修「笔记里看不到图」")
    args = ap.parse_args()

    need_notes = not args.skip_notes
    need_write = need_notes and not args.skip_write

    if args.fix_storage:
        fix_storage()
        return

    if args.check:
        # 全量检查，并把「三种用法各需要什么」一并打出来
        ok = True
        try:
            preflight(True, True)
        except SystemExit:
            ok = False
        log("")
        log("=" * 78)
        log("三种用法，需要的东西不一样")
        log("=" * 78)
        log("  python paper_reading_suite.py <key> --skip-notes --skip-write")
        log("      → 切块 + 章节 + 图表 + 渲染 + 完整性报告")
        log("      → 只要：pymupdf + Zotero")
        log("      → 不要：DeepSeek key、zotero-mcp")
        log("")
        log("  python paper_reading_suite.py <key> --skip-write")
        log("      → 上面全部 + 5 条笔记的文字（不写回 Zotero）")
        log("      → 还要：DeepSeek key")
        log("")
        log("  python paper_reading_suite.py <key>")
        log("      → 全流程，含写回 Zotero（5 条笔记 + 图表页图片）")
        log("      → 还要：zotero-mcp，且必须提供 write_note 和 write_item 两个工具")
        log("")
        if ok:
            log("✅ 上面三项全部具备，可以跑完整流程")
        else:
            log("⚠️  有缺项。按上面三条对照，缺哪项补哪项；也可以先用 --skip-notes 跑本地部分。")
        return

    # 工作目录：用户没指定就放在 TMP 下
    work = args.work or os.path.join(TMP, "_suite_" + args.item_key)
    os.makedirs(work, exist_ok=True)
    log("工作目录：%s" % work)
    t_all = time.time()

    preflight(need_notes, need_write)

    meta = load_zotero(args.item_key, work)
    sm = do_extract(meta, work)
    news = do_detect(meta, sm, work)
    rend = do_render(meta, news, work)
    report = build_report(meta, news, rend)

    sec("完整性报告")
    log(report)
    io.open(os.path.join(work, "completeness.md"), "w", encoding="utf-8").write(report)
    io.open(os.path.join(work, "completeness.json"), "w", encoding="utf-8").write(
        json.dumps({"meta": meta,
                    "chapters_source": news["chapters_source"],
                    "sections": [{k: v for k, v in s.items() if k != "text"} for s in news["sections"]],
                    "tables": news["tables"], "figures": news["figures"],
                    "render": rend, "low_conf": news["low_conf"]},
                   ensure_ascii=False, indent=1, default=str))

    if args.skip_notes:
        log("")
        log("（--skip-notes）跳过笔记生成")
        log("总耗时 %.1f 秒" % (time.time()-t_all))
        return

    notes = do_notes(meta, sm, news, work)
    if args.skip_write:
        log("")
        log("（--skip-write）跳过写库")
        log("总耗时 %.1f 秒" % (time.time()-t_all))
        return

    created = do_write(meta, notes, news, rend, report, work)

    sec("完成")
    for label, k, ln in created:
        log("  %s  %-11s %8d 字符" % (label, k, ln))
    log("  共 %d 条笔记" % len(created))
    log("  总耗时 %.1f 秒" % (time.time()-t_all))


if __name__ == "__main__":
    main()
