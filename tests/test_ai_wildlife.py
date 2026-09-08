import numpy as np

from sitewatch_agent.ai_detector import (
    WILDLIFE_SOURCE_LABELS,
    canonical_wildlife_label,
)


def test_wildlife_targets_include_requested_species():
    assert "Mule Deer" in WILDLIFE_SOURCE_LABELS
    assert "Coyote" in WILDLIFE_SOURCE_LABELS
    assert "Swift Fox" in WILDLIFE_SOURCE_LABELS
    assert "Black-tailed Jackrabbit" in WILDLIFE_SOURCE_LABELS
    assert "Grizzly Bear" in WILDLIFE_SOURCE_LABELS
    assert "American Black Bear" in WILDLIFE_SOURCE_LABELS


def test_wildlife_labels_are_canonicalized():
    assert canonical_wildlife_label("Mule Deer") == "deer"
    assert canonical_wildlife_label("Swift Fox") == "fox"
    assert canonical_wildlife_label("Black-tailed Jackrabbit") == "rabbit"
    assert canonical_wildlife_label("Grizzly Bear") == "bear"
    assert canonical_wildlife_label("American Black Bear") == "bear"
    assert canonical_wildlife_label("Coyote") == "coyote"
