"""Common source-requirement accounting for recurrent and reduced policies."""

from clarity.training.reduced.episode import (
    Episode, RolloutSummary, collect_episode, evaluate, measure_episodes,
    merge_measurements, summarize_measurements,
)
