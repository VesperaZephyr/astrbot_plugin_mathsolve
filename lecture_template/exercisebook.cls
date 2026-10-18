%% ===================================================================
%% exercisebook.cls
%% 自定义习题集 LaTeX 模板
%% ===================================================================
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{exercisebook}[2023/11/01 Custom Exercise Book Class based on ctexbook]

% 传递所有未知选项给底层的 ctexbook 文档类
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{ctexbook}}
\ProcessOptions\relax

% 加载底层文档类 (默认 a4 纸张，小四号字体，双面排版)
\LoadClass[a4paper, 11pt, twoside]{ctexbook}
% ===================================================================
% 1. 加载宏包
% ===================================================================
% 数学与符号
\RequirePackage{amsmath, amsthm, amssymb, mathrsfs, mathtools}
% 图形与表格
\RequirePackage{graphicx, float, subfigure, caption, booktabs, array}
% 列表与排版
\RequirePackage{enumerate, tasks, setspace, manfnt}
\RequirePackage{paralist} % 更灵活的列表环境
% 页面与页眉页脚
\RequirePackage{geometry}
\RequirePackage{fancyhdr}
% 盒子环境 (用于定理框)
\RequirePackage[many]{tcolorbox}
% TikZ 绘图
\RequirePackage{tikz}
\RequirePackage{tikz-cd}
% 交叉引用 (通常放在最后加载)
\RequirePackage{hyperref}
% 其他
\RequirePackage{xcolor}
\RequirePackage{eso-pic}
\RequirePackage{datetime}
\RequirePackage{ifthen}
% 自定义命令
\RequirePackage{mycommand}

\RequirePackage{bm} % 避免宏包冲突
% ===================================================================
% 2. 基本排版与间距设置
% ===================================================================
% 页面边距设置 (可根据需要自行微调)
\geometry{
    top=2.5cm, bottom=2.5cm,
    left=2.0cm, right=2.0cm,
    headheight=15pt
}

% 设置 1.5 倍行距 (setspace 宏包提供的标准 1.5 倍)
\onehalfspacing 
% (注：如果想要 Word 里那种极其宽的 1.5 倍，可改用 \linespread{1.5} \selectfont)

% ===================================================================
% 3. TikZ 配置
% ===================================================================
\usetikzlibrary{calc, positioning, arrows.meta, intersections, decorations.markings}

% ===================================================================
% 4. 定理环境配置 (共用计数器)
% ===================================================================
% 中文排版习惯：定理内容使用直立体，避免中文字体缺失斜体警告
\theoremstyle{definition} 

% 定义基础计数器（按章编号）
\newtheorem{theorem}{定理}[section]

% 其他环境共用 theorem 计数器
\newtheorem{definition}[theorem]{定义}
\newtheorem{proposition}[theorem]{命题}
\newtheorem{lemma}[theorem]{引理}
\newtheorem{corollary}[theorem]{推论}
\newtheorem{example}[theorem]{例}
\newtheorem{exercise}[theorem]{练习}
\newtheorem{remark}[theorem]{注记}
\newtheorem{conclusion}[theorem]{结论}
\newtheorem*{note}{笔记}
\newtheorem*{source}{题目来源}
% ===================================================================
% 5. tcolorbox 定理框配置
% ===================================================================
% 为 定理、命题、引理、推论 设置统一的 tcolorbox 样式
% 这里采用左侧粗线+极浅灰背景的优雅样式，不会显得过于花哨
\tcbset{
    theoremboxstyle/.style={
        enhanced, 
        breakable,                % 允许跨页
        frame hidden,             % 隐藏四周外框
        colback=gray!8!white,     % 背景色
        borderline west={3pt}{0pt}{blue!60!black}, % 左侧边框线
        top=3pt, bottom=3pt, left=8pt, right=8pt,
        before skip=12pt, after skip=12pt,
        fonttitle=\bfseries
    },
    problemboxstyle/.style={
        enhanced, 
        breakable,                % 允许跨页
        frame hidden,             % 隐藏四周外框
        colback=green!8!white,     % 背景色
        borderline west={3pt}{0pt}{green!60!black}, % 左侧边框线
        top=3pt, bottom=3pt, left=8pt, right=8pt,
        before skip=12pt, after skip=12pt,
        fonttitle=\bfseries
    }
}

% 将指定的 amstheorem 环境包裹进 tcolorbox 中
\tcolorboxenvironment{theorem}{theoremboxstyle}
\tcolorboxenvironment{proposition}{theoremboxstyle}
\tcolorboxenvironment{lemma}{theoremboxstyle}
\tcolorboxenvironment{corollary}{theoremboxstyle}
\tcolorboxenvironment{example}{problemboxstyle}
\tcolorboxenvironment{exercise}{problemboxstyle}
% 修改 proof 环境：黑体、加粗、自定义标点
\renewenvironment{proof}[1][\proofname]{\par
  \pushQED{\qed} % 在末尾自动添加方块
  \normalfont \topsep6\p@\@plus6\p@\relax
  \trivlist
  \item[\hskip\labelsep
        \bfseries\heiti #1]\ignorespaces
}{%
  \popQED\endtrivlist\@endpefalse
}

% 创建 solution 环境：黑体、加粗、自定义标点
\newenvironment{solution}[1][解]{\par
  \pushQED{\qed}
  \normalfont \topsep6\p@\@plus6\p@\relax
  \trivlist
  \item[\hskip\labelsep
        \bfseries\heiti #1]\ignorespaces
}{%
  \popQED\endtrivlist\@endpefalse
}
% ===================================================================
% 6. 页眉页脚配置
% ===================================================================
\pagestyle{fancy}
\fancyhf{} % 清空默认设置

% 偶数页(E)左侧(L)显示页码，奇数页(O)右侧(R)显示页码
\fancyhead[LE,RO]{\thepage}
% 偶数页右侧显示章名
\fancyhead[RE]{\leftmark}
% 奇数页左侧显示节名
\fancyhead[LO]{\rightmark}

% 页眉横线
\renewcommand{\headrulewidth}{0.4pt}

% 重新定义 plain 样式 (用于每章第一页)
\fancypagestyle{plain}{
    \fancyhf{}
    \fancyfoot[C]{\thepage}
    \renewcommand{\headrulewidth}{0pt}
}

% ===================================================================
% 7. 超链接配置
% ===================================================================
\hypersetup{
    colorlinks=true,
    linkcolor=blue!70!black,
    urlcolor=blue!70!black,
    citecolor=green!50!black,
    bookmarksnumbered=true
}

% 封面设置
%===============================================================================
% Internal storage for cover information
%===============================================================================
\renewcommand{\@title}{}
\renewcommand{\@author}{}
\renewcommand{\@date}{\today}
\newcommand{\@institution}{}
\newcommand{\@extrainfo}{}
\newcommand{\@coverimage}{}
\newcommand{\@personalhomepage}{}
%===============================================================================
% User commands to set cover data
%===============================================================================
\renewcommand{\title}[1]{\renewcommand{\@title}{#1}}
\renewcommand{\author}[1]{\renewcommand{\@author}{#1}}
\renewcommand{\date}[1]{\renewcommand{\@date}{#1}}
\newcommand{\institution}[1]{\renewcommand{\@institution}{#1}}
\newcommand{\extrainfo}[1]{\renewcommand{\@extrainfo}{#1}}
\newcommand{\coverimage}[1]{\renewcommand{\@coverimage}{#1}}
\newcommand{\personalhomepage}[1]{\renewcommand{\@personalhomepage}{#1}} 
%===============================================================================
% Redefine \maketitle to produce the custom cover page
%===============================================================================
\renewcommand{\maketitle}{%
  \begin{titlepage}
    \centering
    % -------------------------------------------------------------------------
    % Cover image (if provided)
    % -------------------------------------------------------------------------
    \ifthenelse{\equal{\@coverimage}{}}{%
      % No image: just a vertical space to maintain layout
      \vspace*{2cm}%
    }{%
      \includegraphics[width=0.8\textwidth]{\@coverimage}\par
      \vspace{1cm}%
    }
    
    % -------------------------------------------------------------------------
    % Title
    % -------------------------------------------------------------------------
    {\Huge\bfseries \@title\par}
    \vspace{1cm}
    
    % -------------------------------------------------------------------------
    % Author
    % -------------------------------------------------------------------------
    {\Large \@author\par}
    \vspace{0.5cm}
    
    % -------------------------------------------------------------------------
    % Institution (optional)
    % -------------------------------------------------------------------------
    \ifthenelse{\equal{\@institution}{}}{}{%
      {\large \@institution\par}
      \vspace{0.5cm}%
    }
    
    % -------------------------------------------------------------------------
    % Date
    % -------------------------------------------------------------------------
    {\large \@date\par}
    
    \vfill
    
    % -------------------------------------------------------------------------
    % Extra information (optional, placed at bottom)
    % -------------------------------------------------------------------------
    \ifthenelse{\equal{\@extrainfo}{}}{}{%
      {\normalsize \@extrainfo\par}%
    }
    
  \end{titlepage}
}

%===============================================================================
% New command: \makeinnercover
% Produces an inner cover page (typically the back of the cover or the first
% inside page) containing the personal homepage link and a basic copyright line.
%===============================================================================
\newcommand{\makeinnercover}{%
  \newpage
  \thispagestyle{empty}          % no page number
  \vspace*{\fill}                % push content to vertical center
  \begin{center}
    {\large 作者: \@author}\\[1cm]
    {\large 个人主页: }\\[1cm]
    % Personal homepage (if provided)
    \ifthenelse{\equal{\@personalhomepage}{}}{}{%
      \href{\@personalhomepage}{\@personalhomepage}\\[0.5cm]
    }
    
    % Copyright line (using the date set for the cover)
    \copyright\ \@date\ \@author
  \end{center}
  \vfill                         % ensure content is truly centered
}
\endinput