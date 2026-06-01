"""
Convert LaTeX math notation to readable Unicode text for PDF/DOCX exports.
The web UI uses KaTeX for proper rendering; this is the fallback for file exports.
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

_OPERATORS = {
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

_SUB_TRANS = str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')
_SUP_TRANS = str.maketrans('0123456789', '⁰¹²³⁴⁵⁶⁷⁸⁹')
_SUP_EXTRA = {'+': '⁺', '-': '⁻', 'n': 'ⁿ', 'i': 'ⁱ', 'x': 'ˣ', 'k': 'ᵏ'}


def _to_sub(s: str) -> str:
    return s.translate(_SUB_TRANS)


def _to_sup(s: str) -> str:
    return ''.join(_SUP_EXTRA.get(c, c.translate(_SUP_TRANS)) for c in s)


def latex_to_text(text: str) -> str:
    """Convert LaTeX math delimiters and commands to readable Unicode."""
    if not text:
        return text

    # \frac{num}{den} → (num)/(den)
    text = re.sub(
        r'\\frac\{([^{}]+)\}\{([^{}]+)\}',
        lambda m: f'({latex_to_text(m.group(1))})/({latex_to_text(m.group(2))})',
        text,
    )

    # \sqrt{x} → √(x)
    text = re.sub(r'\\sqrt\{([^{}]+)\}', lambda m: f'√({latex_to_text(m.group(1))})', text)
    text = re.sub(r'\\sqrt\s*(\S)', r'√\1', text)

    # \left / \right delimiters — just remove
    text = re.sub(r'\\(?:left|right)\s*[()[\]{}|.]?', '', text)

    # Greek letters and operator symbols
    all_symbols = {**_GREEK, **_OPERATORS}
    for name, symbol in sorted(all_symbols.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(f'\\{name}', symbol)

    # Superscripts: ^{...} or ^x
    text = re.sub(r'\^\{([^{}]*)\}', lambda m: _to_sup(m.group(1)), text)
    text = re.sub(r'\^([^\s{\\])', lambda m: _to_sup(m.group(1)), text)

    # Subscripts: _{...} or _x
    text = re.sub(r'_\{([^{}]*)\}', lambda m: _to_sub(m.group(1)), text)
    text = re.sub(r'_([^\s{\\])', lambda m: _to_sub(m.group(1)), text)

    # Letter followed by ' → prime symbol ′
    text = re.sub(r"([a-zA-Zα-ωΑ-Ω])'", r'\1′', text)

    # Strip \text{...}, \mathrm{...}, etc. — keep inner content
    text = re.sub(
        r'\\(?:text|mathrm|mathbf|mathit|mathbb|mathcal|operatorname)\{([^{}]*)\}',
        r'\1',
        text,
    )

    # Remove any remaining unknown LaTeX commands
    text = re.sub(r'\\[a-zA-Z]+\b\s*', '', text)

    # Remove math delimiters (all four styles)
    text = re.sub(r'\$\$(.+?)\$\$', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.+?)\\\]', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\$(.+?)\$', r'\1', text)
    text = re.sub(r'\\\((.+?)\\\)', r'\1', text)

    # Remove stray braces
    text = text.replace('{', '').replace('}', '')

    # Collapse multiple spaces
    text = re.sub(r'  +', ' ', text).strip()

    return text
