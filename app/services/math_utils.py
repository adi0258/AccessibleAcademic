"""
Convert LaTeX math notation to readable Unicode text for PDF/DOCX exports.
The web UI uses KaTeX for proper rendering; this module handles file exports.
"""
import re

_GREEK = {
    'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'δ',
    'epsilon': 'ε', 'varepsilon': 'ε', 'zeta': 'ζ', 'eta': 'η',
    'theta': 'θ', 'vartheta': 'θ', 'iota': 'ι', 'kappa': 'κ',
    'lambda': 'λ', 'mu': 'μ', 'nu': 'ν', 'xi': 'ξ',
    'pi': 'π', 'varpi': 'π', 'rho': 'ρ', 'varrho': 'ρ',
    'sigma': 'σ', 'varsigma': 'ς', 'tau': 'τ', 'upsilon': 'υ',
    'phi': 'φ', 'varphi': 'φ', 'chi': 'χ', 'psi': 'ψ', 'omega': 'ω',
    'Gamma': 'Γ', 'Delta': 'Δ', 'Theta': 'Θ', 'Lambda': 'Λ',
    'Xi': 'Ξ', 'Pi': 'Π', 'Sigma': 'Σ', 'Upsilon': 'Υ',
    'Phi': 'Φ', 'Psi': 'Ψ', 'Omega': 'Ω',
}

_FUNCTIONS = (
    'arcsin', 'arccos', 'arctan', 'sinh', 'cosh', 'tanh',
    'sin', 'cos', 'tan', 'cot', 'sec', 'csc',
    'log', 'ln', 'exp', 'lim', 'max', 'min',
    'sup', 'inf', 'gcd', 'det', 'ker', 'deg', 'mod',
    'Re', 'Im',
)

_SYMBOLS = {
    'cdot': '·', 'times': '×', 'div': '÷', 'pm': '±', 'mp': '∓',
    'infty': '∞', 'partial': '∂', 'nabla': '∇', 'prime': '′',
    'sum': '∑', 'prod': '∏', 'int': '∫', 'oint': '∮',
    'leq': '≤', 'le': '≤', 'geq': '≥', 'ge': '≥',
    'neq': '≠', 'ne': '≠', 'approx': '≈', 'equiv': '≡', 'sim': '~',
    'rightarrow': '→', 'to': '→', 'leftarrow': '←', 'mapsto': '↦',
    'Rightarrow': '⇒', 'Leftarrow': '⇐', 'Leftrightarrow': '⟺',
    'in': '∈', 'notin': '∉', 'subset': '⊂', 'supset': '⊃',
    'subseteq': '⊆', 'supseteq': '⊇',
    'cup': '∪', 'cap': '∩', 'emptyset': '∅', 'varnothing': '∅',
    'forall': '∀', 'exists': '∃', 'nexists': '∄',
    'circ': '∘', 'propto': '∝', 'perp': '⊥', 'parallel': '∥',
    'ldots': '…', 'cdots': '⋯', 'vdots': '⋮', 'ddots': '⋱',
    'langle': '⟨', 'rangle': '⟩',
    'lceil': '⌈', 'rceil': '⌉', 'lfloor': '⌊', 'rfloor': '⌋',
    'setminus': '∖', 'oplus': '⊕', 'otimes': '⊗',
    'therefore': '∴', 'because': '∵',
    'iff': '⟺', 'implies': '⇒',
}

# Common shorthand macros used in Israeli math education
_MACROS = {
    r'\R': 'ℝ', r'\N': 'ℕ', r'\Z': 'ℤ', r'\Q': 'ℚ',
    r'\C': 'ℂ', r'\F': '𝔽', r'\eps': 'ε', r'\e': 'e',
}

_SUB_DIGITS  = str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')
_SUP_DIGITS  = str.maketrans('0123456789', '⁰¹²³⁴⁵⁶⁷⁸⁹')
_SUP_EXTRA   = {'+': '⁺', '-': '⁻', 'n': 'ⁿ', 'i': 'ⁱ', 'x': 'ˣ', 'k': 'ᵏ', 'm': 'ᵐ'}
# Unicode subscript letters that actually exist
_SUB_LETTERS = {'a': 'ₐ', 'e': 'ₑ', 'o': 'ₒ', 'x': 'ₓ', 'i': 'ᵢ', 'j': 'ⱼ',
                'n': 'ₙ', 'm': 'ₘ', 'l': 'ₗ', 'r': 'ᵣ', 's': 'ₛ', 't': 'ₜ',
                'u': 'ᵤ', 'v': 'ᵥ', 'k': 'ₖ', 'p': 'ₚ'}

_INNER_SUB = re.compile(r'_([0-9])')


def _needs_group(s: str) -> bool:
    """
    Return True if expression s contains a top-level + or - and therefore
    needs to be wrapped in parentheses when used as a fraction numerator/denominator.
    """
    depth = 0
    for ch in s:
        if ch in '({[':
            depth += 1
        elif ch in ')}]':
            depth -= 1
        elif depth == 0 and ch in '+-':
            return True
    return False


def _to_sub(s: str) -> str:
    """Convert subscript content to Unicode."""
    if re.fullmatch(r'[0-9]+', s):
        return s.translate(_SUB_DIGITS)
    if len(s) == 1:
        if s.isdigit():
            return s.translate(_SUB_DIGITS)
        if s in _SUB_LETTERS:
            return _SUB_LETTERS[s]
        return f'_{s}'
    # Complex subscript: convert nested digit subscripts then show as _content
    s = _INNER_SUB.sub(lambda m: m.group(1).translate(_SUB_DIGITS), s)
    return f'_{s}'


def _to_sup(s: str) -> str:
    """Convert superscript content to Unicode."""
    if re.fullmatch(r'[0-9]+', s):
        return s.translate(_SUP_DIGITS)
    if len(s) == 1:
        if s.isdigit():
            return s.translate(_SUP_DIGITS)
        return _SUP_EXTRA.get(s, f'^{s}')
    result = ''.join(
        c.translate(_SUP_DIGITS) if c.isdigit() else _SUP_EXTRA.get(c, c)
        for c in s
    )
    return result if result != s else f'^{s}'


def _process_math(text: str) -> str:
    """
    Apply all LaTeX-to-Unicode conversions to a math-only string
    (no $ delimiters, no surrounding natural-language text).
    """
    # 0. Restore LaTeX commands corrupted by JSON escape-sequence mishandling.
    # The AI sometimes outputs \boldsymbol, \frac, \rightarrow etc. without
    # doubling the backslash in JSON.  JSON parses \b → U+0008 (backspace),
    # \f → U+000C (form feed), \r → U+000D (CR), \t → U+0009 (tab),
    # \n → U+000A (LF).  Restore them before any other processing.
    text = re.sub(r'\x08([a-zA-Z])', r'\\b\1', text)   # backspace + letter → \b...
    text = re.sub(r'\x0c([a-zA-Z])', r'\\f\1', text)   # form feed + letter  → \f...
    text = re.sub(r'\x0d([a-zA-Z])', r'\\r\1', text)   # CR + letter         → \r...
    text = re.sub(r'\x09([a-zA-Z])', r'\\t\1', text)   # tab + letter        → \t...
    text = re.sub(r'\x0a([a-zA-Z])', r'\\n\1', text)   # LF + letter         → \n...

    # 0a. Expand common shorthand macros (\R → ℝ, \N → ℕ …)
    for macro, replacement in _MACROS.items():
        text = text.replace(macro, replacement)

    # 0b. Strip \begin{...} / \end{...} environment tags — keep inner content
    text = re.sub(r'\\(?:begin|end)\{[^}]*\}', '', text)

    # 0c. Accents: \vec{x} → x⃗, \hat{x} → x̂, etc.
    for _acc, _mark in [('vec','⃗'),('hat','̂'),('bar','̅'),('tilde','̃'),('dot','̇'),('ddot','̈')]:
        text = re.sub(
            r'\\' + _acc + r'\{([^{}]{1,20})\}',
            lambda m, mk=_mark: (m.group(1) + mk if len(m.group(1)) == 1 else f'({m.group(1)}){mk}'),
            text,
        )

    # 1. \frac{num}{den} → num/den  (parens only when the expression contains ±)
    def _frac(m: re.Match) -> str:
        num = _process_math(m.group(1))
        den = _process_math(m.group(2))
        n = f'({num})' if _needs_group(num) else num
        d = f'({den})' if _needs_group(den) else den
        return f'{n}/{d}'
    text = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}', _frac, text)

    # 2. \sqrt{x} → √(x)
    text = re.sub(r'\\sqrt\{([^{}]+)\}',
                  lambda m: f'√({_process_math(m.group(1))})', text)
    text = re.sub(r'\\sqrt\s*(\S)', r'√\1', text)

    # 3. Remove \left / \right bracket helpers
    text = re.sub(r'\\(?:left|right)\s*[()[\]{}|.\\]?', '', text)

    # 4. Spacing helpers → single space; line-break/alignment → space
    text = re.sub(r'\\[,;!]', ' ', text)
    text = re.sub(r'\\ ', ' ', text)          # backslash-space
    text = re.sub(r'\\(?:quad|qquad)\b', '  ', text)
    text = re.sub(r'\\\\', ' ', text)
    text = re.sub(r'&', ' ', text)

    # 5. Style wrappers that should be stripped but content kept
    text = re.sub(r'\\(?:displaystyle|textstyle|scriptstyle|normalsize)\b\s*', '', text)

    # 6. Math function names  e.g. \lim, \sin
    for fn in _FUNCTIONS:
        text = re.sub(r'\\' + re.escape(fn) + r'(?![a-zA-Z])', fn, text)

    # 7. Greek letters and operator symbols (longest first to avoid prefix clashes)
    all_syms = {**_GREEK, **_SYMBOLS}
    for name, sym in sorted(all_syms.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(f'\\{name}', sym)

    # 8. Superscripts — single combined pass
    text = re.sub(
        r'\^\{([^{}]*)\}|\^([^\s{\\])',
        lambda m: _to_sup(m.group(1) if m.group(1) is not None else m.group(2)),
        text,
    )

    # 9. Subscripts — single combined pass
    text = re.sub(
        r'_\{([^{}]*)\}|_([^\s{\\])',
        lambda m: _to_sub(m.group(1) if m.group(1) is not None else m.group(2)),
        text,
    )

    # 10. Letter followed by apostrophe → prime symbol
    text = re.sub(r"([a-zA-Zα-ωΑ-Ω])'", r'\1′', text)

    # 11. Strip formatting wrappers
    text = re.sub(
        r'\\(?:text|mathrm|mathbf|mathit|mathbb|mathcal|operatorname)\{([^{}]*)\}',
        r'\1', text,
    )

    # 12. Remove remaining unknown \commands (but NOT bare backslashes)
    text = re.sub(r'\\[a-zA-Z]+\b\s*', '', text)

    # 13. Strip stray braces
    text = text.replace('{', '').replace('}', '')

    # 14. Strip bare backslashes not starting a valid LaTeX sequence (e.g. from \\)
    text = re.sub(r'\\(?![a-zA-Z{(\\])', '', text)

    # 15. Normalise whitespace
    text = re.sub(r'  +', ' ', text).strip()

    return text


def latex_to_text(text: str) -> str:
    """
    Convert a string that may contain LaTeX math delimiters mixed with
    natural-language text into readable Unicode.

    Display math ($$...$$  or  \\[...\\]) is placed on its own line.
    Inline math ($...$  or  \\(...\\)) is converted in-place.
    """
    if not text:
        return text

    # Restore LaTeX commands corrupted by JSON escape-sequence mishandling
    # (same fix as _process_math step 0, applied to the full mixed text)
    text = re.sub(r'\x08([a-zA-Z])', r'\\b\1', text)
    text = re.sub(r'\x0c([a-zA-Z])', r'\\f\1', text)
    text = re.sub(r'\x0d([a-zA-Z])', r'\\r\1', text)
    text = re.sub(r'\x09([a-zA-Z])', r'\\t\1', text)
    text = re.sub(r'\x0a([a-zA-Z])', r'\\n\1', text)

    # ── 1.  Display math → own line ──────────────────────────────────────────
    def _replace_display(m: re.Match) -> str:
        inner = m.group(1) or m.group(2) or ''
        converted = _process_math(inner.strip())
        return f'\n{converted}\n'

    # \begin{align}...\end{align} and similar environments → display math
    text = re.sub(
        r'\\begin\{(?:align|equation|gather|multline)\*?\}(.+?)\\end\{(?:align|equation|gather|multline)\*?\}',
        _replace_display, text, flags=re.DOTALL,
    )
    text = re.sub(r'\$\$(.+?)\$\$',    _replace_display, text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.+?)\\\]',     _replace_display, text, flags=re.DOTALL)

    # ── 2.  Inline math → processed in-place ─────────────────────────────────
    def _replace_inline(m: re.Match) -> str:
        inner = m.group(1) or m.group(2) or ''
        return _process_math(inner.strip())

    text = re.sub(r'\$(.+?)\$',         _replace_inline, text)
    text = re.sub(r'\\\((.+?)\\\)',      _replace_inline, text)

    # ── 3.  Any remaining LaTeX outside delimiters (edge cases) ──────────────
    text = _process_math(text)

    # ── 4.  Collapse excessive blank lines but keep intentional ones ──────────
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text
