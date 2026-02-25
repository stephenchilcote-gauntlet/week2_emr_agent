from __future__ import annotations

import pytest

from tests.eval.runner import EvalRunner


def test_eval_dataset_requires_at_least_one_assertion() -> None:
    with pytest.raises(ValueError, match="has no assertions"):
        EvalRunner._validate_dataset(
            [
                {
                    "id": "case-1",
                    "expected": {},
                }
            ]
        )
