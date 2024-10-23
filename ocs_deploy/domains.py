import aws_cdk as cdk
from aws_cdk import aws_ses as ses, aws_certificatemanager as acm
from constructs import Construct

from ocs_deploy.config import OCSConfig


class DomainStack(cdk.Stack):
    """Create Domain Certificate and Email identity for SES

    There is still a manual step of adding the validation records to the DNS.
    """

    def __init__(self, scope: Construct, config: OCSConfig) -> None:
        super().__init__(
            scope, config.stack_name(OCSConfig.DOMAINS_STACK), env=config.cdk_env()
        )

        self.certificate = self.create_certificate(config)
        self.email_identity = self.create_email_identity(config)

    def create_certificate(self, config):
        return acm.Certificate(
            self,
            config.make_name("Certificate"),
            certificate_name=config.make_name("Certificate"),
            domain_name=config.domain_name,
            validation=acm.CertificateValidation.from_dns(),
        )

    def create_email_identity(self, config):
        email_identity = ses.EmailIdentity(
            self,
            config.make_name("EmailIdentity"),
            identity=ses.Identity.domain(config.email_domain),
        )
        email_identity.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        for i, record in enumerate(email_identity.dkim_records):
            cdk.CfnOutput(
                self,
                config.make_name(f"EmailIdentityDKIMRecord{i}"),
                value=f"{record.name}.	1	IN	CNAME	{record.value}. ; SES for {config.domain_name}",
                export_name=f"EmailIdentityDKIMRecord{i}",
            )

        return email_identity
