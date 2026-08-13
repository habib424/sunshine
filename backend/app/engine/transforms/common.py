import re

import pandas as pd

from app.engine.registry import register_transform


@register_transform("passthrough")
def passthrough(series: pd.Series, params: dict) -> pd.Series:
    return series


@register_transform("strip_whitespace")
def strip_whitespace(series: pd.Series, params: dict) -> pd.Series:
    return series.astype(str).str.strip()


@register_transform("clean_identifier")
def clean_identifier(series: pd.Series, params: dict) -> pd.Series:
    def clean(value) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        text = str(value).strip()
        return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text

    return series.apply(clean)


@register_transform("extract_pattern")
def extract_pattern(series: pd.Series, params: dict) -> pd.Series:
    pattern = params.get("pattern", "(.*)")
    return series.astype(str).str.extract(pattern, expand=False).fillna("")


@register_transform("parse_currency")
def parse_currency(series: pd.Series, params: dict) -> pd.Series:
    thousands_sep = params.get("thousands_separator", ",")
    result = series.astype(str).str.replace(thousands_sep, "", regex=False)
    result = result.str.replace("$", "", regex=False)
    result = result.str.replace("€", "", regex=False)
    result = result.str.replace(" ", "", regex=False)
    result = result.replace({"": "0", "nan": "0", "None": "0"})
    return pd.to_numeric(result, errors="coerce").fillna(0)


@register_transform("constant")
def constant(series: pd.Series, params: dict) -> pd.Series:
    value = params.get("value", "")
    return pd.Series([value] * len(series), index=series.index)


@register_transform("rename")
def rename(series: pd.Series, params: dict) -> pd.Series:
    return series


@register_transform("normalize_date")
def normalize_date(series: pd.Series, params: dict) -> pd.Series:
    input_format = params.get("input_format")
    output_format = params.get("output_format", "%Y-%m-%d")
    if input_format:
        dates = pd.to_datetime(series, format=input_format, errors="coerce")
    else:
        dates = pd.to_datetime(series, errors="coerce")
        numeric = pd.to_numeric(series, errors="coerce")
        excel_serial_mask = numeric.between(20000, 80000)
        if excel_serial_mask.any():
            excel_dates = pd.to_datetime(
                numeric[excel_serial_mask],
                unit="D",
                origin="1899-12-30",
                errors="coerce",
            )
            dates.loc[excel_serial_mask] = excel_dates
    return dates.dt.strftime(output_format).fillna("")


@register_transform("pad_left")
def pad_left(series: pd.Series, params: dict) -> pd.Series:
    width = params.get("width", 6)
    char = params.get("char", "0")
    return series.astype(str).str.zfill(width) if char == "0" else series.astype(str).str.rjust(width, char)


@register_transform("uppercase")
def uppercase(series: pd.Series, params: dict) -> pd.Series:
    return series.astype(str).str.upper()


@register_transform("lowercase")
def lowercase(series: pd.Series, params: dict) -> pd.Series:
    return series.astype(str).str.lower()


@register_transform("map_values")
def map_values(series: pd.Series, params: dict) -> pd.Series:
    mapping = params.get("mapping", {})
    default = params.get("default")
    if default is not None:
        return series.map(mapping).fillna(default)
    return series.map(mapping).fillna(series)


@register_transform("concat_columns")
def concat_columns(series: pd.Series, params: dict) -> pd.Series:
    separator = params.get("separator", " ")
    return series.astype(str).str.cat(sep=separator)


@register_transform("split_and_take")
def split_and_take(series: pd.Series, params: dict) -> pd.Series:
    separator = params.get("separator", "-")
    index = params.get("index", 0)
    return series.astype(str).str.split(separator).str[index].fillna("")
