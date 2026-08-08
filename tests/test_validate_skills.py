"""Regression tests for scripts/validate-skills.py."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate-skills.py"
SPEC = importlib.util.spec_from_file_location("validate_skills", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validate_skills = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_skills)


class ValidatorFrontmatterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.root_patch = patch.object(validate_skills, "REPO_ROOT", self.repo_root)
        self.root_patch.start()

    def tearDown(self) -> None:
        self.root_patch.stop()
        self.temp_dir.cleanup()

    def write_skill(self, category: str, name: str, frontmatter: str) -> Path:
        path = self.repo_root / "skills" / category / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\n{frontmatter}\n---\n\n# Test\n", encoding="utf-8")
        return path

    def test_valid_portable_frontmatter_passes(self) -> None:
        path = self.write_skill(
            "disciplines",
            "test-skill",
            "name: test-skill\n"
            "description: Handles test fixtures. Use when validating a test skill.\n"
            "compatibility: Python 3.11+",
        )

        errors, warnings = validate_skills.validate_file(path)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_agent_specific_keys_are_rejected_and_allowed_tools_warns(self) -> None:
        path = self.write_skill(
            "disciplines",
            "test-skill",
            "name: test-skill\n"
            "description: Handles test fixtures. Use when validating a test skill.\n"
            "model: example-model\n"
            "allowed-tools: Read",
        )

        errors, warnings = validate_skills.validate_file(path)

        self.assertTrue(any("forbidden frontmatter key: 'model'" in error for error in errors))
        self.assertTrue(any("'allowed-tools'" in warning for warning in warnings))

    def test_compatibility_is_non_empty_and_bounded(self) -> None:
        empty = self.write_skill(
            "first",
            "empty-compatibility",
            "name: empty-compatibility\n"
            "description: Handles fixtures. Use when testing empty compatibility.\n"
            "compatibility:",
        )
        oversized = self.write_skill(
            "second",
            "large-compatibility",
            "name: large-compatibility\n"
            "description: Handles fixtures. Use when testing large compatibility.\n"
            f"compatibility: {'x' * 501}",
        )

        empty_errors, _ = validate_skills.validate_file(empty)
        oversized_errors, _ = validate_skills.validate_file(oversized)

        self.assertTrue(any("must not be empty" in error for error in empty_errors))
        self.assertTrue(any("max 500" in error for error in oversized_errors))

    def test_description_soft_limit_warns_and_xml_tags_fail(self) -> None:
        description = f"{'a' * 201} <example>"
        path = self.write_skill(
            "disciplines",
            "test-skill",
            f"name: test-skill\ndescription: {description}",
        )

        errors, warnings = validate_skills.validate_file(path)

        self.assertTrue(any("must not contain XML tags" in error for error in errors))
        self.assertTrue(any("soft target 200" in warning for warning in warnings))

    def test_generic_type_syntax_is_not_mistaken_for_xml(self) -> None:
        path = self.write_skill(
            "disciplines",
            "test-skill",
            "name: test-skill\n"
            "description: Explains GetNode<T>. Use when validating generic APIs.",
        )

        errors, _ = validate_skills.validate_file(path)

        self.assertFalse(any("XML tags" in error for error in errors))

    def test_duplicate_names_are_rejected_repo_wide(self) -> None:
        first = self.write_skill(
            "first",
            "same-name",
            "name: same-name\n"
            "description: Handles first fixtures. Use when testing duplicate names.",
        )
        second = self.write_skill(
            "second",
            "same-name",
            "name: same-name\n"
            "description: Handles second fixtures. Use when testing duplicate names.",
        )

        errors = validate_skills.validate_unique_names([first, second])

        self.assertEqual(len(errors), 1)
        self.assertIn("duplicate skill name 'same-name'", errors[0])
        self.assertIn("skills/first/same-name/SKILL.md", errors[0])
        self.assertIn("skills/second/same-name/SKILL.md", errors[0])


if __name__ == "__main__":
    unittest.main()
