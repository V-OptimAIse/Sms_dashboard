import streamlit as st


st.set_page_config(
    page_title="V-OptimAI",
    page_icon="🏭",
    layout="wide",
)

pages = [
    st.Page("views/home.py", title="Home", icon="🏠", default=True),
    st.Page("pages/2_Demo.py", title="Demo", icon="🧪"),
    st.Page("pages/HW_Standardisation.py", title="HM Standardisation", icon="📡"),
]

navigation = st.navigation(pages)
navigation.run()
