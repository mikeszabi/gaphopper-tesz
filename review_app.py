"""A régi, önálló szakértői felület helyett: streamlit run app.py (Szakértői sor oldal).

Ez a fájl csak a megszokott indítás miatt maradt meg: streamlit run review_app.py
"""
import streamlit as st

from ui import page_review

st.set_page_config(page_title="Gaphopper – szakértői sor", layout="wide")
page_review.page()
