from typing import Any

species_names: list[str]
move_names: list[str]

class Battle:
    turn: int
    def __init__(self, data: bytes) -> None: ...
    def side(self, i: int) -> SideProxy: ...

class Durations:
    def __init__(self, data: bytes) -> None: ...

class SideProxy:
    order: list[int]
    def pokemon(self, i: int) -> PokemonProxy: ...
    def active(self) -> ActivePokemonProxy: ...
    def stored(self) -> PokemonProxy: ...
    def slot(self, s: int) -> PokemonProxy: ...

class PokemonProxy:
    species: int
    level: int
    hp: int
    status: int
    types: int
    def move(self, i: int) -> MoveSlotProxy: ...
    def stats(self) -> StatsProxy: ...
    def species_name(self) -> str: ...
    def status_name(self) -> str: ...
    def percent(self) -> float: ...

class ActivePokemonProxy(PokemonProxy):
    def boosts(self) -> BoostsProxy: ...
    def volatiles(self) -> VolatilesProxy: ...

class MoveSlotProxy:
    id: int
    pp: int
    def name(self) -> str: ...

class StatsProxy:
    hp: int
    atk: int
    def_: int
    spe: int
    spc: int

class BoostsProxy:
    atk: int
    def_: int
    spe: int
    spc: int
    acc: int
    eva: int
    raw: int

class VolatilesProxy:
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
    p1_empirical: list[float]

class Heap: ...

class Agent:
    budget: str
    bandit: str
    eval: str

class Set:
    species: int
    level: int
    moves: list[int]

def search(
    battle: Battle, durations: Durations, heap: Heap, agent: Agent
) -> Output: ...
def load_teams(path: str) -> list[list[Set]]: ...
