import re
import ast
import textwrap
import unicodedata

def preprocess_code(src: str) -> str:
    """Normalize code to handle paste gremlins and inconsistent formatting"""
    if src.strip().startswith("```"):
        src = re.sub(r"^```[a-zA-Z0-9_+-]*\n", "", src.strip(), count=1)
        if src.strip().endswith("```"):
            src = src.strip()[:-3]

    src = unicodedata.normalize("NFKC", src).replace("\r\n", "\n").replace("\r", "\n")

    src = src.replace("\t", "    ")

    src = textwrap.dedent(src)

    src = "\n".join(line.rstrip() for line in src.split("\n"))

    return src


def format_syntax_error(src: str, e: SyntaxError) -> str:
    """Format syntax error with context lines and pointer"""
    lineno = (e.lineno or 1) - 1
    lines = src.split("\n")
    start = max(0, lineno - 2)
    end = min(len(lines), lineno + 3)

    caret = ""
    if e.offset and 0 <= lineno < len(lines):
        caret = " " * (e.offset - 1) + "^"

    block = "\n".join(f"{i+1:>4}: {lines[i]}" for i in range(start, end))
    if caret:
        block += f"\n      {caret}"

    return f"{block}\n\n{e.msg} at line {e.lineno}, column {e.offset}"


def diff_namespace(before: dict, after: dict) -> dict:
    """Show what changed in the namespace"""
    b = {k: v for k, v in before.items() if k != "__builtins__"}
    a = {k: v for k, v in after.items() if k != "__builtins__"}

    added = {k: a[k] for k in a.keys() - b.keys()}
    modified = {k: a[k] for k in a.keys() & b.keys() if a[k] is not b[k]}

    return {"added": added, "modified": modified}


def round_recursive(obj, decimals=10):
    """Recursively round floats in nested structures"""
    if isinstance(obj, float):
        if abs(obj) > 1e-10:
            return round(obj, decimals)
        return obj
    elif isinstance(obj, dict):
        return {k: round_recursive(v, decimals) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [round_recursive(item, decimals) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(round_recursive(item, decimals) for item in obj)
    return obj


class ASTGuard(ast.NodeVisitor):
    """Security guard to prevent unsafe AST nodes"""

    def __init__(self, safe_builtins=None):
        self.safe_builtins = safe_builtins or {}

    def visit_Import(self, node):
        # Allow imports of modules already in SAFE_BUILTINS
        for alias in node.names:
            if alias.name not in self.safe_builtins:
                raise SyntaxError(f"Import of '{alias.name}' is disabled for security")
        # All imports are safe, allow them
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        # Allow imports from modules already in SAFE_BUILTINS
        if node.module and node.module in self.safe_builtins:
            self.generic_visit(node)
            return
        raise SyntaxError(f"Imports from '{node.module}' are disabled for security")

    def visit_Name(self, node):
        if "__" in node.id:
            raise SyntaxError(f"Dunder access is disabled: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if isinstance(node.attr, str) and "__" in node.attr:
            raise SyntaxError(f"Dunder access is disabled: {node.attr}")
        self.generic_visit(node)
