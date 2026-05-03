"""Eval runner entry point: ``python -m partner_ticket_agentic.evals``.

In the scaffold commit the runner reports that no eval golden sets are
present and exits 0, so the GitHub Actions workflow stays green. Subsequent
commits add the JSONL golden sets under ``evals/`` and the per-feature
scoring logic that prints precision/recall per agent.
"""

from __future__ import annotations

from pathlib import Path


def _evals_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "evals").is_dir() and (parent / "pyproject.toml").exists():
            return parent / "evals"
    return Path.cwd() / "evals"


def main() -> int:
    evals_root = _evals_dir()
    if not evals_root.exists():
        print("(no evals/ directory yet — scaffold commit; subsequent commits land golden sets)")
        return 0
    goldens = sorted(p for p in evals_root.glob("*.jsonl") if p.is_file())
    if not goldens:
        print(f"(no .jsonl golden sets in {evals_root}/ — scaffold commit)")
        return 0
    print(f"[evals] discovered {len(goldens)} golden set(s) in {evals_root}/:")
    for g in goldens:
        print(f"  - {g.name}")
    print("(scoring not yet wired; subsequent commits land per-feature precision/recall)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
