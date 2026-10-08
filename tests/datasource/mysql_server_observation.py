"""Independent bounded observation of two already-closed owned MySQL connections."""

import time

from tests.datasource.environment import mysql_analysis as mysql


def wait_for_owned_connection_release(data_id: int, control_id: int) -> float:
    """Require server disappearance without issuing cancellation or cleanup SQL."""
    started = time.monotonic()
    with mysql.connection(admin=True) as observer, observer.cursor() as cursor:
        while True:
            cursor.execute(
                "SELECT ID, COMMAND, STATE FROM information_schema.PROCESSLIST WHERE ID IN (%s,%s)",
                (data_id, control_id),
            )
            remaining: object = cursor.fetchall()
            elapsed = time.monotonic() - started
            if remaining == ():
                return elapsed
            assert elapsed < 5, remaining
            time.sleep(0.01)
