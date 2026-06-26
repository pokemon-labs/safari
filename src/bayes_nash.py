import numpy as np


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


class Player:
    def __init__(self, actions: list[int], omega: list[float]):
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

    def padded_logits(self):
        logits = np.zeros((self.n, self.K))
        for i in range(self.n):
            logits[i, self.actions[i] :] = -np.inf
        return logits


class Solver:
    def __init__(
        self, p1: Player, p2: Player, payoffs: dict[tuple[int, int], np.array]
    ):
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
        p1_logits, p2_logits = self.p1.padded_logits(), self.p2.padded_logits()

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
        return (r * self.omega).sum()


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

    class args:
        iterations: int = 10000
        lr: float = 1.0
        lr_decay: float = 1

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


if __name__ == "__main__":
    simple()
