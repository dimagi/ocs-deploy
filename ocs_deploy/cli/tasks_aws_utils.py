import os

from invoke import Context, Exit, task
from termcolor import cprint

from ocs_deploy.config import OCSConfig

DEFAULT_PROFILE = os.getenv("AWS_PROFILE")

PROFILE_HELP = {
    "profile": "AWS profile to use for deployment. Will read from AWS_PROFILE env var if not set."
}


@task(name="login", help=PROFILE_HELP)
def aws_login(c: Context, profile=DEFAULT_PROFILE):
    """Login to AWS SSO."""
    result = c.run(f"aws sso login --profile {profile}", echo=True)
    return result.ok


def _check_credentials(c: Context, profile: str):
    result = c.run(
        f"aws sts get-caller-identity --profile {profile}", warn=True, hide=True
    )
    return result.ok


def get_profile_and_auth(c: Context, profile):
    if not profile:
        profile = input("AWS profile not set. Enter profile: ")

    if not _check_credentials(c, profile):
        if not aws_login(c, profile):
            raise Exit("Failed to login", -1)

    return profile


def _get_config(c: Context):
    env = c.config.environment
    if not env:
        raise Exit(
            "No environment specified. Use '--env' or the 'OCS_DEPLOY_ENV' environment variable.",
            -1,
        )
    cprint(f"Using environment: {env}", color="blue")
    return OCSConfig(env)
