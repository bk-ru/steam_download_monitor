from steam_monitor.steam import AppManifestStore, VdfKeyValueParser


def test_manifest_store_reads_manifest(tmp_path):
    steamapps = tmp_path / "steamapps"
    steamapps.mkdir()
    manifest_path = steamapps / "appmanifest_123.acf"
    manifest_path.write_text(
        '\n'.join(
            [
                '"appid" "123"',
                '"name" "Test Game"',
                '"BytesDownloaded" "100"',
                '"BytesToDownload" "1000"',
                '"StateFlags" "0"',
            ]
        ),
        encoding="utf-8",
    )

    store = AppManifestStore(VdfKeyValueParser(), "steamapps/appmanifest_{appid}.acf")
    manifests = store.list_manifests([tmp_path])
    assert len(manifests) == 1
    m = manifests[0]
    assert m.appid == "123"
    assert m.name == "Test Game"
    assert m.bytes_downloaded == 100
    assert m.bytes_to_download == 1000
    assert m.remaining_bytes() == 900

