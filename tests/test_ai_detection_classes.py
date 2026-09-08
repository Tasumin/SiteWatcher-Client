from sitewatch_agent.ai_detector import DEFAULT_DETECTION_CLASSES


def test_default_detection_classes_include_common_animals():
    expected = {"bird", "cat", "dog", "horse", "sheep", "cow", "bear", "zebra", "giraffe", "elephant"}
    assert expected.issubset(set(DEFAULT_DETECTION_CLASSES))


def test_default_detection_classes_keep_people_and_vehicles():
    expected = {"person", "car", "truck", "bus", "motorcycle", "bicycle"}
    assert expected.issubset(set(DEFAULT_DETECTION_CLASSES))
