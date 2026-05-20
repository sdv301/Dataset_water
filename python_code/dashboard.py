import streamlit as st
import pandas as pd
import numpy as np
import datetime
import plotly.graph_objects as go
import plotly.express as px
import folium
from streamlit_folium import st_folium
from flood_predictor import FloodPredictor

st.set_page_config(page_title="Прогноз паводков", layout="wide")

# Инициализация (заглушка)
@st.cache_resource
def get_predictor():
    return FloodPredictor()

predictor = get_predictor()

# -- Сайдбар --
st.sidebar.title("Параметры прогноза")

mode = st.sidebar.radio(
    "Режим прогноза",
    ["Прогноз на дату", "Прогноз на месяц", "Прогноз на год", "Дашборды"]
)

station = st.sidebar.selectbox("Гидпопост / Река", ["р. Обь - г. Барнаул", "р. Томь - г. Томск", "р. Лена - г. Якутск"])

st.sidebar.markdown("---")
st.sidebar.subheader("Критические уровни (см)")
warning_level = st.sidebar.number_input("Повышенный уровень (НЯ)", value=500, step=10)
danger_level = st.sidebar.number_input("Опасный уровень (ОЯ)", value=650, step=10)

st.sidebar.markdown("---")
st.sidebar.subheader("Сценарий 'Что যদি'")
temp_mod = st.sidebar.slider("Изменение температуры (°C)", -5.0, 5.0, 0.0, 0.5)
precip_mod = st.sidebar.slider("Осадки (% от нормы)", 0, 200, 100, 10)
snow_mod = st.sidebar.slider("Снежный покров (% от нормы)", 0, 200, 100, 10)

# Генерация моковых данных для отображения, так как реальных нет
def generate_mock_forecast(days, base_val, trend):
    dates = [datetime.date.today() + datetime.timedelta(days=i) for i in range(days)]
    median = [base_val + i*trend + np.sin(i/3)*20 + temp_mod*2 + (precip_mod-100)*0.5 for i in range(days)]
    q90 = [m + 30 + i*1.5 for i, m in enumerate(median)]
    q95 = [m + 50 + i*2.5 for i, m in enumerate(median)]
    
    return pd.DataFrame({
        'date': dates,
        'median': median,
        'q90': q90,
        'q95': q95
    })

# -- Основной экран --
st.title("🌊 Вероятностный прогноз паводков")

if mode == "Прогноз на дату":
    target_date = st.sidebar.date_input("Дата прогноза", datetime.date.today() + datetime.timedelta(days=7))
    st.markdown(f"### Прогноз для: **{station}** на ближайшие 60 дней")
    
    df = generate_mock_forecast(60, 400, 2)
    current_risk = "Низкий"
    risk_color = "green"
    max_q95 = df['q95'].max()
    if max_q95 >= danger_level:
        current_risk = "ОПАСНЫЙ (ОЯ)"
        risk_color = "red"
    elif max_q95 >= warning_level:
        current_risk = "ПОВЫШЕННЫЙ (НЯ)"
        risk_color = "orange"
        
    st.markdown(f"**Резюме:** В течение следующих 60 дней ожидается {current_risk.lower()} риск паводка. Пиковое значение по оптимистичному сценарию (медиана) не превысит уровней, однако с вероятностью 5% уровень может достичь **{max_q95:.0f} см**.")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Текущий уровень", f"410 см", delta="12 см")
    col2.metric("Макс. прогноз (медиана)", f"{df['median'].max():.0f} см")
    col3.metric("Риск превышения ОЯ", "15%" if max_q95 >= danger_level else "1%", delta_color="inverse")
    
    # Гидрограф
    fig = go.Figure()
    
    # Заливка 0.5 - 0.95
    fig.add_trace(go.Scatter(
        x=df['date'].tolist() + df['date'].tolist()[::-1],
        y=df['q95'].tolist() + df['median'].tolist()[::-1],
        fill='toself',
        fillcolor='rgba(0,100,255,0.2)',
        line=dict(color='rgba(255,255,255,0)'),
        showlegend=True,
        name='Доверительный интервал (90%)'
    ))
    
    fig.add_trace(go.Scatter(x=df['date'], y=df['median'], line=dict(color='blue', width=3), name='Медиана (0.5)'))
    fig.add_trace(go.Scatter(x=df['date'], y=df['q90'], line=dict(color='orange', width=2, dash='dash'), name='Квантиль 0.9'))
        
    fig.add_hline(y=warning_level, line_dash="dot", line_color="orange", annotation_text="НЯ")
    fig.add_hline(y=danger_level, line_dash="dot", line_color="red", annotation_text="ОЯ")
    
    fig.update_layout(title="Вероятностный гидрограф", xaxis_title="Дата", yaxis_title="Уровень (см)", height=500)
    st.plotly_chart(fig, use_container_width=True)

elif mode == "Прогноз на месяц":
    sel_month = st.sidebar.selectbox("Месяц", range(1, 13), index=datetime.date.today().month-1)
    sel_year = st.sidebar.selectbox("Год", [2023, 2024, 2025], index=1)
    
    st.markdown(f"### Месячный прогноз риска ({sel_month:02d}.{sel_year})")
    
    df = generate_mock_forecast(30, 450, 5)
    
    fig = px.density_heatmap(df, x="date", y="q95", z="median", histfunc="avg", title="Тепловая карта риска по дням")
    st.plotly_chart(fig, use_container_width=True)
    
    st.subheader("Детальная таблица")
    st.dataframe(df.style.highlight_max(axis=0))

elif mode == "Прогноз на год":
    sel_year = st.sidebar.selectbox("Год", [2024, 2025, 2026])
    st.markdown(f"### Годовой прогноз ({sel_year})")
    
    months = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
    median_levels = [100, 110, 150, 400, 550, 480, 300, 250, 280, 200, 150, 100]
    
    fig = px.bar(x=months, y=median_levels, 
                 color=[m > warning_level for m in median_levels],
                 color_discrete_map={True: 'red', False: 'cornflowerblue'},
                 labels={'x': 'Месяц', 'y': 'Ср. макс. уровень (см)'},
                 title="Пиковые уровни по месяцам")
    fig.add_hline(y=warning_level, line_dash="dot", line_color="orange", annotation_text="НЯ")
    st.plotly_chart(fig, use_container_width=True)

elif mode == "Дашборды":
    st.markdown("### 📊 Панель комплексных дашбордов")
    st.info("Здесь собраны сводные аналитические панели по всем речным бассейнам.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Количество постов с превышением НЯ")
        fig = px.pie(values=[2, 8, 35], names=['ОЯ', 'НЯ', 'Норма'], hole=0.4, color_discrete_sequence=['red', 'orange', 'green'])
        st.plotly_chart(fig, use_container_width=True)
        
    with col2:
        st.subheader("Сводка по бассейнам")
        data = pd.DataFrame({
            "Бассейн": ["Обский", "Енисейский", "Ленский", "Амурский"],
            "Индекс риска": [0.8, 0.4, 0.9, 0.2]
        })
        fig = px.bar(data, x="Индекс риска", y="Бассейн", orientation='h', color="Индекс риска", color_continuous_scale="Reds")
        st.plotly_chart(fig, use_container_width=True)

# Карта
st.markdown("---")
st.subheader("Карта гидрологической обстановки")
m = folium.Map(location=[55.0, 82.0], zoom_start=5)
folium.CircleMarker(
    location=[53.3, 83.7], # Барнаул
    radius=10,
    color="orange" if mode == "Прогноз на дату" else "green",
    fill=True,
    fill_opacity=0.7,
    popup=station
).add_to(m)
st_folium(m, width=1200, height=400)
