# About

Safari is a derivative of [Foul-Play](https://github.com/pmariglia/foul-play) for Generation 1 using an [Oak](https://github.com/pokemon-labs/oak) search backend.

# Notes

Retain types between turns and condition their likelihood on observed actions

* How do we handle dozens of possibilities we can't sample/use? Just ignore and only touch sampled probs? If we just store logits then we don't need to renormalize after eliminating impossible types. 
