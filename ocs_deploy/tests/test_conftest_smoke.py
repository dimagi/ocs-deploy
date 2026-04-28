def test_ocs_config_fixture_constructs(ocs_config):
    assert ocs_config.environment == "test"
    assert ocs_config.app_name == "ocs"
    assert ocs_config.account == "111111111111"
    assert ocs_config.region == "us-east-1"
    assert ocs_config.email_domain == "chat.example.com"
    assert ocs_config.domain_name == "ocs.example.com"
