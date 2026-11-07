import streamlit as st
import numpy as np
import pandas as pd
import requests

# ページ設定
st.set_page_config(page_title="iDeCo×退職金 出口戦略シミュレーター", layout="wide")

st.title("iDeCo × 退職金 出口戦略シミュレーター")
st.warning("【免責事項】本ツールは簡易的なシミュレーションであり、正確な税額や社会保険料、運用成果を保証するものではありません。実際の税務申告や投資判断は、必ず税理士などの専門家にご相談の上、ご自身の責任で行ってください。入力された資産データ等は一切保存・送信されません。")

# --- UI: サイドバーでパラメータ設定 ---
st.sidebar.header("1. iDeCo受取の完了期限")
limit_age = st.sidebar.slider(
    "受取完了リミット年齢", 
    60, 75, 70,
    help="iDeCoを何歳までに全額受け取り終えたいかを設定します（法令上の上限は75歳）。"
)

st.sidebar.header("2. 会社の退職金設定")
comp_severance = st.sidebar.number_input("退職金見込み額 (円)", value=15000000, step=1000000)
comp_start = st.sidebar.slider("入社時の年齢", 18, 50, 22)
comp_end = st.sidebar.slider("退職時の年齢", 50, 75, 60)

st.sidebar.header("3. iDeCo設定")
ideco_balance = st.sidebar.number_input("iDeCo元本 (円)", value=5000000, step=1000000)
ideco_start = st.sidebar.slider("積立開始年齢", 20, 60, 40)
ideco_end = st.sidebar.slider("拠出終了希望年齢", 60, 65, 60)

st.sidebar.header("4. iDeCoの運用設定")
mean_rate_pct = st.sidebar.slider(
    "受取期間中のiDeCo想定運用年利 (%)", 
    0.0, 10.0, 3.0, step=0.5,
    help="受取を遅らせる据置期間や、年金受給中の残額にかかる想定運用利回りです。"
)

st.sidebar.header("5. その他の収入設定")
public_pension = st.sidebar.number_input("公的年金見込み年額 (円)", value=2000000, step=100000)
wage_income = st.sidebar.number_input("退職後の給与等年額 (円)", value=0, step=100000)

GAS_WEBHOOK_URL = ""

# ==============================================================================
# 計算ロジック
# ==============================================================================
def calc_deduction(years):
    years = max(1, int(np.ceil(years)))
    if years <= 20: 
        return 400_000 * years
    else: 
        return 8_000_000 + 700_000 * (years - 20)

def calc_tax_table(taxable):
    if taxable <= 0: 
        return 0.0
    brackets = [
        (1_950_000, 0.05, 0), 
        (3_300_000, 0.10, 97_500), 
        (6_950_000, 0.20, 427_500),
        (8_999_000, 0.23, 636_000), 
        (17_999_000, 0.33, 1_536_000),
        (39_999_000, 0.40, 2_796_000), 
        (float('inf'), 0.45, 4_796_000)
    ]
    for limit, rate, ded in brackets:
        if taxable <= limit: 
            return (taxable * rate - ded) * 1.021
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
        elif total_income <= 3_200_000: care_premium = base_care * 1.7
        else: care_premium = base_care * 2.1
    return nhi_premium, care_premium

def eval_annuity_breakdown(principal, years, rate, start_age, public_p, wage):
    if principal <= 0 or years <= 0: 
        return 0, 0, 0, 0, 0
    annual_pay = principal * (rate * (1 + rate)**years) / ((1 + rate)**years - 1) if rate > 0 else principal / years
    tot_inc_tax, tot_res_tax, tot_nhi, tot_care, tot_recv = 0, 0, 0, 0, 0
    
    for i in range(years):
        age = start_age + i
        base_p = public_p if age >= 65 else 0
        tot_p = annual_pay + base_p
        
        ded_w = calc_pension_deduction(age, tot_p)
        taxable_inc_w = max(0, tot_p - ded_w + wage - 480_000) 
        taxable_res_w = max(0, tot_p - ded_w + wage - 430_000) 
        inc_w = calc_tax_table(taxable_inc_w)
        res_w = taxable_res_w * 0.10
        nhi_w, care_w = calc_social_insurance_detailed(age, tot_p, wage)

        ded_b = calc_pension_deduction(age, base_p)
        taxable_inc_b = max(0, base_p - ded_b + wage - 480_000)
        taxable_res_b = max(0, base_p - ded_b + wage - 430_000)
        inc_b = calc_tax_table(taxable_inc_b)
        res_b = taxable_res_b * 0.10
        nhi_b, care_b = calc_social_insurance_detailed(age, base_p, wage)

        tot_inc_tax += max(0, inc_w - inc_b)
        tot_res_tax += max(0, res_w - res_b)
        tot_nhi += max(0, nhi_w - nhi_b)
        tot_care += max(0, care_w - care_b)
        tot_recv += annual_pay
    return tot_recv, tot_inc_tax, tot_res_tax, tot_nhi, tot_care

def run_evaluation(c_end, target_age, l_ratio, ann_len, mean_r):
    hold_y = max(0, target_age - c_end)
    balance_at_recv = ideco_balance * ((1 + mean_r) ** hold_y)
    ideco_lump = balance_at_recv * l_ratio
    ideco_ann = balance_at_recv * (1.0 - l_ratio)

    ideco_y = max(1, c_end - ideco_start)
    comp_y = max(1, comp_end - comp_start)
    overlap_y = max(0, min(c_end, comp_end) - max(ideco_start, comp_start))
    diff_y = comp_end - target_age

    comp_base_ded = calc_deduction(comp_y)
    ideco_base_ded = calc_deduction(ideco_y)
    overlap_ded = calc_deduction(overlap_y)

    if diff_y == 0:
        comb_ded = calc_deduction(max(ideco_y, comp_y))
        l_inc, l_res = calc_lump_taxes_detailed(comp_severance + ideco_lump, comb_ded)
        rule = "同一年（合算枠）"
    elif diff_y > 0:
        c_ded = max(0, comp_base_ded - overlap_ded) if diff_y < 20 else comp_base_ded
        c_inc, c_res = calc_lump_taxes_detailed(comp_severance, c_ded)
        i_inc, i_res = calc_lump_taxes_detailed(ideco_lump, ideco_base_ded)
        l_inc, l_res = c_inc + i_inc, c_res + i_res
        rule = f"iDeCo
