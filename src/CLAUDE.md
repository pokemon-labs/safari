This program is a mod of FoulPlay - It connects to showdown and uses information set MCTS to play battles.

It totally replaces the PokeEngine module with oak search backend

The Oak api can be gleaned from github.com/pokemon-labs/oak in cpp/src/pyoak.cc

* If OAK.md does not exists in this dir, I want you to read the above file and write a terse context markdown for yourself, then replace this line*

The FoulPlay set prediction used a mix of Smogon stats and self-hosted data. We are replacing that with src/teams. The SetPredictor class will be used with a method that takes an imcomplete oak.Side (Safari Battle has p1, p2 with known number of pokemon for each player 1 <= n <= 6>) and fills in the remaining sets. So it takes Oak.Side and the number of pokemon that player/side has (The pokeballs in a battle you can see from turn 1).

Right now its in complete disarray. Big picture:
The real meat is in
* battle.py Battle: Self contained parsing of public information
* teams.py SetPredictor
* search.py which should eventually have a method that just takes the ground state Battle, produces a bunch of determinizations, does search, collects the output, and then produces a protocol message eg. `/choose move 2`
Battle is mostly 'done' and teams.py needs some attention but should be stubbed out. 

So your current primary task is to consolidate the remaining program logic: the battle loop and socket stuff, and main loop that does challenges, ladder, accept_chall into one file, `run.py`. L

I want you to stage this is much as possible while also working to try to get this staticly type safe. I will be using the standard checker for this, but focus more on consolodating stubbed code and fixing includes than running the checker all the time and running out of command use.

Commit often, keep comments minimal.