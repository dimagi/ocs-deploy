# Open Chat Studio CDK Deploy

This project contains the CDK stack for deploying the [Open Chat Studio](https://github.com/dimagi/open-chat-studio/)
infrastructure.

The rough architecture is as follows:

* RDS PostgreSQL database
* Elasticache Redis
* Elastic Container Registry (ECR)
* ECS Fargate service for the Django application
  * Django web service
    * This service has two containers, one to run Django migrations and the other to run the Gunicorn server.
      The migrations container runs first and then the Gunicorn container is started once it has completed.
    * The Django service is behind an ALB and has a target group for health checks
  * Celery worker service
  * Celery beat service

In addition to the above services, this project also sets up the following:

* VPC with public and private subnets
* Load balancer for the Django service
* S3 buckets for media files
* Certificate Manager certificates for the domain
* Email identity verification for the domain
* GitHub Actions roles for the CI/CD pipeline
* Secrets Manager for storing the Django secrets

## Project Layout

* `ocs_deploy/cli/*.py` - Invoke tasks for deploying the CDK stacks, managing secrets etc
  * Run `ocs -l` to see the available tasks
* `cdk.json` file tells the CDK Toolkit how to execute the app
* `ocs_deploy/` directory contains the CDK stack definitions

## Quickstart

This assumes you already have `uv` [installed](https://docs.astral.sh/uv/getting-started/installation/).

### 1. Set up the tools

```shell
$ uv venv
$ uv pip install -e .
$ source .venv/bin/activate
$ ocs -l
```

### 2. Create your configuration

```shell
$ ocs init <env>
```

Now edit the `.env.{env name}` file to set the necessary configuration.

## First time deploy steps

Assumptions:

* You have an AWS Account with the necessary permissions and SSO configured
* `export AWS_PROFILE=XXX` is set
* SSO credentials are set up (`aws sso login`)
* You have the necessary permissions to create the resources in the account

Steps:

1. Set up the ECR repository

    ```shell
    ocs --env <env> aws.deploy -s ecr -v
    ```

    Now push the initial version of the Docker image to the registry. This is needed to create the ECS service.
    See https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html
       
2. Set up RDS, Redis, S3

    ```shell
    ocs --env <env> aws.deploy -s rds,redis,s3 -v
    ```
   
3. Set up the domains, github roles etc.

    ```shell
    ocs --env <env> aws.deploy -s domains,github -v
    ```

   * Create the DNS entries for the domain and email domain verification
   * The CNAME records will be included in the stack output

4. Create the necessary secrets

    ```shell
    ocs --env <env> secrets.create-missing
    ```

5. Set up the Django service

    ```shell
    ocs --env <env> aws.deploy -s django -v
    ```
   
## Steady state deploy steps

After the initial deploy, you can deploy any stack independently. Typically, you would only need to 
run the CDK deploy when changing the infrastructure. For code deploys you can use the GitHub Actions defined
in the [Open Chat Studio](https://github.com/dimagi/open-chat-studio/) repository.

## Connecting to a running service

To connect to the running service, you can use the `ocs connect` command to run a command or get a shell:

```shell
ocs --env <env> connect  # the default command is /bin/bash
ocs --env <env> connect --command "python manage.py shell"
```

## Adding a new environment variable

The Django services require certain environment variables to be set. These can be either non-secret or secret environment variables.

### A non-secret environment variable

To add a non-secret environment variable:

1. Add the environment variable to `.env.<env>` and `.env.example` files.
2. Update the `ocs_deploy.config.OCSConfig` class to include the new environment variable.
3. Update the `ocs_deploy/fargate.py` file to include the new environment variable in the `env_dict` method.

Having completed this you can update the Django service to make the new environment variable available:

```shell
ocs --env <env> aws.deploy -s django
```

### A secret environment variable

To add a secret to the Secrets Manager, first add the secret name to the `ocs_deploy/secrets.yml` file. Then run:

```shell
ocs --env <env> secrets.set SECRET_NAME SECRET_VALUE
```

After setting the secret value you can update the Django service include the new secret as an environment variable:

```shell
ocs --env <env> aws.deploy -s django
```

## Other Useful CDK commands

 * `cdk ls`          list all stacks in the app
 * `cdk synth`       emits the synthesized CloudFormation template
 * `cdk deploy`      deploy this stack to your default AWS account/region
 * `cdk diff`        compare deployed stack with current state
 * `cdk docs`        open CDK documentation
