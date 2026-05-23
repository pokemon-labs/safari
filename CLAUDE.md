# Safari

FoulPlay mod using Oak (MCTS) instead of PokeEngine. Connects to Pokemon Showdown.

## Structure
- `run.py` — entry point: login, challenge/ladder/accept modes, battle loop, websocket client
- `src/battle.py` — `Battle`: parses showdown protocol into oak.Battle state
- `src/teams.py` — `TeamPredictor`: completes unknown opponent sets from `teams150/`
- `src/search.py` — determinizes Battle, runs oak.search in parallel, returns `/choose` string

## Status
- `battle.py` — done
- `teams.py` — stubbed (complete_side needs real sampling logic)
- `search.py` — wired; move selection picks argmax of empirical visit counts
- `run.py` — consolidated from websocket_client + run_battle + old main

## Notes
- Oak API: see OAK.md
- Static typing: work toward it; focus on correctness over exhaustive annotation
- Commit often, minimal comments
