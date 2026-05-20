import pandas as pd
import json

file_path = r'c:\Users\sdvf5\Downloads\testovoe\Dataset_water\Реки\данные январь\Уровни_воды.xlsx'
df = pd.read_excel(file_path)
with open('excel_dump.json', 'w', encoding='utf-8') as f:
    json.dump({
        'columns': list(df.columns),
        'sample': df.head().to_dict(orient='records'),
        'unique_rivers': df['Река'].dropna().unique().tolist() if 'Река' in df.columns else []
    }, f, ensure_ascii=False, indent=2)
