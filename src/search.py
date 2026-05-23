from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import numpy as np

import oak
from config import FoulPlayConfig
from src.battle import Battle
from src.teams import TeamPredictor, _species_id, _move_id

logger = logging.getLogger(__name__)

_predictor: TeamPredictor | None = None


def _get_predictor() -> TeamPredictor:
    global _predictor
    if _predictor is None:
        _predictor = TeamPredictor("teams150")
    return _predictor


def _fill_opponent(battle: Battle, det: oak.Battle) -> None:
    """Write predicted opponent sets into det.side(1) for unknown slots."""
    pred = _get_predictor()
    n = battle.p2.pokemon
    side1 = det.side(1)

    known_species: list[str] = []
    for i in range(n):
        sp = side1.pokemon(i).species
        if sp != 0:
            from src.helpers import normalize_name
            known_species.append(normalize_name(oak.species_names[sp]))

    candidates = [
        t for t in pred.teams
        if all(k in t.species_set() for k in known_species)
    ]
    if not candidates:
        candidates = pred.teams
    chosen = random.choice(candidates)

    slot = len(known_species)
    for pset in chosen:
        if slot >= n:
            break
        from src.helpers import normalize_name
        if pset.species in {normalize_name(oak.species_names[side1.pokemon(i).species])
                            for i in range(len(known_species))}:
            continue
        pkmn = side1.pokemon(slot)
        pkmn.species = _species_id(pset.species)
        pkmn.level = 100
        for mi, move_name in enumerate(pset.moves[:4]):
            pkmn.move(mi).id = _move_id(move_name)
            pkmn.move(mi).pp = 63
        slot += 1


def _make_determinization(battle: Battle) -> oak.Battle:
    det = deepcopy(battle.public)
    _fill_opponent(battle, det)
    return det


def _oak_result(
    det: oak.Battle,
    durations: oak.Durations,
    budget: str,
    evl: str,
    bandit: str,
) -> oak.Output:
    heap = oak.Heap()
    agent = oak.Agent()
    agent.budget = budget
    agent.bandit = bandit or "pexp3-1.0-0.1"
    agent.eval = evl or "fp"
    return oak.search(det, durations, heap, agent)


def _select_move(results: list[tuple[oak.Output, float, int]]) -> str:
    total = np.zeros(9)
    for output, weight, _ in results:
        for i in range(9):
            total[i] += weight * output.p1_empirical[i]
    logger.debug("empirical: %s", total)
    idx = int(np.argmax(total))
    if idx < 4:
        return f"move {idx + 1}"
    return f"switch {idx - 3}"


def perform_searches_and_select_move(battle: Battle) -> str:
    n = max(1, FoulPlayConfig.parallelism)
    weight = 1.0 / n
    durations = battle.durations
    budget = FoulPlayConfig.budget

    dets = [_make_determinization(battle) for _ in range(n)]
    logger.info("searching %d determinizations budget=%s", n, budget)

    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = [
            (ex.submit(_oak_result, det, durations, budget, FoulPlayConfig.eval, FoulPlayConfig.bandit), weight, i)
            for i, det in enumerate(dets)
        ]

    results = [(f.result(), w, i) for f, w, i in futures]
    choice = _select_move(results)
    logger.info("choice: %s", choice)
    return choice
