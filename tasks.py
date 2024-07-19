from dotenv import dotenv_values
from invoke import Context, Exit, task

from ocs_deploy.config import OCSConfig

DEFAULT_PROFILE = "ocs-test"


@task
def login(c: Context, profile=DEFAULT_PROFILE):
    result = c.run(f"aws sso login --profile {profile}", echo=True)
    return result.ok


def _check_credentials(c: Context, profile: str):
    result = c.run(
        f"aws sts get-caller-identity --profile {profile}", warn=True, hide=True
    )
    return result.ok


@task(
    help={
        "stack": f"Name of the stack to deploy ({' | '.join(OCSConfig.ALL_STACKS)})",
        "verbose": "Enable verbose output",
    }
)
def deploy(c: Context, stack=None, verbose=False, profile=DEFAULT_PROFILE):
    if not _check_credentials(c, profile):
        if not login(c, profile):
            raise Exit("Failed to login", -1)

    config = OCSConfig(dotenv_values(".env"))
    cmd = f"cdk deploy --profile {profile}"
    if stack:
        cmd += f" {config.stack_name(stack)}"
    if verbose:
        cmd += " --verbose"
    c.run(cmd, echo=True, pty=True)


@task
def requirements(c: Context, upgrade_all=False, upgrade_package=None):
    if upgrade_all and upgrade_package:
        raise Exit("Cannot specify both upgrade and upgrade-package", -1)
    args = " -U" if upgrade_all else ""
    has_uv = c.run("uv -V", hide=True, timeout=1, warn=True)
    if has_uv.ok:
        cmd_base = "uv pip compile"
    else:
        cmd_base = "pip-compile --resolver=backtracking"
    env = {"CUSTOM_COMPILE_COMMAND": "inv requirements"}
    if upgrade_package:
        cmd_base += f" --upgrade-package {upgrade_package}"
    base_path = "requirements/requirements"
    c.run(f"{cmd_base} {base_path}.in -o {base_path}.txt{args}", env=env)
    c.run(f"{cmd_base} {base_path}-dev.in -o {base_path}-dev.txt{args}", env=env)

    if _confirm("\nInstall requirements ?", _exit=False):
        cmd = "uv pip" if has_uv.ok else "pip"
        c.run(f"{cmd} install -r requirements-dev.txt", echo=True, pty=True)


@task
def ruff(c: Context, no_fix=False, unsafe_fixes=False):
    """Run ruff checks and formatting. Use --unsafe-fixes to apply unsafe fixes."""
    fix_flag = "" if no_fix else "--fix"
    unsafe_fixes_flag = "--unsafe-fixes" if unsafe_fixes else ""
    c.run(f"ruff check {fix_flag} {unsafe_fixes_flag}", echo=True, pty=True)
    c.run("ruff format", echo=True, pty=True)


def _confirm(message, _exit=True, exit_message="Done"):
    response = input(f"{message} (y/n): ")
    confirmed = response.lower() == "y"
    if not confirmed and _exit:
        raise Exit(exit_message, -1)
    return confirmed
