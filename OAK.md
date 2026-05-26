# Oak API Context

Oak is a Gen 1 Pokemon battle engine exposed as a Python extension (`import oak`).

## Core Types

### `oak.Battle(bytes(384))`
Ground-truth battle state buffer.
- `.side(i: int) -> SideProxy` — 0 = us (p1), 1 = opponent (p2)
- `.turn: int`
- `.last_damage: int`
- `.rng: int`
- `.bytes() -> bytes`

### `oak.Durations(bytes(8))`
Tracks multi-turn move durations.
- `.get(side: int) -> DurationProxy` — side 0 or 1
- `.bytes() -> bytes`

### `SideProxy`
- `.pokemon(i: int) -> PokemonProxy` — storage slot 0–5 (in order revealed)
- `.slot(s: int) -> PokemonProxy` — 1-indexed slot using order[]
- `.active -> ActivePokemonProxy`
- `.stored() -> PokemonProxy` — currently stored (non-active) lead
- `.order: list[int]` — maps slot position to storage index; active is order[0]
- `.last_selected_move: int`
- `.last_used_move: int`

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
- `.percent() -> int`

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
- `.bits: int` — raw bitmask
- `.state: int`

## Search

### `oak.search(battle, durations, result, heap, agent) -> oak.Output`

```python
import oak
battle, durations, result = oak.parse_battle(battle_string)
heap = oak.Heap()
agent = oak.Agent()
agent.budget = "500"
agent.bandit = "pexp3-1.0-0.1"
agent.eval = "fp"
output = oak.search(battle, durations, result, heap, agent)
```

### Output fields (`oak.Output`)
- `.p1_empirical: np.ndarray` — shape (9,), empirical visit frequencies for p1 actions
- `.p2_empirical: np.ndarray` — shape (9,)
- `.p1_prior: np.ndarray` — shape (9,)
- `.p2_prior: np.ndarray` — shape (9,)
- `.p1_nash: np.ndarray` — shape (9,)
- `.p2_nash: np.ndarray` — shape (9,)
- `.visit_matrix: np.ndarray` — shape (9, 9), joint visit counts
- `.value_matrix: np.ndarray` — shape (9, 9)
- `.empirical_value: float`
- `.nash_value: float`
- `.iterations: int`
- `.duration_ms: int`

### `oak.Heap()` / `oak.Agent()`
- `agent.budget: str` — e.g. `"500"` (ms)
- `agent.bandit: str` — e.g. `"pexp3-1.0-0.1"`
- `agent.eval: str` — e.g. `"fp"`
- `agent.discrete: bool`
- `agent.matrix_ucb: bool`
- `agent.table: bool`

## Global Data
- `oak.species_names: list[str]` — index → species name
- `oak.move_names: list[str]` — index → move name
- `oak.species_id(number: int) -> str` — species index → string id
- `oak.move_id(number: int) -> str` — move index → string id
- `oak.id_to_species(species_id: str) -> int`
- `oak.id_to_move(move_id: str) -> int`

## Team Loading
- `oak.load_teams(path: str) -> list[list[oak.Set]]`
- `oak.Set` — `.species: int`, `.level: int`, `.moves: list[int]` (4 move ids)

## Other Utilities
- `oak.parse_battle(battle_string: str, seed: int = 0x123456) -> (Battle, Durations, int)`
- `oak.update(battle, durations, c1, c2) -> int` — advance battle state, returns result
- `oak.solve_matrix(row_payoff: np.ndarray, discretize_factor: int) -> (p1_nash, p2_nash, value)`
- `oak.battle_string(battle, durations) -> str`
- `oak.format(battle, durations, output) -> str`
