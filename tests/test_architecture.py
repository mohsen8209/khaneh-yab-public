from __future__ import annotations

import unittest

from scripts.update_architecture import check_current, render_document, source_manifest


class ArchitectureTests(unittest.TestCase):
    def test_architecture_map_is_current_and_registries_are_complete(self) -> None:
        render_document(source_manifest())
        current, message = check_current()
        self.assertTrue(current, message)


if __name__ == "__main__":
    unittest.main()
