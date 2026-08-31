"""Handmade numpy-only implementation of the RL training stack.

Layers/losses/optimizer here implement forward + analytic backward by hand,
no autograd library. Goal: train the same recurrent actor-critic + PPO
pipeline as rl/ without paying the torch CPU baseline (~270 MB libtorch).

The finite difference validation harness is
``tests/training/validate_recurrent_gradients.py``.
"""
