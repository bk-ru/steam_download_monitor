from steam_monitor.steam import ContentLogParser, DownloadSnapshot


def test_parse_downloading_with_rate():
    parser = ContentLogParser()
    text = "\n".join(
        [
            "[2026-02-03 20:23:01] AppID 123 update started : download 1/10",
            "[2026-02-03 20:23:15] Current download rate: 10.0 Mbps",
        ]
    )
    snapshot = parser.parse(text, previous=None)
    assert snapshot.appid == "123"
    assert snapshot.status == "downloading"
    assert snapshot.rate == "10.0 Mbps"


def test_parse_paused_sets_zero_rate():
    parser = ContentLogParser()
    text = "\n".join(
        [
            "[2026-02-03 20:23:01] AppID 123 update started : download 1/10",
            "[2026-02-03 20:23:15] Current download rate: 10.0 Mbps",
            "[2026-02-03 20:23:40] AppID 123 update canceled : Disabled (Suspended)",
        ]
    )
    snapshot = parser.parse(text, previous=None)
    assert snapshot.appid == "123"
    assert snapshot.status == "paused"
    assert snapshot.rate == "0 Mbps"


def test_parse_resume_without_new_rate_does_not_reuse_old_rate():
    parser = ContentLogParser()
    text = "\n".join(
        [
            "[2026-02-03 20:23:01] AppID 123 update started : download 1/10",
        ]
    )
    snapshot = parser.parse(text, previous=None)
    assert snapshot.appid == "123"
    assert snapshot.status == "downloading"
    assert snapshot.rate is None


def test_parse_uses_previous_when_no_new_events():
    parser = ContentLogParser()
    previous = DownloadSnapshot(appid="123", status="downloading", rate="5.0 Mbps")
    snapshot = parser.parse("", previous=previous)
    assert snapshot.appid == "123"
    assert snapshot.status == "downloading"
    assert snapshot.rate == "5.0 Mbps"

