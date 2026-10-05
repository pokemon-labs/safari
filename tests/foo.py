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
parser.add_argument("--powers", default="20")
args = parser.parse_args()

outputs = {}

# def main():
seed = 0
RNG = random.Random(seed)

powers = args.powers.split(",")
eval = oak.search.parse_eval("/home/user/RL/sunday/2000.battle.net", False)
bandit = oak.search.parse_bandit(args.bandit)

battle, durations, result = get_position(RNG)

# print(f"Battle: {oak.battle_string(battle, durations)}")

p1_empiricals = []
p2_empiricals = []

for p in powers:
    iterations = int(2 ** float(p))
    print(f"Iterations: {iterations}")
    heap = oak.search.Node()
    budget = oak.search.parse_budget(str(iterations))
    output = oak.search.run(battle, durations, budget, bandit, heap, eval)
    p1_empiricals.append(output.p1.empirical)
    p2_empiricals.append(output.p2.empirical)
    outputs[p] = output


for empiricals in [p1_empiricals, p2_empiricals]:
    for x in empiricals:
        print(x)

    data = compare_to_last(empiricals)
    print(data)


# if __name__ == "__main__":
#     main()

"""
While loop sampling, max = 4, no baseline

p1_logits=[-1290847.0000 -2455190.2500 -2167520.7500 -1721665.6250 4.0000 -365635.4688 -378293.0625 -740022.5000 -inf ] p2_logits=[-36615.4180 -774772.5625 -1382676.6250 4.0000 -97327.0234 -516775.1875 -325602.1250 -inf -inf ]
[0.012555   0.0125066  0.01256561 0.0125522  0.91181982 0.01272213
 0.01255274 0.01272589 0.        ]
(array([0.46989594]), array([0.]))
[0.01425356 0.01432776 0.01434976 0.91351265 0.01472068 0.01429999
 0.01453561 0.         0.        ]
(array([0.44929364]), array([0.]))
>>>
>>> e = outputs["24"].empirical_matrix
>>> e
array([[0.56240166, 0.49464568, 0.50366857, 0.51139771, 0.48325959,
        0.56465719, 0.50059861, 0.        , 0.        ],
       [0.52968893, 0.46247274, 0.50459544, 0.44216133, 0.45483666,
        0.43772099, 0.46743523, 0.        , 0.        ],
       [0.53300314, 0.43988597, 0.46697447, 0.45711006, 0.48325189,
        0.54063315, 0.51359157, 0.        , 0.        ],
       [0.62987943, 0.59860601, 0.51857042, 0.48076495, 0.5479186 ,
        0.43131623, 0.5600358 , 0.        , 0.        ],
       [0.58208407, 0.63517035, 0.67515999, 0.58850446, 0.59503876,
        0.61920254, 0.60604985, 0.        , 0.        ],
       [0.66444854, 0.70121058, 0.65608957, 0.56222774, 0.57840462,
        0.62827968, 0.61564859, 0.        , 0.        ],
       [0.58646787, 0.58607954, 0.63778794, 0.56485311, 0.56562456,
        0.60039727, 0.59185715, 0.        , 0.        ],
       [0.61815575, 0.63956122, 0.65154848, 0.54096113, 0.55575612,
        0.60947837, 0.5770108 , 0.        , 0.        ],
       [0.        , 0.        , 0.        , 0.        , 0.        ,
        0.        , 0.        , 0.        , 0.        ]])
>>> sum(e)
array([4.7061294 , 4.55763209, 4.61439489, 4.14798049, 4.26409079,
       4.43168543, 4.4322276 , 0.        , 0.        ])
>>> e = outputs["24"].visit_matrix
>>> sum(e)
array([  239135,   240380,   240749, 15326199,   246972,   239914,
         243867,        0,        0], dtype=uint64)
>>> sum(e.transpose())
array([  210638,   209826,   210816,   210591, 15297798,   213442,
         210600,   213505,        0], dtype=uint64)


p1_logits=[-1384898.1250 -2159895.2500 -2062540.2500 -1465829.3750 0.0000 -329458.6250 -360083.1875 -631973.5000 -inf ] p2_logits=[-204526.9219 -710431.1250 -1380755.6250 0.0000 -148016.9219 -422544.8125 -309887.6562 -inf -inf ]
[0.01252824 0.01246375 0.01246715 0.01255399 0.90105361 0.02266699
 0.01335621 0.01291007 0.        ]
(array([0.51266104]), array([0.]))
[0.01430374 0.01427752 0.01428097 0.73002428 0.19848311 0.0143066
 0.01432377 0.         0.        ]
(array([0.85435147]), array([0.]))
>>> e = outputs["24"].visit_matrix
>>> sum(e.transpose())
array([  210189,   209107,   209164,   210621, 15117171,   380289,
         224080,   216595,        0], dtype=uint64)
>>> sum(e)
array([  239977,   239537,   239595, 12247775,  3329994,   240025,
         240313,        0,        0], dtype=uint64)
>>> e
array([[    2932,     2947,     3042,   153737,    41494,     3043,
            2994,        0,        0],
       [    2959,     3024,     3069,   152303,    41947,     2911,
            2894,        0,        0],
       [    2994,     2952,     3085,   152568,    41621,     2953,
            2991,        0,        0],
       [    3009,     3021,     3088,   152631,    42849,     3012,
            3011,        0,        0],
       [  216161,   215712,   215326, 11168656,  2868569,   216157,
          216590,        0,        0],
       [    5658,     5491,     5475,   158765,   193856,     5524,
            5520,        0,        0],
       [    3191,     3232,     3300,   154991,    52918,     3285,
            3163,        0,        0],
       [    3073,     3158,     3210,   154124,    46740,     3140,
            3150,        0,        0],
       [       0,        0,        0,        0,        0,        0,
               0,        0,        0]], dtype=uint64)

"""
