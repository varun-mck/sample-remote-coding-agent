import unittest

from app import greeting


class TestGreeting(unittest.TestCase):
    def test_greeting(self) -> None:
        self.assertEqual(greeting(), "hello world")
