from aws_admin import aws_client, config


def test_amplify_client_uses_region(monkeypatch):
    captured = {}

    def fake_client(service, region_name=None):
        captured["service"] = service
        captured["region"] = region_name
        return object()

    monkeypatch.setattr(aws_client.boto3, "client", fake_client)
    aws_client.amplify_client()
    assert captured["service"] == "amplify"
    assert captured["region"] == config.REGION
