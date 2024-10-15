import os
import shlex

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
    result = c.run(aws_cli("sso login", profile), echo=True)
    return result.ok


def _check_credentials(c: Context, profile: str):
    result = c.run(aws_cli("sts get-caller-identity", profile), warn=True, hide=True)
    return result.ok


def get_profile_and_auth(c: Context, profile):
    if not profile:
        env = c.config.environment
        cprint(
            "AWS profile not set. You can pass it via '--profile' or the AWS_PROFILE env var.",
            color="light_grey",
        )
        default = f"ocs-{env}"
        profile = input(f"Enter profile: [Press enter to use '{default}'] ") or default

    cprint(f"Using AWS profile: {profile}", color="blue")
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


def aws_cli(cmd, profile, **kwargs):
    """Generate an AWS CLI command."""
    args = ""
    for k, v in kwargs.items():
        k = k.replace("_", "-")
        if v is True:
            args += f" --{k}"
        elif v is False:
            continue
        else:
            if isinstance(v, NoQuote):
                value = v
            else:
                value = shlex.quote(v)
            args += f" --{k} {value}"
    return f"aws --no-cli-pager {cmd} --profile={profile} {args}"


class NoQuote(str):
    """A string that should not be quoted when passed to a shell command."""

    pass
