
"""讲义子系统（P1）：群内题目采集 -> 五维评分 -> 候选区 -> 管理员审核。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter

LECTURE_SCHEMA_VERSION = 1

LECTURE_SUBJECTS = (
    "数学分析", "高等代数", "解析几何", "常微分方程",
    "概率论与数理统计", "数论与组合", "专业进阶",
)

_MATH_SIGNAL_RE = re.compile(
    r"\$|\\\(|\\\[|\\frac|\\sqrt|\\int|\\sum|\\lim"
    r"|[∈∉⊂⊃⊆⊇∀∃≤≥≠≈∞∫∑∏√πλμσξαβθ]"
    r"|证明|求证|求解|计算|已知|求极限|求导|求积分|矩阵|特征值"
    r"|收敛|发散|连续|可导|可积|方程|不等式|多项式|概率|期望|方差|随机变量"
    r"|设\s*[A-Za-z]\s*[为是]"
)
_NOISE_RE = re.compile(r"^(?:哈+|h+|草|[6牛]+|笑死|好+|对+|是的|ok|嗯+|收到|谢谢)[!！。.~\s]*\$", re.I)
_CMD_RE = re.compile(r"^[/!！]?\s*(?:pdf|spdf|pc|pe|memclear|答疑报告生成|讲义)", re.I)

SCORE_PROMPT = """你是数学题库的审题员。判断下面这条群消息是不是一道可独立成题的数学题，并打分。

只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释。字段：
{
  "is_problem": true 或 false,
  "stem_tex": "规范化题干（LaTeX；去掉聊天寒暄；不要包含解答）",
  "subject": "数学分析/高等代数/解析几何/常微分方程/概率论与数理统计/数论与组合/专业进阶 之一",
  "topic_tags": ["考点标签（最多4个）"],
  "exam_type": "CMC/考研/通用 之一",
  "difficulty": "1-5 的整数",
  "score_classic": "0-10，经典程度",
  "score_insight": "0-10，启发程度（是否有关键构造/顿悟点）",
  "score_comprehensive": "0-10，综合程度",
  "score_suitability": "0-10，对 CMC 与考研数学训练的价值",
  "one_line": "一句话概括这道题在考什么（20字内）"
}

以下情形 is_problem=false：闲聊；单纯符号或数字；与题目无关的求助；题目残缺无法作答；只有作业截图但没有题干。
"""


def _now() -> float:
    return time.time()


def _fingerprint(text: str) -> str:
    t = re.sub(r"\s+", "", text or "")
    t = "".join(ch for ch in t if ch.isalnum() or ch in "+-*/=<>()[]{}^_")
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


class LectureStore:
    """SQLite 存储层（problems / reviews / meta）。"""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS problems (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                stem_tex TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                topic_tags TEXT NOT NULL DEFAULT '[]',
                exam_type TEXT NOT NULL DEFAULT '通用',
                difficulty INTEGER NOT NULL DEFAULT 3,
                score_classic REAL NOT NULL DEFAULT 0,
                score_insight REAL NOT NULL DEFAULT 0,
                score_comprehensive REAL NOT NULL DEFAULT 0,
                score_suitability REAL NOT NULL DEFAULT 0,
                quality REAL NOT NULL DEFAULT 0,
                one_line TEXT NOT NULL DEFAULT '',
                source_label TEXT NOT NULL DEFAULT '群内分享',
                fingerprint TEXT NOT NULL DEFAULT '',
                group_id TEXT NOT NULL DEFAULT '',
                sender_id TEXT NOT NULL DEFAULT '',
                model_used TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_problems_status ON problems(status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_problems_fp ON problems(fingerprint)")
            c.execute("""CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                problem_id TEXT, action TEXT, before TEXT, after TEXT, at REAL)""")
            cols = {r[1] for r in c.execute("PRAGMA table_info(problems)")}
            for _col in ("solution_tex", "idea_tex", "knowledge_tex"):
                if _col not in cols:
                    c.execute("ALTER TABLE problems ADD COLUMN " + _col + " TEXT NOT NULL DEFAULT ''")
            c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                      ("schema_version", str(LECTURE_SCHEMA_VERSION)))

    def next_id(self) -> str:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key='seq'").fetchone()
            seq = int(row["value"]) if row else 0
            seq += 1
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('seq', ?)", (str(seq),))
        return "Q-%06d" % seq

    def fingerprint_exists(self, fp: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM problems WHERE fingerprint=? LIMIT 1", (fp,)).fetchone() is not None

    def count_today(self, group_id: str, since: float) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(1) AS n FROM problems WHERE group_id=? AND created_at>=?",
                (group_id, since)).fetchone()
        return int(row["n"]) if row else 0

    def insert(self, data: Dict[str, Any]) -> str:
        pid = self.next_id()
        ts = _now()
        with self._conn() as c:
            c.execute("""INSERT INTO problems(
                id, status, stem_tex, subject, topic_tags, exam_type, difficulty,
                score_classic, score_insight, score_comprehensive, score_suitability,
                quality, one_line, source_label, fingerprint, group_id, sender_id,
                model_used, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                pid, data.get("status", "candidate"), data.get("stem_tex", ""),
                data.get("subject", ""), json.dumps(data.get("topic_tags") or [], ensure_ascii=False),
                data.get("exam_type", "通用"), int(data.get("difficulty", 3) or 3),
                float(data.get("score_classic", 0) or 0), float(data.get("score_insight", 0) or 0),
                float(data.get("score_comprehensive", 0) or 0), float(data.get("score_suitability", 0) or 0),
                float(data.get("quality", 0) or 0), data.get("one_line", ""),
                data.get("source_label", "群内分享"), data.get("fingerprint", ""),
                data.get("group_id", ""), data.get("sender_id", ""),
                data.get("model_used", ""), ts, ts,
            ))
        return pid

    def get(self, pid: str) -> Optional[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM problems WHERE id=?", (pid,)).fetchone()

    def set_status(self, pid: str, status: str, action: str = "") -> bool:
        row = self.get(pid)
        if row is None:
            return False
        with self._conn() as c:
            c.execute("UPDATE problems SET status=?, updated_at=? WHERE id=?", (status, _now(), pid))
            c.execute("INSERT INTO reviews(problem_id, action, before, after, at) VALUES(?,?,?,?,?)",
                      (pid, action or status, row["status"], status, _now()))
        return True

    def update_fields(self, pid: str, **fields) -> bool:
        row = self.get(pid)
        if row is None or not fields:
            return False
        allowed = {"stem_tex", "subject", "topic_tags", "exam_type", "difficulty",
                   "score_classic", "score_insight", "score_comprehensive",
                   "score_suitability", "quality", "one_line", "status",
                   "solution_tex", "idea_tex", "knowledge_tex"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "topic_tags":
                v = json.dumps(v or [], ensure_ascii=False)
            sets.append(k + "=?")
            vals.append(v)
        if not sets:
            return False
        vals.extend([_now(), pid])
        with self._conn() as c:
            c.execute("UPDATE problems SET " + ", ".join(sets) + ", updated_at=? WHERE id=?", vals)
            c.execute("INSERT INTO reviews(problem_id, action, before, after, at) VALUES(?,?,?,?,?)",
                      (pid, "update", json.dumps(dict(row), ensure_ascii=False, default=str),
                       json.dumps(fields, ensure_ascii=False, default=str), _now()))
        return True

    def recompute_quality(self, pid: str) -> float:
        row = self.get(pid)
        if row is None:
            return 0.0
        q = round(float(row["score_classic"]) * 0.3 + float(row["score_insight"]) * 0.3
                 + float(row["score_comprehensive"]) * 0.2 + float(row["score_suitability"]) * 0.2, 2)
        self.update_fields(pid, quality=q)
        return q

    def list_candidates(self, limit: int = 10) -> List[sqlite3.Row]:
        with self._conn() as c:
            return list(c.execute(
                "SELECT * FROM problems WHERE status='candidate' ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall())

    def list_approved(self, subject: Optional[str] = None, dmin: int = 1, dmax: int = 5,
                      limit: int = 50) -> List[sqlite3.Row]:
        sql = "SELECT * FROM problems WHERE status='approved' AND difficulty>=? AND difficulty<=?"
        params: List[Any] = [dmin, dmax]
        if subject:
            sql += " AND subject=?"
            params.append(subject)
        sql += " ORDER BY subject, difficulty, id LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            return list(c.execute(sql, params).fetchall())

    def stats(self) -> Dict[str, int]:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(1) AS n FROM problems GROUP BY status").fetchall()
        return {r["status"]: int(r["n"]) for r in rows}


class LectureMixin:
    """挂在 MarkdownConverterPlugin 上；依赖 _cfg / context / DATA_DIR / pdf 辅助方法。"""

    def _lec_cfg(self, key: str, default: Any = None) -> Any:
        try:
            return self._cfg(key, default)
        except Exception:
            return default

    def _lec_store(self) -> LectureStore:
        store = getattr(self, "_lecture_store", None)
        if store is None:
            path = os.path.join(self.DATA_DIR, "lecture", "lecture.db")
            store = LectureStore(path)
            self._lecture_store = store
        return store

    def _lec_group_id(self, event: AstrMessageEvent) -> str:
        umo = getattr(event, "unified_msg_origin", "") or ""
        parts = str(umo).split(":")
        return parts[-1] if len(parts) >= 3 else ""

    def _lec_is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            fn = getattr(event, "is_admin", None)
            if callable(fn) and fn():
                return True
        except Exception:
            pass
        try:
            admins = self.context.get_config().get("admins_id") or []
            return str(event.get_sender_id()) in {str(x) for x in admins}
        except Exception:
            return False

    def _lec_prefilter(self, raw: str, has_image: bool) -> bool:
        text = (raw or "").strip()
        if text and _NOISE_RE.match(text):
            return False
        if text and _CMD_RE.match(text):
            rest = _CMD_RE.sub("", text, count=1).strip()
            if len(rest) < 20:
                return False
            text = rest
        if not text and not has_image:
            return False
        if text and len(text) < 20 and not has_image:
            return False
        if text and not _MATH_SIGNAL_RE.search(text) and not has_image:
            return False
        return True

    def _lec_schedule_collect(self, event: AstrMessageEvent, raw: str, images: Optional[List[str]]) -> None:
        try:
            if not bool(self._lec_cfg("lecture_enable", False)):
                return
            group_id = self._lec_group_id(event)
            whitelist = self._lec_cfg("lecture_group_whitelist", ["529535624"]) or []
            if not group_id or group_id not in {str(x) for x in whitelist}:
                return
            imgs = list(images or [])
            if not self._lec_prefilter(raw, bool(imgs)):
                return
            asyncio.create_task(self._lec_collect_safe(event, raw, imgs, group_id))
        except Exception as exc:
            logger.warning(f"[讲义] 采集调度失败: {type(exc).__name__}: {exc}")

    async def _lec_collect_safe(self, event, raw, imgs, group_id) -> None:
        try:
            await self._lec_collect(event, raw, imgs, group_id)
        except Exception as exc:
            logger.warning(f"[讲义] 采集失败: {type(exc).__name__}: {exc}")

    async def _lec_collect(self, event, raw: str, imgs: List[str], group_id: str,
                           status: str = "candidate", force: bool = False):
        store = self._lec_store()
        cap = int(self._lec_cfg("lecture_daily_score_cap", 30) or 30)
        day_start = _now() - (_now() % 86400)
        if store.count_today(group_id, day_start) >= cap:
            logger.info(f"[讲义] 群 {group_id} 今日已达采集上限 {cap}，跳过")
            return

        prompt = SCORE_PROMPT
        if raw:
            prompt += "\n\n群消息原文：\n" + raw
        else:
            prompt += "\n\n这条消息只包含图片，请直接从图片中识别题目。"

        text, pid_used = await self._lec_ask_model(event, prompt, imgs)
        data = self._lec_parse_json(text)
        if not data or not data.get("is_problem"):
            return

        stem = str(data.get("stem_tex") or "").strip()
        if len(stem) < 10:
            return
        fp = _fingerprint(stem)
        if store.fingerprint_exists(fp):
            return

        def num(key: str, lo: float = 0, hi: float = 10) -> float:
            try:
                return max(lo, min(hi, float(data.get(key) or 0)))
            except Exception:
                return lo

        classic = num("score_classic")
        insight = num("score_insight")
        comp = num("score_comprehensive")
        suit = num("score_suitability")
        quality = round(classic * 0.3 + insight * 0.3 + comp * 0.2 + suit * 0.2, 2)
        threshold = float(self._lec_cfg("lecture_score_threshold", 6.5) or 6.5)
        if quality < threshold and not force:
            return

        subject = str(data.get("subject") or "").strip()
        if subject not in LECTURE_SUBJECTS:
            subject = "专业进阶"
        try:
            difficulty = int(data.get("difficulty") or 3)
        except Exception:
            difficulty = 3
        difficulty = max(1, min(5, difficulty))

        new_id = store.insert({
            "status": status,
            "stem_tex": stem,
            "subject": subject,
            "topic_tags": data.get("topic_tags") or [],
            "exam_type": str(data.get("exam_type") or "通用"),
            "difficulty": difficulty,
            "score_classic": classic,
            "score_insight": insight,
            "score_comprehensive": comp,
            "score_suitability": suit,
            "quality": quality,
            "one_line": str(data.get("one_line") or ""),
            "source_label": "群内分享",
            "fingerprint": fp,
            "group_id": group_id,
            "sender_id": str(event.get_sender_id() or ""),
            "model_used": pid_used or "",
        })
        logger.info(f"[讲义] 新题目 {new_id} status={status} 质量分={quality} 分类={subject} 难度={difficulty}")
        return new_id

    async def _lec_ask_model(self, event, prompt: str, images: List[str]) -> Tuple[str, str]:
        primary = str(self._lec_cfg("lecture_solution_provider", "") or "")
        current = ""
        try:
            current = await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)
        except Exception:
            current = ""
        chain = self._pdf_build_provider_chain(primary, current)
        text, pid, _attempted = await self._pdf_llm_generate_with_fallback(
            chain, prompt, image_urls=(images or None), purpose="lecture_score")
        return text or "", pid or ""

    def _lec_parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        FENCE = chr(96) * 3
        s = text.strip()
        if s.startswith(FENCE):
            s = s.split("\n", 1)[1] if "\n" in s else s
            s = s.rsplit(FENCE, 1)[0]
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return None
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            return None

    def _lec_deny(self, event) -> bool:
        return not self._lec_is_admin(event)

    async def _lec_cmd_candidates(self, event: AstrMessageEvent):
        """列出待审候选（仅管理员）"""
        if self._lec_deny(event):
            yield event.plain_result("仅管理员可用。")
            return
        parts = (event.message_str or "").split()
        try:
            limit = int(parts[-1])
        except Exception:
            limit = 10
        limit = max(1, min(30, limit))
        rows = self._lec_store().list_candidates(limit)
        if not rows:
            yield event.plain_result("暂无待审候选。")
            return
        lines = [f"待审候选（{len(rows)} 条）"]
        for r in rows:
            lines.append("%s | %s | 难度%d | 质量%.1f | %s" % (
                r["id"], r["subject"], int(r["difficulty"]), float(r["quality"]),
                (r["one_line"] or r["stem_tex"] or "")[:36]))
        yield event.plain_result("\n".join(lines))

    async def _lec_cmd_approve(self, event: AstrMessageEvent):
        """确认候选入库（仅管理员）"""
        if self._lec_deny(event):
            yield event.plain_result("仅管理员可用。")
            return
        parts = (event.message_str or "").split()
        pid = parts[-1].strip() if parts else ""
        if not pid:
            yield event.plain_result("用法：/讲义确认 Q-000123")
            return
        ok = self._lec_store().set_status(pid, "approved", "approve")
        yield event.plain_result(("已确认入库：" + pid) if ok else ("未找到题目：" + pid))

    async def _lec_cmd_delete(self, event: AstrMessageEvent):
        """删除/拒绝题目（仅管理员）"""
        if self._lec_deny(event):
            yield event.plain_result("仅管理员可用。")
            return
        parts = (event.message_str or "").split()
        pid = parts[-1].strip() if parts else ""
        if not pid:
            yield event.plain_result("用法：/讲义删除 Q-000123")
            return
        ok = self._lec_store().set_status(pid, "rejected", "reject")
        yield event.plain_result(("已移出：" + pid) if ok else ("未找到题目：" + pid))

    async def _lec_cmd_stats(self, event: AstrMessageEvent):
        """题库统计（仅管理员）"""
        if self._lec_deny(event):
            yield event.plain_result("仅管理员可用。")
            return
        st = self._lec_store().stats()
        if not st:
            yield event.plain_result("题库为空。")
            return
        yield event.plain_result("题库统计：" + "，".join(f"{k}={v}" for k, v in sorted(st.items())))

    def _lec_reply_text(self, event) -> str:
        """从引用消息里取出文本（用于 /讲义新增 回复收录）。"""
        try:
            msg = getattr(event, "message_obj", None)
            chain = getattr(msg, "message", None) or []
            for comp in chain:
                if type(comp).__name__ == "Reply":
                    texts = []
                    for c in (getattr(comp, "chain", None) or []):
                        t = getattr(c, "text", None)
                        if t:
                            texts.append(str(t))
                    if texts:
                        return " ".join(texts).strip()
        except Exception:
            pass
        return ""

    async def _lec_cmd_add(self, event: AstrMessageEvent):
        """管理员手动收录：回复一条含题目的消息后发送本指令，直接进正式库"""
        if self._lec_deny(event):
            yield event.plain_result("仅管理员可用。")
            return
        parts = (event.message_str or "").split(maxsplit=1)
        raw = parts[1].strip() if len(parts) > 1 else ""
        if len(raw) < 10:
            raw = self._lec_reply_text(event) or raw
        try:
            imgs = await self._pdf_get_event_image_inputs(event)
        except Exception:
            imgs = []
        if len(raw) < 10 and not imgs:
            yield event.plain_result("用法：回复一条含题目的消息后发送 /讲义新增；或 /讲义新增 题干……")
            return
        yield event.plain_result("已收到，正在评分并写入正式库……")
        try:
            new_id = await self._lec_collect(event, raw, imgs, self._lec_group_id(event),
                                             status="approved", force=True)
        except Exception as exc:
            yield event.plain_result("收录失败：" + type(exc).__name__)
            return
        yield event.plain_result(("已收录：" + new_id) if new_id else "模型判定这不是一道题，未收录。")

    async def _lec_cmd_set_subject(self, event: AstrMessageEvent):
        """修改题目主分类（仅管理员）"""
        if self._lec_deny(event):
            yield event.plain_result("仅管理员可用。")
            return
        parts = (event.message_str or "").split()
        if len(parts) < 2:
            yield event.plain_result("用法：/讲义改类 Q-000123 数学分析" + chr(10) + "可选：" + " / ".join(LECTURE_SUBJECTS))
            return
        pid, subject = parts[-2].strip(), parts[-1].strip()
        if subject not in LECTURE_SUBJECTS:
            yield event.plain_result("主分类无效。可选：" + " / ".join(LECTURE_SUBJECTS))
            return
        ok = self._lec_store().update_fields(pid, subject=subject)
        yield event.plain_result(("已改为 " + subject + "：" + pid) if ok else ("未找到题目：" + pid))

    async def _lec_cmd_set_score(self, event: AstrMessageEvent):
        """修改题目评分（仅管理员）：/讲义改分 Q-000123 启发 9"""
        if self._lec_deny(event):
            yield event.plain_result("仅管理员可用。")
            return
        parts = (event.message_str or "").split()
        if len(parts) < 3:
            yield event.plain_result("用法：/讲义改分 Q-000123 启发 9" + chr(10) + "维度：经典 / 启发 / 综合 / 适合 / 难度")
            return
        pid, dim, val = parts[-3].strip(), parts[-2].strip(), parts[-1].strip()
        dim_map = {"经典": "score_classic", "启发": "score_insight", "综合": "score_comprehensive",
                   "适合": "score_suitability", "难度": "difficulty"}
        field = dim_map.get(dim)
        if field is None:
            yield event.plain_result("维度无效。可选：经典 / 启发 / 综合 / 适合 / 难度")
            return
        try:
            num = float(val)
        except Exception:
            yield event.plain_result("分值必须是数字。")
            return
        if field == "difficulty":
            num = max(1, min(5, int(num)))
        else:
            num = max(0.0, min(10.0, num))
        store = self._lec_store()
        if not store.update_fields(pid, **{field: num}):
            yield event.plain_result("未找到题目：" + pid)
            return
        q = store.recompute_quality(pid)
        yield event.plain_result("%s 的 %s 已改为 %s，质量分重算为 %.2f" % (pid, dim, num, q))



# =============================================================================
# P3：讲义导出（LaTeX 生成 + 容器内 xelatex 编译 + 发群）
# =============================================================================

LECTURE_TPL_FILES = ("exercisebook.cls", "lecture.sty", "mycommand.sty", "mathdots.sty", "coverimage.jpg")

_DANGEROUS_TEX_RE = re.compile(
    r"\\(?:write18|input|include|includeonly|openout|read|write|catcode|csname|"
    r"newenvironment|renewcommand|def|let|immediate|special|usepackage|"
    r"documentclass|begin\s*{\s*document\s*}|end\s*{\s*document\s*})\b"
)

SOLUTION_PROMPT = r"""你是数学讲义的解答撰写者。为下面这道题写解答。

只输出一个 JSON 对象，不要 markdown 代码块，不要解释。字段：
{
  "idea": "想法：怎么想到的（关键构造/切入点，1-3 句，LaTeX）",
  "knowledge": "知识点：用到的定理/方法（2-5 条，LaTeX，用分号隔开）",
  "solution": "完整解答（LaTeX，自然、不跳步、但不啰嗦；重要推导给出理由）"
}

要求：与 /pdf 的解答标准一致——不跳步，但也不要复述教科书；公式用 $...$ 或 \[...\]。

题目：
"""



# Unicode 数学符号 -> LaTeX（用 \ensuremath 包裹，数学环境内外都安全）
_UNICODE_MATH_MAP = {
    "≤": r"\le",
    "≥": r"\ge",
    "≠": r"\ne",
    "≈": r"\approx",
    "≡": r"\equiv",
    "∼": r"\sim",
    "⇒": r"\Rightarrow",
    "⇐": r"\Leftarrow",
    "⇔": r"\Leftrightarrow",
    "→": r"\to",
    "←": r"\leftarrow",
    "↔": r"\leftrightarrow",
    "∈": r"\in",
    "∉": r"\notin",
    "⊂": r"\subset",
    "⊆": r"\subseteq",
    "⊃": r"\supset",
    "⊇": r"\supseteq",
    "∪": r"\cup",
    "∩": r"\cap",
    "∅": r"\emptyset",
    "∞": r"\infty",
    "±": r"\pm",
    "∓": r"\mp",
    "×": r"\times",
    "÷": r"\div",
    "·": r"\cdot",
    "∑": r"\sum",
    "∏": r"\prod",
    "∫": r"\int",
    "√": r"\surd",
    "∀": r"\forall",
    "∃": r"\exists",
    "∂": r"\partial",
    "∇": r"\nabla",
    "★": r"\bigstar",
    "☆": r"\star",
    "∘": r"\circ",
    "°": r"\circ",
    "…": r"\ldots",
}


def _map_unicode_symbols(text: str) -> str:
    if not text:
        return text
    out = []
    for ch in text:
        rep = _UNICODE_MATH_MAP.get(ch)
        if rep is None:
            out.append(ch)
        else:
            out.append("\\ensuremath{" + rep + "}")
    return "".join(out)

def sanitize_latex(src: str) -> str:
    """去掉危险控制序列与文档结构，避免 AI 产出逃逸出模板。"""
    s = (src or "").replace("\r\n", "\n").replace("\r", "\n")
    s = _map_unicode_symbols(s)
    s = _DANGEROUS_TEX_RE.sub("", s)
    s = re.sub(r"(?<!\\)%", r"\%", s)
    return s.strip()


def _difficulty_band(d: int) -> str:
    if d <= 2:
        return "基础篇"
    if d <= 4:
        return "提高篇"
    return "冲刺篇"


class LectureExportMixin:
    """讲义导出。依赖 LectureMixin 的 helpers 与宿主类的编译信号量。"""

    def _lec_template_dir(self) -> str:
        """优先使用插件内置模板（随仓库分发），否则回退到数据目录（兼容旧部署）。"""
        bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lecture_template")
        if all(os.path.exists(os.path.join(bundled, f)) for f in LECTURE_TPL_FILES):
            return bundled
        return os.path.join(self.DATA_DIR, "lecture", "template")

    def _lec_render_idea(self, row) -> str:
        idea = sanitize_latex(row["idea_tex"])
        know = sanitize_latex(row["knowledge_tex"])
        parts = []
        if idea:
            parts.append(r"\textbf{想法：}" + idea)
        if know:
            parts.append(r"\textbf{知识点：}" + know)
        return "\n\n".join(parts)

    def _lec_render_solution(self, row) -> str:
        sol = sanitize_latex(row["solution_tex"])
        # solution 环境自带「解」字，去掉正文里重复的开头标记
        for _m in ("解：", "解:", "解 ", "解答：", "证明：", "证："):
            if sol.startswith(_m):
                sol = sol[len(_m):].lstrip()
                break
        return sol


    def _lec_build_tex(self, rows: List[Any], title: str = "CMC/考研数学习题讲义") -> str:
        date_str = time.strftime("%Y 年 %m 月 %d 日")
        head = [
            r"\documentclass{exercisebook}",
            r"\usepackage{lecture}",
            r"\title{" + title + "}",
            r"\author{全体群友}",
            r"\date{" + date_str + "}",
            r"\extrainfo{\textit{取法乎上，仅得其中}}",
            r"\coverimage{coverimage.jpg}",
            r"\usepackage{mathdots}",
            r"\numberwithin{equation}{section}",
            r"\begin{document}",
            r"\maketitle",
            r"\tableofcontents",
            "",
        ]
        body = []
        by_subject: Dict[str, List[Any]] = {}
        for r in rows:
            subj = str(r["subject"] or "专业进阶")
            by_subject.setdefault(subj, []).append(r)
        ordered = [s for s in LECTURE_SUBJECTS if s in by_subject]
        ordered += [s for s in by_subject if s not in LECTURE_SUBJECTS]
        for subj in ordered:
            body.append(r"\chapter{" + subj + "}")
            bands: Dict[str, List[Any]] = {}
            for r in sorted(by_subject[subj], key=lambda x: int(x["difficulty"] or 3)):
                bands.setdefault(_difficulty_band(int(r["difficulty"] or 3)), []).append(r)
            for band in ("基础篇", "提高篇", "冲刺篇"):
                if band not in bands:
                    continue
                body.append(r"\section{" + band + "}")
                for r in bands[band]:
                    tags = []
                    try:
                        tags = json.loads(r["topic_tags"] or "[]")
                    except Exception:
                        tags = []
                    tag_text = "、".join(str(t) for t in tags[:3]) or "综合"
                    one = sanitize_latex(r["one_line"] or "")
                    body.append(r"\begin{problem}[" + (one or tag_text) + "]")
                    body.append(r"\qmeta{" + sanitize_latex(r["source_label"] or "群内分享") + "}{" +
                                str(int(r["difficulty"] or 3)) + "}{" + tag_text + "}")
                    body.append(r"\qid{" + str(r["id"]) + "}")
                    body.append(sanitize_latex(r["stem_tex"]))
                    body.append(r"\end{problem}")
                    body.append("")
                    idea = self._lec_render_idea(r)
                    if idea:
                        body.append(r"\begin{idea}")
                        body.append(idea)
                        body.append(r"\end{idea}")
                        body.append("")
                    sol = self._lec_render_solution(r)
                    if sol:
                        body.append(r"\begin{solution}")
                        body.append(sol)
                        body.append(r"\end{solution}")
                        body.append("")
                        body.append("")
        tail = [r"\end{document}", ""]
        return "\n".join(head + body + tail)

    def _lec_preflight(self, tex: str) -> Optional[str]:
        """发送前自检：模板/产物占位符残留、模板完整性。返回错误说明或 None。"""
        for marker in ("§", "@@"):
            if marker in tex:
                return "生成的 TeX 残留占位符 " + marker
        tpl = self._lec_template_dir()
        for f in LECTURE_TPL_FILES:
            path = os.path.join(tpl, f)
            if not os.path.exists(path):
                return "模板文件缺失：" + f
            if f.endswith((".sty", ".cls")):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
                # 注意：LaTeX 源码合法使用 \@@ 宏，模板里只能查 § 占位符
                if "§" in body:
                    return "模板 " + f + " 残留占位符 §"
        if r"\begin{document}" not in tex or r"\end{document}" not in tex:
            return "TeX 结构不完整（缺少 document 环境）"
        return None

    def _lec_postcheck(self, pdf_bytes: bytes) -> Optional[str]:
        """产物自检：前两页不得出现 LaTeX 源码痕迹。"""
        try:
            import io
            import pypdfium2 as pdfium
        except Exception:
            return None
        try:
            doc = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
            for i in range(min(len(doc), 40)):
                text = doc[i].get_textpage().get_text_range()
                for marker in ("newtcolorbox", "newenvironment", "newcommand",
                               "\\documentclass", "§", "@@"):
                    if marker in text:
                        return "PDF 第 %d 页出现源码痕迹：%s" % (i + 1, marker)
        except Exception as exc:
            logger.warning(f"[讲义] 产物自检异常（已忽略）: {exc}")
        return None

    async def _lec_compile(self, tex: str) -> Optional[bytes]:
        self._lec_last_error = ""
        bad = self._lec_preflight(tex)
        if bad:
            self._lec_last_error = bad
            logger.warning(f"[讲义] 预检未通过: {bad}")
            return None
        tpl = self._lec_template_dir()

        def _run() -> Optional[bytes]:
            import shutil
            import subprocess
            import tempfile
            with tempfile.TemporaryDirectory(prefix="astrbot_lecture_") as td:
                try:
                    for f in LECTURE_TPL_FILES:
                        shutil.copy2(os.path.join(tpl, f), os.path.join(td, f))
                    with open(os.path.join(td, "lecture.tex"), "w", encoding="utf-8") as fh:
                        fh.write(tex)
                    for _ in range(2):
                        subprocess.run(
                            ["xelatex", "-interaction=nonstopmode", "-no-shell-escape", "lecture.tex"],
                            cwd=td, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240)
                    pdf_path = os.path.join(td, "lecture.pdf")
                    if not os.path.exists(pdf_path):
                        log_path = os.path.join(td, "lecture.log")
                        tail = ""
                        if os.path.exists(log_path):
                            with open(log_path, encoding="utf-8", errors="replace") as fh:
                                tail = fh.read()[-1200:]
                        logger.warning("[讲义] xelatex 编译失败: " + tail.replace("\n", " | "))
                        return None
                    log_path = os.path.join(td, "lecture.log")
                    if os.path.exists(log_path):
                        with open(log_path, encoding="utf-8", errors="replace") as fh:
                            tail = fh.read()[-1200:]
                        if "Missing character" in tail:
                            n_missing = tail.count("Missing character")
                            logger.warning(f"[讲义] 编译告警：{n_missing} 处缺字形（见上方日志）")
                    with open(pdf_path, "rb") as fh:
                        return fh.read()
                except Exception as exc:
                    logger.warning(f"[讲义] 编译异常: {type(exc).__name__}: {exc}")
                    return None

        async with self._tex_compile_sema:
            pdf = await asyncio.to_thread(_run)
        if not pdf:
            self._lec_last_error = self._lec_last_error or "xelatex 编译失败（详见日志）"
            return None
        bad = self._lec_postcheck(pdf)
        if bad:
            self._lec_last_error = bad
            logger.warning(f"[讲义] 产物自检未通过: {bad}")
            return None
        return pdf

    async def _lec_write_solution(self, row, event) -> bool:
        prompt = SOLUTION_PROMPT + (row["stem_tex"] or "")
        try:
            text, _pid = await self._lec_ask_model(event, prompt, [])
        except Exception as exc:
            logger.warning(f"[讲义] 解答生成失败 {row['id']}: {exc}")
            return False
        data = self._lec_parse_json(text)
        if not data:
            return False
        sol = str(data.get("solution") or "").strip()
        if len(sol) < 20:
            return False
        self._lec_store().update_fields(
            row["id"],
            solution_tex=sol,
            idea_tex=str(data.get("idea") or ""),
            knowledge_tex=str(data.get("knowledge") or ""),
        )
        return True

    async def _lec_cmd_export(self, event: AstrMessageEvent):
        """按分类/难度导出讲义 PDF（仅管理员）"""
        if self._lec_deny(event):
            yield event.plain_result("仅管理员可用。")
            return
        parts = (event.message_str or "").split()[1:]
        subject = None
        dmin, dmax = 1, 5
        limit = int(self._lec_cfg("lecture_export_limit", 50) or 50)
        for p in parts:
            if p in LECTURE_SUBJECTS:
                subject = p
                continue
            m = re.fullmatch(r"([1-5])(?:-([1-5]))?", p)
            if m:
                dmin = int(m.group(1))
                dmax = int(m.group(2) or m.group(1))
                continue
            if p.isdigit():
                limit = max(1, min(200, int(p)))
        store = self._lec_store()
        rows = store.list_approved(subject=subject, dmin=dmin, dmax=dmax, limit=limit)
        if not rows:
            yield event.plain_result("题库里还没有符合条件的正式题目。")
            return
        missing = [r for r in rows if not (r["solution_tex"] or "").strip()]
        if missing:
            yield event.plain_result(f"正在为 {len(missing)} 道题撰写解答，请稍候……")
            ok = 0
            for r in missing:
                if await self._lec_write_solution(r, event):
                    ok += 1
            if ok == 0:
                yield event.plain_result("解答生成全部失败，已中止导出。")
                return
        rows = store.list_approved(subject=subject, dmin=dmin, dmax=dmax, limit=limit)
        tex = self._lec_build_tex(rows)
        yield event.plain_result(f"正在编译讲义（{len(rows)} 题），请稍候……")
        pdf = await self._lec_compile(tex)
        if not pdf:
            yield event.plain_result("编译失败，未发送任何文件：" + (getattr(self, "_lec_last_error", "") or "详见日志"))
            return
        fname = "数学习题讲义_%s.pdf" % time.strftime("%Y%m%d_%H%M")
        out_path = os.path.join(self.PDF_CACHE_DIR, "lecture_" + time.strftime("%Y%m%d_%H%M%S") + ".pdf")
        try:
            os.makedirs(self.PDF_CACHE_DIR, exist_ok=True)
            with open(out_path, "wb") as fh:
                fh.write(pdf)
        except Exception as exc:
            yield event.plain_result(f"写文件失败：{type(exc).__name__}")
            return
        try:
            from astrbot.core.message.components import File as FileComponent
            await event.send(MessageChain([FileComponent(file=out_path, name=fname)]))
        except Exception as exc:
            logger.warning(f"[讲义] 发送失败: {exc}")
            yield event.plain_result(f"讲义已生成但发送失败：{type(exc).__name__}（路径 {out_path}）")
            return
        yield event.plain_result(f"讲义已发送（{len(rows)} 题）。")


# =============================================================================
# P4：/pdf 检索联动（只读题库，另发一条消息）
# =============================================================================

LINK_PROMPT = """你在为数学答疑机器人做「讲义关联」。
用户刚问了一道题，请从候选讲义题目中挑出最相关的 1-3 道，说明关联点。

候选格式：id | 主分类 | 难度 | 一句话 | 考点

只输出 JSON，不要代码块，不要解释：
{"links": [{"id": "Q-000001", "why": "同一手法：Abel 变换"}], "note": "（可选）用到的经典结论或知识点"}

规则：
- 最多 3 条，按相关度排序；
- why 不超过 20 字，说明「同一手法 / 同类结论 / 前置知识点 / 经典题」中的一种；
- 没有真正相关的内容就返回 {"links": []}，不要硬凑。

候选列表：
"""


def _bigrams(text: str) -> set:
    t = re.sub(r"\s+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


class LectureLinkMixin:
    """检索联动。依赖 LectureMixin 的 helpers。"""

    def _lec_schedule_pdf_link(self, event, question: str, images) -> None:
        try:
            if not bool(self._lec_cfg("lecture_retrieval_enable", True)):
                return
            try:
                if not self._lec_store().stats().get("approved"):
                    return
            except Exception:
                return
            asyncio.create_task(self._lec_pdf_link_safe(event, question or "", list(images or [])))
        except Exception as exc:
            logger.warning(f"[讲义] 联动调度失败: {type(exc).__name__}: {exc}")

    async def _lec_pdf_link_safe(self, event, question: str, images) -> None:
        try:
            await self._lec_pdf_link(event, question, images)
        except Exception as exc:
            logger.warning(f"[讲义] 联动失败: {type(exc).__name__}: {exc}")

    def _lec_coarse_pick(self, question: str, limit: int = 40) -> List[Any]:
        store = self._lec_store()
        rows = store.list_approved(limit=200)
        if not rows:
            return []
        qg = _bigrams(question)
        scored = []
        for r in rows:
            try:
                tags = " ".join(json.loads(r["topic_tags"] or "[]"))
            except Exception:
                tags = ""
            blob = " ".join([str(r["stem_tex"] or "")[:400], str(r["one_line"] or ""), tags, str(r["subject"] or "")])
            rg = _bigrams(blob)
            overlap = len(qg & rg)
            score = overlap + float(r["quality"] or 0) * 0.05
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _s, r in scored[:limit]]

    async def _lec_pdf_link(self, event, question: str, images) -> None:
        rows = self._lec_coarse_pick(question or "", 40)
        if not rows:
            return
        lines = [LINK_PROMPT]
        for r in rows:
            try:
                tags = "、".join(json.loads(r["topic_tags"] or "[]")[:3])
            except Exception:
                tags = ""
            lines.append("%s | %s | %s | %s | %s" % (
                r["id"], r["subject"], int(r["difficulty"] or 3),
                (r["one_line"] or str(r["stem_tex"] or "")[:60]).replace("\n", " "),
                tags))
        prompt = "\n".join(lines)
        if question:
            prompt += "\n\n用户刚问的题目：\n" + question[:1500]
        text, _pid = await self._lec_ask_model(event, prompt, images[:2] if images else [])
        data = self._lec_parse_json(text)
        if not data:
            return
        links = data.get("links") or []
        if not isinstance(links, list):
            return
        store = self._lec_store()
        picked = []
        for item in links[:3]:
            if not isinstance(item, dict):
                continue
            row = store.get(str(item.get("id") or ""))
            if row is None:
                continue
            picked.append((row, str(item.get("why") or "")[:40]))
        if not picked:
            return
        note = str(data.get("note") or "").strip()
        stars = lambda d: "★" * int(d) + "☆" * (5 - int(d))
        md = ["### 讲义关联", ""]
        for row, why in picked:
            md.append("- **%s** · %s · %s — %s" % (
                row["id"], row["subject"], stars(int(row["difficulty"] or 3)), why or "相关题目"))
        if note:
            md.append("")
            md.append("> " + note)
        md_text = "\n".join(md)
        out_path = os.path.join(self.PDF_CACHE_DIR, "lecture_link_" + str(int(time.time())) + ".png")
        sent = False
        try:
            os.makedirs(self.PDF_CACHE_DIR, exist_ok=True)
            images_out = await self._markdown_to_image_playwright(md_text, out_path)
            if images_out:
                from astrbot.core.message.components import Image as ImageComponent
                await event.send(MessageChain([ImageComponent(file=str(p)) for p in images_out]))
                sent = True
        except Exception as exc:
            logger.warning(f"[讲义] 联动卡片渲染失败，降级纯文本: {type(exc).__name__}: {exc}")
        if not sent:
            from astrbot.core.message.components import Plain as PlainComponent
            await event.send(MessageChain([PlainComponent(md_text)]))
