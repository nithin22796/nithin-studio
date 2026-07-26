from app.main import _extract_alert


def test_extract_alert_up():
    payload = {
        "monitor": {"name": "frame-extractor"},
        "heartbeat": {"status": 1, "msg": "OK"},
        "msg": "Up",
    }
    monitor_name, status, message = _extract_alert(payload)
    assert monitor_name == "frame-extractor"
    assert status == "up"
    assert message == "Up"


def test_extract_alert_down():
    payload = {
        "monitor": {"name": "frame-extractor"},
        "heartbeat": {"status": 0, "msg": "Connection refused"},
    }
    monitor_name, status, message = _extract_alert(payload)
    assert monitor_name == "frame-extractor"
    assert status == "down"
    assert message == "Connection refused"


def test_extract_alert_missing_monitor():
    monitor_name, status, message = _extract_alert({"heartbeat": {"status": 1}})
    assert monitor_name == "unknown"
    assert status == "up"
