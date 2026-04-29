from ocs_deploy.config import OCSConfig


def test_email_inbound_domains_defaults_to_empty_list(ocs_config):
    assert ocs_config.email_inbound_domains == []


def test_all_inbound_domains_with_no_extras(ocs_config):
    assert ocs_config.all_inbound_domains == ["chat.example.com"]


def test_email_inbound_domains_parses_csv(ocs_config_factory):
    config = ocs_config_factory(EMAIL_INBOUND_DOMAINS="a.com,b.com")
    assert config.email_inbound_domains == ["a.com", "b.com"]


def test_email_inbound_domains_strips_whitespace(ocs_config_factory):
    config = ocs_config_factory(EMAIL_INBOUND_DOMAINS=" a.com , b.com ,c.com")
    assert config.email_inbound_domains == ["a.com", "b.com", "c.com"]


def test_email_inbound_domains_skips_empty(ocs_config_factory):
    config = ocs_config_factory(EMAIL_INBOUND_DOMAINS="a.com,,b.com")
    assert config.email_inbound_domains == ["a.com", "b.com"]


def test_all_inbound_domains_includes_primary_first(ocs_config_factory):
    config = ocs_config_factory(
        EMAIL_DOMAIN="primary.com", EMAIL_INBOUND_DOMAINS="a.com,b.com"
    )
    assert config.all_inbound_domains == ["primary.com", "a.com", "b.com"]


def test_all_inbound_domains_dedupes_primary(ocs_config_factory):
    config = ocs_config_factory(
        EMAIL_DOMAIN="primary.com", EMAIL_INBOUND_DOMAINS="primary.com,a.com"
    )
    assert config.all_inbound_domains == ["primary.com", "a.com"]


def test_anymail_webhook_secret_name(ocs_config):
    assert ocs_config.anymail_webhook_secret_name == "ocs/test/anymail-webhook-secret"


def test_anymail_webhook_domain_defaults_to_domain_name(ocs_config):
    assert ocs_config.anymail_webhook_domain == ocs_config.domain_name


def test_anymail_webhook_domain_can_be_overridden(ocs_config_factory):
    config = ocs_config_factory(
        DOMAIN_NAME="new.example.com",
        ANYMAIL_WEBHOOK_DOMAIN="legacy.example.com",
    )
    assert config.anymail_webhook_domain == "legacy.example.com"
    assert config.domain_name == "new.example.com"


def test_ses_inbound_stack_in_all_stacks():
    assert "ses-inbound" in OCSConfig.ALL_STACKS
    assert OCSConfig.SES_INBOUND_STACK == "ses-inbound"
