import random
import numpy as np

import oak
import oak.search

import argparse

def get_position(RNG: random.Random):
    string = "alakazam 82% par thunderwave psychic recover seismictoss; exeggutor 43% par psychic sleeppowder explosion stunspore; gengar hypnosis nightshade explosion psychic; snorlax slp1 bodyslam rest hyperbeam reflect; tauros bodyslam hyperbeam blizzard earthquake | chansey 65% spc-1 softb seismictoss thunderwave icebeam; snorlax slp2 bodyslam selfdestruct blizzard earthquake; tauros 68% bodyslam hyperbeam blizzard earthquake; starmie recover thunderwave psychic blizzard"
    return oak.parse_battle(string)


def consecutive_ce_kl(dists):
    dists = [np.asarray(p, dtype=float) for p in dists]

    ce = []
    kl = []

    for p, q in zip(dists[:-1], dists[1:]):
        if p.shape != q.shape:
            raise ValueError("All distributions must have the same shape.")

        if not (np.isclose(p.sum(), 1) and np.isclose(q.sum(), 1)):
            raise ValueError("Distributions must sum to 1.")

        mask = p > 0

        # Cross-entropy
        if np.any((q == 0) & mask):
            ce.append(np.inf)
            kl.append(np.inf)
            continue

        ce_val = -np.sum(p[mask] * np.log(q[mask]))
        h_p = -np.sum(p[mask] * np.log(p[mask]))
        kl_val = ce_val - h_p

        ce.append(ce_val)
        kl.append(kl_val)

    return np.array(ce), np.array(kl)

import numpy as np

def compare_to_last(dists):
    dists = [np.asarray(p, dtype=float) for p in dists]
    ref = dists[-1]
    ce = []
    kl = []
    for p in dists:
        if p.shape != ref.shape:
            raise ValueError("Distributions must have same shape.")
        if not (np.isclose(p.sum(), 1) and np.isclose(ref.sum(), 1)):
            raise ValueError("Distributions must sum to 1.")
        mask = p > 0
        if np.any((ref == 0) & mask):
            ce.append(np.inf)
            kl.append(np.inf)
            continue
        ce_val = -np.sum(p[mask] * np.log(ref[mask]))
        entropy = -np.sum(p[mask] * np.log(p[mask]))
        kl_val = ce_val - entropy

        ce.append(ce_val)
        kl.append(kl_val)
    return np.array(ce), np.array(kl)


parser = argparse.ArgumentParser()

parser.add_argument("--bandit", default="pexp3-1.0-0.1")
args = parser.parse_args()

outputs = {}

# def main():
seed = 0
RNG = random.Random(seed)

powers = [i for i in range(20, 21)]
agent = oak.search.Agent()
agent.eval = "/home/user/pacnet"
agent.bandit = args.bandit

battle, durations, result = get_position(RNG)

# print(f"Battle: {oak.battle_string(battle, durations)}")

p1_empiricals = []
p2_empiricals = []

for p in powers:
    iterations = 2**p
    heap = oak.search.Heap()
    agent.budget = str(iterations)
    output = oak.search.search(battle, durations, result, heap, agent)
    print(f"Iterations: {iterations}")
    p1_empiricals.append(output.p1_empirical)
    p2_empiricals.append(output.p2_empirical)
    outputs[p] = output


for empiricals in [p1_empiricals, p2_empiricals]:
    for x in empiricals:
        print(x)

    data = compare_to_last(empiricals)
    print(data)


# if __name__ == "__main__":
#     main()
