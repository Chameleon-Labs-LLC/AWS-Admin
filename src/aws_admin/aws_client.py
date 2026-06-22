"""Thin boto3 client factories (use the default ~/.aws profile)."""
from __future__ import annotations

import boto3

from . import config


def amplify_client():
    return boto3.client("amplify", region_name=config.REGION)


def ce_client():
    """Cost Explorer. Region-pinned to us-east-1 (the global CE endpoint)."""
    return boto3.client("ce", region_name=config.REGION)


def freetier_client():
    """Free Tier API. Only served from us-east-1."""
    return boto3.client("freetier", region_name=config.REGION)


def sts_client():
    return boto3.client("sts", region_name=config.REGION)
