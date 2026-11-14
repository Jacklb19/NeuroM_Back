"""Checks of the variables the training job needs before it reaches Supabase.

Run from this folder (only the standard library is needed):
    .venv/Scripts/python -m unittest test_store
"""

import unittest

from store import check_environment

SECRET = "service-role-value"


class CheckEnvironmentTest(unittest.TestCase):
    def test_accepts_both_variables(self) -> None:
        check_environment({"SUPABASE_URL": "https://project.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": SECRET})

    def test_names_every_missing_variable(self) -> None:
        with self.assertRaises(SystemExit) as stopped:
            check_environment({})
        message = str(stopped.exception.code)
        self.assertIn("SUPABASE_URL", message)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY", message)

    def test_an_empty_secret_counts_as_missing(self) -> None:
        # What GitHub passes for a secret that was never defined.
        with self.assertRaises(SystemExit) as stopped:
            check_environment({"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": SECRET})
        message = str(stopped.exception.code)
        self.assertIn("SUPABASE_URL", message)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", message)
        self.assertNotIn(SECRET, message)

    def test_refuses_a_url_without_scheme_without_printing_it(self) -> None:
        with self.assertRaises(SystemExit) as stopped:
            check_environment({"SUPABASE_URL": "project.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": SECRET})
        message = str(stopped.exception.code)
        self.assertIn("SUPABASE_URL", message)
        self.assertNotIn("project.supabase.co", message)


if __name__ == "__main__":
    unittest.main()
