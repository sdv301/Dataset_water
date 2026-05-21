import pandas as pd
import json

file_path = r'c:\Users\sdvf5\Downloads\testovoe\Dataset_water\Реки\данные январь\Уровни_воды.xlsx'
df = pd.read_excel(file_path)
def _json_safe_records(frame: pd.DataFrame) -> list:
    """Сериализация строк Excel в JSON без обрыва на NaN/датах."""
    rows = frame.head().copy()
    for col in rows.columns:
        if rows[col].dtype == object:
            rows[col] = rows[col].apply(
                lambda v: None if pd.isna(v) else (v.isoformat() if hasattr(v, "isoformat") else str(v))
            )
    return rows.where(pd.notna(rows), None).to_dict(orient="records")


with open('excel_dump.json', 'w', encoding='utf-8') as f:
    json.dump({
        'columns': list(df.columns),
        'sample': _json_safe_records(df),
        'unique_rivers': df['Река'].dropna().unique().tolist() if 'Река' in df.columns else []
    }, f, ensure_ascii=False, indent=2)
