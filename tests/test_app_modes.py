from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest


class AppModeTests(unittest.TestCase):
    def test_application_has_no_publication_v2_mode_or_import(self) -> None:
        source = Path("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        sidebar_radios = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "radio"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "sidebar"
        ]

        self.assertNotIn("utils.publication_v2_ui", imported_modules)
        self.assertNotIn("Peta Publikasi V2", source)
        self.assertFalse(
            any(
                call.args
                and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == "Mode aplikasi"
                for call in sidebar_radios
            )
        )

    def test_publication_v2_modules_and_renderer_hooks_are_removed(self) -> None:
        module_names = (
            "utils.publication_v2",
            "utils.publication_v2_ui",
            "utils.publication_v2_models",
            "utils.publication_v2_coverage",
        )
        for module_name in module_names:
            with self.subTest(module_name=module_name):
                self.assertIsNone(importlib.util.find_spec(module_name))

        renderer_source = Path("utils/publication_map.py").read_text(encoding="utf-8")
        for identifier in (
            "bounds_override",
            "custom_legend_title",
            "custom_legend_entries",
            "custom_detail_lines",
            "basemap_attribution_override",
            "fit_publication_bounds",
            "render_configured_basemap",
            "publication_projector",
            "warp_geographic_overlay",
        ):
            with self.subTest(identifier=identifier):
                self.assertNotIn(identifier, renderer_source)

    def test_earthquake_publication_builder_remains_available(self) -> None:
        source = Path("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "render_publication_builder"
        ]

        self.assertIn("render_publication_builder", functions)
        self.assertTrue(calls)
        self.assertIn("Buat Peta Publikasi", source)


if __name__ == "__main__":
    unittest.main()
