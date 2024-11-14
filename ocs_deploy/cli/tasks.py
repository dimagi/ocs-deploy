import os
from pathlib import Path
from typing import List, Optional

from invoke import Argument, Collection, Context, Exit, task
from invoke import Program
from termcolor import cprint

from ocs_deploy.cli import tasks_aws
from ocs_deploy.cli import tasks_secrets
from ocs_deploy.cli.tasks_aws_utils import aws_login, django_shell


@task
def init(c: Context, env):
    """Initialize an environment."""

    env_file = f".env.{env}"
    path = Path(env_file)
    if path.exists():
        raise Exit(f"Environment {env} already exists.")

    c.run(f"cp .env.example {env_file}")
    cprint(f"Environment {env} initialized.", color="green")
    print(f"  - Update the configuration in '{path}'.")


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
    init,
    ruff,
    django_shell,
    Collection.from_module(tasks_secrets, name="secrets"),
    aws_collection,
)


class OcsInvokeProgram(Program):
    def core_args(self):
        core_args = super().core_args()
        extra_args = [
            Argument(
                name="env",
                help="The environment to use",
            ),
        ]
        return core_args + extra_args

    def parse_core(self, argv: Optional[List[str]]) -> None:
        super().parse_core(argv)
        env = self.args.env.value
        if not env:
            env = os.getenv("OCS_DEPLOY_ENV")
        self.config["environment"] = env


program = OcsInvokeProgram(name="ocs-deploy", namespace=namespace)
