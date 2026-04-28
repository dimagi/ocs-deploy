def test_anymail_webhook_secret_is_managed(ocs_config):
    secrets = ocs_config.get_secrets_list()
    matching = [s for s in secrets if s.name.endswith("/anymail-webhook-secret")]
    assert len(matching) == 1
    assert matching[0].managed is True
