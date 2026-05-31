from typing import Any
import numpy as np

species_names: list[str]
move_names: list[str]

def species_id(number: int) -> str: ...
def move_id(number: int) -> str: ...
def id_to_species(species_id: str) -> int: ...
def id_to_move(move_id: str) -> int: ...

class Battle:
    turn: int
    last_damage: int
    rng: int
    def __init__(self, data: bytes = ...) -> None: ...
    def side(self, i: int) -> Side: ...
    def bytes(self) -> bytes: ...

class Durations:
    def __init__(self, data: bytes = ...) -> None: ...
    def get(self, side: int) -> Duration: ...
    def bytes(self) -> bytes: ...

class Duration:
    confusion: int
    disable: int
    attacking: int
    binding: int
    raw: int
    def sleep(self, slot: int) -> int: ...
    def set_sleep(self, slot: int, value: int) -> None: ...

class Side:
    order: list[int]
    last_selected_move: int
    last_used_move: int
    def pokemon(self, i: int) -> Pokemon: ...
    def active(self) -> ActivePokemon: ...
    def stored(self) -> Pokemon: ...
    def slot(self, s: int) -> Pokemon: ...

class Pokemon:
    species: int
    level: int
    hp: int
    status: int
    types: int
    def move(self, i: int) -> MoveSlot: ...
    def stats(self) -> Stats: ...
    def species_name(self) -> str: ...
    def status_name(self) -> str: ...
    def percent(self) -> int: ...

class ActivePokemon(Pokemon):
    def boosts(self) -> Boosts: ...
    def volatiles(self) -> Volatiles: ...

class MoveSlot:
    id: int
    pp: int
    def name(self) -> str: ...

class Stats:
    hp: int
    atk: int
    def_: int
    spe: int
    spc: int

class Boosts:
    atk: int
    def_: int
    spe: int
    spc: int
    acc: int
    eva: int
    raw: int

class Volatiles:
    bide: bool
    thrashing: bool
    multi_hit: bool
    flinch: bool
    charging: bool
    binding: bool
    invulnerable: bool
    confusion: bool
    mist: bool
    focus_energy: bool
    substitute: bool
    recharging: bool
    rage: bool
    leech_seed: bool
    toxic: bool
    light_screen: bool
    reflect: bool
    transform: bool
    confusion_left: int
    attacks: int
    disable_left: int
    substitute_hp: int
    transform_species: int
    disable_move: int
    toxic_counter: int
    bits: int
    state: int

class Output:
    p1_empirical: np.ndarray  # shape (9,)
    p2_empirical: np.ndarray  # shape (9,)
    p1_prior: np.ndarray  # shape (9,)
    p2_prior: np.ndarray  # shape (9,)
    p1_nash: np.ndarray  # shape (9,)
    p2_nash: np.ndarray  # shape (9,)
    visit_matrix: np.ndarray  # shape (9, 9)
    value_matrix: np.ndarray  # shape (9, 9)
    empirical_value: float
    nash_value: float
    iterations: int
    duration_ms: int

class Heap:
    def empty(self) -> bool: ...
    def type(self) -> str: ...

class Agent:
    budget: str
    bandit: str
    eval: str
    discrete: bool
    matrix_ucb: bool
    table: bool

class Set:
    species: int
    level: int
    moves: list[int]

def search(
    battle: Battle,
    durations: Durations,
    result: int,
    heap: Heap,
    agent: Agent,
    output: Output = ...,
) -> Output: ...
def parse_battle(
    battle_string: str, seed: int = ...
) -> tuple[Battle, Durations, int]: ...
def update(battle: Battle, durations: Durations, c1: int, c2: int) -> int: ...
def battle_string(battle: Battle, durations: Durations) -> str: ...
def format(battle: Battle, durations: Durations, output: Output) -> str: ...
def solve_matrix(
    row_payoff: np.ndarray, discretize_factor: int = ...
) -> tuple[np.ndarray, np.ndarray, float]: ...
def load_teams(path: str) -> list[list[Set]]: ...
def complete_pokemon_from_set(pokemon: Pokemon, set: Set) -> None: ...
