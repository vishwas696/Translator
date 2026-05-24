from datetime import UTC, datetime
from decimal import Decimal
import os
import unittest

from translator.storage.mysql import (
    bool_env,
    db_datetime_from_iso,
    decimal_to_float,
    iso_from_db,
    json_value,
    mysql_config_from_env,
)


class BackendMySqlStoreTests(unittest.TestCase):
    def test_mysql_config_defaults_to_local_translator_database(self) -> None:
        previous_values = {
            name: os.environ.get(name)
            for name in [
                "MYSQL_HOST",
                "MYSQL_PORT",
                "MYSQL_DATABASE",
                "MYSQL_USER",
                "MYSQL_PASSWORD",
            ]
        }
        for name in previous_values:
            os.environ.pop(name, None)
        try:
            config = mysql_config_from_env()
        finally:
            for name, value in previous_values.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(config.host, "localhost")
        self.assertEqual(config.port, 3306)
        self.assertEqual(config.database, "translator_backend")
        self.assertEqual(config.user, "root")

    def test_json_value_accepts_mysql_json_strings_and_native_values(self) -> None:
        self.assertEqual(json_value('{"a": 1}', {}), {"a": 1})
        self.assertEqual(json_value(b'["x"]', []), ["x"])
        self.assertEqual(json_value({"already": True}, {}), {"already": True})
        self.assertEqual(json_value("", {"fallback": True}), {"fallback": True})

    def test_datetime_helpers_normalize_to_utc_iso_and_db_datetime(self) -> None:
        source = "2026-05-22T10:30:15+00:00"

        db_value = db_datetime_from_iso(source)
        iso_value = iso_from_db(datetime(2026, 5, 22, 10, 30, 15))

        self.assertEqual(db_value.tzinfo, None)
        self.assertEqual(db_value.isoformat(), "2026-05-22T10:30:15")
        self.assertEqual(iso_value, "2026-05-22T10:30:15+00:00")
        self.assertEqual(
            iso_from_db(datetime(2026, 5, 22, 10, 30, 15, tzinfo=UTC)),
            "2026-05-22T10:30:15+00:00",
        )

    def test_decimal_to_float_and_bool_env(self) -> None:
        previous = os.environ.get("MYSQL_SSL_DISABLED")
        os.environ["MYSQL_SSL_DISABLED"] = "true"
        try:
            enabled = bool_env("MYSQL_SSL_DISABLED", False)
        finally:
            if previous is None:
                os.environ.pop("MYSQL_SSL_DISABLED", None)
            else:
                os.environ["MYSQL_SSL_DISABLED"] = previous

        self.assertTrue(enabled)
        self.assertEqual(decimal_to_float(Decimal("1.250000")), 1.25)


if __name__ == "__main__":
    unittest.main()
