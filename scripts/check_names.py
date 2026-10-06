"""Static undefined-name check (stdlib only): py_compile cannot catch a NameError waiting at runtime.

Scans custom_components/reliable_radoneye/**/*.py (or the paths given); prints `file:line name` for every
name that is read but bound nowhere in scope (function, enclosing functions, module, builtins) and exits 1 if any.
Deliberately loose (a name bound anywhere in a function counts as bound there): no false alarms, still catches
the stray-reference class of bug.
"""
import ast
import builtins
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components/reliable_radoneye"
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__class__"}
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def bound(node):
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, DEFS):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
    return names


def check(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"), str(path))
    top = set(BUILTINS)
    for n in tree.body:                                   # module scope: not what is bound inside functions
        top |= {n.name} if isinstance(n, DEFS) else bound(n)
    hits = []

    def visit(node, scope):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                visit(ch, scope | bound(ch))
            else:
                if isinstance(ch, ast.Name) and isinstance(ch.ctx, ast.Load) and ch.id not in scope:
                    hits.append((ch.lineno, ch.id))
                visit(ch, scope)
    visit(tree, top)
    return hits


def main(argv):
    files = [pathlib.Path(a) for a in argv] or sorted(ROOT.rglob("*.py"))
    if not files:
        print("check_names: no files to scan")
        return 1
    bad = 0
    for f in files:
        for line, name in check(f):
            print(f"{f}:{line} {name}")
            bad += 1
    print(f"check_names: {len(files)} files, {bad} undefined names")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
