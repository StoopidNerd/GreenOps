# Sample infrastructure for GreenOps Agent development.
# This file is illustrative only - do NOT apply it to a real account.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
  # No credentials are configured here on purpose.
}

variable "environment" {
  type    = string
  default = "dev"
}

resource "aws_instance" "web_01" {
  ami           = "ami-00000000000000000" # placeholder
  instance_type = "m5.xlarge"

  tags = {
    Name        = "web-01"
    Environment = var.environment
    Owner       = "platform-team"
  }
}

resource "aws_instance" "batch_01" {
  ami           = "ami-00000000000000000" # placeholder
  instance_type = "c5.xlarge"

  tags = {
    Name        = "batch-01"
    Environment = var.environment
    Owner       = "data-team"
  }
}

resource "aws_instance" "dev_sandbox_01" {
  ami           = "ami-00000000000000000" # placeholder
  instance_type = "m5.2xlarge"

  tags = {
    Name        = "dev-sandbox-01"
    Environment = "dev"
    Owner       = "ml-team"
  }
}
