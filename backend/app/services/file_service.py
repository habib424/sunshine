import uuid
from pathlib import Path

import openpyxl
import pandas as pd

from app.config import settings


def save_upload(file_bytes: bytes, original_name: str) -> tuple[str, Path]:
    file_id = str(uuid.uuid4())
    ext = Path(original_name).suffix
    filename = f"{file_id}{ext}"
    dest = settings.uploads_path / filename
    dest.write_bytes(file_bytes)
    return file_id, dest


def read_file_metadata(file_path: Path) -> dict:
    ext = file_path.suffix.lower()

    if ext in (".xlsx", ".xls", ".xlsm"):
        return _read_excel_metadata(file_path)
    elif ext == ".csv":
        return _read_csv_metadata(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _read_excel_metadata(file_path: Path) -> dict:
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    sheet_names = wb.sheetnames
    ws = wb[sheet_names[0]]

    headers = []
    for cell in next(ws.iter_rows(min_row=1, max_row=1)):
        if cell.value is not None:
            headers.append(str(cell.value))

    row_count = ws.max_row - 1 if ws.max_row else 0  # exclude header
    wb.close()

    return {
        "sheet_names": sheet_names,
        "column_headers": headers,
        "row_count": row_count,
    }


def _read_csv_metadata(file_path: Path) -> dict:
    df = pd.read_csv(file_path, nrows=0)
    row_count = sum(1 for _ in open(file_path)) - 1
    return {
        "sheet_names": ["Sheet1"],
        "column_headers": list(df.columns),
        "row_count": max(row_count, 0),
    }


def read_file_preview(file_path: Path, limit: int = 20, sheet: int | str = 0, header_row: int = 0) -> dict:
    ext = file_path.suffix.lower()

    if ext in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(file_path, sheet_name=sheet, header=header_row, nrows=limit, engine="openpyxl")
    elif ext == ".csv":
        df = pd.read_csv(file_path, header=header_row, nrows=limit)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    df = df.fillna("")
    return {
        "headers": list(df.columns.astype(str)),
        "rows": df.values.tolist(),
        "total_rows": len(df),
    }


def read_file_as_dataframe(file_path: Path, sheet: int | str = 0, header_row: int = 0, skip_footer: int = 0) -> pd.DataFrame:
    ext = file_path.suffix.lower()

    if ext in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(file_path, sheet_name=sheet, header=header_row, engine="openpyxl")
    elif ext == ".csv":
        df = pd.read_csv(file_path, header=header_row)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    if skip_footer > 0:
        df = df.iloc[:-skip_footer]

    return df
