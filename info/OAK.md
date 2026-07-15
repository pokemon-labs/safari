# Oak API Context

Oak is a Gen 1 Pokemon battle engine exposed as a set of Python extensions. The
core module is `oak` (`import oak`); a few features live in submodules that
must be imported explicitly: `oak.search` and `oak.log`. (There is also
`oak.train`, used for the NN training pipeline — out of scope for this doc.)

## Module Constants
- `oak.BATTLE_SIZE: int` — 384, the byte size of a `Battle`
- `oak.DURATIONS_SIZE: int` — 8, the byte size of `Durations`

## Core Types

### `oak.Battle(bytes(384))`
Ground-truth battle state buffer.
- `.side(i: int) -> SideProxy` — 0 = us (p1), 1 = opponent (p2)
- `.last_move(i: int) -> MoveDetails` — last move used by player 0 or 1
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
- `.species: int` — Species enum value as uint8 (use `oak.species_id()` / `.species_name()` for the name)
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
- `.id: int` — Move enum value as uint8
- `.pp: int`
- `.name() -> str`

### `MoveDetails` (from `battle.last_move(i)`)
- `.index: int` — the move's id (uint8)
- `.counterable: int` — whether this move can be hit back by Counter (uint8, 0/1)

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

Search lives in its own extension module — `import oak.search` in addition to
`import oak` before using anything below.

### `oak.search.search(battle, durations, result, heap, agent, output=oak.search.Output()) -> oak.search.Output`

```python
import oak
import oak.search

battle, durations, result = oak.parse_battle(battle_string)
heap = oak.search.Heap()
agent = oak.search.Agent()
agent.budget = "500"
agent.bandit = "pexp3-1.0-0.1"
agent.eval = "fp"
output = oak.search.search(battle, durations, result, heap, agent)
```

### Output fields (`oak.search.Output`)
- `.m: int` — number of legal p1 actions (k)
- `.n: int` — number of legal p2 actions (k)
- `.p1_empirical: np.ndarray` — shape (9,), empirical visit frequencies for p1 actions
- `.p2_empirical: np.ndarray` — shape (9,)
- `.p1_prior: np.ndarray` — shape (9,)
- `.p2_prior: np.ndarray` — shape (9,)
- `.p1_nash: np.ndarray` — shape (9,)
- `.p2_nash: np.ndarray` — shape (9,)
- `.visit_matrix: np.ndarray` — shape (9, 9), joint visit counts
- `.value_matrix: np.ndarray` — shape (9, 9)
- `.empirical_matrix: np.ndarray` — shape (9, 9), `value_matrix / visit_matrix` (0.5 where unvisited)
- `.empirical_value: float`
- `.nash_value: float`
- `.iterations: int`
- `.duration_ms: int`

### `oak.search.Heap()` / `oak.search.Agent()`
- `heap.empty() -> bool`
- `heap.type() -> str` — which bandit variant is currently active (empty string if none)
- `agent.budget: str` — e.g. `"500"` (ms)
- `agent.bandit: str` — e.g. `"pexp3-1.0-0.1"`
- `agent.eval: str` — e.g. `"fp"`
- `agent.discrete: bool`
- `agent.matrix_ucb: str`
- `agent.table: bool`

### Other search-module functions
- `oak.search.search_mp(battle_bytes: bytes, durations_bytes: bytes, budget: str, bandit: str, eval: str, matrix_ucb: str = "") -> dict` — bytes-in/bytes-out variant of `search`, returns a plain dict (`m`, `n`, `visit_matrix`, `value_matrix`, `iterations`, `duration`, `empirical_value`, `nash_value`, `p1_choices`, `p2_choices`, `p1_empirical`, `p2_empirical`, `p1_nash`, `p2_nash`, `p1_prior`, `p2_prior`)
- `oak.search.output_string(battle, durations, result: int, output: oak.search.Output) -> str` — human-readable rendering of a search result (formerly `oak.format`)
- `oak.search.cpp_inference(battle_frames, network_path: str, discrete: bool, budget: str) -> OutputBuffer` — batched network inference over `oak.train.BattleFrames`; training-pipeline internal, not typically needed for lab exploration

## Global Data
- `oak.species_id(number: int) -> str` — species index → string id
- `oak.move_id(number: int) -> str` — move index → string id
- `oak.id_to_species(species_id: str) -> int`
- `oak.id_to_move(move_id: str) -> int`

Note: there is no longer a `species_names`/`move_names` list exposed on the
module — look up names one at a time via `species_id`/`move_id`, or use
`PokemonProxy.species_name()` / `MoveSlotProxy.name()` when you already have a
proxy in hand.

## Team Loading
- `oak.load_teams(path: str) -> list[list[oak.Set]]`
- `oak.Set` — `.species: int`, `.level: int`, `.moves: list[int]` (4 move ids)

## Other Utilities
- `oak.parse_battle(battle_string: str, seed: int = 0x123456) -> (Battle, Durations, int)`
- `oak.update(battle, durations, c1, c2) -> int` — advance battle state, returns result
- `oak.solve_matrix(row_payoff: np.ndarray, discretize_factor: int) -> (p1_nash, p2_nash, value)`
- `oak.battle_string(battle, durations) -> str`
- `oak.choices(battle, result: int) -> (list[int], list[int])` — legal choices for (p1, p2)
- `oak.result_type(result: int) -> int`
- `oak.choice_label(side: SideProxy, choice: int) -> str` — human-readable label for a choice
- `oak.switch_in(pokemon: PokemonProxy, active: ActivePokemonProxy) -> None` — initializes `active` in place from a stored `pokemon`
- `oak.move_data(move: int) -> dict` — `{effect, bp, type, accuracy}`
- `oak.get_effectiveness(attacking_type: int, defending_type: int) -> int`

Note: `oak.format` no longer exists — use `oak.search.output_string` instead
(see Search section above).

### Team / Set Helpers
- `oak.stats(species: int, level: int = 100, EVs: list[int] = [255]*5, DVs: list[int] = [15]*5) -> list[int]` — computes `[hp, atk, def, spe, spc]`
- `oak.complete_pokemon_from_set(pokemon: PokemonProxy, set: oak.Set) -> None` — fully initializes an empty/partial Pokemon slot (species, level, stats, types, moves) from a `Set`, only filling fields that are still zero
- `oak.complete_pokemon_moves(pokemon: PokemonProxy, set: oak.Set) -> None` — fills empty move slots from a `Set`
- `oak.complete_active_moves(active: ActivePokemonProxy, set: oak.Set) -> None` — same, for an `ActivePokemon`
- `oak.copy_moves_to_active(pokemon: PokemonProxy, active: ActivePokemonProxy) -> None`
- `oak.fill_random_moveset(set: oak.Set, seed: int = 0) -> None` — fills any empty move slots in `set` with randomly sampled legal OU moves (seed 0 = random device)

## Logging

Logging lives in its own extension module — `import oak.log` in addition to
`import oak`.

- `oak.log.update(battle, durations, c1, c2, view: int) -> (int, list[str])` — like `oak.update`, but also returns the parsed protocol log lines. `view`: 0 = omniscient, 1 = p1, 2 = p2.
- `oak.log.compare_battles(public_battle, public_durations, truth_battle, truth_durations, key_battle, key_durations) -> (bool, str)` — checks whether a public (obfuscated) battle view is consistent with the ground-truth battle given a key; returns `(matches, reason)`.
