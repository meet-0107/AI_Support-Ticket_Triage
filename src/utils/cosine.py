import math
from typing import Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Computes the cosine similarity between two numerical vectors.
    
    Args:
        a: First vector of floats.
        b: Second vector of floats.
        
    Returns:
        Float cosine similarity in range [-1.0, 1.0]. Returns 0.0 if either vector has zero magnitude.
        
    Raises:
        ValueError: If vector lengths do not match.
    """
    if len(a) != len(b):
        raise ValueError(f"Vector dimensions must match: got {len(a)} and {len(b)}")

    dot_product = 0.0
    norm_a_sq = 0.0
    norm_b_sq = 0.0

    for x, y in zip(a, b):
        dot_product += x * y
        norm_a_sq += x * x
        norm_b_sq += y * y

    if norm_a_sq == 0.0 or norm_b_sq == 0.0:
        return 0.0

    similarity = dot_product / (math.sqrt(norm_a_sq) * math.sqrt(norm_b_sq))
    # Clamp to avoid floating point precision overflow
    return max(-1.0, min(1.0, similarity))
