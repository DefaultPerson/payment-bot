import importlib


def test_main_module_imports_without_runtime_side_effects() -> None:
    module = importlib.import_module("bot.__main__")

    assert hasattr(module, "main")
    assert hasattr(module, "run")
