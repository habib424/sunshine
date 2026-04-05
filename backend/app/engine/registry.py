from typing import Callable

import pandas as pd

TransformFunc = Callable[[pd.Series, dict], pd.Series]
ValidatorFunc = Callable[[pd.DataFrame, dict], list[dict]]

_transforms: dict[str, TransformFunc] = {}
_validators: dict[str, ValidatorFunc] = {}


def register_transform(name: str):
    def decorator(func: TransformFunc) -> TransformFunc:
        _transforms[name] = func
        return func
    return decorator


def register_validator(name: str):
    def decorator(func: ValidatorFunc) -> ValidatorFunc:
        _validators[name] = func
        return func
    return decorator


def get_transform(name: str) -> TransformFunc:
    if name not in _transforms:
        raise KeyError(f"Transform '{name}' not found. Available: {list(_transforms.keys())}")
    return _transforms[name]


def get_validator(name: str) -> ValidatorFunc:
    if name not in _validators:
        raise KeyError(f"Validator '{name}' not found. Available: {list(_validators.keys())}")
    return _validators[name]
