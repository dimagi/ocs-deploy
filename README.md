# Open Chat Studio CDK Deploy

This project contains the CDK stack for deploying the [Open Chat Studio](https://github.com/dimagi/open-chat-studio/)
infrastructure.

The rough architecture is as follows:

* RDS PostgreSQL database
* Elasticache Redis cache, Celery broker
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

* `task*.py` - Invoke tasks for deploying the CDK stacks, managing secrets etc
  * Run `inv -l` to see the available tasks
* `cdk.json` file tells the CDK Toolkit how to execute the app
* `requirements.txt` file lists the Python dependencies
* `ocs_deploy/` directory contains the CDK stack definitions

To manually create a virtualenv on MacOS and Linux:

```
$ python3 -m venv .venv
```

After the init process completes and the virtualenv is created, you can use the following
step to activate your virtualenv.

```
$ source .venv/bin/activate
```

If you are a Windows platform, you would activate the virtualenv like this:

```
% .venv\Scripts\activate.bat
```

Once the virtualenv is activated, you can install the required dependencies.

```
$ pip install -r requirements.txt
```

### Other Useful CDK commands

 * `cdk ls`          list all stacks in the app
 * `cdk synth`       emits the synthesized CloudFormation template
 * `cdk deploy`      deploy this stack to your default AWS account/region
 * `cdk diff`        compare deployed stack with current state
 * `cdk docs`        open CDK documentation

## First time deploy steps

Assumptions:

* You have an AWS Account with the necessary permissions and SSO configured
* `export AWS_PROFILE=XXX` is set
* SSO credentials are set up (`aws sso login`)
* You have the necessary permissions to create the resources in the account

Steps:

1. Create a `.env` file with the necessary environment variables. You can copy the `.env.example` file and fill in the values.

    ```shell
    cp .env.example .env
    ```

2. Set up the ECR repository

    ```shell
    inv aws.deploy -s ecr -v
    ```

    Now push the initial version of the Docker image to the registry. This is needed to create the ECS service.
    See https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html
       
3. Set up RDS, Redis, S3

    ```shell
    inv aws.deploy -s rds,redis,s3 -v
    ```
   
4. Set up the domains, github roles etc.

    ```shell
    inv aws.deploy -s domains,github -v
    ```

   * Create the DNS entries for the domain and email domain verification
   * The CNAME records will be included in the stack

5. Create the necessary secrets

    ```shell
    inv secrets.create-missing
    ```

6. Set up the Django service

    ```shell
    inv aws.deploy -s django -v
    ```
   
## Steady state deploy steps

After the initial deploy, you can deploy any stack independently. Typically, you would only need to 
run the CDK deploy when changing the infrastructure. For code deploys you can use the GitHub Actions defined
in the [Open Chat Studio](https://github.com/dimagi/open-chat-studio/) repository.

## Connecting to a running service

To connect to the running service, you can use the `inv connect` command to run a command or get a shell:

```shell
inv connect  # the default command is /bin/bash
inv connect --command "python manage.py shell"
```

## Adding a new environment variable

The Django services require certain environment variables to be set. These can be either non-secret or secret environment variables.

### A non-secret environment variable

To add a non-secret environment variable:

1. Add the environment variable to `.env` and `.env.example` files.
2. Update the `ocs_deploy.config.OCSConfig` class to include the new environment variable.
3. Update the `ocs_deploy/fargate.py` file to include the new environment variable in the `env_dict` method.

Having completed this you can update the Django service to make the new environment variable available:

```shell
inv aws.deploy -s django
```

### A secret environment variable

To add a secret to the Secrets Manager, first add the secret name to the `ocs_deploy/secrets.yml` file. Then run:

```shell
inv secrets.set SECRET_NAME SECRET_VALUE
```

After setting the secret value you can update the Django service include the new secret as an environment variable:

```shell
inv aws.deploy -s django
```
