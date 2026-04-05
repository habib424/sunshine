_registry: dict = {}


def register_file_transform(name: str):
    def decorator(fn):
        _registry[name] = fn
        return fn
    return decorator


def get_file_transform(name: str):
    if name not in _registry:
        raise KeyError(f"No file transform registered: '{name}'")
    return _registry[name]
