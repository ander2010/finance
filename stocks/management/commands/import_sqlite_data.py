import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from psycopg2.extras import execute_values


TABLES = [
    'auth_group',
    'auth_user',
    'auth_user_groups',
    'auth_user_user_permissions',
    'django_admin_log',
    'django_session',
    'stocks_ticker',
    'stocks_tickerprice',
    'stocks_tickeranalysis',
    'stocks_stocklist',
    'stocks_tradingprofile',
    'stocks_watchlist',
    'stocks_trade',
]

TRUNCATE_TABLES = [
    'stocks_trade',
    'stocks_watchlist',
    'stocks_stocklist',
    'stocks_tradingprofile',
    'stocks_tickeranalysis',
    'stocks_tickerprice',
    'stocks_ticker',
    'django_admin_log',
    'django_session',
    'auth_user_groups',
    'auth_user_user_permissions',
    'auth_user',
    'auth_group',
]

JSON_COLUMNS = {
    'stocks_tickeranalysis': {'strategies_triggered', 'sma9_data', 'market_structure'},
    'stocks_trade': {'validation_reasons', 'score_breakdown'},
}

IMPORT_BATCH_SIZE = 5000

SEQUENCE_TABLES = [
    'auth_group',
    'auth_user',
    'django_admin_log',
    'stocks_ticker',
    'stocks_tickerprice',
    'stocks_tickeranalysis',
    'stocks_stocklist',
    'stocks_tradingprofile',
    'stocks_watchlist',
    'stocks_trade',
]


class Command(BaseCommand):
    help = 'Import project data from the old SQLite database into the active Django database.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sqlite-path',
            default=str(settings.BASE_DIR / 'db.sqlite3'),
            help='Path to the SQLite database to import from.',
        )
        parser.add_argument(
            '--truncate',
            action='store_true',
            help='Delete existing user/app data in the active database before importing.',
        )

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('Active database must be PostgreSQL.')

        sqlite_path = Path(options['sqlite_path'])
        if not sqlite_path.exists():
            raise CommandError(f'SQLite database not found: {sqlite_path}')

        sqlite_conn = sqlite3.connect(str(sqlite_path))
        sqlite_conn.row_factory = sqlite3.Row
        self._bool_columns_cache = {}

        try:
            with transaction.atomic():
                if options['truncate']:
                    self._truncate_postgres()

                total = 0
                for table in TABLES:
                    count = self._copy_table(sqlite_conn, table)
                    total += count
                    self.stdout.write(f'  {table:<28} {count:>8}')
                    self.stdout.flush()

                self._reset_sequences()

            self.stdout.write(self.style.SUCCESS(f'\nImported {total} rows from {sqlite_path}.'))
        finally:
            sqlite_conn.close()

    def _truncate_postgres(self):
        quoted = ', '.join(connection.ops.quote_name(t) for t in TRUNCATE_TABLES)
        with connection.cursor() as cursor:
            cursor.execute(f'TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE')
        self.stdout.write('Existing PostgreSQL user/app data truncated.\n')

    def _copy_table(self, sqlite_conn, table):
        columns = self._sqlite_columns(sqlite_conn, table)
        if not columns:
            return 0

        total_rows = sqlite_conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if not total_rows:
            return 0

        quoted_table = connection.ops.quote_name(table)
        quoted_columns = ', '.join(connection.ops.quote_name(c) for c in columns)
        placeholders = []
        json_columns = JSON_COLUMNS.get(table, set())

        for column in columns:
            if column in json_columns:
                placeholders.append('CAST(%s AS jsonb)')
            else:
                placeholders.append('%s')

        sql = f'INSERT INTO {quoted_table} ({quoted_columns}) VALUES %s'
        template = f'({", ".join(placeholders)})'
        bool_columns = self._postgres_boolean_columns(table)
        copied = 0
        source_cursor = sqlite_conn.execute(f'SELECT * FROM "{table}"')
        with connection.cursor() as cursor:
            while True:
                rows = source_cursor.fetchmany(IMPORT_BATCH_SIZE)
                if not rows:
                    break

                values = [
                    tuple(self._clean_value(table, column, row[column], bool_columns) for column in columns)
                    for row in rows
                ]
                execute_values(cursor.cursor, sql, values, template=template, page_size=len(values))
                copied += len(rows)

                if total_rows >= IMPORT_BATCH_SIZE * 5:
                    self.stdout.write(f'    {table}: {copied}/{total_rows}')
                    self.stdout.flush()

        return copied

    def _sqlite_columns(self, sqlite_conn, table):
        info = sqlite_conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        return [row['name'] for row in info]

    def _postgres_boolean_columns(self, table):
        if table in self._bool_columns_cache:
            return self._bool_columns_cache[table]

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                  AND data_type = 'boolean'
                """,
                [table],
            )
            columns = {row[0] for row in cursor.fetchall()}

        self._bool_columns_cache[table] = columns
        return columns

    def _clean_value(self, table, column, value, bool_columns):
        if value is not None and column in bool_columns:
            return bool(value)
        return value

    def _reset_sequences(self):
        with connection.cursor() as cursor:
            for table in SEQUENCE_TABLES:
                cursor.execute(
                    """
                    SELECT setval(
                        pg_get_serial_sequence(%s, 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1),
                        (SELECT MAX(id) FROM {table}) IS NOT NULL
                    )
                    """.format(table=connection.ops.quote_name(table)),
                    [table],
                )
