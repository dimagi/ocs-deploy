import json
import os

from dotenv import dotenv_values
from invoke import Context, Exit, task

from ocs_deploy.config import OCSConfig, Secret

DEFAULT_PROFILE = os.environ.get("AWS_PROFILE")

PROFILE_HELP = {
    "profile": "AWS profile to use for deployment. Will read from AWS_PROFILE env var if not set."
}


@task(help=PROFILE_HELP)
def login(c: Context, profile=DEFAULT_PROFILE):
    result = c.run(f"aws sso login --profile {profile}", echo=True)
    return result.ok


def _check_credentials(c: Context, profile: str):
    result = c.run(
        f"aws sts get-caller-identity --profile {profile}", warn=True, hide=True
    )
    return result.ok


def _check_auth(c: Context, profile: str):
    if not _check_credentials(c, profile):
        if not login(c, profile):
            raise Exit("Failed to login", -1)


@task(
    help={
        "command": "Command to execute in the container. Defaults to '/bin/bash'",
    }
    | PROFILE_HELP
)
def connect(c: Context, command="/bin/bash", profile=DEFAULT_PROFILE):
    """Connect to a running ECS container and execute the given command."""
    profile = _get_profile_and_auth(c, profile)

    config = _get_config()
    cluster = config.make_name("Cluster")
    service = config.make_name("CeleryBeat")

    result = c.run(
        f"aws ecs list-tasks --cluster {cluster} --service {service}", hide=True
    )
    response = json.loads(result.stdout)
    tasks = response.get("taskArns", [])
    if not tasks:
        raise Exit(
            f"No tasks found for the '{service}' service in the '{cluster}' cluster.",
            -1,
        )

    fargate_task = tasks[0]
    container = "celery-beat"
    c.run(
        f"aws ecs execute-command "
        f"--cluster {cluster} "
        f"--task {fargate_task} "
        f"--container {container} "
        f"--command {command} "
        f"--profile {profile} "
        f"--interactive"
    )


@task(
    help={
        "stacks": f"Comma-separated list of the stacks to deploy ({' | '.join(OCSConfig.ALL_STACKS)})",
        "verbose": "Enable verbose output",
    }
    | PROFILE_HELP
)
def deploy(c: Context, stacks=None, verbose=False, profile=DEFAULT_PROFILE):
    profile = _get_profile_and_auth(c, profile)

    config = _get_config()
    cmd = f"cdk deploy --profile {profile}"
    if stacks:
        stacks = " ".join([config.stack_name(stack) for stack in stacks.split(",")])
        cmd += f" {stacks}"
    else:
        _confirm("Deploy all stacks ?", _exit=True, exit_message="Aborted")
        cmd += " --all"
    if verbose:
        cmd += " --verbose"
    c.run(cmd, echo=True, pty=True)


def _get_profile_and_auth(c: Context, profile):
    if not profile:
        profile = input("AWS profile not set. Enter profile: ")

    if not _check_credentials(c, profile):
        if not login(c, profile):
            raise Exit("Failed to login", -1)

    return profile


@task(help=PROFILE_HELP)
def list_secrets(c: Context, profile=DEFAULT_PROFILE):
    config = _get_config()
    profile = _get_profile_and_auth(c, profile)
    secrets = _get_secrets(c, config, profile, include_missing=True)
    rows = [secret.table_row() for secret in secrets]
    writer = TableWriter(["Name", "Created", "Last Accessed", "Last Changed"], rows)
    writer.write_table()


def _get_secrets(c, config, profile, name="", include_missing=True):
    filter_expr = f'Key="name",Values="{config.make_secret_name(name)}"'
    results = c.run(
        f"aws secretsmanager list-secrets --filter {filter_expr} --profile {profile}",
        hide=True,
        echo=True,
    )
    response = json.loads(results.stdout)
    secrets = [Secret.from_dict(raw) for raw in response.get("SecretList", [])]

    if include_missing:
        present = {secret.name for secret in secrets}
        secrets.extend(
            [
                secret
                for secret in config.get_secrets_list()
                if secret.name not in present
            ]
        )

    return sorted(secrets, key=lambda s: s.name)


@task(help={"name": "Name of the secret to retrieve"} | PROFILE_HELP)
def get_secret_value(c: Context, name, profile=DEFAULT_PROFILE):
    config = _get_config()
    profile = _get_profile_and_auth(c, profile)
    prefix = config.make_secret_name("")
    if not name.startswith(prefix):
        name = config.make_secret_name(name)
    results = c.run(
        f"aws secretsmanager get-secret-value --secret-id {name} --profile {profile}",
        hide=True,
        echo=True,
    )
    response = json.loads(results.stdout)
    secret = Secret.from_dict(response)
    print(f"Name: {secret.name}")
    print(f"Value: {secret.value}")


@task(
    help={
        "name": "Name of the secret to set",
        "value": "Value to set for the secret",
    }
    | PROFILE_HELP
)
def set_secret_value(c: Context, name, value, profile=DEFAULT_PROFILE):
    config = _get_config()
    profile = _get_profile_and_auth(c, profile)
    try:
        secret = config.get_secret(name)
    except ValueError:
        raise Exit("Unknown secret", -1)

    existing = _get_secrets(c, config, profile, name)

    prefix = config.make_secret_name("")
    if not name.startswith(prefix):
        name = config.make_secret_name(name)

    if secret.managed:
        _confirm(
            "This secret is managed. Are you sure you want to update it?",
            _exit=True,
            exit_message="Aborted",
        )

    if existing:
        _confirm(f"Create secret: {name} ?", _exit=True, exit_message="Aborted")
        c.run(
            f"aws secretsmanager create-secret --name {name} --secret-string '{value}' --profile {profile}",
            echo=True,
        )
    else:
        c.run(
            f"aws secretsmanager put-secret-value --secret-id {name} --secret-string '{value}' --profile {profile}",
            echo=True,
        )


@task(help={"name": "Name of the secret to delete"} | PROFILE_HELP)
def delete_secret(c: Context, name, profile=DEFAULT_PROFILE):
    config = _get_config()
    profile = _get_profile_and_auth(c, profile)
    secret = config.get_secret(name)

    if secret.managed:
        _confirm(
            "This secret is managed. Are you sure you want to delete it?",
            _exit=True,
            exit_message="Aborted",
        )

    if _confirm(f"Delete secret {secret.name} ?", _exit=True, exit_message="Aborted"):
        c.run(
            f"aws secretsmanager delete-secret --secret-id {secret.name} --profile {profile}",
            echo=True,
        )


@task(help=PROFILE_HELP)
def create_missing_secrets(c: Context, profile=DEFAULT_PROFILE):
    """Iterate through secrets and prompt for each one that is missing."""
    config = _get_config()
    profile = _get_profile_and_auth(c, profile)
    secrets = _get_secrets(c, config, profile, include_missing=True)
    for secret in secrets:
        if secret.created:
            continue

        value = input(f"Enter value for secret {secret.name} (blank to skip): ")
        if not value:
            print("Skipping...")
            continue

        c.run(
            f"aws secretsmanager create-secret --name {secret.name} --secret-string '{value}' --profile {profile}",
            echo=True,
        )


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


def _get_config():
    return OCSConfig(dotenv_values(".env"))


class TableWriter:
    def __init__(self, headers, rows):
        self.headers = headers
        self.col_widths = [
            max(len(str(cell)) for cell in column) for column in zip(headers, *rows)
        ]
        self.template = " | ".join(
            ["{{:<{}}}".format(width) for width in self.col_widths]
        )
        self.rows = rows

    def write_table(self):
        self.write_headers()
        self.write_separator()
        self.write_rows()
        self.write_separator()

    def write_headers(self):
        print(self.template.format(*self.headers))

    def write_rows(self):
        for row in self.rows:
            print(self.template.format(*row))

    def write_separator(self):
        print(self.template.format(*["-" * width for width in self.col_widths]))
