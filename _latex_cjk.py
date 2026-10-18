"""Make complete XeLaTeX documents with CJK text render reliably on Linux."""
import re


def ensure_chinese_support(source: str) -> str:
    # Comments must neither trigger CJK support nor pretend to load a package.
    def mask_comment(match):
        value = match.group()
        prefix = value[:value.index('%')]
        return prefix + ' ' * (len(value) - len(prefix))
    active = re.sub(r'(?<!\\)(?:\\\\)*%[^\n]*', mask_comment, source)
    if not re.search(r'[\u3400-\u9fff\U00020000-\U000323af]', active):
        return source
    document = re.search(r'\\documentclass\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}', active)
    if document is None:
        return source
    class_name = document.group(1).strip()
    packages = set()
    for match in re.finditer(r'\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}', active):
        packages.update(part.strip() for part in match.group(1).split(','))
    if class_name.startswith('ctex') or 'ctex' in packages:
        return source
    # Existing xeCJK keeps its explicit font settings. Supply a real CJK font
    # only when none was selected; Latin Modern alone has no Chinese glyphs.
    if 'xeCJK' in packages:
        if re.search(r'\\setCJKmainfont\s*(?:\[[^\]]*\]\s*)?\{', active):
            return source
        marker = re.search(r'\\begin\s*\{document\}', active)
        if marker is None:
            return source
        addition = '\\setCJKmainfont[BoldFont=FandolSong-Bold.otf,ItalicFont=FandolKai-Regular.otf]{FandolSong-Regular.otf}\n'
        if not re.search(r'\\setCJKsansfont\s*(?:\[[^\]]*\]\s*)?\{', active):
            addition += '\\setCJKsansfont[BoldFont=FandolHei-Bold.otf]{FandolHei-Regular.otf}\n'
        if not re.search(r'\\setCJKmonofont\s*(?:\[[^\]]*\]\s*)?\{', active):
            addition += '\\setCJKmonofont{FandolFang-Regular.otf}\n'
        return source[:marker.start()] + addition + source[marker.start():]
    # Inject directly after the real document class, before user packages such
    # as hyperref. Keep the class and its layout; Fandol ships with TeX Live.
    position = document.end()
    addition = '\n% Automatic Chinese support for XeLaTeX\n\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}\n'
    return source[:position] + addition + source[position:]
