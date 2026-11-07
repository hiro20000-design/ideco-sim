import streamlit as st
import numpy as np
import pandas as pd
import requests

# ページ設定
st.set_page_config(page_title="iDeCo×退職金 出口戦略シミュレーター", layout="wide")

st.title("iDeCo × 退職金 出口戦略シミュレーター")
st.warning("【免責事項】本ツールは簡易的なシミュレーションであり、正確な税額や社会保険料、運用成果を保証するものではありません。実際の税務申告や投資判断は、必ず税理士などの専門家にご相談の上、ご自身の責任で行ってください。入力された資産データ等は一切保存・送信されません。")

# --- UI: サイドバーでパラメータ設定 ---
st.sidebar.header("1. 現金化のリミット")
limit_age = st.sidebar.slider("現金化リミット年齢", 60, 75, 68)

st.sidebar.header("2. 会社の退職金設定")
comp_severance = st.sidebar.number_input("退職金見込み額 (円)", value=22000000, step=1000000)
comp_start = st.sidebar.slider("入社時の年齢", 18, 50, 47)
comp_end = st.sidebar.slider("退職時の年齢", 50, 75, 65)

st.sidebar.header("3. iDeCo設定")
ideco_balance = st.sidebar.number_input("iDeCo元本 (円)", value=10000000, step=1000000)
ideco_start = st.sidebar.slider("積立開始年齢", 20, 60, 52)
ideco_end = st.sidebar.slider("拠出終了希望年齢", 60, 65, 65)

st.sidebar.header("4. 収入・運用設定")
public_pension = st.sidebar.number_input("公的年金見込み年額 (円)", value=2400000, step=100000)
wage_income = st.sidebar.number_input("退職後の給与等年額 (円)", value=0, step=100000)
mean_rate_pct = st.sidebar.slider("想定平均年利 (%)", 0.0, 10.0, 4.0, step=0.5)

# 実行回数カウント用（今は使わないため空欄にしてあります）
GAS_WEBHOOK_URL = ""

# ==============================================================================
# 計算ロジック
# ==============================================================================
def calc_deduction(years):
    years = max(1, int(np.ceil(years)))
    if years <= 20: return 400_000 * years
    else: return 8_000_000 + 700_000 * (years - 20)

def calc_tax_table(taxable):
    if taxable <= 0: return 0.0
    brackets = [(1_950_000, 0.05, 0), (3_300_000, 0.10, 97_500), (6_950_000, 0.20, 427_500),
                (8_999_000, 0.23, 636_000), (17_999_000, 0.33, 1_536_000),
                (39_999_000, 0.40, 2_796_000), (float('inf'), 0.45, 4_796_000)]
    for limit, rate, ded in brackets:
        if taxable <= limit: return (taxable * rate - ded) * 1.021
    return 0.0

def calc_lump_taxes_detailed(amount, deduction):
    taxable = max(0, (amount - deduction) / 2)
    taxable = np.floor(taxable / 1000) * 1000
    inc_tax = calc_tax_table(taxable)
    res_tax = taxable * 0.10
    return np.floor(inc_tax), np.floor(res_tax)

def calc_pension_deduction(age, val):
    if age < 65:
        if val <= 1_300_000: return min(600_000, val)
        elif val <= 4_100_000: return val * 0.25 + 275_000
        else: return val * 0.15 + 685_000
    else:
        if val <= 3_300_000: return min(1_100_000, val)
        elif val <= 7_700_000: return val * 0.25 + 275_000
        else: return val * 0.15 + 1_045_000

def calc_social_insurance_detailed(age, total_pension, wage_income):
    pension_ded = calc_pension_deduction(age, total_pension)
    misc_income = max(0, total_pension - pension_ded)
    total_income = misc_income + wage_income 
    base_nhi_income = max(0, total_income - 430_000)
    nhi_premium = min(850_000, base_nhi_income * 0.098 + 60_000) if total_income > 0 else 0
    care_premium = 0
    if age < 65:
        care_premium = min(170_000, base_nhi_income * 0.025 + 20_000) if total_income > 0 else 0
    else:
        base_care = 73_000
        if total_income <= 0: care_premium = base_care * 0.3
        elif total_income <= 800_000: care_premium = base_care * 0.7
        elif total_income <= 1_200_000: care_premium = base_care * 1.0
        elif total_income <= 2_100_000: care_premium = base_care * 1.5
        elif total_income <= 3_200_000: care_premium = base_care * 1
