"""Ensure privacy is applied to data before any exporter receives it."""
import json
import unittest

from src.uk728.report_signing import public_block, HIDDEN


class SigningPrivacyTests(unittest.TestCase):
    def test_house_identity_and_mark_are_filtered(self):
        snapshot = dict(date="2025-12-31", privacy="house_identity", insert_images=True,
                        people=[dict(side="company", name="Учебный директор", position="Директор",
                                     role="", image={"content":"COMPANY_MARK"}),
                                dict(side="house", name="Учебный собственник", position="", role="Совет",
                                     image={"content":"PRIVATE_MARK"}, details={"premises":"PRIVATE_ROOM"})])
        serialized = json.dumps(public_block(snapshot), ensure_ascii=False)
        self.assertIn("Учебный директор", serialized)
        self.assertIn("COMPANY_MARK", serialized)
        self.assertNotIn("Учебный собственник", serialized)
        self.assertNotIn("PRIVATE_MARK", serialized)
        self.assertNotIn("PRIVATE_ROOM", serialized)
        self.assertIn(HIDDEN, serialized)
        snapshot["privacy"] = "house_mark"
        serialized = json.dumps(public_block(snapshot), ensure_ascii=False)
        self.assertIn("Учебный собственник", serialized)
        self.assertNotIn("PRIVATE_MARK", serialized)
        snapshot["privacy"] = "all_identity"
        serialized = json.dumps(public_block(snapshot), ensure_ascii=False)
        self.assertNotIn("Учебный директор", serialized)
        self.assertNotIn("COMPANY_MARK", serialized)
