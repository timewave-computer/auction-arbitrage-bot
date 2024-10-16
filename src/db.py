from sqlite3 import Cursor
from typing import Any


def order_row_count(cur: Cursor) -> int:
    """
    Gets the number of orders in the database.
    """

    res = cur.execute("SELECT COUNT(*) AS cnt FROM orders")

    (cnt,) = res.fetchone()

    return int(cnt)


def insert_order_rows(cur: Cursor, rows: list[list[Any]]) -> None:
    """
    Inserts a new row into orders for each order row.
    Does not commit the transaction.
    """

    cur.executemany(
        """INSERT INTO orders(
        theoretical_profit,
        expected_profit,
        realized_profit,
        status,
        time_created,
        logs_enabled
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )


def insert_legs_rows(cur: Cursor, rows: list[list[Any]]) -> None:
    """
    Inserts a new row into legs for each leg row.
    Does not commit the transaction.
    """

    cur.executemany(
        """INSERT INTO legs(
        route_index,
        order_uid,
        in_amount,
        out_amount,
        in_asset,
        out_asset,
        kind,
        executed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def insert_logs_rows(cur: Cursor, rows: list[tuple[int, int, str]]) -> None:
    """
    Inserts a new row for each log row.
    Does not commit the transaction.
    """

    cur.executemany(
        "INSERT INTO logs(log_index, order_uid, contents) VALUES (?, ?, ?)", rows
    )


def migrate(cur: Cursor) -> None:
    """
    Creates requisite tables in the on-disk db in case they do not already exist.
    Does not commit the transaction.
    """

    cur.execute(
        """CREATE TABLE IF NOT EXISTS orders(
        uid INTEGER NOT NULL PRIMARY KEY,
        theoretical_profit TEXT NOT NULL,
        expected_profit TEXT NOT NULL,
        realized_profit TEXT,
        status TEXT NOT NULL,
        time_created DATETIME NOT NULL,
        logs_enabled BOOL NOT NULL
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS legs(
        route_index INTEGER NOT NULL,
        order_uid INTEGER NOT NULL,
        in_amount TEXT NOT NULL,
        out_amount TEXT,
        in_asset TEXT NOT NULL,
        out_asset TEXT NOT NULL,
        kind TEXT NOT NULL,
        executed BOOL NOT NULL,
        PRIMARY KEY (route_index, order_uid),
        FOREIGN KEY(order_uid) REFERENCES orders(uid)
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS logs(
        log_index INTEGER NOT NULL,
        order_uid INTEGER NOT NULL,
        contents TEXT NOT NULL,
        PRIMARY KEY (log_index, order_uid),
        FOREIGN KEY(order_uid) REFERENCES orders(uid)
        )"""
    )
