"""Generate uniquely solvable rectangle-partition puzzles."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


ShapeClass = str
DifficultyTier = str

TIERS: tuple[DifficultyTier, ...] = ("easy", "medium", "hard", "expert")

DEFAULT_BOARD_SIZES: dict[DifficultyTier, tuple[tuple[int, int], ...]] = {
    "easy": ((5, 5), (5, 6), (6, 5), (6, 6)),
    "medium": ((6, 6), (6, 7), (7, 6), (7, 7)),
    "hard": ((7, 7), (7, 8), (8, 7), (8, 8)),
    "expert": ((8, 8), (8, 9), (9, 8), (9, 9)),
}


@dataclass(frozen=True, slots=True)
class Rectangle:
    row: int
    col: int
    height: int
    width: int

    @property
    def area(self) -> int:
        return self.height * self.width

    @property
    def shape_class(self) -> ShapeClass:
        return classify_shape(self.width, self.height)

    @property
    def is_strip(self) -> bool:
        return self.height == 1 or self.width == 1

    @property
    def strip_length(self) -> int:
        return max(self.height, self.width)

    def cells(self) -> Iterable[tuple[int, int]]:
        for row in range(self.row, self.row + self.height):
            for col in range(self.col, self.col + self.width):
                yield row, col

    def contains(self, row: int, col: int) -> bool:
        return (
            self.row <= row < self.row + self.height
            and self.col <= col < self.col + self.width
        )

    def to_dict(self) -> dict[str, int]:
        return {"x": self.col, "y": self.row, "w": self.width, "h": self.height}


@dataclass(frozen=True, slots=True)
class Clue:
    row: int
    col: int
    area: int
    shape: ShapeClass

    def to_dict(self) -> dict[str, Any]:
        return {"row": self.row, "col": self.col, "area": self.area, "shape": self.shape}


@dataclass(slots=True)
class CandidateRectangle:
    clue_index: int
    rect: Rectangle
    bitmask: int


@dataclass(slots=True)
class SolverResult:
    solution_count: int
    solutions: list[list[Rectangle]]
    nodes: int
    elapsed_ms: float


@dataclass(slots=True)
class GradingMetrics:
    techniques_used: list[str]
    steps: int
    branching_required: bool
    max_branch_depth: int
    contradiction_eliminations: int
    initial_candidate_counts: list[int]
    final_difficulty: DifficultyTier

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PuzzleRecord:
    identifier: str
    difficulty: DifficultyTier
    board_rows: int
    board_cols: int
    seed: int
    clues: list[Clue]
    solution_rectangles: list[Rectangle]
    generation_metrics: dict[str, Any]
    grading_metrics: GradingMetrics
    canonical_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "difficulty": self.difficulty,
            "board_rows": self.board_rows,
            "board_cols": self.board_cols,
            "seed": self.seed,
            "clues": [clue.to_dict() for clue in self.clues],
            "solution_rectangles": [rect.to_dict() for rect in self.solution_rectangles],
            "generation_metrics": self.generation_metrics,
            "grading_metrics": self.grading_metrics.to_dict(),
            "canonical_hash": self.canonical_hash,
        }


@dataclass(slots=True)
class TierSettings:
    board_sizes: tuple[tuple[int, int], ...]
    strip_max_length: int
    strip_max_ratio: float
    anchor_mode: str
    allow_branching: bool
    min_branch_depth: int
    max_branch_depth: int
    min_rectangles: int
    max_rectangles: int
    min_average_candidates: float
    max_average_candidates: float
    max_singletons_ratio: float
    min_steps: int = 0
    max_steps: int | None = None
    min_contradiction_eliminations: int = 0
    max_contradiction_eliminations: int | None = None


@dataclass(slots=True)
class GenerationConfig:
    counts: dict[DifficultyTier, int]
    output_dir: Path
    random_seed: int
    max_attempts_per_tier: int
    transposition_aware: bool = True
    per_puzzle_json: bool = False
    summary_csv: bool = True
    global_dedup: bool = False
    size_overrides: dict[DifficultyTier, tuple[tuple[int, int], ...]] = field(
        default_factory=dict
    )
    config_path: Path | None = None


DEFAULT_TIER_SETTINGS: dict[DifficultyTier, TierSettings] = {
    "easy": TierSettings(
        board_sizes=DEFAULT_BOARD_SIZES["easy"],
        strip_max_length=4,
        strip_max_ratio=0.30,
        anchor_mode="edge-heavy",
        allow_branching=False,
        min_branch_depth=0,
        max_branch_depth=0,
        min_rectangles=6,
        max_rectangles=11,
        min_average_candidates=1.0,
        max_average_candidates=2.2,
        max_singletons_ratio=0.65,
        min_steps=0,
        max_steps=17,
        max_contradiction_eliminations=0,
    ),
    "medium": TierSettings(
        board_sizes=DEFAULT_BOARD_SIZES["medium"],
        strip_max_length=4,
        strip_max_ratio=0.34,
        anchor_mode="edge-or-near-center",
        allow_branching=False,
        min_branch_depth=0,
        max_branch_depth=0,
        min_rectangles=8,
        max_rectangles=14,
        min_average_candidates=2.0,
        max_average_candidates=3.6,
        max_singletons_ratio=0.55,
        min_steps=15,
        max_steps=24,
        min_contradiction_eliminations=0,
        max_contradiction_eliminations=4,
    ),
    "hard": TierSettings(
        board_sizes=DEFAULT_BOARD_SIZES["hard"],
        strip_max_length=6,
        strip_max_ratio=0.40,
        anchor_mode="interior-heavy",
        allow_branching=True,
        min_branch_depth=0,
        max_branch_depth=2,
        min_rectangles=10,
        max_rectangles=16,
        min_average_candidates=2.6,
        max_average_candidates=4.8,
        max_singletons_ratio=0.50,
        min_steps=21,
        max_steps=29,
        min_contradiction_eliminations=0,
    ),
    "expert": TierSettings(
        board_sizes=DEFAULT_BOARD_SIZES["expert"],
        strip_max_length=8,
        strip_max_ratio=0.45,
        anchor_mode="interior-heavy",
        allow_branching=True,
        min_branch_depth=0,
        max_branch_depth=4,
        min_rectangles=12,
        max_rectangles=20,
        min_average_candidates=2.8,
        max_average_candidates=6.0,
        max_singletons_ratio=0.45,
        min_steps=28,
        min_contradiction_eliminations=0,
    ),
}


def classify_shape(width: int, height: int) -> ShapeClass:
    if width == height:
        return "square"
    if width > height:
        return "wide"
    return "tall"


def cell_bit(rows: int, cols: int, row: int, col: int) -> int:
    return 1 << (row * cols + col)


def rectangle_bitmask(rows: int, cols: int, rect: Rectangle) -> int:
    bitmask = 0
    for row, col in rect.cells():
        bitmask |= cell_bit(rows, cols, row, col)
    return bitmask


def bit_count(value: int) -> int:
    return value.bit_count()


def in_bounds(rows: int, cols: int, rect: Rectangle) -> bool:
    return (
        0 <= rect.row
        and 0 <= rect.col
        and rect.row + rect.height <= rows
        and rect.col + rect.width <= cols
    )


def factor_dimensions(area: int, shape: ShapeClass) -> list[tuple[int, int]]:
    dims: list[tuple[int, int]] = []
    limit = int(math.isqrt(area))
    for height in range(1, limit + 1):
        if area % height != 0:
            continue
        width = area // height
        rect_shape = classify_shape(width, height)
        if rect_shape == shape:
            dims.append((height, width))
        if width != height:
            rect_shape_swapped = classify_shape(height, width)
            if rect_shape_swapped == shape:
                dims.append((width, height))
    dims = sorted(set(dims))
    return dims


def enumerate_candidates(rows: int, cols: int, clues: Sequence[Clue]) -> list[list[CandidateRectangle]]:
    candidates: list[list[CandidateRectangle]] = []
    clue_positions = [(clue.row, clue.col) for clue in clues]
    for clue_index, clue in enumerate(clues):
        clue_candidates: list[CandidateRectangle] = []
        for height, width in factor_dimensions(clue.area, clue.shape):
            row_start_min = max(0, clue.row - height + 1)
            row_start_max = min(clue.row, rows - height)
            col_start_min = max(0, clue.col - width + 1)
            col_start_max = min(clue.col, cols - width)
            for row in range(row_start_min, row_start_max + 1):
                for col in range(col_start_min, col_start_max + 1):
                    rect = Rectangle(row=row, col=col, height=height, width=width)
                    if any(
                        other_index != clue_index and rect.contains(other_row, other_col)
                        for other_index, (other_row, other_col) in enumerate(clue_positions)
                    ):
                        continue
                    clue_candidates.append(
                        CandidateRectangle(
                            clue_index=clue_index,
                            rect=rect,
                            bitmask=rectangle_bitmask(rows, cols, rect),
                        )
                    )
        if not clue_candidates:
            raise ValueError(f"Clue {clue} has no valid rectangles.")
        clue_candidates.sort(
            key=lambda item: (item.rect.row, item.rect.col, item.rect.height, item.rect.width)
        )
        candidates.append(clue_candidates)
    return candidates


def choose_anchor(rect: Rectangle, mode: str, rng: random.Random) -> tuple[int, int]:
    cells = list(rect.cells())
    corner_cells: list[tuple[int, int]] = []
    edge_cells: list[tuple[int, int]] = []
    centerish_cells: list[tuple[int, int]] = []
    rect_center_row = rect.row + (rect.height - 1) / 2
    rect_center_col = rect.col + (rect.width - 1) / 2
    for row, col in cells:
        on_top_or_bottom = row in (rect.row, rect.row + rect.height - 1)
        on_left_or_right = col in (rect.col, rect.col + rect.width - 1)
        if on_top_or_bottom and on_left_or_right:
            corner_cells.append((row, col))
        elif on_top_or_bottom or on_left_or_right:
            edge_cells.append((row, col))
        else:
            centerish_cells.append((row, col))
    if mode == "edge-heavy":
        ordered_groups = [corner_cells, edge_cells, centerish_cells]
    elif mode == "edge-or-near-center":
        near_center = sorted(
            centerish_cells,
            key=lambda cell: abs(cell[0] - rect_center_row) + abs(cell[1] - rect_center_col),
        )[: max(1, len(centerish_cells) // 2 or 1)]
        ordered_groups = [near_center, edge_cells + corner_cells, centerish_cells]
    elif mode == "interior-heavy":
        near_center = sorted(
            centerish_cells or cells,
            key=lambda cell: abs(cell[0] - rect_center_row) + abs(cell[1] - rect_center_col),
        )[: max(1, len((centerish_cells or cells)) // 2 or 1)]
        ordered_groups = [near_center, centerish_cells, edge_cells, corner_cells, cells]
    else:
        ordered_groups = [cells]
    for group in ordered_groups:
        if group:
            return rng.choice(group)
    return rng.choice(cells)


def clue_from_rectangle(rect: Rectangle, mode: str, rng: random.Random) -> Clue:
    row, col = choose_anchor(rect, mode, rng)
    return Clue(row=row, col=col, area=rect.area, shape=rect.shape_class)


def symmetry_transforms(rows: int, cols: int, include_transpose: bool) -> list[tuple[int, int, str]]:
    transforms: list[tuple[int, int, str]] = []
    transforms.append((rows, cols, "identity"))
    transforms.append((rows, cols, "rot180"))
    transforms.append((rows, cols, "flip_h"))
    transforms.append((rows, cols, "flip_v"))
    if rows == cols:
        transforms.extend(
            [
                (rows, cols, "rot90"),
                (rows, cols, "rot270"),
                (rows, cols, "diag"),
                (rows, cols, "anti"),
            ]
        )
    elif include_transpose:
        transforms.extend(
            [
                (cols, rows, "transpose"),
                (cols, rows, "transpose_rot180"),
                (cols, rows, "transpose_flip_h"),
                (cols, rows, "transpose_flip_v"),
            ]
        )
    return transforms


def apply_transform(
    rows: int, cols: int, row: int, col: int, name: str
) -> tuple[int, int]:
    if name == "identity":
        return row, col
    if name == "rot180":
        return rows - 1 - row, cols - 1 - col
    if name == "flip_h":
        return row, cols - 1 - col
    if name == "flip_v":
        return rows - 1 - row, col
    if name == "rot90":
        return col, rows - 1 - row
    if name == "rot270":
        return cols - 1 - col, row
    if name == "diag":
        return col, row
    if name == "anti":
        return rows - 1 - col, cols - 1 - row
    if name == "transpose":
        return col, row
    if name == "transpose_rot180":
        return cols - 1 - col, rows - 1 - row
    if name == "transpose_flip_h":
        return col, rows - 1 - row
    if name == "transpose_flip_v":
        return cols - 1 - col, row
    raise ValueError(f"Unknown transform {name}")


def transform_shape(shape: ShapeClass, transform: str) -> ShapeClass:
    if shape == "square":
        return shape
    if transform in {"rot90", "rot270", "diag", "anti", "transpose", "transpose_rot180", "transpose_flip_h", "transpose_flip_v"}:
        return "tall" if shape == "wide" else "wide"
    return shape


def canonical_clue_layout(
    rows: int, cols: int, clues: Sequence[Clue], include_transpose: bool
) -> tuple[str, str]:
    variants: list[str] = []
    for out_rows, out_cols, transform in symmetry_transforms(rows, cols, include_transpose):
        transformed = []
        for clue in clues:
            new_row, new_col = apply_transform(rows, cols, clue.row, clue.col, transform)
            transformed.append((new_row, new_col, clue.area, transform_shape(clue.shape, transform)))
        transformed.sort()
        payload = json.dumps(
            {"rows": out_rows, "cols": out_cols, "clues": transformed},
            separators=(",", ":"),
        )
        variants.append(payload)
    best = min(variants)
    return best, hashlib.sha256(best.encode("utf-8")).hexdigest()


def create_empty_grid(rows: int, cols: int) -> list[list[int]]:
    return [[-1 for _ in range(cols)] for _ in range(rows)]


def tiling_rectangles_to_grid(rows: int, cols: int, rectangles: Sequence[Rectangle]) -> list[list[int]]:
    grid = create_empty_grid(rows, cols)
    for index, rect in enumerate(rectangles):
        for row, col in rect.cells():
            if grid[row][col] != -1:
                raise ValueError("Overlapping rectangles in tiling.")
            grid[row][col] = index
    return grid


def enumerate_fill_rectangles(
    rows: int,
    cols: int,
    grid: list[list[int]],
    start_row: int,
    start_col: int,
    settings: TierSettings,
    rng: random.Random,
) -> Rectangle | None:
    max_height = 0
    for row in range(start_row, rows):
        if grid[row][start_col] == -1:
            max_height += 1
        else:
            break
    candidates: list[tuple[float, Rectangle]] = []
    for height in range(1, max_height + 1):
        max_width = 0
        for col in range(start_col, cols):
            if all(grid[row][col] == -1 for row in range(start_row, start_row + height)):
                max_width += 1
            else:
                break
        for width in range(1, max_width + 1):
            if width == 1 and height == 1:
                continue
            rect = Rectangle(row=start_row, col=start_col, height=height, width=width)
            if rect.is_strip and rect.strip_length > settings.strip_max_length:
                continue
            area = rect.area
            if area < 2 or area > 12:
                continue
            shape_bonus = 0.25 if rect.shape_class == "square" else 0.0
            area_bonus = 0.15 if 4 <= area <= 8 else 0.0
            strip_penalty = 0.6 if rect.is_strip else 0.0
            score = rng.random() + shape_bonus + area_bonus - strip_penalty
            candidates.append((score, rect))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [rect for _, rect in candidates[: min(10, len(candidates))]]


def place_rectangle(grid: list[list[int]], rect: Rectangle, value: int) -> None:
    for row, col in rect.cells():
        grid[row][col] = value


def has_isolated_empty_cell(rows: int, cols: int, grid: list[list[int]]) -> bool:
    for row in range(rows):
        for col in range(cols):
            if grid[row][col] != -1:
                continue
            degree = 0
            for d_row, d_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                next_row = row + d_row
                next_col = col + d_col
                if 0 <= next_row < rows and 0 <= next_col < cols and grid[next_row][next_col] == -1:
                    degree += 1
            if degree == 0:
                return True
    return False


def fill_board_randomly(rows: int, cols: int, settings: TierSettings, rng: random.Random) -> list[Rectangle] | None:
    grid = create_empty_grid(rows, cols)
    rectangles: list[Rectangle] = []
    node_budget = rows * cols * 80
    nodes_used = 0

    def search() -> bool:
        nonlocal nodes_used
        if nodes_used >= node_budget:
            return False
        nodes_used += 1
        next_cell: tuple[int, int] | None = None
        for row in range(rows):
            for col in range(cols):
                if grid[row][col] == -1:
                    next_cell = (row, col)
                    break
            if next_cell is not None:
                break
        if next_cell is None:
            return True
        row, col = next_cell
        options = enumerate_fill_rectangles(rows, cols, grid, row, col, settings, rng)
        if not options:
            return False
        for rect in options:
            place_rectangle(grid, rect, len(rectangles))
            if has_isolated_empty_cell(rows, cols, grid):
                place_rectangle(grid, rect, -1)
                continue
            rectangles.append(rect)
            if search():
                return True
            rectangles.pop()
            place_rectangle(grid, rect, -1)
        return False

    if search():
        return rectangles[:]
    return None


def rectangle_union_if_rectangular(first: Rectangle, second: Rectangle) -> Rectangle | None:
    top = min(first.row, second.row)
    left = min(first.col, second.col)
    bottom = max(first.row + first.height, second.row + second.height)
    right = max(first.col + first.width, second.col + second.width)
    union = Rectangle(row=top, col=left, height=bottom - top, width=right - left)
    if union.area != first.area + second.area:
        return None
    return union


def resplit_union(
    union_rect: Rectangle, settings: TierSettings, rng: random.Random
) -> list[Rectangle] | None:
    options: list[list[Rectangle]] = []
    for split_row in range(1, union_rect.height):
        first = Rectangle(union_rect.row, union_rect.col, split_row, union_rect.width)
        second = Rectangle(
            union_rect.row + split_row,
            union_rect.col,
            union_rect.height - split_row,
            union_rect.width,
        )
        if all(is_valid_tiling_rectangle(item, settings) for item in (first, second)):
            options.append([first, second])
    for split_col in range(1, union_rect.width):
        first = Rectangle(union_rect.row, union_rect.col, union_rect.height, split_col)
        second = Rectangle(
            union_rect.row,
            union_rect.col + split_col,
            union_rect.height,
            union_rect.width - split_col,
        )
        if all(is_valid_tiling_rectangle(item, settings) for item in (first, second)):
            options.append([first, second])
    for first_height in range(1, union_rect.height - 1):
        for second_height in range(1, union_rect.height - first_height):
            pieces = [
                Rectangle(union_rect.row, union_rect.col, first_height, union_rect.width),
                Rectangle(
                    union_rect.row + first_height,
                    union_rect.col,
                    second_height,
                    union_rect.width,
                ),
                Rectangle(
                    union_rect.row + first_height + second_height,
                    union_rect.col,
                    union_rect.height - first_height - second_height,
                    union_rect.width,
                ),
            ]
            if all(is_valid_tiling_rectangle(item, settings) for item in pieces):
                options.append(pieces)
    for first_width in range(1, union_rect.width - 1):
        for second_width in range(1, union_rect.width - first_width):
            pieces = [
                Rectangle(union_rect.row, union_rect.col, union_rect.height, first_width),
                Rectangle(
                    union_rect.row,
                    union_rect.col + first_width,
                    union_rect.height,
                    second_width,
                ),
                Rectangle(
                    union_rect.row,
                    union_rect.col + first_width + second_width,
                    union_rect.height,
                    union_rect.width - first_width - second_width,
                ),
            ]
            if all(is_valid_tiling_rectangle(item, settings) for item in pieces):
                options.append(pieces)
    if not options:
        return None
    return rng.choice(options)


def shift_adjacent_rectangles(
    first: Rectangle, second: Rectangle, settings: TierSettings, rng: random.Random
) -> list[Rectangle] | None:
    options: list[list[Rectangle]] = []
    if first.row == second.row and first.height == second.height:
        left, right = sorted((first, second), key=lambda rect: rect.col)
        if left.col + left.width == right.col:
            for delta in (-1, 1):
                new_width = left.width + delta
                other_width = right.width - delta
                if new_width <= 0 or other_width <= 0:
                    continue
                candidate = [
                    Rectangle(left.row, left.col, left.height, new_width),
                    Rectangle(right.row, right.col + delta, right.height, other_width),
                ]
                if all(is_valid_tiling_rectangle(item, settings) for item in candidate):
                    options.append(candidate)
    if first.col == second.col and first.width == second.width:
        top, bottom = sorted((first, second), key=lambda rect: rect.row)
        if top.row + top.height == bottom.row:
            for delta in (-1, 1):
                new_height = top.height + delta
                other_height = bottom.height - delta
                if new_height <= 0 or other_height <= 0:
                    continue
                candidate = [
                    Rectangle(top.row, top.col, new_height, top.width),
                    Rectangle(bottom.row + delta, bottom.col, other_height, bottom.width),
                ]
                if all(is_valid_tiling_rectangle(item, settings) for item in candidate):
                    options.append(candidate)
    if not options:
        return None
    return rng.choice(options)


def mutate_tiling(
    rows: int,
    cols: int,
    rectangles: list[Rectangle],
    settings: TierSettings,
    rng: random.Random,
    rounds: int,
) -> list[Rectangle]:
    current = rectangles[:]
    for _ in range(rounds):
        if len(current) < 2:
            break
        i, j = rng.sample(range(len(current)), 2)
        replacement = shift_adjacent_rectangles(current[i], current[j], settings, rng)
        if replacement is None:
            union = rectangle_union_if_rectangular(current[i], current[j])
            if union is None:
                continue
            replacement = resplit_union(union, settings, rng)
            if replacement is None:
                continue
            if sorted((r.area for r in replacement)) == sorted((current[i].area, current[j].area)):
                continue
        next_rectangles = [rect for k, rect in enumerate(current) if k not in (i, j)]
        next_rectangles.extend(replacement)
        try:
            tiling_rectangles_to_grid(rows, cols, next_rectangles)
        except ValueError:
            continue
        current = next_rectangles
    current.sort(key=lambda rect: (rect.row, rect.col, rect.height, rect.width))
    return current


def is_valid_tiling_rectangle(rect: Rectangle, settings: TierSettings) -> bool:
    if rect.area <= 1:
        return False
    if rect.is_strip and rect.strip_length > settings.strip_max_length:
        return False
    return True


def tiling_quality(rows: int, cols: int, rectangles: Sequence[Rectangle], settings: TierSettings) -> tuple[bool, str]:
    strip_count = sum(1 for rect in rectangles if rect.is_strip)
    strip_ratio = strip_count / len(rectangles)
    if strip_ratio > settings.strip_max_ratio:
        return False, "too_many_strips"
    if not (settings.min_rectangles <= len(rectangles) <= settings.max_rectangles):
        return False, "tiling_shape_count"
    areas = Counter(rect.area for rect in rectangles)
    repeated_area_clusters = sum(count - 1 for count in areas.values() if count >= 3)
    if repeated_area_clusters > max(2, len(rectangles) // 3):
        return False, "repetitive_areas"
    signatures = {(rect.row, rect.col, rect.height, rect.width) for rect in rectangles}
    max_overlap_ratio = 0.0
    for _, _, transform in symmetry_transforms(rows, cols, include_transpose=False):
        if transform == "identity":
            continue
        transformed = set()
        for rect in rectangles:
            top_left = apply_transform(rows, cols, rect.row, rect.col, transform)
            bottom_right = apply_transform(
                rows,
                cols,
                rect.row + rect.height - 1,
                rect.col + rect.width - 1,
                transform,
            )
            min_row = min(top_left[0], bottom_right[0])
            min_col = min(top_left[1], bottom_right[1])
            max_row = max(top_left[0], bottom_right[0])
            max_col = max(top_left[1], bottom_right[1])
            transformed.add((min_row, min_col, max_row - min_row + 1, max_col - min_col + 1))
        overlap_ratio = len(signatures & transformed) / len(signatures)
        max_overlap_ratio = max(max_overlap_ratio, overlap_ratio)
    if max_overlap_ratio >= 0.8:
        return False, "too_symmetric"
    return True, "ok"


def generate_solution_tiling(
    rows: int, cols: int, settings: TierSettings, rng: random.Random
) -> tuple[list[Rectangle] | None, dict[str, Any]]:
    fill_attempts = 0
    while fill_attempts < 25:
        fill_attempts += 1
        base = fill_board_randomly(rows, cols, settings, rng)
        if base is None:
            continue
        mutated = mutate_tiling(rows, cols, base, settings, rng, rounds=max(4, rows))
        ok, reason = tiling_quality(rows, cols, mutated, settings)
        if ok:
            return mutated, {
                "fill_attempts": fill_attempts,
                "mutation_rounds": max(4, rows),
                "strip_count": sum(1 for rect in mutated if rect.is_strip),
                "rectangle_count": len(mutated),
            }
        if reason != "ok":
            continue
    return None, {"fill_attempts": fill_attempts}


def domains_from_candidates(candidates: Sequence[Sequence[CandidateRectangle]]) -> list[set[int]]:
    return [set(range(len(items))) for items in candidates]


def deep_copy_domains(domains: Sequence[set[int]]) -> list[set[int]]:
    return [set(values) for values in domains]


def fixed_assignment(domains: Sequence[set[int]]) -> dict[int, int]:
    return {index: next(iter(domain)) for index, domain in enumerate(domains) if len(domain) == 1}


def candidate_union_bitmask(clue_candidates: Sequence[CandidateRectangle], domain: set[int]) -> int:
    bitmask = 0
    for index in domain:
        bitmask |= clue_candidates[index].bitmask
    return bitmask


def region_components(rows: int, cols: int, bitmask: int) -> list[int]:
    visited = 0
    components: list[int] = []
    for row in range(rows):
        for col in range(cols):
            cell = cell_bit(rows, cols, row, col)
            if bitmask & cell == 0 or visited & cell:
                continue
            queue = deque([(row, col)])
            visited |= cell
            component = 0
            while queue:
                cur_row, cur_col = queue.popleft()
                cur_bit = cell_bit(rows, cols, cur_row, cur_col)
                component |= cur_bit
                for d_row, d_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    next_row = cur_row + d_row
                    next_col = cur_col + d_col
                    if not (0 <= next_row < rows and 0 <= next_col < cols):
                        continue
                    next_bit = cell_bit(rows, cols, next_row, next_col)
                    if bitmask & next_bit == 0 or visited & next_bit:
                        continue
                    visited |= next_bit
                    queue.append((next_row, next_col))
            components.append(component)
    return components


@dataclass(slots=True)
class PropagationResult:
    consistent: bool
    techniques: list[str]
    steps: int
    contradiction_eliminations: int
    singleton_creations: int


def propagate_domains(
    rows: int,
    cols: int,
    candidates: Sequence[Sequence[CandidateRectangle]],
    domains: list[set[int]],
    max_contradiction_rounds: int,
    max_contradiction_trials: int = 4,
) -> PropagationResult:
    techniques: list[str] = []
    steps = 0
    contradiction_eliminations = 0
    counted_singletons = {index for index, domain in enumerate(domains) if len(domain) == 1}
    singleton_creations = 0
    while True:
        changed = False
        assigned = fixed_assignment(domains)
        assigned_mask = 0
        for clue_index, candidate_index in assigned.items():
            assigned_mask |= candidates[clue_index][candidate_index].bitmask

        for clue_index, domain in enumerate(domains):
            if not domain:
                return PropagationResult(
                    False,
                    techniques,
                    steps,
                    contradiction_eliminations,
                    singleton_creations,
                )
            if len(domain) == 1:
                continue
            blocked_mask = assigned_mask
            for other_clue, candidate_index in assigned.items():
                if other_clue == clue_index:
                    blocked_mask ^= candidates[other_clue][candidate_index].bitmask
            invalid = {
                item
                for item in domain
                if candidates[clue_index][item].bitmask & blocked_mask
            }
            if invalid:
                domain.difference_update(invalid)
                techniques.append("overlap_elimination")
                steps += len(invalid)
                changed = True
                if not domain:
                    return PropagationResult(
                        False,
                        techniques,
                        steps,
                        contradiction_eliminations,
                        singleton_creations,
                    )

        unresolved = [index for index, domain in enumerate(domains) if len(domain) > 1]
        if unresolved:
            for clue_index in unresolved:
                if len(domains[clue_index]) == 1:
                    continue
                if len(domains[clue_index]) == 0:
                    return PropagationResult(
                        False,
                        techniques,
                        steps,
                        contradiction_eliminations,
                        singleton_creations,
                    )

        cell_to_options: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for clue_index, domain in enumerate(domains):
            for candidate_index in domain:
                candidate = candidates[clue_index][candidate_index]
                mask = candidate.bitmask
                while mask:
                    low_bit = mask & -mask
                    cell_to_options[low_bit].append((clue_index, candidate_index))
                    mask ^= low_bit

        for row in range(rows):
            for col in range(cols):
                bit = cell_bit(rows, cols, row, col)
                options = cell_to_options.get(bit, [])
                if not options:
                    return PropagationResult(
                        False,
                        techniques,
                        steps,
                        contradiction_eliminations,
                        singleton_creations,
                    )
                if len(options) == 1:
                    clue_index, candidate_index = options[0]
                    if len(domains[clue_index]) > 1:
                        domains[clue_index].intersection_update({candidate_index})
                        techniques.append("forced_cell_coverage")
                        steps += 1
                        changed = True

        possible_cover = 0
        for clue_index, domain in enumerate(domains):
            possible_cover |= candidate_union_bitmask(candidates[clue_index], domain)
        for component in region_components(rows, cols, possible_cover):
            clue_indexes = [
                clue_index
                for clue_index, domain in enumerate(domains)
                if candidate_union_bitmask(candidates[clue_index], domain) & component
            ]
            if not clue_indexes:
                return PropagationResult(
                    False,
                    techniques,
                    steps,
                    contradiction_eliminations,
                    singleton_creations,
                )
            min_area = sum(
                min(candidates[clue_index][candidate_index].rect.area for candidate_index in domains[clue_index])
                for clue_index in clue_indexes
            )
            if min_area > bit_count(component):
                return PropagationResult(
                    False,
                    techniques,
                    steps,
                    contradiction_eliminations,
                    singleton_creations,
                )
            exact_area = sum(candidates[clue_index][next(iter(domains[clue_index]))].rect.area for clue_index in clue_indexes if len(domains[clue_index]) == 1)
            if exact_area == bit_count(component) and exact_area:
                for clue_index in clue_indexes:
                    if len(domains[clue_index]) == 1:
                        continue
                    allowed = {
                        idx
                        for idx in domains[clue_index]
                        if candidates[clue_index][idx].bitmask & component == candidates[clue_index][idx].bitmask
                    }
                    if allowed and allowed != domains[clue_index]:
                        domains[clue_index].intersection_update(allowed)
                        techniques.append("region_completion")
                        steps += 1
                        changed = True

        newly_singletons = {
            index for index, domain in enumerate(domains) if len(domain) == 1
        } - counted_singletons
        if newly_singletons:
            counted_singletons.update(newly_singletons)
            singleton_creations += len(newly_singletons)
            techniques.append("single_candidate_clue")
            steps += len(newly_singletons)
            changed = True

        if not changed and max_contradiction_rounds > 0:
            contradiction_round_used = False
            target_clues = [index for index, domain in enumerate(domains) if len(domain) > 1]
            for clue_index in sorted(target_clues, key=lambda idx: len(domains[idx]))[:2]:
                eliminated: set[int] = set()
                domain_snapshot = sorted(domains[clue_index])[:max_contradiction_trials]
                for candidate_index in domain_snapshot:
                    trial = deep_copy_domains(domains)
                    trial[clue_index] = {candidate_index}
                    result = propagate_domains(
                        rows,
                        cols,
                        candidates,
                        trial,
                        max_contradiction_rounds=0,
                        max_contradiction_trials=0,
                    )
                    if not result.consistent:
                        eliminated.add(candidate_index)
                if eliminated:
                    domains[clue_index].difference_update(eliminated)
                    contradiction_eliminations += len(eliminated)
                    techniques.append("contradiction_reasoning")
                    steps += len(eliminated)
                    changed = True
                    contradiction_round_used = True
                    if not domains[clue_index]:
                        return PropagationResult(
                            False,
                            techniques,
                            steps,
                            contradiction_eliminations,
                            singleton_creations,
                        )
                    break
            if contradiction_round_used:
                continue

        if not changed:
            return PropagationResult(
                True,
                techniques,
                steps,
                contradiction_eliminations,
                singleton_creations,
            )


def select_next_clue(
    candidates: Sequence[Sequence[CandidateRectangle]],
    domains: Sequence[set[int]],
    occupied_mask: int,
) -> int:
    best_clue = -1
    best_score: tuple[int, int] | None = None
    for clue_index, domain in enumerate(domains):
        if len(domain) <= 1:
            continue
        viable = [
            candidate_index
            for candidate_index in domain
            if candidates[clue_index][candidate_index].bitmask & occupied_mask == 0
        ]
        score = (len(viable), len(domain))
        if best_score is None or score < best_score:
            best_score = score
            best_clue = clue_index
    return best_clue


def solve_unique(
    rows: int,
    cols: int,
    clues: Sequence[Clue],
    candidates: Sequence[Sequence[CandidateRectangle]] | None = None,
    solution_limit: int = 2,
) -> SolverResult:
    start = time.perf_counter()
    if sum(clue.area for clue in clues) != rows * cols:
        raise ValueError("Clue areas must sum to the full board area.")
    if candidates is None:
        candidates = enumerate_candidates(rows, cols, clues)
    domains = domains_from_candidates(candidates)
    solutions: list[list[Rectangle]] = []
    nodes = 0

    def search(local_domains: list[set[int]]) -> None:
        nonlocal nodes
        if len(solutions) >= solution_limit:
            return
        propagation = propagate_domains(
            rows,
            cols,
            candidates,
            local_domains,
            max_contradiction_rounds=1,
            max_contradiction_trials=3,
        )
        nodes += 1
        if not propagation.consistent:
            return
        assigned = fixed_assignment(local_domains)
        if len(assigned) == len(local_domains):
            solution = [candidates[clue_index][candidate_index].rect for clue_index, candidate_index in sorted(assigned.items())]
            solutions.append(solution)
            return
        occupied = 0
        for clue_index, candidate_index in assigned.items():
            occupied |= candidates[clue_index][candidate_index].bitmask
        clue_index = select_next_clue(candidates, local_domains, occupied)
        if clue_index < 0:
            return
        ordered_candidates = sorted(
            local_domains[clue_index],
            key=lambda item: (
                bit_count(candidates[clue_index][item].bitmask & occupied),
                candidates[clue_index][item].rect.row,
                candidates[clue_index][item].rect.col,
            ),
        )
        for candidate_index in ordered_candidates:
            if candidates[clue_index][candidate_index].bitmask & occupied:
                continue
            next_domains = deep_copy_domains(local_domains)
            next_domains[clue_index] = {candidate_index}
            search(next_domains)
            if len(solutions) >= solution_limit:
                return

    search(domains)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return SolverResult(
        solution_count=len(solutions),
        solutions=solutions,
        nodes=nodes,
        elapsed_ms=elapsed_ms,
    )


def grade_puzzle(
    rows: int,
    cols: int,
    clues: Sequence[Clue],
    settings_by_tier: dict[DifficultyTier, TierSettings],
) -> GradingMetrics:
    if sum(clue.area for clue in clues) != rows * cols:
        raise ValueError("Clue areas must sum to the full board area.")
    candidates = enumerate_candidates(rows, cols, clues)
    initial_counts = [len(items) for items in candidates]
    domains = domains_from_candidates(candidates)
    propagation = propagate_domains(
        rows,
        cols,
        candidates,
        domains,
        max_contradiction_rounds=1,
        max_contradiction_trials=6,
    )
    if not propagation.consistent:
        raise ValueError("Puzzle failed grading because it is inconsistent.")
    techniques_used = list(dict.fromkeys(propagation.techniques))
    steps = propagation.steps
    contradiction_eliminations = propagation.contradiction_eliminations
    branch_depth_reached = 0

    def branch(
        local_domains: list[set[int]],
        depth: int,
        max_depth: int,
        collect_metrics: bool,
    ) -> tuple[bool, int]:
        nonlocal steps
        propagation_result = propagate_domains(
            rows,
            cols,
            candidates,
            local_domains,
            max_contradiction_rounds=1,
            max_contradiction_trials=6,
        )
        if collect_metrics:
            techniques_used.extend(propagation_result.techniques)
            steps += propagation_result.steps
        if not propagation_result.consistent:
            return False, depth
        if all(len(domain) == 1 for domain in local_domains):
            return True, depth
        if depth >= max_depth:
            return False, depth
        occupied = 0
        for clue_index, candidate_index in fixed_assignment(local_domains).items():
            occupied |= candidates[clue_index][candidate_index].bitmask
        clue_index = select_next_clue(candidates, local_domains, occupied)
        if clue_index < 0:
            return False, depth
        if collect_metrics:
            techniques_used.append("shallow_branching")
        ordered = sorted(local_domains[clue_index])
        deepest = depth
        for candidate_index in ordered:
            next_domains = deep_copy_domains(local_domains)
            next_domains[clue_index] = {candidate_index}
            solved, child_depth = branch(next_domains, depth + 1, max_depth, collect_metrics)
            deepest = max(deepest, child_depth)
            if solved:
                return True, deepest
        return False, deepest

    def minimum_required_branch_depth() -> int | None:
        if all(len(domain) == 1 for domain in deep_copy_domains(domains)):
            return 0
        for depth_limit in range(0, settings_by_tier["expert"].max_branch_depth + 1):
            trial_domains = deep_copy_domains(domains)
            solved, _ = branch(trial_domains, 0, depth_limit, collect_metrics=False)
            if solved:
                return depth_limit
        return None

    solved_without_branching = all(len(domain) == 1 for domain in domains)
    branching_required = not solved_without_branching
    min_branch_depth = minimum_required_branch_depth()
    if min_branch_depth is None:
        branch_depth_reached = settings_by_tier["expert"].max_branch_depth + 1
    else:
        branch_depth_reached = min_branch_depth
        branching_required = min_branch_depth > 0
        if branching_required:
            solved, _ = branch(deep_copy_domains(domains), 0, min_branch_depth, collect_metrics=True)
            if not solved:
                branch_depth_reached = settings_by_tier["expert"].max_branch_depth + 1

    techniques = list(dict.fromkeys(techniques_used))
    avg_candidates = sum(initial_counts) / len(initial_counts)
    singleton_ratio = sum(1 for count in initial_counts if count == 1) / len(initial_counts)

    def tier_fit_distance(tier: DifficultyTier) -> float:
        settings = settings_by_tier[tier]
        distance = 0.0
        if len(clues) < settings.min_rectangles:
            distance += (settings.min_rectangles - len(clues)) * 0.5
        if len(clues) > settings.max_rectangles:
            distance += (len(clues) - settings.max_rectangles) * 0.5
        if avg_candidates < settings.min_average_candidates:
            distance += settings.min_average_candidates - avg_candidates
        if avg_candidates > settings.max_average_candidates:
            distance += avg_candidates - settings.max_average_candidates
        if singleton_ratio > settings.max_singletons_ratio:
            distance += (singleton_ratio - settings.max_singletons_ratio) * 4.0
        if steps < settings.min_steps:
            distance += (settings.min_steps - steps) * 0.2
        if settings.max_steps is not None and steps > settings.max_steps:
            distance += (steps - settings.max_steps) * 0.2
        if branch_depth_reached < settings.min_branch_depth:
            distance += (settings.min_branch_depth - branch_depth_reached) * 2.0
        if branch_depth_reached > settings.max_branch_depth:
            distance += (branch_depth_reached - settings.max_branch_depth) * 2.0
        if contradiction_eliminations < settings.min_contradiction_eliminations:
            distance += settings.min_contradiction_eliminations - contradiction_eliminations
        if (
            settings.max_contradiction_eliminations is not None
            and contradiction_eliminations > settings.max_contradiction_eliminations
        ):
            distance += contradiction_eliminations - settings.max_contradiction_eliminations
        if branching_required and not settings.allow_branching:
            distance += 3.0
        return distance

    def tier_center_distance(tier: DifficultyTier) -> float:
        settings = settings_by_tier[tier]
        avg_center = (settings.min_average_candidates + settings.max_average_candidates) / 2
        branch_center = (settings.min_branch_depth + settings.max_branch_depth) / 2
        contradiction_center = float(settings.min_contradiction_eliminations)
        if settings.max_steps is None:
            step_center = float(settings.min_steps + 8)
        else:
            step_center = (settings.min_steps + settings.max_steps) / 2
        return (
            abs(avg_candidates - avg_center)
            + abs(steps - step_center) * 0.12
            + abs(branch_depth_reached - branch_center) * 1.5
            + abs(contradiction_eliminations - contradiction_center) * 0.75
            + max(0.0, singleton_ratio - settings.max_singletons_ratio) * 2.0
        )

    matching_tiers: list[DifficultyTier] = []
    for tier in TIERS:
        settings = settings_by_tier[tier]
        if len(clues) < settings.min_rectangles or len(clues) > settings.max_rectangles:
            continue
        if avg_candidates < settings.min_average_candidates:
            continue
        if avg_candidates > settings.max_average_candidates:
            continue
        if singleton_ratio > settings.max_singletons_ratio:
            continue
        if steps < settings.min_steps:
            continue
        if settings.max_steps is not None and steps > settings.max_steps:
            continue
        if branch_depth_reached < settings.min_branch_depth:
            continue
        if contradiction_eliminations < settings.min_contradiction_eliminations:
            continue
        if (
            settings.max_contradiction_eliminations is not None
            and contradiction_eliminations > settings.max_contradiction_eliminations
        ):
            continue
        if branching_required and not settings.allow_branching:
            continue
        if branch_depth_reached > settings.max_branch_depth:
            continue
        matching_tiers.append(tier)
    if matching_tiers:
        assigned_tier = min(matching_tiers, key=tier_center_distance)
    else:
        assigned_tier = min(TIERS, key=tier_fit_distance)

    return GradingMetrics(
        techniques_used=techniques,
        steps=steps,
        branching_required=branching_required,
        max_branch_depth=branch_depth_reached,
        contradiction_eliminations=contradiction_eliminations,
        initial_candidate_counts=initial_counts,
        final_difficulty=assigned_tier,
    )


def render_ascii(rows: int, cols: int, clues: Sequence[Clue]) -> str:
    clue_map = {(clue.row, clue.col): f"{clue.area}{clue.shape[0].upper()}" for clue in clues}
    lines = []
    for row in range(rows):
        cells = []
        for col in range(cols):
            cells.append(f"{clue_map.get((row, col), '.'):>3}")
        lines.append(" ".join(cells))
    return "\n".join(lines)


def generate_candidate_puzzle(
    tier: DifficultyTier,
    rows: int,
    cols: int,
    seed: int,
    settings_by_tier: dict[DifficultyTier, TierSettings],
    include_transpose: bool,
) -> tuple[PuzzleRecord | None, str]:
    settings = settings_by_tier[tier]
    rng = random.Random(seed)
    solution_rectangles, metrics = generate_solution_tiling(rows, cols, settings, rng)
    if solution_rectangles is None:
        return None, "tiling_generation_failed"
    clues = [clue_from_rectangle(rect, settings.anchor_mode, rng) for rect in solution_rectangles]
    clues.sort(key=lambda clue: (clue.row, clue.col, clue.area, clue.shape))
    canonical_payload, canonical_hash = canonical_clue_layout(
        rows, cols, clues, include_transpose=include_transpose
    )
    candidates = enumerate_candidates(rows, cols, clues)
    candidate_counts = [len(items) for items in candidates]
    avg_candidates = sum(candidate_counts) / len(candidate_counts)
    if avg_candidates < 1.2:
        return None, "too_trivial"
    solver_result = solve_unique(rows, cols, clues, candidates=candidates, solution_limit=2)
    if solver_result.solution_count != 1:
        return None, "not_unique"
    grading = grade_puzzle(rows, cols, clues, settings_by_tier)
    if grading.final_difficulty != tier:
        return None, "wrong_difficulty"
    identifier = f"{tier}-{rows}x{cols}-{seed}"
    generation_metrics = {
        **metrics,
        "candidate_counts": candidate_counts,
        "average_candidates": avg_candidates,
        "solver_nodes": solver_result.nodes,
        "solver_elapsed_ms": round(solver_result.elapsed_ms, 3),
        "canonical_payload": canonical_payload,
        "ascii": render_ascii(rows, cols, clues),
    }
    return (
        PuzzleRecord(
            identifier=identifier,
            difficulty=tier,
            board_rows=rows,
            board_cols=cols,
            seed=seed,
            clues=clues,
            solution_rectangles=solution_rectangles,
            generation_metrics=generation_metrics,
            grading_metrics=grading,
            canonical_hash=canonical_hash,
        ),
        "accepted",
    )


def merge_json_config(
    config: GenerationConfig, settings: dict[DifficultyTier, TierSettings]
) -> tuple[GenerationConfig, dict[DifficultyTier, TierSettings]]:
    if config.config_path is None:
        return config, settings
    payload = json.loads(config.config_path.read_text(encoding="utf-8"))
    for tier, counts in payload.get("counts", {}).items():
        if tier in config.counts:
            config.counts[tier] = int(counts)
    if "max_attempts_per_tier" in payload:
        config.max_attempts_per_tier = int(payload["max_attempts_per_tier"])
    if "transposition_aware" in payload:
        config.transposition_aware = bool(payload["transposition_aware"])
    tier_payload = payload.get("tier_settings", {})
    for tier, overrides in tier_payload.items():
        if tier not in settings:
            continue
        current = settings[tier]
        current.board_sizes = tuple(
            tuple(item) for item in overrides.get("board_sizes", current.board_sizes)
        )
        current.strip_max_length = int(
            overrides.get("strip_max_length", current.strip_max_length)
        )
        current.strip_max_ratio = float(
            overrides.get("strip_max_ratio", current.strip_max_ratio)
        )
        current.anchor_mode = str(overrides.get("anchor_mode", current.anchor_mode))
        current.allow_branching = bool(
            overrides.get("allow_branching", current.allow_branching)
        )
        current.max_branch_depth = int(
            overrides.get("max_branch_depth", current.max_branch_depth)
        )
        current.min_branch_depth = int(
            overrides.get("min_branch_depth", current.min_branch_depth)
        )
        current.min_rectangles = int(
            overrides.get("min_rectangles", current.min_rectangles)
        )
        current.max_rectangles = int(
            overrides.get("max_rectangles", current.max_rectangles)
        )
        current.min_average_candidates = float(
            overrides.get("min_average_candidates", current.min_average_candidates)
        )
        current.max_average_candidates = float(
            overrides.get("max_average_candidates", current.max_average_candidates)
        )
        current.max_singletons_ratio = float(
            overrides.get("max_singletons_ratio", current.max_singletons_ratio)
        )
        current.min_steps = int(overrides.get("min_steps", current.min_steps))
        max_steps_override = overrides.get("max_steps", current.max_steps)
        current.max_steps = None if max_steps_override is None else int(max_steps_override)
        current.min_contradiction_eliminations = int(
            overrides.get(
                "min_contradiction_eliminations", current.min_contradiction_eliminations
            )
        )
        current.max_contradiction_eliminations = overrides.get(
            "max_contradiction_eliminations", current.max_contradiction_eliminations
        )
    return config, settings


def parse_counts(raw_values: Sequence[str] | None, count_per_tier: int | None) -> dict[DifficultyTier, int]:
    counts = {tier: 0 for tier in TIERS}
    if count_per_tier is not None:
        for tier in TIERS:
            counts[tier] = count_per_tier
    if raw_values:
        for item in raw_values:
            tier, raw_count = item.split("=", 1)
            tier = tier.strip().lower()
            if tier not in counts:
                raise ValueError(f"Unknown tier in --counts: {tier}")
            counts[tier] = int(raw_count)
    if all(count == 0 for count in counts.values()):
        raise ValueError("At least one tier count must be greater than zero.")
    return counts


def parse_board_override(raw_items: Sequence[str] | None) -> dict[DifficultyTier, tuple[tuple[int, int], ...]]:
    overrides: dict[DifficultyTier, tuple[tuple[int, int], ...]] = {}
    if not raw_items:
        return overrides
    parsed: dict[DifficultyTier, list[tuple[int, int]]] = defaultdict(list)
    for item in raw_items:
        tier, size_token = item.split("=", 1)
        row_token, col_token = size_token.lower().split("x", 1)
        parsed[tier.lower()].append((int(row_token), int(col_token)))
    for tier, items in parsed.items():
        if tier not in TIERS:
            raise ValueError(f"Unknown tier in --board-size: {tier}")
        overrides[tier] = tuple(items)
    return overrides


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", nargs="*", help="Per-tier counts like easy=25 medium=10")
    parser.add_argument("--count-per-tier", type=int, help="Shared count for every tier")
    parser.add_argument("--out", required=False, default="./generated", help="Output directory")
    parser.add_argument("--seed", type=int, default=12345, help="Deterministic base seed")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3000,
        help="Max generation attempts per tier",
    )
    parser.add_argument(
        "--board-size",
        action="append",
        help="Override board sizes, e.g. easy=5x5",
    )
    parser.add_argument("--config", help="Optional JSON config path")
    parser.add_argument("--per-puzzle-json", action="store_true", help="Write one JSON file per puzzle")
    parser.add_argument("--no-summary-csv", action="store_true", help="Disable summary CSV")
    parser.add_argument("--global-dedup", action="store_true", help="Deduplicate across all tiers")
    parser.add_argument(
        "--no-transpose-dedup",
        action="store_true",
        help="Disable transposition-aware canonicalization for rectangular boards",
    )
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests and exit")
    parser.add_argument("--workers", type=int, default=1, help="Reserved; current implementation is single-process")
    return parser


def summarize_rejections(rejections: Counter[str]) -> str:
    if not rejections:
        return "none"
    return ", ".join(f"{reason}={count}" for reason, count in sorted(rejections.items()))


def write_outputs(
    records_by_tier: dict[DifficultyTier, list[PuzzleRecord]],
    config: GenerationConfig,
) -> None:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    all_records = [record for tier in TIERS for record in records_by_tier[tier]]
    main_payload = {
        "meta": {
            "generated_at_epoch": int(time.time()),
            "seed": config.random_seed,
            "counts": {tier: len(records_by_tier[tier]) for tier in TIERS},
            "transposition_aware": config.transposition_aware,
        },
        "puzzles": [record.to_dict() for record in all_records],
    }
    (output_dir / "puzzles.json").write_text(
        json.dumps(main_payload, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    if config.per_puzzle_json:
        for record in all_records:
            tier_dir = output_dir / record.difficulty
            tier_dir.mkdir(parents=True, exist_ok=True)
            (tier_dir / f"{record.identifier}.json").write_text(
                json.dumps(record.to_dict(), indent=2),
                encoding="utf-8",
            )
    if config.summary_csv:
        with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "id",
                    "difficulty",
                    "board_rows",
                    "board_cols",
                    "seed",
                    "canonical_hash",
                    "steps",
                    "branching_required",
                    "max_branch_depth",
                    "solver_nodes",
                ],
            )
            writer.writeheader()
            for record in all_records:
                writer.writerow(
                    {
                        "id": record.identifier,
                        "difficulty": record.difficulty,
                        "board_rows": record.board_rows,
                        "board_cols": record.board_cols,
                        "seed": record.seed,
                        "canonical_hash": record.canonical_hash,
                        "steps": record.grading_metrics.steps,
                        "branching_required": record.grading_metrics.branching_required,
                        "max_branch_depth": record.grading_metrics.max_branch_depth,
                        "solver_nodes": record.generation_metrics["solver_nodes"],
                    }
                )


def print_summary(
    records_by_tier: dict[DifficultyTier, list[PuzzleRecord]],
    attempts_by_tier: dict[DifficultyTier, int],
    rejection_by_tier: dict[DifficultyTier, Counter[str]],
    started_at: float,
) -> None:
    elapsed = time.perf_counter() - started_at
    print("\nSummary")
    print("tier     accepted  attempts  rejection_breakdown")
    for tier in TIERS:
        print(
            f"{tier:<8} {len(records_by_tier[tier]):>8}  {attempts_by_tier[tier]:>8}  "
            f"{summarize_rejections(rejection_by_tier[tier])}"
        )
    print(f"elapsed_seconds={elapsed:.2f}")


def run_self_test() -> int:
    assert classify_shape(2, 2) == "square"
    assert classify_shape(4, 2) == "wide"
    assert classify_shape(2, 4) == "tall"

    clues = [Clue(0, 0, 4, "square")]
    candidates = enumerate_candidates(3, 3, clues)
    candidate_rects = {(item.rect.row, item.rect.col, item.rect.height, item.rect.width) for item in candidates[0]}
    assert candidate_rects == {
        (0, 0, 2, 2),
    }

    square_clues = [Clue(0, 1, 6, "wide"), Clue(2, 2, 4, "square")]
    _, hash_a = canonical_clue_layout(4, 4, square_clues, include_transpose=True)
    rotated = [Clue(1, 3, 6, "tall"), Clue(2, 1, 4, "square")]
    _, hash_b = canonical_clue_layout(4, 4, rotated, include_transpose=True)
    assert hash_a == hash_b

    rect_clues = [Clue(0, 1, 6, "wide"), Clue(1, 3, 2, "tall")]
    _, hash_c = canonical_clue_layout(3, 5, rect_clues, include_transpose=True)
    transposed = [Clue(1, 0, 6, "tall"), Clue(3, 1, 2, "wide")]
    _, hash_d = canonical_clue_layout(5, 3, transposed, include_transpose=True)
    assert hash_c == hash_d

    unique_clues = [Clue(0, 0, 4, "square"), Clue(0, 2, 2, "tall"), Clue(2, 1, 3, "wide")]
    solver = solve_unique(3, 3, unique_clues)
    assert solver.solution_count == 1

    serialized = PuzzleRecord(
        identifier="test",
        difficulty="easy",
        board_rows=3,
        board_cols=3,
        seed=1,
        clues=unique_clues,
        solution_rectangles=[
            Rectangle(0, 0, 2, 2),
            Rectangle(0, 2, 2, 1),
            Rectangle(2, 0, 1, 3),
        ],
        generation_metrics={"solver_nodes": 1},
        grading_metrics=GradingMetrics(
            techniques_used=["single_candidate_clue"],
            steps=1,
            branching_required=False,
            max_branch_depth=0,
            contradiction_eliminations=0,
            initial_candidate_counts=[1, 1],
            final_difficulty="easy",
        ),
        canonical_hash="abc",
    )
    payload = serialized.to_dict()
    assert payload["id"] == "test"
    assert payload["clues"][0]["area"] == 4
    print("self-test passed")
    return 0


def generate_batch(config: GenerationConfig) -> int:
    settings_by_tier = {
        tier: TierSettings(**asdict(DEFAULT_TIER_SETTINGS[tier])) for tier in TIERS
    }
    if config.size_overrides:
        for tier, board_sizes in config.size_overrides.items():
            settings_by_tier[tier].board_sizes = board_sizes
    config, settings_by_tier = merge_json_config(config, settings_by_tier)
    records_by_tier: dict[DifficultyTier, list[PuzzleRecord]] = {tier: [] for tier in TIERS}
    attempts_by_tier: dict[DifficultyTier, int] = {tier: 0 for tier in TIERS}
    rejection_by_tier: dict[DifficultyTier, Counter[str]] = {tier: Counter() for tier in TIERS}
    global_hashes: set[str] = set()
    started_at = time.perf_counter()

    for tier_index, tier in enumerate(TIERS):
        target = config.counts[tier]
        if target <= 0:
            continue
        tier_rng = random.Random(config.random_seed + tier_index * 1_000_003)
        tier_hashes: set[str] = set()
        print(f"Generating tier={tier} target={target}")
        while len(records_by_tier[tier]) < target and attempts_by_tier[tier] < config.max_attempts_per_tier:
            attempts_by_tier[tier] += 1
            seed = tier_rng.randrange(1, 2**63)
            rows, cols = tier_rng.choice(settings_by_tier[tier].board_sizes)
            record, reason = generate_candidate_puzzle(
                tier=tier,
                rows=rows,
                cols=cols,
                seed=seed,
                settings_by_tier=settings_by_tier,
                include_transpose=config.transposition_aware,
            )
            if record is None:
                rejection_by_tier[tier][reason] += 1
            else:
                if record.canonical_hash in tier_hashes:
                    rejection_by_tier[tier]["duplicate"] += 1
                    continue
                if config.global_dedup and record.canonical_hash in global_hashes:
                    rejection_by_tier[tier]["global_duplicate"] += 1
                    continue
                tier_hashes.add(record.canonical_hash)
                global_hashes.add(record.canonical_hash)
                records_by_tier[tier].append(record)
                print(
                    f"  accepted {len(records_by_tier[tier])}/{target} "
                    f"attempt={attempts_by_tier[tier]} "
                    f"seed={seed} size={rows}x{cols}"
                )
            if attempts_by_tier[tier] % 50 == 0:
                print(
                    f"  progress tier={tier} accepted={len(records_by_tier[tier])}/{target} "
                    f"attempts={attempts_by_tier[tier]} rejections={summarize_rejections(rejection_by_tier[tier])}"
                )
        if len(records_by_tier[tier]) < target:
            print_summary(records_by_tier, attempts_by_tier, rejection_by_tier, started_at)
            print(
                f"Failed tier={tier}: accepted={len(records_by_tier[tier])} "
                f"target={target} attempts={attempts_by_tier[tier]} "
                f"rejections={summarize_rejections(rejection_by_tier[tier])}",
                file=sys.stderr,
            )
            return 1

    write_outputs(records_by_tier, config)
    print_summary(records_by_tier, attempts_by_tier, rejection_by_tier, started_at)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.workers != 1:
        print("workers>1 is reserved; current implementation runs single-process.", file=sys.stderr)
    counts = parse_counts(args.counts, args.count_per_tier)
    config = GenerationConfig(
        counts=counts,
        output_dir=Path(args.out),
        random_seed=args.seed,
        max_attempts_per_tier=args.max_attempts,
        transposition_aware=not args.no_transpose_dedup,
        per_puzzle_json=args.per_puzzle_json,
        summary_csv=not args.no_summary_csv,
        global_dedup=args.global_dedup,
        size_overrides=parse_board_override(args.board_size),
        config_path=Path(args.config) if args.config else None,
    )
    return generate_batch(config)


if __name__ == "__main__":
    raise SystemExit(main())
