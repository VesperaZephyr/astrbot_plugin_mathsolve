"""答疑 / 图片卡片 / 讲义的主题解析：配色与模板骨架。"""
from typing import Any, Callable, Dict, Optional

PALETTES: Dict[str, Dict[str, str]] = {
    "blue":  {"main": "4d82bb", "soft": "eef6ff", "deep": "2f5f92", "tex": "blue"},
    "green": {"main": "5f8d43", "soft": "eef6e8", "deep": "3f6a28", "tex": "green"},
    "amber": {"main": "c8942d", "soft": "fff4cf", "deep": "8a6516", "tex": "orange"},
    "mono":  {"main": "4a4a4a", "soft": "f4f4f4", "deep": "1f1f1f", "tex": "black"},
}

TEMPLATES = ("article", "exercisebook", "exam")

_reader: Optional[Callable[[str, Any], Any]] = None


def bind(reader: Callable[[str, Any], Any]) -> None:
    """由插件在初始化时绑定配置读取函数 cfg(key, default)。"""
    global _reader
    _reader = reader


def _cfg(key: str, default: Any = None) -> Any:
    try:
        if _reader is not None:
            value = _reader(key, default)
            return default if value is None else value
    except Exception:
        pass
    return default


def norm_color(value: Any, fallback: str = "blue") -> str:
    name = str(value or "").strip().lower()
    return name if name in PALETTES else fallback


def _follow(raw: Any, base: str) -> str:
    value = str(raw or "follow").strip().lower()
    return base if value in ("", "follow") else norm_color(value, base)


def current() -> Dict[str, str]:
    """解析当前生效的主题（每次调用都读配置，允许热更新）。"""
    template = str(_cfg("qa_template", "article") or "article").strip()
    if template not in TEMPLATES:
        template = "article"
    color = norm_color(_cfg("qa_color", "blue"))
    return {
        "template": template,
        "color": color,
        "card_color": _follow(_cfg("card_color", "follow"), color),
        "lecture_color": _follow(_cfg("lecture_color", "follow"), color),
    }


def palette(name: str) -> Dict[str, str]:
    return PALETTES.get(norm_color(name), PALETTES["blue"])


def tex_color_defs(name: str) -> str:
    BS = chr(92)
    p = palette(name)
    return chr(10).join([
        "%sdefinecolor{qaMain}{HTML}{%s}" % (BS, p["main"]),
        "%sdefinecolor{qaSoft}{HTML}{%s}" % (BS, p["soft"]),
        "%sdefinecolor{qaDeep}{HTML}{%s}" % (BS, p["deep"]),
    ])


def css_vars(name: str) -> str:
    p = palette(name)
    return chr(10).join([
        "                --qa-main: #%s;" % p["main"],
        "                --qa-soft: #%s;" % p["soft"],
        "                --qa-deep: #%s;" % p["deep"],
    ])


def tex_box_defs(template: str, color: str) -> str:
    """题目框/解答框定义。模板差异目前体现在框体结构与留白风格上。"""
    BS = chr(92)
    if template == "exercisebook":
        problem = (
            "%snewtcolorbox{problemBox}[1][]{enhanced, breakable, frame hidden, "
            "colback=qaSoft, borderline west={3pt}{0pt}{qaMain}, "
            "top=5pt, bottom=5pt, left=8pt, right=8pt, before skip=10pt, after skip=8pt, "
            "before upper={%spar%snoindent{%sbfseries%squad 题目}%spar%ssmallskip}, #1}"
            % (BS, BS, BS, BS, BS, BS, BS)
        )
    elif template == "exam":
        problem = (
            "%snewtcolorbox{problemBox}[1][]{enhanced, breakable, colback=white, "
            "colframe=qaMain, boxrule=0.7pt, arc=1mm, left=8pt, right=8pt, "
            "before skip=8pt, after skip=16pt, "
            "title={%sbfseries 题目}, coltitle=qaDeep, #1}"
            % (BS, BS)
        )
    else:
        problem = (
            "%snewtcolorbox{problemBox}[1][]{colback=qaSoft, colframe=qaMain, "
            "title=题目, fonttitle=%sbfseries, coltitle=qaDeep, #1}"
            % (BS, BS)
        )
    common = [
        "%snewtcolorbox{theoremBox}[2][]{colback=teal!3!white, colframe=teal!65!black, "
        "title=定理：#2, fonttitle=%sbfseries, #1}" % (BS, BS),
        "%snewtcolorbox{lemmaBox}[2][]{colback=orange!5!white, colframe=orange!80!black, "
        "title=引理：#2, fonttitle=%sbfseries, #1}" % (BS, BS),
        "%snewtcolorbox{formulaBox}[2][]{colback=violet!3!white, colframe=violet!65!black, "
        "title=公式：#2, fonttitle=%sbfseries, #1}" % (BS, BS),
    ]
    lines = [tex_color_defs(color), problem] + common
    if template != "article":
        lines.append(
            "%snewtcolorbox{solutionBox}[1][]{enhanced, breakable, frame hidden, "
            "colback=white, borderline west={2pt}{0pt}{qaMain}, "
            "top=4pt, bottom=4pt, left=8pt, right=8pt, before skip=6pt, after skip=6pt, "
            "before upper={%spar%snoindent{%sbfseries%squad 解答}%spar%ssmallskip}, #1}"
            % (BS, BS, BS, BS, BS, BS, BS)
        )
    return chr(10).join(lines)
