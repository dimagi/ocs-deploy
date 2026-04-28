# Inbound Email Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire AWS SES inbound mail delivery to the OCS Django app via S3+SNS, supporting a deploy-time-configured list of OCS-owned subdomains.

**Architecture:** New `SesInboundStack` owns the SNS topic, S3 bucket, receipt rule set with S3+SNS actions, anymail webhook secret, and HTTPS subscription. `DomainStack` is extended to create one `EmailIdentity` per domain (primary + extras). `FargateStack` gains `sns:ConfirmSubscription` and `s3:GetObject` IAM permissions and injects `ANYMAIL_WEBHOOK_SECRET` into the Django container. DNS records (MX + DKIM CNAMEs) are surfaced as `CfnOutput`s for manual configuration.

**Tech Stack:** AWS CDK 2.161 (Python), aws-cdk-lib (`aws_ses`, `aws_sns`, `aws_sns_subscriptions`, `aws_s3`, `aws_secretsmanager`, `aws_iam`), pytest with `aws_cdk.assertions.Template`.

**Companion app PR:** [dimagi/open-chat-studio#3175](https://github.com/dimagi/open-chat-studio/pull/3175)

**Spec:** `docs/superpowers/specs/2026-04-28-inbound-email-design.md`

---

## Task 1: Add a CDK synth test fixture

The current test directory under `ocs_deploy/tests/` only has the WAF regex tests. Synth tests need an `OCSConfig` instance, but `OCSConfig.__init__` reads `.env.<env>` from the current working directory. Add a pytest fixture that writes a minimal `.env.test` file in a temp dir, `chdir`s into it, and yields a constructed `OCSConfig("test")`.

**Files:**
- Create: `ocs_deploy/tests/conftest.py`
- Test: `ocs_deploy/tests/test_conftest_smoke.py`

- [ ] **Step 1: Write the smoke test that exercises the fixture**

```python
# ocs_deploy/tests/test_conftest_smoke.py
def test_ocs_config_fixture_constructs(ocs_config):
    assert ocs_config.environment == "test"
    assert ocs_config.app_name == "ocs"
    assert ocs_config.account == "111111111111"
    assert ocs_config.region == "us-east-1"
    assert ocs_config.email_domain == "chat.example.com"
    assert ocs_config.domain_name == "ocs.example.com"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest ocs_deploy/tests/test_conftest_smoke.py -v`
Expected: FAIL — fixture `ocs_config` not found.

- [ ] **Step 3: Write the fixture**

```python
# ocs_deploy/tests/conftest.py
import pytest

from ocs_deploy.config import OCSConfig

DEFAULT_ENV = {
    "APP_NAME": "ocs",
    "CDK_ACCOUNT": "111111111111",
    "CDK_REGION": "us-east-1",
    "EMAIL_DOMAIN": "chat.example.com",
    "DOMAIN_NAME": "ocs.example.com",
    "DJANGO_ALLOWED_HOSTS": "ocs.example.com",
}


@pytest.fixture
def ocs_config(tmp_path, monkeypatch):
    """Construct an OCSConfig from a temp `.env.test` file."""
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in DEFAULT_ENV.items())
    )
    monkeypatch.chdir(tmp_path)
    return OCSConfig("test")


@pytest.fixture
def ocs_config_factory(tmp_path, monkeypatch):
    """Factory for constructing OCSConfig with overridden env values."""
    def _make(**overrides):
        env = {**DEFAULT_ENV, **overrides}
        env_file = tmp_path / ".env.test"
        env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in env.items())
        )
        monkeypatch.chdir(tmp_path)
        return OCSConfig("test")
    return _make
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest ocs_deploy/tests/test_conftest_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Run full test suite to confirm no regressions**

Run: `uv run pytest ocs_deploy/tests/ -v`
Expected: 88 passed (87 existing + 1 new).

- [ ] **Step 6: Commit**

```bash
git add ocs_deploy/tests/conftest.py ocs_deploy/tests/test_conftest_smoke.py
git commit -m "test: add OCSConfig fixture for CDK synth tests"
```

---

## Task 2: Add inbound-domain config to `OCSConfig`

Parse `EMAIL_INBOUND_DOMAINS` (CSV, optional) into `email_inbound_domains: list[str]`. Add `all_inbound_domains` returning `[email_domain, *email_inbound_domains]` with duplicates removed (preserve order, primary first). Add the `SES_INBOUND_STACK` constant and the `anymail_webhook_secret_name` accessor. Don't yet wire it into any stack.

**Files:**
- Modify: `ocs_deploy/config.py`
- Test: `ocs_deploy/tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# ocs_deploy/tests/test_config.py
import pytest

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


def test_ses_inbound_stack_in_all_stacks():
    assert "ses-inbound" in OCSConfig.ALL_STACKS
    assert OCSConfig.SES_INBOUND_STACK == "ses-inbound"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ocs_deploy/tests/test_config.py -v`
Expected: FAIL — `email_inbound_domains` / `all_inbound_domains` / `anymail_webhook_secret_name` / `SES_INBOUND_STACK` don't exist yet.

- [ ] **Step 3: Implement the config additions**

Edit `ocs_deploy/config.py`:

1. Add the constant inside `OCSConfig`:
```python
    SES_INBOUND_STACK = "ses-inbound"
```

2. Add `SES_INBOUND_STACK` to the `ALL_STACKS` list (insert after `DJANGO_STACK`, before `WAF_STACK`):
```python
    ALL_STACKS = [
        GITHUB_STACK,
        EC2_TMP_STACK,
        DOMAINS_STACK,
        S3_STACK,
        VPC_STACK,
        ECR_STACK,
        RDS_STACK,
        REDIS_STACK,
        DJANGO_STACK,
        SES_INBOUND_STACK,
        WAF_STACK,
        GUARD_DUTY_STACK,
        SECURITYHUB_STACK,
        DETECTIVE_STACK,
    ]
```

3. In `OCSConfig.__init__`, after the line that sets `self.email_domain`, add:
```python
        raw_inbound = self._config.get("EMAIL_INBOUND_DOMAINS", "") or ""
        self.email_inbound_domains = [
            d.strip() for d in raw_inbound.split(",") if d.strip()
        ]
```

4. Add a property method on `OCSConfig` (e.g. immediately after `s3_whatsapp_audio_bucket`):
```python
    @property
    def all_inbound_domains(self):
        """Primary email domain plus any extras from EMAIL_INBOUND_DOMAINS, primary first, deduped."""
        seen = set()
        ordered = []
        for d in [self.email_domain, *self.email_inbound_domains]:
            if d and d not in seen:
                ordered.append(d)
                seen.add(d)
        return ordered

    @property
    def anymail_webhook_secret_name(self):
        return self.make_secret_name("anymail-webhook-secret")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ocs_deploy/tests/test_config.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add ocs_deploy/config.py ocs_deploy/tests/test_config.py
git commit -m "feat: add EMAIL_INBOUND_DOMAINS config and ses-inbound stack constant"
```

---

## Task 3: Update `secrets.yml` and `.env.example`

Add the `anymail-webhook-secret` (managed by CDK — same pattern as `django-secret-key`) to `secrets.yml`. Add `EMAIL_INBOUND_DOMAINS=` to `.env.example`.

**Files:**
- Modify: `ocs_deploy/secrets.yml`
- Modify: `.env.example`

- [ ] **Step 1: Add the secret to `secrets.yml`**

Add this line in `secrets.yml` near the other `managed: true` entries:
```yaml
  - name: anymail-webhook-secret
    managed: true
```

- [ ] **Step 2: Add the env var to `.env.example`**

Edit `.env.example`. Find the `# Domains` block and replace it with:
```
# Domains
EMAIL_DOMAIN=
# Optional: comma-separated list of additional inbound email domains
# Each gets a SES EmailIdentity (DKIM-verified) and is added to the receipt rule.
EMAIL_INBOUND_DOMAINS=
DJANGO_SERVER_EMAIL=noreply@openchatstudio.com
DJANGO_DEFAULT_FROM_EMAIL=noreply@openchatstudio.com
DOMAIN_NAME=
```

- [ ] **Step 3: Verify yaml parses and config secret list is correct**

```python
# ocs_deploy/tests/test_secrets_yaml.py
def test_anymail_webhook_secret_is_managed(ocs_config):
    secrets = ocs_config.get_secrets_list()
    matching = [s for s in secrets if s.name.endswith("/anymail-webhook-secret")]
    assert len(matching) == 1
    assert matching[0].managed is True
```

Run: `uv run pytest ocs_deploy/tests/test_secrets_yaml.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ocs_deploy/secrets.yml .env.example ocs_deploy/tests/test_secrets_yaml.py
git commit -m "feat: declare anymail-webhook-secret as a managed secret"
```

---

## Task 4: Loop `DomainStack` over `all_inbound_domains`

Replace the single `EmailIdentity` creation in `DomainStack.create_email_identity` with a loop over `config.all_inbound_domains`. Each domain gets its own identity (sharing the existing `Default` configuration set) with three DKIM `CfnOutput`s, each named/exported with a domain-suffixed slug so they don't collide.

**Files:**
- Modify: `ocs_deploy/domains.py`
- Test: `ocs_deploy/tests/test_domains_stack.py`

- [ ] **Step 1: Write the failing tests**

```python
# ocs_deploy/tests/test_domains_stack.py
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
    template.has_resource_properties(
        "AWS::SES::EmailIdentity",
        {
            "EmailIdentity": "primary.com",
            "ConfigurationSetAttributes": {"ConfigurationSetName": "Default"},
        },
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest ocs_deploy/tests/test_domains_stack.py -v`
Expected: FAIL on multi-domain count and per-domain output naming.

- [ ] **Step 3: Implement the loop**

Replace the body of `ocs_deploy/domains.py` with:

```python
import re

import aws_cdk as cdk
from aws_cdk import aws_ses as ses, aws_certificatemanager as acm
from constructs import Construct

from ocs_deploy.config import OCSConfig


def _slug(domain: str) -> str:
    """Convert a domain to a CFN-safe construct id suffix."""
    return re.sub(r"[^A-Za-z0-9]", "", domain.title())


class DomainStack(cdk.Stack):
    """Create Domain Certificate and SES EmailIdentity per inbound domain.

    DNS validation records (cert + DKIM) are emitted as CfnOutputs for the
    operator to add manually.
    """

    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.DOMAINS_STACK), env=config.cdk_env()
        )
        self.config = config
        self.certificate = self.create_certificate(config)
        self.configuration_set = ses.ConfigurationSet(
            self,
            config.make_name("SesConfigurationSet"),
            configuration_set_name="Default",
        )
        self.email_identities = {
            domain: self._create_identity(domain)
            for domain in config.all_inbound_domains
        }

    def create_certificate(self, config):
        return acm.Certificate(
            self,
            config.make_name("Certificate"),
            certificate_name=config.make_name("Certificate"),
            domain_name=config.domain_name,
            validation=acm.CertificateValidation.from_dns(),
        )

    def _create_identity(self, domain: str) -> ses.EmailIdentity:
        slug = _slug(domain)
        identity = ses.EmailIdentity(
            self,
            self.config.make_name(f"EmailIdentity-{slug}"),
            identity=ses.Identity.domain(domain),
            configuration_set=self.configuration_set,
        )
        identity.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        for i, record in enumerate(identity.dkim_records):
            cdk.CfnOutput(
                self,
                self.config.make_name(f"EmailIdentityDKIMRecord-{slug}-{i}"),
                value=f"{record.name}.\t1\tIN\tCNAME\t{record.value}. ; SES DKIM for {domain}",
                export_name=f"EmailIdentityDKIMRecord-{slug}-{i}",
            )
        return identity
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest ocs_deploy/tests/test_domains_stack.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest ocs_deploy/tests/ -v`
Expected: All passing, no regressions.

- [ ] **Step 6: Commit**

```bash
git add ocs_deploy/domains.py ocs_deploy/tests/test_domains_stack.py
git commit -m "feat: create one SES EmailIdentity per inbound domain"
```

---

## Task 5: Create `SesInboundStack` — bucket + bucket policy

Create the new stack with just the S3 bucket for raw mail, BlockPublicAccess on, 7-day lifecycle on `inbound/`, and a bucket policy granting `s3:PutObject` to `ses.amazonaws.com` scoped via `aws:SourceAccount`.

**Files:**
- Create: `ocs_deploy/ses_inbound.py`
- Test: `ocs_deploy/tests/test_ses_inbound_stack.py`

- [ ] **Step 1: Write failing tests**

```python
# ocs_deploy/tests/test_ses_inbound_stack.py
import aws_cdk as cdk
import aws_cdk.assertions as assertions

from ocs_deploy.ses_inbound import SesInboundStack


def _synth(config):
    app = cdk.App()
    stack = SesInboundStack(app, config)
    return assertions.Template.from_stack(stack)


def test_bucket_is_private(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )


def test_bucket_has_seven_day_lifecycle(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "LifecycleConfiguration": {
                "Rules": [
                    {
                        "Status": "Enabled",
                        "ExpirationInDays": 7,
                        "Prefix": "inbound/",
                    }
                ],
            },
        },
    )


def test_bucket_policy_allows_ses_putobject(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        assertions.Match.object_like(
            {
                "PolicyDocument": {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Effect": "Allow",
                                    "Principal": {"Service": "ses.amazonaws.com"},
                                    "Action": "s3:PutObject",
                                    "Condition": {
                                        "StringEquals": {
                                            "aws:SourceAccount": "111111111111"
                                        }
                                    },
                                }
                            )
                        ]
                    ),
                }
            }
        ),
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ocs_deploy/tests/test_ses_inbound_stack.py -v`
Expected: FAIL — `ses_inbound` module doesn't exist.

- [ ] **Step 3: Create the stack with the bucket**

```python
# ocs_deploy/ses_inbound.py
import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct

from ocs_deploy.config import OCSConfig

INBOUND_PREFIX = "inbound/"


class SesInboundStack(cdk.Stack):
    """SES inbound mail plumbing: S3 bucket, SNS topic, receipt rules, webhook secret."""

    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.SES_INBOUND_STACK), env=config.cdk_env()
        )
        self.config = config
        self.bucket = self._create_bucket()

    def _create_bucket(self) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            self.config.make_name("SesInboundBucket"),
            bucket_name=self.config.make_name("ses-inbound-mail"),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    enabled=True,
                    prefix=INBOUND_PREFIX,
                    expiration=cdk.Duration.days(7),
                )
            ],
        )
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowSESPuts",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("ses.amazonaws.com")],
                actions=["s3:PutObject"],
                resources=[f"{bucket.bucket_arn}/{INBOUND_PREFIX}*"],
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.config.account},
                },
            )
        )
        cdk.CfnOutput(
            self,
            self.config.make_name("SesInboundBucketName"),
            value=bucket.bucket_name,
        )
        return bucket
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ocs_deploy/tests/test_ses_inbound_stack.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add ocs_deploy/ses_inbound.py ocs_deploy/tests/test_ses_inbound_stack.py
git commit -m "feat: SesInboundStack scaffolding with S3 bucket for raw mail"
```

---

## Task 6: Add SNS topic, webhook secret, and receipt rule set to `SesInboundStack`

Add an SNS topic for SES notifications, a Secrets Manager secret with auto-generated value (excluding `:`, `/`, `@` so basic-auth URLs parse cleanly), and a `ReceiptRuleSet` containing one rule whose `recipients` matches all inbound domains and whose actions are `[S3Action, SnsAction]` in that order.

**Files:**
- Modify: `ocs_deploy/ses_inbound.py`
- Modify: `ocs_deploy/tests/test_ses_inbound_stack.py`

- [ ] **Step 1: Write failing tests**

Append to `ocs_deploy/tests/test_ses_inbound_stack.py`:

```python
def test_sns_topic_created(ocs_config):
    template = _synth(ocs_config)
    template.resource_count_is("AWS::SNS::Topic", 1)


def test_anymail_webhook_secret_excludes_url_unsafe_chars(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::SecretsManager::Secret",
        {
            "Name": "ocs/test/anymail-webhook-secret",
            "GenerateSecretString": assertions.Match.object_like(
                {
                    "ExcludeCharacters": assertions.Match.string_like_regexp(
                        ".*[:/@].*"
                    ),
                    "PasswordLength": 32,
                }
            ),
        },
    )


def test_receipt_rule_set_created(ocs_config):
    template = _synth(ocs_config)
    template.resource_count_is("AWS::SES::ReceiptRuleSet", 1)


def test_receipt_rule_recipients_include_all_domains(ocs_config_factory):
    config = ocs_config_factory(
        EMAIL_DOMAIN="primary.com",
        EMAIL_INBOUND_DOMAINS="a.com,b.com",
    )
    template = _synth(config)
    template.has_resource_properties(
        "AWS::SES::ReceiptRule",
        {
            "Rule": assertions.Match.object_like(
                {
                    "Recipients": ["primary.com", "a.com", "b.com"],
                    "Enabled": True,
                    "ScanEnabled": True,
                }
            ),
        },
    )


def test_receipt_rule_actions_are_s3_then_sns(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::SES::ReceiptRule",
        {
            "Rule": assertions.Match.object_like(
                {
                    "Actions": [
                        assertions.Match.object_like(
                            {
                                "S3Action": assertions.Match.object_like(
                                    {"ObjectKeyPrefix": "inbound/"}
                                )
                            }
                        ),
                        assertions.Match.object_like(
                            {"SNSAction": assertions.Match.object_like({"Encoding": "Base64"})}
                        ),
                    ],
                }
            ),
        },
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ocs_deploy/tests/test_ses_inbound_stack.py -v`
Expected: 5 new failures.

- [ ] **Step 3: Extend `SesInboundStack`**

Edit `ocs_deploy/ses_inbound.py`. Add the new imports at the top:

```python
from aws_cdk import (
    aws_iam as iam,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_ses as ses,
    aws_ses_actions as ses_actions,
    aws_sns as sns,
)
```

Extend `__init__` to call new helpers (after `self.bucket = self._create_bucket()`):

```python
        self.topic = self._create_topic()
        self.webhook_secret = self._create_webhook_secret()
        self.rule_set, self.rule = self._create_receipt_rules()
```

Add the helper methods to the class:

```python
    def _create_topic(self) -> sns.Topic:
        topic = sns.Topic(
            self,
            self.config.make_name("SesInboundTopic"),
            topic_name=self.config.make_name("ses-inbound"),
            display_name="OCS SES Inbound",
        )
        cdk.CfnOutput(
            self,
            self.config.make_name("SesInboundTopicArn"),
            value=topic.topic_arn,
        )
        return topic

    def _create_webhook_secret(self) -> secretsmanager.Secret:
        return secretsmanager.Secret(
            self,
            self.config.make_name("AnymailWebhookSecret"),
            secret_name=self.config.anymail_webhook_secret_name,
            description="Basic-auth value used by anymail's SES inbound webhook.",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=32,
                exclude_characters=":/@\"' \\",
                exclude_punctuation=False,
            ),
        )

    def _create_receipt_rules(self) -> tuple[ses.ReceiptRuleSet, ses.ReceiptRule]:
        rule_set = ses.ReceiptRuleSet(
            self,
            self.config.make_name("SesInboundRuleSet"),
            receipt_rule_set_name=self.config.make_name("inbound"),
        )
        rule = rule_set.add_rule(
            self.config.make_name("DeliverInboundMail"),
            recipients=self.config.all_inbound_domains,
            scan_enabled=True,
            enabled=True,
            actions=[
                ses_actions.S3(
                    bucket=self.bucket,
                    object_key_prefix=INBOUND_PREFIX,
                ),
                ses_actions.Sns(
                    topic=self.topic,
                    encoding=ses_actions.EmailEncoding.BASE64,
                ),
            ],
        )

        cdk.CfnOutput(
            self,
            self.config.make_name("SesInboundRuleSetName"),
            value=rule_set.receipt_rule_set_name,
        )
        cdk.CfnOutput(
            self,
            self.config.make_name("ActivateReceiptRuleSetCommand"),
            value=(
                f"aws ses set-active-receipt-rule-set "
                f"--rule-set-name {rule_set.receipt_rule_set_name}"
            ),
            description="Run this once after deploy to make the rule set active.",
        )
        return rule_set, rule
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ocs_deploy/tests/test_ses_inbound_stack.py -v`
Expected: All passed (8 total in this file).

- [ ] **Step 5: Commit**

```bash
git add ocs_deploy/ses_inbound.py ocs_deploy/tests/test_ses_inbound_stack.py
git commit -m "feat: SNS topic, webhook secret, and receipt rule set in SesInboundStack"
```

---

## Task 7: Add the HTTPS SNS subscription with secret-injected basic auth

Create the HTTPS SNS subscription whose endpoint embeds the webhook secret value via `cdk.SecretValue.secrets_manager(...)`. CFN resolves the dynamic reference at deploy time.

**Files:**
- Modify: `ocs_deploy/ses_inbound.py`
- Modify: `ocs_deploy/tests/test_ses_inbound_stack.py`

- [ ] **Step 1: Write failing tests**

Append to `ocs_deploy/tests/test_ses_inbound_stack.py`:

```python
def test_sns_subscription_is_https_to_webhook_path(ocs_config):
    template = _synth(ocs_config)
    template.has_resource_properties(
        "AWS::SNS::Subscription",
        assertions.Match.object_like(
            {
                "Protocol": "https",
                "Endpoint": assertions.Match.string_like_regexp(
                    r".*ocs\.example\.com/anymail/amazon_ses/inbound/$"
                ),
            }
        ),
    )


def test_sns_subscription_endpoint_uses_secret_dynamic_reference(ocs_config):
    template = _synth(ocs_config)
    subs = template.find_resources("AWS::SNS::Subscription")
    assert len(subs) == 1
    endpoint = next(iter(subs.values()))["Properties"]["Endpoint"]
    # CDK renders Fn::Join when interpolating dynamic references.
    rendered = str(endpoint)
    assert "Fn::Join" in rendered
    assert "anymail-webhook-secret" in rendered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ocs_deploy/tests/test_ses_inbound_stack.py -v -k subscription`
Expected: 2 failures — no SNS subscription exists yet.

- [ ] **Step 3: Add the subscription**

Edit `ocs_deploy/ses_inbound.py`. Add to the imports:

```python
from aws_cdk import aws_sns_subscriptions as sns_subs
```

Append a call inside `__init__` after `self.rule_set, self.rule = self._create_receipt_rules()`:

```python
        self._add_webhook_subscription()
```

Add the method:

```python
    def _add_webhook_subscription(self) -> None:
        secret_value = cdk.SecretValue.secrets_manager(
            self.webhook_secret.secret_name
        ).unsafe_unwrap()
        endpoint = (
            f"https://anymail:{secret_value}@"
            f"{self.config.domain_name}/anymail/amazon_ses/inbound/"
        )
        self.topic.add_subscription(sns_subs.UrlSubscription(endpoint))
```

> **Note for the implementer:** `unsafe_unwrap` is required because CDK's `SecretValue` is opaque by default. The "unsafe" name reflects the design tradeoff — the resolved secret will appear in the CFN template params for the SNS subscription resource. This is the documented CDK pattern for embedding a secret in a string passed to a non-`SecretValue` parameter, and matches how anymail expects the URL to be built. Same trust boundary as the secret itself (only IAM-authorized users can read CFN events for this stack).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ocs_deploy/tests/test_ses_inbound_stack.py -v`
Expected: All 10 passed.

- [ ] **Step 5: Commit**

```bash
git add ocs_deploy/ses_inbound.py ocs_deploy/tests/test_ses_inbound_stack.py
git commit -m "feat: subscribe Django webhook to SES inbound SNS topic"
```

---

## Task 8: Wire `FargateStack` to consume the new IAM perms and secret

`FargateStack.__init__` takes a new `ses_inbound_stack` argument. The task role gets `sns:ConfirmSubscription` (scoped to the topic ARN) and `s3:GetObject` (scoped to `<bucket-arn>/inbound/*`). `secrets_dict` adds `ANYMAIL_WEBHOOK_SECRET` from the new secret.

**Files:**
- Modify: `ocs_deploy/fargate.py`
- Test: `ocs_deploy/tests/test_fargate_stack.py`

- [ ] **Step 1: Write failing tests**

```python
# ocs_deploy/tests/test_fargate_stack.py
import aws_cdk as cdk
import aws_cdk.assertions as assertions

from ocs_deploy.domains import DomainStack
from ocs_deploy.ec2_tmp import Ec2TmpStack
from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.rds import RdsStack
from ocs_deploy.redis import RedisStack
from ocs_deploy.ses_inbound import SesInboundStack
from ocs_deploy.vpc import VpcStack


def _synth_fargate(config):
    app = cdk.App()
    domain_stack = DomainStack(app, config)
    vpc_stack = VpcStack(app, config)
    Ec2TmpStack(app, vpc_stack.vpc, config)
    ecr_stack = EcrStack(app, config)
    rds_stack = RdsStack(app, vpc_stack.vpc, config)
    redis_stack = RedisStack(app, vpc_stack.vpc, config)
    ses_inbound_stack = SesInboundStack(app, config)
    fargate = FargateStack(
        app,
        vpc_stack.vpc,
        ecr_stack.repo,
        rds_stack,
        redis_stack,
        domain_stack,
        ses_inbound_stack,
        config,
    )
    return assertions.Template.from_stack(fargate)


def test_task_role_has_sns_confirm_subscription(ocs_config):
    template = _synth_fargate(ocs_config)
    template.has_resource_properties(
        "AWS::IAM::Policy",
        assertions.Match.object_like(
            {
                "PolicyDocument": {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Action": "sns:ConfirmSubscription",
                                    "Effect": "Allow",
                                }
                            )
                        ]
                    )
                }
            }
        ),
    )


def test_task_role_has_s3_getobject_on_inbound_prefix(ocs_config):
    template = _synth_fargate(ocs_config)
    template.has_resource_properties(
        "AWS::IAM::Policy",
        assertions.Match.object_like(
            {
                "PolicyDocument": {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Action": "s3:GetObject",
                                    "Effect": "Allow",
                                }
                            )
                        ]
                    )
                }
            }
        ),
    )


def test_anymail_webhook_secret_in_task_definition(ocs_config):
    template = _synth_fargate(ocs_config)
    # Both Django web and Celery task definitions should have ANYMAIL_WEBHOOK_SECRET.
    matches = template.find_resources(
        "AWS::ECS::TaskDefinition",
        assertions.Match.object_like(
            {
                "Properties": {
                    "ContainerDefinitions": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Secrets": assertions.Match.array_with(
                                        [
                                            assertions.Match.object_like(
                                                {"Name": "ANYMAIL_WEBHOOK_SECRET"}
                                            )
                                        ]
                                    )
                                }
                            )
                        ]
                    )
                }
            }
        ),
    )
    # web + celery worker + celery beat + migration = 4
    assert len(matches) >= 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest ocs_deploy/tests/test_fargate_stack.py -v`
Expected: failures — `FargateStack.__init__` doesn't accept `ses_inbound_stack`, and the IAM perms / secret aren't wired up.

- [ ] **Step 3: Update `FargateStack`**

In `ocs_deploy/fargate.py`:

1. Update the `__init__` signature and store the new arg:
```python
    def __init__(
        self,
        scope: Construct,
        vpc,
        ecr_repo,
        rds_stack,
        redis_stack,
        domain_stack,
        ses_inbound_stack,
        config: OCSConfig,
    ) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.DJANGO_STACK), env=config.cdk_env()
        )

        self.config = config
        self.rds_stack = rds_stack
        self.redis_stack = redis_stack
        self.domain_stack = domain_stack
        self.ses_inbound_stack = ses_inbound_stack
```

2. In `task_role` (the `cached_property`), append two more `add_to_policy` calls before the final `return task_role`:
```python
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sns:ConfirmSubscription"],
                effect=iam.Effect.ALLOW,
                resources=[self.ses_inbound_stack.topic.topic_arn],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                effect=iam.Effect.ALLOW,
                resources=[
                    f"{self.ses_inbound_stack.bucket.bucket_arn}/inbound/*"
                ],
            )
        )
```

3. In `secrets_dict`, after the existing secrets dict literal but before the for-loop over `get_existing_secrets_list`, add:
```python
        secrets["ANYMAIL_WEBHOOK_SECRET"] = ecs.Secret.from_secrets_manager(
            self.ses_inbound_stack.webhook_secret
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest ocs_deploy/tests/test_fargate_stack.py -v`
Expected: All passed.

- [ ] **Step 5: Commit**

```bash
git add ocs_deploy/fargate.py ocs_deploy/tests/test_fargate_stack.py
git commit -m "feat: wire ANYMAIL_WEBHOOK_SECRET and inbound IAM perms into Fargate"
```

---

## Task 9: Wire `SesInboundStack` into `app.py`

Instantiate `SesInboundStack` and pass it into `FargateStack`. Add the `add_dependency` for stack ordering.

**Files:**
- Modify: `app.py`
- Test: `ocs_deploy/tests/test_app_synth.py`

- [ ] **Step 1: Write a smoke synth test**

```python
# ocs_deploy/tests/test_app_synth.py
import aws_cdk as cdk

from ocs_deploy.config import OCSConfig
from ocs_deploy.domains import DomainStack
from ocs_deploy.ecr import EcrStack
from ocs_deploy.fargate import FargateStack
from ocs_deploy.rds import RdsStack
from ocs_deploy.redis import RedisStack
from ocs_deploy.ses_inbound import SesInboundStack
from ocs_deploy.vpc import VpcStack


def test_app_can_synth_with_ses_inbound(ocs_config):
    app = cdk.App()
    config = ocs_config

    domain_stack = DomainStack(app, config)
    vpc_stack = VpcStack(app, config)
    ecr_stack = EcrStack(app, config)
    rds_stack = RdsStack(app, vpc_stack.vpc, config)
    redis_stack = RedisStack(app, vpc_stack.vpc, config)
    ses_inbound_stack = SesInboundStack(app, config)
    fargate = FargateStack(
        app,
        vpc_stack.vpc,
        ecr_stack.repo,
        rds_stack,
        redis_stack,
        domain_stack,
        ses_inbound_stack,
        config,
    )
    fargate.add_dependency(ses_inbound_stack)

    # Should not raise.
    app.synth()
```

- [ ] **Step 2: Run the test to verify it passes (synth-only smoke check)**

Run: `uv run pytest ocs_deploy/tests/test_app_synth.py -v`
Expected: PASS — exercises the full synth path.

- [ ] **Step 3: Update `app.py`**

Edit `app.py`. After the import block, add:
```python
from ocs_deploy.ses_inbound import SesInboundStack
```

After the `redis_stack` block and before the `ocs_services = FargateStack(...)` call, add:
```python
ses_inbound_stack = SesInboundStack(app, config)
```

Update the `FargateStack` invocation to pass it:
```python
ocs_services = FargateStack(
    app,
    vpc_stack.vpc,
    ecr_stack.repo,
    rds_stack,
    redis_stack,
    domain_stack,
    ses_inbound_stack,
    config,
)
```

After the existing `ocs_services.add_dependency(...)` block, add:
```python
ocs_services.add_dependency(ses_inbound_stack)
```

- [ ] **Step 4: Verify CDK synth runs end-to-end**

Run: `uv run cdk synth --all -c ocs_env=test 2>&1 | tail -5`
Expected: cdk synth completes; printed YAML or success message, no Python errors.

(Note: this requires the `.env.test` file used by the fixture; if running outside the fixture, copy `.env.example` to `.env.test` and fill in the same `DEFAULT_ENV` values from `conftest.py`.)

- [ ] **Step 5: Commit**

```bash
git add app.py ocs_deploy/tests/test_app_synth.py
git commit -m "feat: wire SesInboundStack into app.py"
```

---

## Task 10: Update `.env` files and README

Operator-facing changes: `.env.dev` / `.env.prod` get the new optional `EMAIL_INBOUND_DOMAINS=` line, and the README documents the deploy ordering, MX/DKIM record requirements, and the manual `set-active-receipt-rule-set` step.

**Files:**
- Modify: `.env.dev`
- Modify: `.env.prod`
- Modify: `README.md`

- [ ] **Step 1: Add the env var to `.env.dev` and `.env.prod`**

In each file, add a single line after `EMAIL_DOMAIN=...`:
```
EMAIL_INBOUND_DOMAINS=
```

Leave the value empty (operators fill it in when adding a second domain).

- [ ] **Step 2: Add a README section**

Insert this section in `README.md`, immediately after the existing "First Time Deployment Steps" section's step 6 (Run Initial Migrations):

```markdown
### Configure Inbound Email (optional)

Inbound email is delivered to the Django app via SES → S3 → SNS → anymail webhook.

1. **Set the inbound domains** in `.env.<env>`:
   ```
   EMAIL_INBOUND_DOMAINS=chat2.openchatstudio.com
   ```
   The primary `EMAIL_DOMAIN` is always included automatically. Leave the var empty for a single-domain setup.

2. **Deploy the SES inbound stack:**
   ```bash
   ocs --env <env> aws.deploy --stacks ses-inbound
   ```
   Note the outputs — they include the SNS topic ARN, the bucket name, the receipt rule set name, and the activation command.

3. **Re-deploy Django** so it picks up `ANYMAIL_WEBHOOK_SECRET` and the new IAM permissions:
   ```bash
   ocs --env <env> aws.deploy --stacks django
   ```

4. **Activate the receipt rule set** (one-time):
   ```bash
   aws ses set-active-receipt-rule-set --rule-set-name <RuleSetName from output>
   ```
   Only one rule set per region can be active. Verify with:
   ```bash
   aws ses describe-active-receipt-rule-set
   ```

5. **Add DNS records** for each inbound domain:
   - **MX** (priority 10): `inbound-smtp.us-east-1.amazonaws.com`
   - **DKIM CNAMEs** (×3 per domain): values are stack outputs from the `domains` stack, named `EmailIdentityDKIMRecord-<DomainSlug>-<index>`.

6. **Test** by sending an email to `support@<your-inbound-domain>`. Confirm:
   - `aws s3 ls s3://ocs-<env>-ses-inbound-mail/inbound/` shows the message.
   - Django CloudWatch logs show the anymail inbound signal firing.

**Failure modes worth knowing:**
- If the SNS subscription is left "Pending Confirmation" in the AWS console, the Django task role is missing `sns:ConfirmSubscription` — re-deploy the django stack. As a manual workaround, click the confirmation URL in the SNS console.
- If mail delivery silently stops, check `aws ses describe-active-receipt-rule-set` — only one rule set per region can be active, and a region-wide change can deactivate yours.
- The S3 bucket has a 7-day lifecycle on `inbound/`. Don't store anything else there.
```

- [ ] **Step 3: Verify pytest still all-green and the README renders**

Run: `uv run pytest ocs_deploy/ -v && uv run pytest ocs_deploy/tests/ --tb=short`
Expected: All passing, including the 87 existing waf tests + the new tests.

- [ ] **Step 4: Commit**

```bash
git add .env.dev .env.prod README.md
git commit -m "docs: document inbound email deploy and DNS setup"
```

---

## Final Validation

- [ ] **Run all tests:**

```bash
uv run pytest ocs_deploy/tests/ -v
```
Expected: all green.

- [ ] **Synth all stacks for the dev env:**

```bash
uv run cdk synth --all -c ocs_env=dev 2>&1 | tail -10
```
Expected: success message; no Python errors.

- [ ] **Run `cdk diff` against the dev env (read-only) to preview the changeset:**

```bash
uv run cdk diff --all -c ocs_env=dev 2>&1 | head -100
```
Expected: shows the new SesInboundStack resources, the additional EmailIdentities (if any), and the FargateStack IAM diff. No unexpected destructive changes.

- [ ] **Final commit (if any cleanup needed):**

If anything was left dirty during the run, commit it now. Otherwise this step is a no-op.

---

## What this plan deliberately does **not** do

- Activate the receipt rule set automatically (operator runs one CLI command).
- Configure DNS (operator adds MX + DKIM records from the stack outputs).
- Process attachments in the Django app — the infra makes it possible, but the OCS PR currently ignores them; that's a follow-up app-side change.
- Provide a per-environment toggle for SNS-only vs S3+SNS — always S3+SNS.
