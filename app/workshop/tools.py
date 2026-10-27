"""The workshop's tools (step 4), run by the tool itself (plan T4).

calculator  safe arithmetic (AST whitelist, ported from the old branch's
            calculator.py), with limits so a student cannot make the server
            compute 9**9**9**9
kb_search   the student's document as a knowledge base, through a
            Retriever (Phase 8, T15)
sandbox_exec is still not offered (D7).

A tool takes the argument text the model wrote and returns (ok, text)."""

import ast
import operator

from .ports import Retriever, ToolSpec

MAX_EXPRESSION_CHARS = 200
MAX_EXPONENT = 100
MAX_MAGNITUDE = 1e100

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

CALCULATOR = ToolSpec(
    "calculator",
    "Evaluates an arithmetic expression with + - * / ** % // and parentheses.",
    "the expression, for example (3 + 5) * 2",
)
KB_SEARCH = ToolSpec(
    "kb_search",
    "Searches the attached document and returns the most relevant excerpts.",
    "what to look for, in a few words",
)


def _eval(node: ast.AST):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _check(_OPS[type(node.op)](_eval(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ValueError(f"exponent larger than {MAX_EXPONENT}")
        return _check(_OPS[type(node.op)](left, right))
    raise ValueError(f"not allowed in an arithmetic expression: {type(node).__name__}")


def _check(value):
    if isinstance(value, complex):
        raise ValueError("the result is not a real number")
    if abs(value) > MAX_MAGNITUDE:
        raise ValueError("the result is too large")
    return value


def calculator(expression: str) -> tuple[bool, str]:
    expr = expression.strip().replace("×", "*").replace("÷", "/").replace("^", "**")
    if not expr:
        return False, "no expression"
    if len(expr) > MAX_EXPRESSION_CHARS:
        return False, f"the expression is longer than {MAX_EXPRESSION_CHARS} characters"
    try:
        value = _eval(ast.parse(expr, mode="eval").body)
    except ZeroDivisionError:
        return False, "division by zero"
    except (SyntaxError, ValueError, TypeError, OverflowError) as e:
        return False, str(e) or "not a valid expression"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    if isinstance(value, float):
        # 1688.2632150000002 -> 1688.263215: binary noise is not part of the answer.
        return True, f"{value:.10f}".rstrip("0").rstrip(".") if abs(value) < 1e15 else repr(value)
    return True, str(value)


def kb_search(retriever: Retriever, query: str, k: int = 3) -> tuple[bool, str]:
    if not query.strip():
        return False, "no query"
    chunks = [c for c in retriever.search(query.strip(), k) if c.selected]
    if not chunks:
        return True, "No excerpt matched."
    return True, "\n\n".join(f"[{c.rank}] {c.source}\n{c.text}" for c in chunks)
