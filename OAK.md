# TODO update this from the cloaned oak REPO
# Pay attention to Output api
# aND the fact that we remove Input in favor of component battle, durations, and result

# Oak API Context

Oak is a Gen 1 Pokemon battle engine exposed as a Python extension (`import oak`).

## Core Types

### `oak.Battle(bytes(384))`
Ground-truth battle state buffer.
- `.side(i: int) -> SideProxy` — 0 = us (p1), 1 = opponent (p2)
- `.turn: int`

### `oak.Durations(bytes(8))`
Tracks multi-turn move durations.

### `SideProxy`
- `.pokemon(i: int) -> PokemonProxy` — storage slot 0–5 (in order revealed)
- `.slot(s: int) -> PokemonProxy` — 1-indexed slot using order[]
- `.active() -> ActivePokemonProxy`
- `.stored() -> PokemonProxy` — currently stored (non-active) lead
- `.order: list[int]` — maps slot position to storage index; active is order[0]

### `PokemonProxy`
- `.species: int` — index into `oak.species_names`
- `.level: int`
- `.hp: int`
- `.status: int` — bitmask (brn=0x10, frz=0x20, par=0x40, psn/tox=0x08, slp=0x04|turns)
- `.types: int`
- `.move(i: int) -> MoveSlotProxy` — i in 0–3
- `.stats() -> StatsProxy`
- `.species_name() -> str`
- `.status_name() -> str`
- `.percent() -> float`

### `ActivePokemonProxy`
Same as PokemonProxy plus:
- `.boosts() -> BoostsProxy`
- `.volatiles() -> VolatilesProxy`

### `MoveSlotProxy`
- `.id: int` — index into `oak.move_names`
- `.pp: int`
- `.name() -> str`

### `StatsProxy`
- `.hp, .atk, .def_, .spe, .spc: int`

### `BoostsProxy`
- `.atk, .def_, .spe, .spc, .acc, .eva: int` — range [-6, 6]
- `.raw: int`

### `VolatilesProxy`
Booleans: `.bide, .thrashing, .multi_hit, .flinch, .charging, .binding, .invulnerable, .confusion, .mist, .focus_energy, .substitute, .recharging, .rage, .leech_seed, .toxic, .light_screen, .reflect, .transform`
Counts: `.confusion_left, .attacks, .disable_left, .substitute_hp, .transform_species, .disable_move, .toxic_counter`
- `.bits: int` — raw bitmask (set to 0 to clear all)
- `.state: int`

## Search

### `oak.search(battle, durations, heap, agent) -> oak.Output`
Runs MCTS. Returns Output with:
- `.p1_empirical: list[float]` — length 9, empirical visit frequencies for each action

### `oak.Heap()` / `oak.Agent()`
- `agent.budget: str` — e.g. `"500"` (ms)
- `agent.bandit: str` — e.g. `"pexp3-1.0-0.1"`
- `agent.eval: str` — e.g. `"fp"`

## Global Data
- `oak.species_names: list[str]` — index → species name
- `oak.move_names: list[str]` — index → move name

## Team Loading
- `oak.load_teams(path: str) -> list[list[oak.Set]]`
- `oak.Set` — a complete Pokemon set with moves list
