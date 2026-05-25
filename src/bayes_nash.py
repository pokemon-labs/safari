import numpy as np
from typing import Dict, List
import argparse
import random

parser = argparse.ArgumentParser()
parser.add_argument(
    "--main",
    type=str,
    required=True,
    help="The function to run as main()",
)
parser.add_argument(
    "--seed",
    type=int,
    help="Initial seed",
)
parser.add_argument(
    "--iterations",
    type=int,
    help="Number of iterations to use when solving",
)
parser.add_argument(
    "--ucb-iterations",
    type=int,
    help="Number of iterations to use when solving",
)
parser.add_argument(
    "--lr",
    type=float,
    default=1.0,
    help="Learning rate for NeuRD update.",
)
parser.add_argument(
    "--lr-decay",
    type=float,
    default=1.0,
    help="Learning rate for NeuRD update.",
)
parser.add_argument(
    "--games",
    type=int,
    help="Number of Bayesian games to try solving",
)
parser.add_argument(
    "--uniform-types",
    action="store_true",
    help="Use uniform pdf over the both players types",
)
parser.add_argument(
    "--no-plots",
    action="store_true",
    help="Skip matplotlib data vis.",
)
parser.add_argument(
    "--min-types",
    type=int,
    default=1,
    help="Min types for both players",
)
parser.add_argument(
    "--max-types",
    type=int,
    default=1,
    help="Max types for both players",
)
parser.add_argument(
    "--min-actions",
    type=int,
    default=2,
    help="Min actions for both players",
)
parser.add_argument(
    "--max-actions",
    type=int,
    default=2,
    help="Max actions for both players",
)

def add_player_args(parser, prefix: str):
    parser.add_argument(
        prefix + "min-types",
        type=int,
        help="",
    )
    parser.add_argument(
        prefix + "max-types",
        type=int,
        help="",
    )
    parser.add_argument(
        prefix + "min-actions",
        type=int,
        help="",
    )
    parser.add_argument(
        prefix + "max-actions",
        type=int,
        help="",
    )


add_player_args(parser, "--p1-")
add_player_args(parser, "--p2-")

args = parser.parse_args()


def softmax(x, axis=-1):
    x = np.asarray(x)
    x_max = np.max(x, axis=axis, keepdims=True)
    e = np.exp(x - x_max)
    return e / np.sum(e, axis=axis, keepdims=True)


def p_norm(pdf, p):
    powered = pdf**p
    return powered / powered.sum(axis=1, keepdims=True)


def argmax(pdf):
    out = (pdf == pdf.max(axis=1, keepdims=True)).astype(float)
    # print(pdf.shape, out.shape)
    return out


def do_ucb(matrix: np.ndarray, iter: int, c: float):
    m, n = matrix.shape

    p1_counts = np.zeros(m, dtype=int)
    p1_rewards = np.zeros(m, dtype=float)
    p2_counts = np.zeros(n, dtype=int)
    p2_rewards = np.zeros(n, dtype=float)

    for t in range(1, iter + 1):
        p1_ucb = np.zeros(m)
        for i in range(m):
            if p1_counts[i] == 0:
                p1_ucb[i] = np.inf
            else:
                p1_ucb[i] = p1_rewards[i] / p1_counts[i] + c * np.sqrt(
                    np.log(t) / p1_counts[i]
                )
        p1_choice = np.argmax(p1_ucb)

        p2_ucb = np.zeros(n)
        for j in range(n):
            if p2_counts[j] == 0:
                p2_ucb[j] = np.inf
            else:
                p2_ucb[j] = p2_rewards[j] / p2_counts[j] + c * np.sqrt(
                    np.log(t) / p2_counts[j]
                )
        p2_choice = np.argmax(p2_ucb)

        reward = matrix[p1_choice, p2_choice]

        p1_rewards[p1_choice] += reward
        p1_counts[p1_choice] += 1

        p2_rewards[p2_choice] += 1 - reward
        p2_counts[p2_choice] += 1

    return p1_counts / p1_counts.sum(), p2_counts / p2_counts.sum()


class Player:

    def __init__(self, actions, omega):
        eps = 0.001
        assert len(actions) == len(omega), "Mismatched actions and omega lengths"
        assert all(k >= 1 for k in actions), "Some types have number of actions < 1"
        assert (
            abs(sum(omega) - 1) < eps
        ), f"Omega pdf does not sum to [1 - eps, 1 + eps], eps = {eps}"
        self.n = len(actions)
        self.K = max(actions)
        self.actions = np.array(actions)
        self.omega = np.array(omega)

    def logits(self):
        logits = np.zeros((self.n, self.K))
        for i in range(self.n):
            logits[i, self.actions[i] :] = -np.inf
        return logits


class Solver:

    def __init__(self, p1: Player, p2: Player, payoffs: Dict[[int, int], np.array]):
        self.p1 = p1
        self.p2 = p2
        self.payoffs = payoffs

        self.n1 = p1.n
        self.n2 = p2.n
        self.K1 = p1.K
        self.K2 = p2.K
        self.omega = np.outer(p1.omega, p2.omega)[..., None]
        self.batched_payoffs = np.zeros((self.n1, self.n2, self.K1, self.K2))
        for i in range(self.n1):
            for j in range(self.n2):
                self.batched_payoffs[i, j, 0 : p1.actions[i], 0 : p2.actions[j]] = (
                    payoffs[i, j]
                )

    def run(self, iterations: int, lr: float, lr_decay: float, p: bool = False):
        p1_logits, p2_logits = self.p1.logits(), self.p2.logits()

        p1_total_policies = np.zeros_like(p1_logits)
        p2_total_policies = np.zeros_like(p2_logits)

        p1_policies = None
        p2_policies = None

        for _ in range(iterations):
            p1_policies = softmax(p1_logits)
            p2_policies = softmax(p2_logits)
            p1_total_policies += p1_policies
            p2_total_policies += p2_policies
            p1_returns = np.einsum("ijmn,jn->ijm", self.batched_payoffs, p2_policies)
            p2_returns = -np.einsum("im,ijmn->ijn", p1_policies, self.batched_payoffs)
            # payoff = np.einsum('ijn,jn->ij', p2_returns, p2_policies)[..., None] # mind the negative!
            p1_payoffs = np.einsum("im,ijm->ij", p1_policies, p1_returns)[..., None]
            p2_payoffs = -p1_payoffs
            p1_advantages = p1_returns - p1_payoffs
            p2_advantages = p2_returns - p2_payoffs
            p1_gradient = np.sum(p1_advantages * self.omega, axis=1)
            p2_gradient = np.sum(p2_advantages * self.omega, axis=0)
            p1_logits += lr * p1_gradient
            p2_logits += lr * p2_gradient
            lr *= lr_decay

        return (
            p1_total_policies / iterations,
            p2_total_policies / iterations,
            p1_policies,
            p2_policies,
        )

    def expl(self, p1_policies: np.array, p2_policies: np.array) -> float:
        p1_returns = np.einsum("ijmn,jn->ijm", self.batched_payoffs, p2_policies)
        p2_returns = -np.einsum("im,ijmn->ijn", p1_policies, self.batched_payoffs)
        # print(p1_returns.shape, p2_returns.shape)
        p1_foo = np.max(p1_returns, axis=2)
        p2_foo = np.max(p2_returns, axis=2)
        # print(p1_foo + p2_foo)
        worst_case = np.max(p1_foo + p2_foo)

        p1_options = np.sum(self.omega * p1_returns, axis=1)
        p2_options = np.sum(self.omega * p2_returns, axis=0)
        # print(p1_options.shape, p2_options.shape)
        p1_best = np.max(p1_options, axis=1)
        p2_best = np.max(p2_options, axis=1)
        # print(p1_best.shape, p2_best.shape)
        average_case = p1_best.sum() + p2_best.sum()
        return average_case, worst_case

    def reward(self, p1_policies: np.array, p2_policies: np.array) -> float:
        r = np.einsum("im,ijmn,jn->ij", p1_policies, self.batched_payoffs, p2_policies)[
            ..., None
        ]
        # print(r.shape)
        # print(self.omega.shape)
        # return r
        return (r * self.omega).sum()

    def average_ucb_policies(self, iterations: int, c: float) -> [np.array, np.array]:

        p1_out = np.zeros((self.n1, self.K1))
        p2_out = np.zeros((self.n2, self.K2))

        for i in range(self.n1):
            for j in range(self.n2):
                m = self.payoffs[(i, j)]
                p1, p2 = do_ucb(m, iterations, c)
                p1_out[i, : m.shape[0]] += self.p2.omega[j] * p1
                p2_out[j, : m.shape[1]] += self.p1.omega[i] * p2

        return argmax(p1_out), argmax(p2_out)


def generate_random_game_solver(seed, args):
    rng = random.Random(seed)
    n1 = rng.randint(
        args.p1_min_types or args.min_types, args.p1_max_types or args.max_types
    )
    n2 = rng.randint(
        args.p2_min_types or args.min_types, args.p2_max_types or args.max_types
    )
    k1 = [
        rng.randint(
            args.p1_min_actions or args.min_actions,
            args.p1_max_actions or args.max_actions,
        )
        for _ in range(n1)
    ]
    k2 = [
        rng.randint(
            args.p2_min_actions or args.min_actions,
            args.p2_max_actions or args.max_actions,
        )
        for _ in range(n2)
    ]
    o1 = None
    o2 = None
    if args.uniform_types:
        o1 = [1.0 / n1 for _ in range(n1)]
        o2 = [1.0 / n2 for _ in range(n2)]
    else:
        raw_o1 = [rng.random() for _ in range(n1)]
        o1 = [x / sum(raw_o1) for x in raw_o1]
        raw_o2 = [rng.random() for _ in range(n2)]
        o2 = [x / sum(raw_o2) for x in raw_o2]

    p1 = Player(k1, o1)
    p2 = Player(k2, o2)
    np_rng = np.random.default_rng(seed)
    matrices = {}
    for i in range(n1):
        for j in range(n2):
            matrices[(i, j)] = np_rng.random((k1[i], k2[j]))
    return Solver(p1, p2, matrices)


def simple():

    p1 = Player([2, 3], [0.5, 0.5])
    p2 = Player([2, 2], [0.5, 0.5])

    def draw(a, b):
        return np.zeros((a, b))

    def win(a, b):
        x = -np.ones((a, b))
        x[0, :] = 1
        return x

    matrices = {}
    matrices[(0, 0)] = draw(2, 2)
    matrices[(1, 0)] = draw(3, 2)
    matrices[(0, 1)] = win(2, 2)
    matrices[(1, 1)] = win(3, 2)

    solver = Solver(p1, p2, matrices)
    p1_average, p2_average, p1_last, p2_last = solver.run(
        iterations=args.iterations, lr=args.lr, lr_decay=args.lr_decay
    )
    e = solver.expl(p1_average, p2_average)

    print(f"Player 1 solution")
    print(p1_average)
    print(f"Player 2 solution")
    print(p2_average)
    print(f"expl: {e}")


def one():
    seed = args.seed or random.randint(0, 2**32 - 1)
    solver = generate_random_game_solver(seed, args)
    p1_average, p2_average, p1_last, p2_last = solver.run(
        iterations=args.iterations, lr=args.lr, lr_decay=args.lr_decay
    )
    e = solver.expl(p1_average, p2_average)
    e_last = solver.expl(p1_last, p2_last)

    for i in range(solver.p1.n):
        for j in range(solver.p2.n):
            print(f"({i}, {j}) p: {solver.omega[i, j, 0]}")
            M = solver.payoffs[(i, j)]
            print(M)

    p1_average, p2_average, p1_last, p2_last = solver.run(
        args.iterations, args.lr, args.lr_decay
    )
    e = solver.expl(p1_average, p2_average)

    print(f"Player 1 solution")
    print(p1_average)
    print(f"Player 2 solution")
    print(p2_average)
    print(f"expl: {e}")


def test():

    iterations = args.iterations

    total_expl = 0
    total_expl_last = 0
    max_expl = 0
    max_expl_seed = None

    for _ in range(args.games):
        seed = random.randint(0, 2**32 - 1)
        solver = generate_random_game_solver(seed, args)
        p1_average, p2_average, p1_last, p2_last = solver.run(
            iterations=iterations, lr=args.lr, lr_decay=args.lr_decay
        )
        e = solver.expl(p1_average, p2_average)
        e_last = solver.expl(p1_last, p2_last)
        if e > max_expl:
            max_expl = e
            max_expl_seed = seed
        total_expl += e
        total_expl_last += e_last

    print(f"Average exploitability: {total_expl / args.games}")
    print(f"Max exploitability: {max_expl} with seed {max_expl_seed}")
    print(f"Average exploitability (last): {total_expl_last / args.games}")


def ucb():

    ucb_wins = 0
    ucb_lower_expl = 0

    total_expl = 0
    total_ucb_expl = 0
    expl_data = []
    ucb_expl_data = []
    vs_p2_average_diff = []

    for _ in range(args.games):

        seed = random.randint(0, 2**32 - 1)
        solver = generate_random_game_solver(seed, args)

        p1_ucb, p2_ucb = solver.average_ucb_policies(args.ucb_iterations, 2.0)
        p1_average, p2_average, p1_last, p2_last = solver.run(
            iterations=args.iterations, lr=args.lr, lr_decay=args.lr_decay
        )

        r = solver.reward(p1_average, p2_average)
        x = solver.reward(p1_ucb, p2_average)
        y = solver.reward(p1_average, p2_ucb)
        a = solver.reward(p1_average, p2_average)
        b = solver.reward(p1_average, p2_ucb)

        vs_p2_average_diff.append(r - x)

        expl, expl_worst = solver.expl(p1_average, p2_average)
        ucb_expl, ucb_expl_worst = solver.expl(p1_ucb, p2_ucb)

        total_expl += expl_worst
        total_ucb_expl += ucb_expl_worst
        expl_data.append(expl_worst)
        ucb_expl_data.append(ucb_expl_worst)

        if x > r:
            ucb_wins += 1
        if ucb_expl < expl:
            ucb_lower_expl += 1

        ucb_exploitation = x - y
        if expl < ucb_exploitation:
            print(f"Expl check failed for seed: {seed}")
            print(f"UCB exploitation: {ucb_exploitation}")
            assert (
                False
            ), "The assert should only ever trip due to floating point errors"

    print(f"UCB win rate: {ucb_wins / args.games}")
    print(f"UCB lower expl rate: {ucb_lower_expl / args.games}")
    print(f"Average expl: {total_expl / args.games}")
    print(f"Average UCB expl: {total_ucb_expl / args.games}")
    print(f"Average diff: {sum(vs_p2_average_diff) / args.games}")

    if args.no_plots:
        exit()

    import matplotlib.pyplot as plt

    if True:
        bin_width = 0.005
        max_expl = max(max(expl_data), max(ucb_expl_data))
        bins = np.arange(0.0, max_expl + bin_width, bin_width)

        fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)

        axes[0].hist(expl_data, bins=bins)
        axes[0].set_title("expl")
        axes[0].set_xlabel("expl")
        axes[0].set_ylabel("frequency")

        axes[1].hist(ucb_expl_data, bins=bins)
        axes[1].set_title("ucb_expl")
        axes[1].set_xlabel("expl")

        plt.tight_layout()
        plt.show()

    if True:
        bin_width = 0.005
        min_diff = min(vs_p2_average_diff)
        max_diff = max(vs_p2_average_diff)
        bins = np.arange(min_diff, max_diff + bin_width, bin_width)

        fig, axes = plt.subplots(1, 1, figsize=(10, 4), sharey=True)

        axes.hist(vs_p2_average_diff, bins=bins)
        axes.set_title("P1_Solved/P1_UCB vs P2_Solved differential")
        axes.set_xlabel("Solved - UCB")
        axes.set_ylabel("frequency")

        # axes[1].hist(ucb_expl_data, bins=bins)
        # axes[1].set_title("ucb_expl")
        # axes[1].set_xlabel("expl")

        plt.tight_layout()
        plt.show()


if __name__ == "__main__":
    if args.main == "simple":
        simple()
    elif args.main == "test":
        test()
    elif args.main == "ucb":
        ucb()
    elif args.main == "one":
        one()
    else:
        print("bad main arg")
