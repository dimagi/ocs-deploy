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
            domain: self._create_identity(
                domain, is_primary=domain == config.email_domain
            )
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

    def _create_identity(self, domain: str, is_primary: bool) -> ses.EmailIdentity:
        # The primary identity keeps the un-suffixed construct ID so its CFN
        # logical ID matches the already-deployed resource. Renaming would
        # orphan-and-recreate, which fails because the orphan still owns the
        # domain in SES.
        if is_primary:
            identity_suffix = "EmailIdentity"
            record_prefix = "EmailIdentityDKIMRecord"
        else:
            slug = _slug(domain)
            identity_suffix = f"EmailIdentity-{slug}"
            record_prefix = f"EmailIdentityDKIMRecord-{slug}-"

        identity = ses.EmailIdentity(
            self,
            self.config.make_name(identity_suffix),
            identity=ses.Identity.domain(domain),
            configuration_set=self.configuration_set,
        )
        identity.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        for i, record in enumerate(identity.dkim_records):
            name = f"{record_prefix}{i}"
            cdk.CfnOutput(
                self,
                self.config.make_name(name),
                value=f"{record.name}.\t1\tIN\tCNAME\t{record.value}. ; SES DKIM for {domain}",
                export_name=name,
            )
        return identity
