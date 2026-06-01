"""
Convert LaTeX math notation to readable Unicode text for PDF/DOCX exports.
The web UI uses KaTeX for proper rendering; this handles file exports.
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

# Math function names — strip \ but keep the ASCII name
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
    'cup': '∪', 'cap': '∩', 'emptyset': '∅',
    'forall': '∀', 'exists': '∃',
    'circ': '∘', 'propto': '∝',
    'ldots': '…', 'cdots': '⋯', 'vdots': '⋮',
}

_SUB_DIGITS = str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')
_SUP_DIGITS = str.maketrans('0123456789', '⁰¹²³⁴⁵⁶⁷⁸⁹')
_SUP_EXTRA = {'+': '⁺', '-': '⁻', 'n': 'ⁿ', 'i': 'ⁱ', 'x': 'ˣ', 'k': 'ᵏ'}

# Regex to convert digit-subscripts inside complex subscript content
_INNER_SUB = re.compile(r'_([0-9])')


def _to_sub(s: str) -> str:
    """
    Convert subscript content to Unicode.
    Pure digit → Unicode subscript digits.
    Single char → keep with _ prefix (e.g. _a, _n).
    Complex → preprocess inner digit-subscripts then return _content.
    """
    if s.isdigit():
        return s.translate(_SUB_DIGITS)
    if len(s) == 1:
        converted = s.translate(_SUB_DIGITS)
        # If it was a digit it converted; otherwise keep the letter with _
        return converted if converted != s else f'_{s}'
    # Complex: convert any digit subscripts nested inside first
    s = _INNER_SUB.sub(lambda m: m.group(1).translate(_SUB_DIGITS), s)
    return f'_{s}'


def _to_sup(s: str) -> str:
    """
    Convert superscript content to Unicode.
    Single known char → Unicode superscript.
    Complex → return ^content.
    """
    if len(s) == 1:
        if s.isdigit():
            return s.translate(_SUP_DIGITS)
        return _SUP_EXTRA.get(s, f'^{s}')
    if s.isdigit():
        return s.translate(_SUP_DIGITS)
    # Complex: convert digit chars where possible
    result = ''.join(
        c.translate(_SUP_DIGITS) if c.isdigit() else _SUP_EXTRA.get(c, c)
        for c in s
    )
    return f'^{result}' if result == s else result


def latex_to_text(text: str) -> str:
    """Convert LaTeX notation to readable Unicode text for PDF/DOCX export."""
    if not text:
        return text

    # 1. \frac{num}{den} → (num)/(den)  — recursive so nested fracs work
    def _frac(m: re.Match) -> str:
        return f'({latex_to_text(m.group(1))})/({latex_to_text(m.group(2))})'
    text = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}', _frac, text)

    # 2. \sqrt{x} → √(x)
    text = re.sub(r'\\sqrt\{([^{}]+)\}',
                  lambda m: f'√({latex_to_text(m.group(1))})', text)
    text = re.sub(r'\\sqrt\s*(\S)', r'√\1', text)

    # 3. Remove \left / \right bracket helpers
    text = re.sub(r'\\(?:left|right)\s*[()[\]{}|.]?', '', text)

    # 4. Thin-space helpers and display alignment
    text = re.sub(r'\\[,;!]', ' ', text)
    text = re.sub(r'\\\\', ' ', text)      # line-break in align environments
    text = re.sub(r'&', ' ', text)          # alignment tab

    # 5. Math function names: \lim → lim, \sin → sin …
    # (?![a-zA-Z]) stops \inf matching inside \infty, and \sup inside \supset
    for fn in _FUNCTIONS:
        text = re.sub(r'\\' + re.escape(fn) + r'(?![a-zA-Z])', fn, text)

    # 6. Greek letters and operator symbols (longest first avoids prefix clashes)
    all_syms = {**_GREEK, **_SYMBOLS}
    for name, sym in sorted(all_syms.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(f'\\{name}', sym)

    # 7. Superscripts — single combined pass (avoids double-processing)
    text = re.sub(
        r'\^\{([^{}]*)\}|\^([^\s{\\])',
        lambda m: _to_sup(m.group(1) if m.group(1) is not None else m.group(2)),
        text,
    )

    # 8. Subscripts — single combined pass
    text = re.sub(
        r'_\{([^{}]*)\}|_([^\s{\\])',
        lambda m: _to_sub(m.group(1) if m.group(1) is not None else m.group(2)),
        text,
    )

    # 9. Apostrophe after letter → prime symbol ′
    text = re.sub(r"([a-zA-Zα-ωΑ-Ω])'", r'\1′', text)

    # 10. Strip formatting wrappers: \text{…}, \mathrm{…}, etc.
    text = re.sub(
        r'\\(?:text|mathrm|mathbf|mathit|mathbb|mathcal|operatorname)\{([^{}]*)\}',
        r'\1', text,
    )

    # 11. Remove remaining unknown \commands
    text = re.sub(r'\\[a-zA-Z]+\b\s*', '', text)

    # 12. Strip all four LaTeX math delimiter styles
    text = re.sub(r'\$\$(.+?)\$\$', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.+?)\\\]',  r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\$(.+?)\$',       r'\1', text)
    text = re.sub(r'\\\((.+?)\\\)',   r'\1', text)

    # 13. Remove stray braces left over
    text = text.replace('{', '').replace('}', '')

    # 14. Normalise whitespace
    text = re.sub(r'  +', ' ', text).strip()

    return text
