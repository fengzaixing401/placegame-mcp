from zoneinfo import ZoneInfo


def test_asia_shanghai_zoneinfo_is_available() -> None:
    assert ZoneInfo("Asia/Shanghai").key == "Asia/Shanghai"
