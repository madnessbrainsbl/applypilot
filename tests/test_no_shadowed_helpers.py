import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "tools" / "autopilot.py"


def test_no_shadowed_helpers():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    helpers = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        assigned = {t.id for t in ast.walk(fn) if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)}
        called = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        clash = assigned & called & helpers
        assert not clash, (
            f"{fn.name}() both assigns and calls {sorted(clash)} — a loop variable shadowing a "
            f"module-level helper raises 'referenced before assignment' at runtime")
    print("no shadowed helpers self-check OK")


if __name__ == "__main__":
    test_no_shadowed_helpers()
