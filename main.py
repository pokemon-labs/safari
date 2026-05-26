import argparse

import oak
from src.battle import PSBattle, PSPlayer
from src.teams import TeamPredictor, Team, set_to_string, team_to_string
from src.search import Player, Search

req_example = '{"active":[{"moves":[{"move":"Return 102","id":"return","pp":32,"maxpp":32,"target":"normal","disabled":false},{"move":"Earthquake","id":"earthquake","pp":16,"maxpp":16,"target":"allAdjacent","disabled":false},{"move":"Shadow Ball","id":"shadowball","pp":24,"maxpp":24,"target":"normal","disabled":false},{"move":"Hyper Beam","id":"hyperbeam","pp":8,"maxpp":8,"target":"normal","disabled":false}]}],"side":{"name":"imlearninghehe","id":"p1","pokemon":[{"ident":"p1: Slaking","details":"Slaking, L78, F","condition":"362/362","active":true,"stats":{"atk":295,"def":201,"spa":193,"spd":146,"spe":201},"moves":["return102","earthquake","shadowball","hyperbeam"],"baseAbility":"truant","item":"choiceband","pokeball":"pokeball"},{"ident":"p1: Magneton","details":"Magneton, L85","condition":"224/224","active":false,"stats":{"atk":108,"def":209,"spa":253,"spd":168,"spe":168},"moves":["thunderbolt","hiddenpowerice","toxic","protect"],"baseAbility":"magnetpull","item":"leftovers","pokeball":"pokeball"},{"ident":"p1: Forretress","details":"Forretress, L81, M","condition":"254/254","active":false,"stats":{"atk":192,"def":273,"spa":144,"spd":143,"spe":111},"moves":["spikes","hiddenpowersteel","explosion","rapidspin"],"baseAbility":"sturdy","item":"leftovers","pokeball":"pokeball"},{"ident":"p1: Nosepass","details":"Nosepass, F","condition":"222/222","active":false,"stats":{"atk":147,"def":327,"spa":147,"spd":237,"spe":117},"moves":["earthquake","thunderwave","rockslide","explosion"],"baseAbility":"magnetpull","item":"leftovers","pokeball":"pokeball"},{"ident":"p1: Gengar","details":"Gengar, L74, M","condition":"211/211","active":false,"stats":{"atk":101,"def":132,"spa":235,"spd":154,"spe":206},"moves":["willowisp","thunderbolt","substitute","icepunch"],"baseAbility":"levitate","item":"leftovers","pokeball":"pokeball"},{"ident":"p1: Jumpluff","details":"Jumpluff, L87, F","condition":"271/271","active":false,"stats":{"atk":145,"def":171,"spa":145,"spd":197,"spe":241},"moves":["encore","hiddenpowerflying","synthesis","sleeppowder"],"baseAbility":"chlorophyll","item":"leftovers","pokeball":"pokeball"}]},"rqid":2}'

parser = argparse.ArgumentParser()
parser.add_argument(
    "--teams",
    required=True,
)

args = parser.parse_args()

predictor = TeamPredictor(args.teams, 0.01)

battle = PSBattle("", PSPlayer(), PSPlayer())

battle.public.side(0).pokemon(0).species = oak.id_to_species("jynx")
p1_matching = predictor.find_all_matching(battle.public.side(0))
assert len(p1_matching) < len(predictor.teams)

battle.public.side(1).pokemon(0).species = oak.id_to_species("gengar")
p2_matching = predictor.find_all_matching(battle.public.side(1))
assert len(p2_matching) < len(predictor.teams)


def check_matching(matching):
    for data in matching:
        team, x = data
        print(team_to_string(team)[:50], x)


check_matching(p1_matching)
print("_")
check_matching(p2_matching)

predictor.sets.print()

# Initialize search.Player, Search using the first 2 matches for each side
MAX_TYPES = 2

def matching_to_player(side: oak.Side, matching: list) -> Player:
    top = matching[:MAX_TYPES]
    teams = [team for team, _ in top]
    # logits are unnormalized; convert to probabilities via softmax-style normalization
    import math
    logits = [logit for _, logit in top]
    exps = [math.exp(l) for l in logits]
    total = sum(exps)
    omega = [e / total for e in exps]
    return Player(side, teams, omega)

p1_player = matching_to_player(battle.public.side(0), p1_matching)
p2_player = matching_to_player(battle.public.side(1), p2_matching)

search = Search(battle, p1_player, p2_player)