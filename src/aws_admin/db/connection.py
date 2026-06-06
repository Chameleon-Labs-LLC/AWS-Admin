"""Build a psycopg connection from config + the vault-stored password."""
from __future__ import annotations

import psycopg

from .. import config, vault


def connect(read_only: bool = True, _connect=None):
    """Open a psycopg connection (sslmode=require). `_connect` is injectable for tests.

    The connection's transactions are read-only unless `read_only=False`.
    """
    connect_fn = _connect or psycopg.connect
    conn = connect_fn(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=vault.get_db_password(),
        sslmode=config.DB_SSLMODE,
    )
    conn.read_only = read_only
    return conn
