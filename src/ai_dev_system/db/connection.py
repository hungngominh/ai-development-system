import psycopg


def get_connection(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=psycopg.rows.dict_row)
