import json

from invoke import Context, Exit, task

from ocs_deploy.config import Secret
from ocs_deploy.cli.tasks_aws_utils import (
    DEFAULT_PROFILE,
    PROFILE_HELP,
    _get_config,
    get_profile_and_auth,
)
from ocs_deploy.cli.tasks_utils import confirm


@task(name="list", help=PROFILE_HELP)
def list_secrets(c: Context, profile=DEFAULT_PROFILE):
    """List all secrets."""
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
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


@task(name="get", help={"name": "Name of the secret to retrieve"} | PROFILE_HELP)
def get_secret_value(c: Context, name, profile=DEFAULT_PROFILE):
    """Get a secret value by name."""
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
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
    name="set",
    help={
        "name": "Name of the secret to set",
        "value": "Value to set for the secret",
    }
    | PROFILE_HELP,
)
def set_secret_value(c: Context, name, value, profile=DEFAULT_PROFILE):
    """Set a secret value by name."""
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
    try:
        secret = config.get_secret(name)
    except ValueError:
        raise Exit("Unknown secret", -1)

    existing = _get_secrets(c, config, profile, name)

    prefix = config.make_secret_name("")
    if not name.startswith(prefix):
        name = config.make_secret_name(name)

    if secret.managed:
        confirm(
            "This secret is managed. Are you sure you want to update it?",
            _exit=True,
            exit_message="Aborted",
        )

    if not existing:
        confirm(f"Create secret: {name} ?", _exit=True, exit_message="Aborted")
        c.run(
            f"aws secretsmanager create-secret --name {name} --secret-string '{value}' --profile {profile}",
            echo=True,
        )
    else:
        c.run(
            f"aws secretsmanager put-secret-value --secret-id {name} --secret-string '{value}' --profile {profile}",
            echo=True,
        )


@task(name="delete", help={"name": "Name of the secret to delete"} | PROFILE_HELP)
def delete_secret(c: Context, name, profile=DEFAULT_PROFILE, force=False):
    """Delete a secret by name."""
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
    secret = config.get_secret(name)

    if secret.managed:
        confirm(
            "This secret is managed. Are you sure you want to delete it?",
            _exit=True,
            exit_message="Aborted",
        )

    cmd = f"aws secretsmanager delete-secret --secret-id {secret.name} --profile {profile}"
    if force:
        cmd += " --force-delete-without-recovery"
    if confirm(f"Delete secret {secret.name} ?", _exit=True, exit_message="Aborted"):
        c.run(
            cmd,
            echo=True,
        )


@task(name="create-missing", help=PROFILE_HELP)
def create_missing_secrets(c: Context, profile=DEFAULT_PROFILE):
    """Iterate through secrets and prompt for each one that is missing."""
    config = _get_config(c)
    profile = get_profile_and_auth(c, profile)
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
