"""A disconnected browser stops work and releases per-run resources."""
import sqlite3
import unittest
from unittest.mock import patch

import lab


class DisconnectedStreamTests(unittest.TestCase):
    def assert_disconnect_closes_resources(self, event_type, exception_type):
        stores, clients, emitted = [], [], []
        original_store = lab.Store
        original_client = lab.anthropic.Anthropic

        def make_store(*args, **kwargs):
            store = original_store(*args, **kwargs)
            stores.append(store)
            return store

        def make_client(*args, **kwargs):
            client = original_client(*args, **kwargs)
            clients.append(client)
            return client

        def disconnected_emit(event):
            emitted.append(event)
            if event["type"] == event_type:
                raise exception_type("The local test browser disconnected.")

        with patch.object(lab, "Store", side_effect=make_store), \
             patch.object(lab.anthropic, "Anthropic", side_effect=make_client):
            with self.assertRaises(exception_type):
                lab.run_lab({"mode": "fixture"}, disconnected_emit)

        self.assertEqual(len(stores), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            stores[0].db.execute("SELECT 1")
        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].is_closed())
        self.assertEqual(emitted[-1]["type"], event_type)
        self.assertFalse(any(event["type"] == "error" for event in emitted))
        return emitted

    def test_disconnect_before_tool_execution_stops_and_closes_both_resources(self):
        emitted = self.assert_disconnect_closes_resources("model_request", BrokenPipeError)
        self.assertEqual(len(emitted), 1)
        self.assertFalse(any(event["type"] == "tool_call" for event in emitted))

    def test_disconnect_during_verification_also_closes_database(self):
        self.assert_disconnect_closes_resources("verification", ConnectionResetError)


if __name__ == "__main__":
    unittest.main()
