
# Welcome to your CDK Python project!

This is a blank project for CDK development with Python.

The `cdk.json` file tells the CDK Toolkit how to execute your app.

This project is set up like a standard Python project.  The initialization
process also creates a virtualenv within this project, stored under the `.venv`
directory.  To create the virtualenv it assumes that there is a `python3`
(or `python` for Windows) executable in your path with access to the `venv`
package. If for any reason the automatic creation of the virtualenv fails,
you can create the virtualenv manually.

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

At this point you can now synthesize the CloudFormation template for this code.

```
$ export AWS_PROFILE=XXX
$ aws sso login
$ cdk synth
```

To add additional dependencies, for example other CDK libraries, just add
them to your `setup.py` file and rerun the `pip install -r requirements.txt`
command.

## Useful commands

 * `cdk ls`          list all stacks in the app
 * `cdk synth`       emits the synthesized CloudFormation template
 * `cdk deploy`      deploy this stack to your default AWS account/region
 * `cdk diff`        compare deployed stack with current state
 * `cdk docs`        open CDK documentation

Enjoy!

## First time deploy steps

Assumptions:

* You have an AWS Account with the necessary permissions and SSO configured
* `export AWS_PROFILE=XXX` is set
* SSO credentials are set up (`aws sso login`)

Steps:

1. Set up the ECR repository

    ```shell
    inv deploy -s ecr -v
    ```

    Now push the initial version of the Docker image to the registry. This is needed to create the ECS service.
    See https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html
       
2. Set up RDS, Redis, S3

    ```shell
    inv deploy -s rds,redis,s3 -v
    ```
   
3. Set up the domains

    ```shell
    inv deploy -s domains -v
    ```

   * Create the DNS entries for the domain and email domain verification
   * The CNAME records will be included in the stack

4. Set up the Django service

    ```shell
    inv deploy -s django -v
    ```
