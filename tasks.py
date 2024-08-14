from invoke import Collection, Context, Exit, task

import tasks_aws
import tasks_secrets
from tasks_aws_utils import aws_login
from tasks_utils import confirm


@task
def requirements(c: Context, upgrade_all=False, upgrade_package=None):
    """Update requirement lock files."""
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

    if confirm("\nInstall requirements ?", _exit=False):
        cmd = "uv pip" if has_uv.ok else "pip"
        c.run(f"{cmd} install -r requirements-dev.txt", echo=True, pty=True)


@task
def ruff(c: Context, no_fix=False, unsafe_fixes=False):
    """Run ruff checks and formatting. Use --unsafe-fixes to apply unsafe fixes."""
    fix_flag = "" if no_fix else "--fix"
    unsafe_fixes_flag = "--unsafe-fixes" if unsafe_fixes else ""
    c.run(f"ruff check {fix_flag} {unsafe_fixes_flag}", echo=True, pty=True)
    c.run("ruff format", echo=True, pty=True)


aws_collection = Collection.from_module(tasks_aws, name="aws")
aws_collection.add_task(aws_login)
namespace = Collection(
    ruff,
    requirements,
    Collection.from_module(tasks_secrets, name="secrets"),
    aws_collection,
)
