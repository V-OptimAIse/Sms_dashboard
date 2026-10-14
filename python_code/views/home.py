import streamlit as st


st.write("# Welcome to V-Board! 👋")

st.sidebar.success("Choose a page from the navigation above.")

st.markdown(
    """
    V-Boards is a dashboard service for quick POC development and demonstration purposes.
    It demonstrates the final web application used for data visualisation and data-driven
    modelling of the Industrial IoT devices developed by V-OptimAI.

    **👈 Choose a page from the sidebar** to explore the available dashboards.

    ### Want to learn more?

    - Book a one-to-one session: [email us](mailto:innovate@v-optimai.com?subject=One-to-one%20session%20for%20Web-App%20demo.)
    - Learn more on the [V-OptimAI website](https://www.v-optimai.com)
    """
)
