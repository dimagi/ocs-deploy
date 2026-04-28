import aws_cdk as cdk
import aws_cdk.assertions as assertions

from ocs_deploy.domains import DomainStack


def _synth(config):
    app = cdk.App()
    stack = DomainStack(app, config)
    return assertions.Template.from_stack(stack)


def test_single_domain_creates_one_identity(ocs_config):
    template = _synth(ocs_config)
    template.resource_count_is("AWS::SES::EmailIdentity", 1)


def test_multiple_domains_creates_one_identity_each(ocs_config_factory):
    config = ocs_config_factory(
        EMAIL_DOMAIN="primary.com",
        EMAIL_INBOUND_DOMAINS="extra1.com,extra2.com",
    )
    template = _synth(config)
    template.resource_count_is("AWS::SES::EmailIdentity", 3)


def test_identity_uses_default_configuration_set(ocs_config_factory):
    config = ocs_config_factory(EMAIL_DOMAIN="primary.com")
    template = _synth(config)
    # Only one ConfigurationSet should be created and shared by all identities.
    template.resource_count_is("AWS::SES::ConfigurationSet", 1)
    template.has_resource_properties(
        "AWS::SES::ConfigurationSet",
        {"Name": "Default"},
    )
    template.has_resource_properties(
        "AWS::SES::EmailIdentity",
        assertions.Match.object_like(
            {
                "EmailIdentity": "primary.com",
                "ConfigurationSetAttributes": {
                    "ConfigurationSetName": assertions.Match.any_value(),
                },
            }
        ),
    )


def test_dkim_outputs_per_domain(ocs_config_factory):
    config = ocs_config_factory(
        EMAIL_DOMAIN="primary.com",
        EMAIL_INBOUND_DOMAINS="extra.com",
    )
    template = _synth(config)
    outputs = template.find_outputs("*")
    # 3 DKIM records per domain, 2 domains = 6 outputs.
    dkim_outputs = {k: v for k, v in outputs.items() if "DKIM" in k}
    assert len(dkim_outputs) == 6
