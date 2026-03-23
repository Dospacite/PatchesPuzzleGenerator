# patches-puzzle-creator

Python 3.12 CLI for generating uniquely solvable rectangle-partition logic puzzles in a "Patches"-style format.

## What It Does

The generator builds a hidden rectangle tiling, derives one clue per rectangle, proves uniqueness, grades the puzzle deterministically, and writes machine-readable output for downstream tooling.

Generation guarantees:

- every emitted puzzle has exactly one solution
- each puzzle is graded into `easy`, `medium`, `hard`, or `expert`
- clue layouts are canonicalized to reject duplicates within a batch

## Repository Layout

- `generate_patches.py`: lightweight wrapper for direct invocation from the repository root
- `src/patches_puzzle_creator/generate_patches.py`: generator, solver, grader, serializer, CLI, and built-in self-test
- `pyproject.toml`: package metadata and console entry point

## Requirements

- Python `3.12+`

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After installation, either of these entry points works:

```bash
python generate_patches.py --self-test
patches-puzzle-creator --self-test
```

## Basic Usage

Generate the same number of puzzles for every tier:

```bash
python generate_patches.py --count-per-tier 10 --out ./generated
```

Generate a custom tier mix:

```bash
python generate_patches.py --counts easy=10 medium=10 hard=5 expert=2 --out ./generated
```

Use a fixed seed for reproducible batches:

```bash
python generate_patches.py --counts easy=25 medium=25 hard=25 expert=25 --seed 20260323 --out ./generated
```

Write one JSON file per puzzle in addition to the aggregate output:

```bash
python generate_patches.py --count-per-tier 5 --per-puzzle-json --out ./generated
```

## CLI Options

- `--counts easy=10 medium=5 ...`: per-tier target counts
- `--count-per-tier 10`: same target count for all tiers
- `--out ./generated`: output directory
- `--seed 12345`: deterministic base seed
- `--max-attempts 3000`: maximum generation attempts per tier
- `--board-size easy=5x5`: restrict one or more tiers to specific board sizes
- `--config path/to/config.json`: apply JSON overrides for counts and tier settings
- `--per-puzzle-json`: emit one JSON file per puzzle
- `--no-summary-csv`: skip `summary.csv`
- `--global-dedup`: deduplicate across all tiers, not just within each tier
- `--no-transpose-dedup`: disable transpose-aware canonicalization for rectangular boards
- `--self-test`: run built-in correctness checks and exit

## Output Format

The generator writes:

- `puzzles.json`: batch metadata plus all generated puzzles
- `summary.csv`: one row per puzzle with key grading and solver metrics
- `<tier>/<id>.json`: optional per-puzzle files when `--per-puzzle-json` is enabled

Each puzzle record includes:

- clue list
- solution rectangles
- generation metrics
- grading metrics
- canonical hash for deduplication

## Generation Pipeline

1. Generate a complete hidden rectangle tiling.
2. Apply local tiling rewrites to reduce repetitive layouts.
3. Choose one clue anchor per rectangle.
4. Enumerate all clue-compatible candidate rectangles.
5. Prove uniqueness with the exact solver.
6. Grade the puzzle using the deterministic heuristic solver.
7. Reject off-tier or duplicate layouts.
8. Serialize the accepted batch to JSON and CSV.

## Difficulty Model

- `easy`: solvable without branching and with low ambiguity
- `medium`: still no branching, but requires stronger eliminations
- `hard`: shallow branching may be required
- `expert`: allows deeper ambiguity and deeper branching

Tier thresholds live in `DEFAULT_TIER_SETTINGS` inside `src/patches_puzzle_creator/generate_patches.py`.

## Validation

Run the built-in self-test:

```bash
python generate_patches.py --self-test
```

Run a small smoke generation:

```bash
python generate_patches.py --counts easy=1 medium=1 hard=1 expert=1 --max-attempts 400 --out ./generated_smoke
```

## Publication Notes

The repository intentionally does not include generated puzzle batches, virtual environments, `egg-info`, or `__pycache__` artifacts. Those are ignored via `.gitignore` and should not be committed.

Choose and add a license before making the repository public.
