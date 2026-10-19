"""Bake the payload's bytecode cache at staging time.

A sealed payload ships no __pycache__: the runtime redirects bytecode
writes to a user-level cache (signature-breaking on macOS, read-only
mount on AppImage/MSIX), so every fresh install pays a cold-compile
stall on first launch. Baking turns that into a warm read:

* compileall runs with the payload's OWN staged interpreter (a pyc
  minted by any other interpreter is silently ignored — the magic tag
  is part of the cache filename);
* ``--invalidation-mode unchecked-hash``: the pyc is trusted without
  source validation and never rewritten, so staging-repack mtimes
  cannot invalidate it and a stale source cannot trigger a rewrite
  into the sealed tree;
* the baked pycs are chmodded read-only BEFORE packaging, so no
  incidental write can touch them; the dirs stay writable because
  in-place rebuilds rmtree the tree, and asserted coverage means no
  cache-miss write can target them;
* the runtime's sys.pycache_prefix redirect is dropped when the baked
  marker is present (the prefix relocates READS too — see
  scripts/build/launcher_wrapper.py), so imports read the source-
  adjacent baked pycs, which is Python's default multi-root lookup:
  payload modules read theirs; plugin/user modules keep caching beside
  their own sources under HERMES_HOME.

Coverage is the performance contract: every parseable module under
the baked roots must have a pyc. Unparseable fixtures (deliberately
invalid test data) are counted and skipped, not failed.
"""

import subprocess
from pathlib import Path

MARKER = ".hermes-baked-pycache"


def _import_roots(root: Path) -> list[Path]:
    """The payload's import roots, in launcher order (repo, venv site, PM)."""
    roots = [root / "hermes-agent"]
    for source_root in roots + [root / "venv", root / "pm-runtime"]:
        if not source_root.is_dir():
            raise FileNotFoundError(f"missing import root: {source_root}")
    sites = sorted((root / "venv").glob("lib/python*/site-packages")) + \
        sorted((root / "venv").glob("Lib/site-packages"))
    if len(sites) != 1:
        raise ValueError(f"expected exactly one staged venv site-packages, found {len(sites)}")
    roots.append(sites[0])
    pm_sites = sorted((root / "pm-runtime").glob("lib/python*/site-packages")) + \
        sorted((root / "pm-runtime").glob("Lib/site-packages"))
    if len(pm_sites) != 1:
        raise ValueError(f"expected exactly one PM runtime site-packages, found {len(pm_sites)}")
    roots.append(pm_sites[0])
    return roots


def _uncovered(root: Path, cache_tag: str | None = None) -> tuple[list[Path], int]:
    """Parseable .py files without a pyc, and unparseable fixtures skipped.

    ``cache_tag`` must be the cache tag of the interpreter that ran
    ``compileall`` (e.g. ``sys.implementation.cache_tag``). When omitted it
    defaults to the running interpreter's tag for tests and local checks.
    Passing the staged tag is essential when the staging driver runs under a
    different Python than the payload's own interpreter.
    """
    from importlib.util import cache_from_source
    missing: list[Path] = []
    unparseable = 0
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if cache_tag is None:
            expected = Path(cache_from_source(path))
        else:
            expected = path.parent / "__pycache__" / f"{path.stem}.{cache_tag}.pyc"
        if expected.exists():
            continue
        try:
            compile(path.read_bytes(), str(path), "exec")
        except SyntaxError:
            # Deliberately invalid test fixtures ship in the snapshot; they
            # can never be imported, so no pyc is expected.
            unparseable += 1
        else:
            missing.append(path)
    return missing, unparseable


def _diagnose_uncovered(root: Path, python: Path, missing: list[Path]) -> None:
    """Emit on-runway evidence when coverage fails; never changes the result."""
    from importlib.util import cache_from_source
    import sys as _sys
    print(f"DEBUG outer Python {_sys.version_info}, cache_tag={_sys.implementation.cache_tag}")
    try:
        staged_tag = subprocess.check_output(
            [str(python), "-I", "-c", "import sys; print(sys.implementation.cache_tag)"],
            text=True, timeout=30).strip()
    except Exception as exc:  # pragma: no cover - diagnostic only
        staged_tag = f"<error: {exc}>"
    print(f"DEBUG staged Python {python}, cache_tag={staged_tag}")
    if not missing:
        return
    for path in missing[:3]:
        cache_dir = path.with_name("__pycache__")
        print(f"DEBUG missing source: {path}")
        print(f"DEBUG expected pyc (outer tag): {Path(cache_from_source(path))}")
        if cache_dir.is_dir():
            files = [c.name for c in cache_dir.iterdir() if c.is_file()]
            print(f"DEBUG __pycache__ contents: {files}")
        else:
            print("DEBUG __pycache__ dir does not exist")
    # Re-run compileall without -q for the first source to capture its real output.
    if missing:
        sample_dir = missing[0].parent
        print(f"DEBUG re-running compileall on {sample_dir}")
        try:
            result = subprocess.run(
                [str(python), "-I", "-m", "compileall",
                 "--invalidation-mode", "unchecked-hash", str(sample_dir)],
                capture_output=True, text=True, timeout=60)
            print(f"DEBUG compileall returncode={result.returncode}")
            if result.stdout:
                print("DEBUG compileall stdout:\n" + result.stdout[:2000])
            if result.stderr:
                print("DEBUG compileall stderr:\n" + result.stderr[:2000])
        except Exception as exc:  # pragma: no cover - diagnostic only
            print(f"DEBUG compileall re-run failed: {exc}")


def bake_bytecode(root: Path, python: Path) -> dict:
    """Compile every payload module with the staged interpreter and seal the
    caches read-only. ``root`` is the payload root; ``python`` MUST be the
    payload's own staged interpreter binary."""
    root = Path(root).resolve()
    python = Path(python).resolve()
    if not python.is_file():
        raise FileNotFoundError(f"staged interpreter is missing: {python}")
    staged_tag = subprocess.check_output(
        [str(python), "-I", "-c", "import sys; print(sys.implementation.cache_tag)"],
        text=True, timeout=30).strip()
    total = 0
    for source_root in _import_roots(root):
        subprocess.run(
            [str(python), "-I", "-m", "compileall", "-q",
             "--invalidation-mode", "unchecked-hash", str(source_root)],
            check=True, timeout=30 * 60, stdin=subprocess.DEVNULL)
        missing, unparseable = _uncovered(source_root, cache_tag=staged_tag)
        if missing:
            _diagnose_uncovered(source_root, python, missing)
            sample = ", ".join(str(m.relative_to(root)) for m in missing[:5])
            raise ValueError(f"{len(missing)} payload modules have no bytecode pyc "
                             f"({sample}…): a fresh install would cold-compile them "
                             "every launch")
        total += sum(1 for _ in source_root.rglob("__pycache__/*.pyc"))
    # Pycs are read-only BEFORE packaging so a stale-source rewrite (which
    # unchecked-hash never triggers anyway) or any incidental write can never
    # touch them and the macOS signature never observes a pyc change. The
    # __pycache__ DIRS stay writable: in-place payload rebuilds rmtree the
    # tree (native._prepare_native), and complete asserted coverage plus
    # unchecked-hash means no cache-miss write can ever target these dirs.
    for source_root in _import_roots(root):
        for cache in source_root.rglob("__pycache__"):
            for pyc in cache.iterdir():
                if pyc.is_file():
                    pyc.chmod(0o444)
    (root / MARKER).write_text("unchecked-hash\n", encoding="utf-8")
    return {"modules": total, "marker": str(root / MARKER)}
