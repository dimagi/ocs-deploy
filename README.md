# Open Chat Studio CDK Deploy

This project contains the AWS Cloud Development Kit (CDK) stack for deploying the
[Open Chat Studio](https://github.com/dimagi/open-chat-studio/) infrastructure.

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Quickstart](#quickstart)
4. [First Time Deployment Steps](#first-time-deployment-steps)
5. [Steady State Deployment Steps](#steady-state-deployment-steps)
6. [Connecting to Running Services](#connecting-to-running-services)
7. [Managing Multiple Environments](#managing-multiple-environments)
8. [Adding Environment Variables](#adding-environment-variables)
9. [Other Useful CDK Commands](#other-useful-cdk-commands)
10. [Troubleshooting](#troubleshooting)

## Architecture Overview

The architecture consists of various AWS components:

- **RDS PostgreSQL** database
- **Elasticache Redis**
- **Elastic Container Registry (ECR)**
- **ECS Fargate** services for the Django application, including:
  - **Django web service**:
    - Runs two containers: one for executing migrations and another for running the Gunicorn server.
    - The migrations container runs first and the Gunicorn container starts afterward.
    - The service is behind an Application Load Balancer (ALB) with a health check target group.
  - **Celery worker service**
  - **Celery beat service**

Additional components set up by this project include:

- VPC with public and private subnets
- Load balancer for the Django service
- S3 buckets for media files
- Certificate Manager certificates for domain management
- Email identity verification for the domain
- GitHub Actions roles for the CI/CD pipeline
- Secrets Manager for storing Django secrets

## Project Structure

This section describes the layout of the project:

- `ocs_deploy/cli/*.py`: Scripts for deploying CDK stacks and managing secrets.
  - Run `ocs -l` to see available tasks.
- `cdk.json`: Configuration file for the CDK Toolkit.
- `ocs_deploy/`: Contains the CDK stack definitions.

## Quickstart

This guide assumes you have [uv](https://docs.astral.sh/uv/getting-started/installation/) installed.

### 1. Set Up the Tools

```bash
$ uv venv
$ uv pip install -e .
$ source .venv/bin/activate
$ ocs -l
```

### 2. Create Your Configuration

```bash
$ ocs init <env>
```

Edit the generated `.env.{env name}` file to set your required configurations.

## First Time Deployment Steps

### Prerequisites

- You have an AWS account with the necessary permissions and configured SSO.
- You have the correct AWS profile set. The `--profile X` argument can also be used via the command line, and the default profile is `ocs-{env name}`.
  ```bash
  export AWS_PROFILE=XXX
  ```
- SSO credentials are configured (`aws configure sso`).
- You have permissions to create resources in the account.
- For new environments, run:
  ```bash
  ocs --env <env> aws.bootstrap
  ```

### Deployment Steps

1. **Set Up RDS, Redis, S3 and the ECR repository**

    ```bash
    ocs --env <env> aws.deploy --stacks ec2tmp,rds,redis,s3,ecr
    ```
   
2. Next, push the initial version of the Docker image to the registry:

    ```bash
    export AWS_ACCOUNT_ID=xxx \
      && export AWS_REGION=us-east-1 \
      && export AWS_PROFILE=xxx \
      && export OCS_ENV=<env e.g. dev> \
      && export OCS_NAME=<name e.g. chatbots> \
      && export REGISTRY=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com \
      && export IMAGE=$REGISTRY/$OCS_NAME-$OCS_ENV-ecr-repo
    docker build . -t "$IMAGE:latest" -f Dockerfile --build-arg SECRET_KEY=123 --build-arg=DJANGO_ALLOWED_HOSTS="dummy"
    aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $REGISTRY
    docker push "$IMAGE" --all-tags
    ```

    For more details on Docker image pushing, visit the [AWS ECR documentation](https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html).

3. **Set Up Domains and GitHub Roles**

    ```bash
    ocs --env <env> aws.deploy --stacks domains,github
    ```
   - Create DNS entries for domain and email domain verification.
   - CNAME records will be provided in the stack output.

4. **Create Necessary Secrets**

    ```bash
    ocs --env <env> secrets.create-missing
    ```

5. **Set Up the Django Service**

    ```bash
    ocs --env <env> aws.deploy --stacks django
    ```

## Steady State Deployment Steps

After the initial deployment, you can deploy any stack independently. Typically, you will only run the CDK deploy when changing infrastructure. For code deployments, use the GitHub Actions defined in the [Open Chat Studio](https://github.com/dimagi/open-chat-studio/) repository.

## Connecting to Running Services

To connect to a running service, use the `ocs connect` command:

```bash
ocs --env <env> connect  # Default command is /bin/bash
ocs --env <env> connect --command "python manage.py shell"
```

## Managing Multiple Environments

This project supports multiple deployment environments (e.g., `dev`, `prod`) using separate `.env` files:

- `.env.dev` for the development environment
- `.env.prod` for the production environment

The environment name is passed using the `--env` argument, e.g.:

```bash
ocs --env dev aws.deploy
```

The environment name is also used to set the default AWS CLI profile:

```bash
AWS_PROFILE="ocs-<env>"  # e.g., "ocs-dev"
```

## Adding Environment Variables

### Non-Secret Environment Variable

1. Add the variable to `.env.<env>` and `.env.example`.
2. Update the `ocs_deploy.config.OCSConfig` class with the new variable.
3. Update the `ocs_deploy/fargate.py` file to include the variable in the `env_dict` method.

Deploy the Django service to apply changes:

```bash
ocs --env <env> aws.deploy --stacks django
```

### Secret Environment Variable

1. Add the secret's name to the `ocs_deploy/secrets.yml`.
2. Set the secret value:

    ```bash
    ocs --env <env> secrets.set SECRET_NAME SECRET_VALUE
    ```

After setting, deploy the Django service to include the new secret:

```bash
ocs --env <env> aws.deploy --stack django
```

If this step fails ensure that all secrets are set. To re-run this step you will need to manually delete the
`$NAME-$ENV-CeleryWorkerLogs`, `$NAME-$ENV-DjangoLogs`, and `$NAME-$ENV-CeleryBeatLogs` log groups in CloudWatch.

## Other Useful CDK Commands

- `cdk ls`: List all stacks in the app.
- `cdk synth`: Emit the synthesized CloudFormation template.
- `cdk deploy`: Deploy the stack to your default AWS account/region.
- `cdk diff`: Compare the deployed stack with the current state.
- `cdk docs`: Open CDK documentation.

## Troubleshooting

**Common Issues**

1. **AWS Credentials Not Set**: Ensure that your AWS credentials are correctly configured and that AWS_PROFILE is set.
2. **Docker Login Issues**: If you encounter issues with Docker login, ensure that you are using the correct AWS region and account ID.
3. **Secrets Not Found** : If a secret is not found, ensure that it is correctly defined in the secrets.yml file and that it has been created in AWS Secrets Manager.
4. 
For more detailed troubleshooting, refer to the [AWS CDK documentation](https://docs.aws.amazon.com/cdk/latest/guide/troubleshooting.html).
