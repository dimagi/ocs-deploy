import difflib
import os
import shlex
from pathlib import Path

from invoke import Context, Exit, task
from termcolor import cprint

from ocs_deploy.cli.tasks_utils import confirm

DEFAULT_VAULT = os.getenv("OP_VAULT", "GSO: Open Chat Studio Team (OCS)")
ITEM_TEMPLATE = os.getenv("OP_ITEM_TEMPLATE", "ocs .env.{env}")

VAULT_HELP = {
    "vault": "1Password vault to use. Reads from the OP_VAULT env var, "
    "otherwise defaults to 'GSO: Open Chat Studio Team (OCS)'."
}


def _env_name(c: Context):
    env = c.config.environment
    if not env:
        raise Exit(
            "No environment specified. Use '--env' or the 'OCS_DEPLOY_ENV' environment variable.",
            -1,
        )
    return env


def _item_title(env: str):
    return ITEM_TEMPLATE.format(env=env)


def _resolve_vault(vault):
    vault = vault or DEFAULT_VAULT
    if not vault:
        raise Exit(
            "No 1Password vault specified. Use '--vault' or the 'OP_VAULT' env var.",
            -1,
        )
    return vault


def _require_op(c: Context):
    if not c.run("command -v op", hide=True, warn=True).ok:
        raise Exit(
            "1Password CLI 'op' not found. See https://developer.1password.com/docs/cli/get-started/",
            -1,
        )


def _item_exists(c: Context, title: str, vault: str):
    result = c.run(
        f"op item get {shlex.quote(title)} --vault {shlex.quote(vault)}",
        hide=True,
        warn=True,
    )
    return result.ok


def _get_document(c: Context, title: str, vault: str):
    result = c.run(
        f"op document get {shlex.quote(title)} --vault {shlex.quote(vault)}",
        hide=True,
    )
    return result.stdout


def _print_diff(remote: str, local: str, title: str, path: Path):
    diff = list(
        difflib.unified_diff(
            remote.splitlines(),
            local.splitlines(),
            fromfile=f"1Password: {title}",
            tofile=f"local: {path}",
            lineterm="",
        )
    )
    if not diff:
        return False

    for line in diff:
        if line.startswith("+"):
            cprint(line, color="green")
        elif line.startswith("-"):
            cprint(line, color="red")
        elif line.startswith("@@"):
            cprint(line, color="cyan")
        else:
            print(line)
    return True


@task(help=VAULT_HELP)
def pull(c: Context, vault=None):
    """Download the .env.<env> file from 1Password."""
    _require_op(c)
    env = _env_name(c)
    vault = _resolve_vault(vault)
    title = _item_title(env)
    path = Path(f".env.{env}")

    if not _item_exists(c, title, vault):
        raise Exit(
            f"No 1Password item '{title}' found in vault '{vault}'. "
            f"Create it first with 'ocs --env {env} env.push'.",
            -1,
        )

    if path.exists():
        confirm(f"{path} already exists. Overwrite it?")

    c.run(
        f"op document get {shlex.quote(title)} --vault {shlex.quote(vault)} "
        f"--out-file {shlex.quote(str(path))} --force",
        echo=True,
    )
    cprint(f"Downloaded {path} from 1Password.", color="green")


@task(help=VAULT_HELP)
def push(c: Context, vault=None):
    """Upload the local .env.<env> file to 1Password."""
    _require_op(c)
    env = _env_name(c)
    vault = _resolve_vault(vault)
    title = _item_title(env)
    path = Path(f".env.{env}")

    if not path.exists():
        raise Exit(f"Environment file not found: {path}", -1)

    quoted_path = shlex.quote(str(path))
    quoted_title = shlex.quote(title)
    quoted_vault = shlex.quote(vault)

    if _item_exists(c, title, vault):
        remote = _get_document(c, title, vault)
        if not _print_diff(remote, path.read_text(), title, path):
            cprint(
                f"'{title}' in vault '{vault}' is already up to date.", color="green"
            )
            return
        confirm(f"This will overwrite '{title}' in vault '{vault}'. Continue?")
        c.run(
            f"op document edit {quoted_title} {quoted_path} --vault {quoted_vault}",
            echo=True,
        )
    else:
        c.run(
            f"op document create {quoted_path} --title {quoted_title} --vault {quoted_vault}",
            echo=True,
        )
    cprint(f"Uploaded {path} to 1Password.", color="green")
