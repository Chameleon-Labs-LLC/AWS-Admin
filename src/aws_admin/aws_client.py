"""Thin boto3 Amplify client factory (uses the default ~/.aws profile)."""
from __future__ import annotations

import boto3

from . import config


def amplify_client():
    return boto3.client("amplify", region_name=config.REGION)
