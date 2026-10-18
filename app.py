import streamlit as st
import pandas as pd
import numpy as np
import os
import base64
import smtplib
import requests
from email.mime.text import MIMEText
from email.header import Header
from zhipuai import ZhipuAI
from dotenv import load_dotenv
from io import BytesIO
from datetime import datetime, timedelta
from entity_aligner import clean_entities
import imaplib
import email
from email.header import decode_header
import json
import sqlite3
import plotly.express as px
import plotly.graph_objects as go
import re
import docx
import time
from docx.oxml.ns import qn

# ================= 环境与配置 =================
load_dotenv()

try:
    ZHIPU_API_KEY = st.secrets["ZHIPU_API_KEY"]
except Exception:
    ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")

client = ZhipuAI(api_key=ZHIPU_API_KEY) if ZHIPU_API_KEY else None

# ================= 页面设置与全局 CSS =================
st.set_page_config(page_title="NexusFlow 智能商业生态", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Merriweather:wght@700&display=swap');

    /* 全局字体 */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
        color: #0F172A;
    }

    /* 主标题深蓝 */
    h1 {
        font-family: 'Merriweather', serif !important;
        font-weight: 700 !important;
        color: #1E3A8A !important; 
        letter-spacing: -0.5px;
    }
    
    /* 二级标题 */
    h2, h3 {
        font-family: 'Inter', sans-serif !important;
        font-weight: 600 !important;
        color: #0F172A !important;
    }

    /* 按钮高级定制 */
    .stButton > button {
        background-color: #0F172A !important;
        color: #FFFFFF !important;
        border-radius: 6px !important;
        border: 1px solid #0F172A !important;
        padding: 0.5rem 1.5rem !important;
        font-weight: 500 !important;
        transition: all 0.3s ease;
    }
    .stButton > button:hover {
        background-color: #1E3A8A !important; 
        border-color: #1E3A8A !important;
        color: #FFFFFF !important;
    }

    /* 隐藏自带菜单和页脚 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* 卡片阴影 */
    div[data-testid="stExpander"], div[data-testid="stVerticalBlockBorderWrapper"] {
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03) !important;
        border-radius: 8px !important;
        border: 1px solid #E5E7EB !important;
        background-color: white !important;
    }

    /* 终极隐藏 st.radio 的红点 */
    div[data-testid="stRadio"] [role="radiogroup"] label > div:first-child,
    div[data-testid="stRadio"] input[type="radio"],
    div[data-baseweb="radio"] > div:first-child {
        display: none !important;
    }
    
    div[data-testid="stRadio"] [role="radiogroup"] label {
        padding: 0.5rem 0 !important;
        margin-right: 1.5rem !important;
        background-color: transparent !important;
        border-radius: 0 !important;
        cursor: pointer;
        transition: all 0.3s ease;
    }

    div[data-testid="stRadio"] [role="radiogroup"] label p {
        color: #64748B !important;
        font-size: 1rem !important;
        font-weight: 500 !important;
        transition: all 0.3s ease;
    }
    
    div[data-testid="stRadio"] [role="radiogroup"] label:hover p {
        color: #1E3A8A !important;
    }
    
    div[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) {
        border-bottom: 2px solid #1E3A8A !important;
    }
    
    div[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) p {
        color: #1E3A8A !important; 
        font-weight: 700 !important;
    }
</style>
""", unsafe_allow_html=True)

st.title("NexusFlow 智能商业生态")
st.markdown("为一人公司与出海企业打造的 AI 驱动财务、经营与绿色金融工具")

# ================= 全局常量 =================
GLOBAL_LANGUAGES = ["英语 (English)", "中文 (Chinese)", "西班牙语 (Spanish)", "法语 (French)", "德语 (German)", "日语 (Japanese)", "韩语 (Korean)", "俄语 (Russian)", "阿拉伯语 (Arabic)", "葡萄牙语 (Portuguese)", "意大利语 (Italian)", "荷兰语 (Dutch)", "波兰语 (Polish)", "土耳其语 (Turkish)", "越南语 (Vietnamese)", "泰语 (Thai)", "印尼语 (Indonesian)", "马来语 (Malay)", "印地语 (Hindi)"]
TARGET_PLATFORMS = ["亚马逊 (Amazon)", "Shopify独立站", "小红书", "抖音", "微信公众号", "LinkedIn", "Twitter (X)"]

# ================= 初始化 Session State =================
if 'merged_df' not in st.session_state: st.session_state.merged_df = None
if 'invoices' not in st.session_state: st.session_state.invoices = []

# ================= 核心引擎 =================
@st.cache_data(ttl=3600)
def get_exchange_rate_by_date(from_currency="USD", to_currency="CNY", date_str=None):
    if from_currency == to_currency: return 1.0
    if not date_str:
        try: return requests.get(f"https://api.frankfurter.app/latest?from={from_currency}&to={to_currency}", timeout=5).json().get('rates', {}).get(to_currency, 7.2)
        except: return 7.2
    try:
        conn = sqlite3.connect('exchange_rates.db')
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS rates (date TEXT, from_curr TEXT, to_curr TEXT, rate REAL, PRIMARY KEY (date, from_curr, to_curr))''')
        cursor.execute("SELECT rate FROM rates WHERE date=? AND from_curr=? AND to_curr=?", (date_str, from_currency, to_currency))
        result = cursor.fetchone()
        if result:
            conn.close()
            return result[0]
        conn.close()
    except Exception: pass
    
    try:
        rate = requests.get(f"https://api.frankfurter.app/{date_str}?from={from_currency}&to={to_currency}", timeout=5).json().get('rates', {}).get(to_currency, 7.2)
    except: rate = 7.2
        
    try:
        conn = sqlite3.connect('exchange_rates.db')
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO rates VALUES (?, ?, ?, ?)", (date_str, from_currency, to_currency, rate))
        conn.commit()
        conn.close()
    except Exception: pass
    return rate

def clean_and_align_data(df, filename="未知文件"):
    column_mapping = {'交易时间': '日期', '交易日期': '日期', 'Date': '日期', '交易对方': '客户名', '对方名称': '客户名', 'Client': '客户名', '金额(元)': '金额', 'Amount': '金额', '收支类型': '类型', 'Type': '类型', '货币': '币种', 'Currency': '币种', '手续费': '手续费', 'Fee': '手续费', '交易描述': '描述', 'Description': '描述'}
    df = df.rename(columns=column_mapping)
    for col in ['日期', '客户名', '金额', '类型', '币种']:
        if col not in df.columns: df[col] = '未知' if col != '金额' else 0.0
    if '手续费' not in df.columns: df['手续费'] = 0.0
    if '描述' not in df.columns: df['描述'] = ''
    
    def parse_date(date_val):
        if pd.isna(date_val): return pd.Timestamp.now().normalize()
        try:
            dt = pd.to_datetime(date_val, errors='coerce')
            if dt.tz is not None: dt = dt.tz_convert('Asia/Shanghai').tz_localize(None)
            return dt.normalize()
        except: return pd.Timestamp.now().normalize()

    df['日期'] = df['日期'].apply(parse_date)
    df = df.dropna(subset=['日期', '金额']).drop_duplicates()
    
    def parse_amount(val):
        if pd.isna(val): return 0.0
        try: return float(str(val).replace('¥', '').replace('$', '').replace(',', '').replace('元', '').replace(' ', ''))
        except: return 0.0
            
    df['金额'] = df['金额'].apply(parse_amount)
    df['手续费'] = df['手续费'].apply(parse_amount)
    
    def normalize_type(row):
        t = str(row['类型']).lower()
        return abs(row['金额']) if t in ['收入', '入账', 'income'] else -abs(row['金额'])
    df['金额'] = df.apply(normalize_type, axis=1)
    
    def convert_currency_and_fee(row):
        rate = get_exchange_rate_by_date(row['币种'], 'CNY', row['日期'].strftime('%Y-%m-%d'))
        amount_cny, fee_cny = row['金额'] * rate, row['手续费'] * rate
        today_rate = get_exchange_rate_by_date(row['币种'], 'CNY', None)
        forex_gain_loss = row['金额'] * (today_rate - rate) if today_rate else 0
        return pd.Series([amount_cny, fee_cny, rate, forex_gain_loss])
        
    df[['金额_CNY', '手续费_CNY', '使用汇率', '汇兑损益']] = df.apply(convert_currency_and_fee, axis=1)
    df['净金额_CNY'] = df['金额_CNY'] - df['手续费_CNY']
    
    def classify_category(desc):
        desc = str(desc).lower()
        if any(k in desc for k in ['顺丰', '物流', '快递', 'ship', 'express']): return '物流费'
        if any(k in desc for k in ['采购', '阿里', '1688', 'purchase', 'supplier']): return '采购成本'
        if any(k in desc for k in ['工资', '薪', 'payroll', 'salary']): return '人力成本'
        if any(k in desc for k in ['广告', '推广', 'ad', 'marketing']): return '营销推广'
        if any(k in desc for k in ['税', 'tax']): return '税费'
        return '其他'
    df['科目分类'] = df['描述'].apply(classify_category)
    df['绿色标签'] = df['描述'].apply(lambda x: '绿色交易' if any(k in str(x).lower() for k in ['环保', '绿电', '太阳能', '新能源', '回收', '节能', '碳排放', 'green', 'recycle', 'solar', 'ev', 'esg']) else '常规交易')

    df = df.sort_values('日期').reset_index(drop=True)
    df['异常标记'] = ''
    df.loc[df['日期'] > pd.Timestamp.now().normalize(), '异常标记'] += '【未来日期】 '
    if len(df) > 3: df.loc[df['净金额_CNY'].abs() > (df['净金额_CNY'].abs().mean() * 10), '异常标记'] += '【大额异常，请复核】 '
    if df['净金额_CNY'].sum() < 0: df['异常标记'] += '【总现金流为负】 '

    df['数据来源文件'] = filename
    df['系统清洗时间'] = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
    
    quality_report = {"来源文件名": filename, "清理后行数": len(df), "涉及币种": list(df['币种'].unique()) if '币种' in df.columns else [], "数据起始日期": str(df['日期'].min()) if not df.empty else "无", "数据结束日期": str(df['日期'].max()) if not df.empty else "无"}
    return df, quality_report   

def update_client_profiles(df):
    if df is None or df.empty: return pd.DataFrame()
    if 'client_profiles' not in st.session_state:
        st.session_state.client_profiles = pd.DataFrame(columns=['客户名', '最近交易日期', '本月总交易额', '本月交易笔数', '历史平均回款周期', 'ESG评分'])
    profile_df = st.session_state.client_profiles.copy()
    today = pd.Timestamp.now().normalize().strftime('%Y-%m-%d')
    for _, row in df.iterrows():
        client = row['客户名_标准'] if '客户名_标准' in row else row['客户名']
        is_green = 1 if row.get('绿色标签') == '绿色交易' else 0
        if client in profile_df['客户名'].values:
            idx = profile_df[profile_df['客户名'] == client].index[0]
            profile_df.at[idx, '最近交易日期'] = today
            profile_df.at[idx, '本月总交易额'] += abs(row['金额'])
            profile_df.at[idx, '本月交易笔数'] += 1
            profile_df.at[idx, 'ESG评分'] = round((profile_df.at[idx, 'ESG评分'] * (profile_df.at[idx, '本月交易笔数'] - 1) + is_green * 100) / profile_df.at[idx, '本月交易笔数'], 2)
        else:
            profile_df = pd.concat([profile_df, pd.DataFrame([{'客户名': client, '最近交易日期': today, '本月总交易额': abs(row['金额']), '本月交易笔数': 1, '历史平均回款周期': 30, 'ESG评分': 100.0 if is_green else 0.0}])], ignore_index=True)
    st.session_state.client_profiles = profile_df
    return profile_df

def send_email_alert(sender, password, receiver, balance, safety_line):
    if not (sender and password and receiver): return False, "邮箱配置不完整"
    try:
        message = MIMEText(f"【现金流预警】余额 ¥{balance:,.2f} 已低于安全线 ¥{safety_line:,.2f}。", 'plain', 'utf-8')
        message['Subject'] = Header("紧急：现金流跌破安全线预警", 'utf-8')
        smtpObj = smtplib.SMTP_SSL("smtp.qq.com", 465)
        smtpObj.login(sender, password)
        smtpObj.sendmail(sender, [receiver], message.as_string())
        smtpObj.quit()
        return True, "发送成功"
    except Exception as e: return False, str(e)

def predict_cash_flow(df, safety_line, extra_expense=0, delay_days=0):
    if df is None or df.empty: return None, None
    days_span = (df['日期'].max() - df['日期'].min()).days + 1
    if days_span <= 0: days_span = 1
    total_income = df[df['类型'] == '收入']['金额_CNY'].sum()
    total_expense = df[df['类型'] == '支出']['金额_CNY'].sum()
    daily_net = (total_income - total_expense) / days_span
    current_balance = total_income - total_expense
    if delay_days > 0: daily_net -= (total_income / days_span) * (delay_days / 30) * 0.5 
    daily_net -= (extra_expense / 30) 
    predictions = {f'{day}天': current_balance + (daily_net * day) for day in [30, 60, 90]}
    return predictions, pd.DataFrame({'日期': [datetime.now() + timedelta(days=i) for i in range(0, 91, 10)], '预测余额': [current_balance + (daily_net * i) for i in range(0, 91, 10)]})

def monte_carlo_simulation(df, forecast_days=90, iterations=1000):
    if df is None or df.empty: return None, None
    daily_net = df.groupby('日期')['净金额_CNY'].sum()
    mean_daily = daily_net.mean()
    std_daily = daily_net.std() if len(daily_net) > 1 else mean_daily * 0.1
    current_balance = df['净金额_CNY'].sum()
    sim_paths = []
    for _ in range(iterations):
        path = [current_balance]
        for _ in range(forecast_days): path.append(path[-1] + np.random.normal(mean_daily, std_daily))
        sim_paths.append(path)
    sim_df = pd.DataFrame(sim_paths).T
    return sim_df, np.percentile(sim_df.iloc[-1], 5)

def process_invoice_ocr(image_file):
    if not client: return "ERROR: API Key未配置"
    try:
        base64_image = base64.b64encode(image_file.getvalue()).decode('utf-8')
        response = client.chat.completions.create(
            model="glm-4v",
            messages=[{"role": "user", "content": [{"type": "text", "text": "请提取发票中的金额、日期和发票号码，纯JSON输出。键名为：'金额', '日期', '发票号码'。不要其他文字。"}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}]
        )
        return response.choices[0].message.content
    except Exception as e: return f"ERROR: {str(e)}"

def call_llm_agent(prompt):
    if not client: return "API Key 未配置，无法调用 AI 助手。"
    try: return client.chat.completions.create(model="glm-4", messages=[{"role": "user", "content": prompt}]).choices[0].message.content
    except Exception as e: return f"AI 调用失败: {str(e)}"

# ================= 新增：全局一键导出组件 =================
def render_ai_output_with_export(content, base_filename, is_text_area=False):
    # 彻底封杀 AI 编造日期，由系统追加真实日期
    today_str = datetime.now().strftime('%Y年%m月%d日')
    full_content = f"{content}\n\n---\n报告编制人：NexusFlow AI系统\n编制日期：{today_str}"
    
    if is_text_area:
        st.text_area("生成结果", full_content, height=300)
    else:
        st.markdown(full_content)
    
    # 生成多格式下载按钮
    st.markdown("**一键导出报告：**")
    col1, col2, col3, _ = st.columns([1, 1, 1, 3])
    
    # TXT 格式
    col1.download_button("下载 TXT", data=full_content.encode('utf-8'), file_name=f"{base_filename}.txt", mime="text/plain", key=f"txt_{base_filename}_{time.time()}")
    # Markdown 格式
    col2.download_button("下载 Markdown", data=full_content.encode('utf-8'), file_name=f"{base_filename}.md", mime="text/markdown", key=f"md_{base_filename}_{time.time()}")
   # Word 格式 (修复中文乱码)
    doc = docx.Document()
    p = doc.add_paragraph()
    run = p.add_run(full_content)
    # 强制设置中文字体为微软雅黑
    run.font.name = '微软雅黑'
    run._element.rPr.rFonts.set(qn('w:eastAsia'), '微软雅黑')
    bio = BytesIO()
    doc.save(bio)
    col3.download_button("下载 Word", data=bio.getvalue(), file_name=f"{base_filename}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", key=f"docx_{base_filename}_{time.time()}")

@st.cache_data(ttl=7200)
def get_wecom_token(corpid, corpsecret):
    if not (corpid and corpsecret): return None
    try: return requests.get(f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}&corpsecret={corpsecret}", timeout=5).json().get("access_token")
    except: return None

def send_personal_alert(corpid, corpsecret, agentid, touser, balance, safety_line, dashboard_url):
    if not (corpid and corpsecret and agentid and touser): return False, "配置不完整"
    token = get_wecom_token(corpid, corpsecret)
    if not token: return False, "获取Token失败"
    data = {"touser": touser, "msgtype": "markdown", "agentid": int(agentid), "markdown": {"content": f"**NexusFlow 现金流预警**\n> 当前余额：¥{balance:,.2f}\n> 安全线：¥{safety_line:,.2f}\n> <font color=\"warning\">已跌破安全线，请及时关注！</font>\n\n[点击此处跳转至分析仪表盘]({dashboard_url})"}}
    try: return (True, "发送成功") if requests.post(f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}", json=data, timeout=5).json().get("errcode") == 0 else (False, "发送失败")
    except Exception as e: return False, str(e)

def auto_reconcile(flow_df, invoice_df):
    if flow_df is None or invoice_df is None or flow_df.empty or invoice_df.empty: return None, None
    income_df = flow_df[flow_df['类型'] == '收入'].copy()
    income_df['核销状态'] = '未核销'
    income_df['关联发票'] = ''
    for idx, inv_row in invoice_df.iterrows():
        for flow_idx, flow_row in income_df.iterrows():
            if income_df.at[flow_idx, '核销状态'] == '未核销' and abs(round(float(inv_row['发票金额']), 2) - round(float(flow_row['金额_CNY']), 2)) <= 1.0:
                income_df.at[flow_idx, '核销状态'] = '已核销'
                income_df.at[flow_idx, '关联发票'] = inv_row['发票号码']
                break
    return income_df, invoice_df[~invoice_df['发票号码'].isin(income_df[income_df['核销状态'] == '已核销']['关联发票'].tolist())]

# ================= 系统配置与导航 =================
with st.expander("系统参数与预警设置 (System Configuration)", expanded=False):
    col_c1, col_c2, col_c3 = st.columns(3)
    with col_c1:
        st.subheader("风控阈值")
        safety_line = st.number_input("最低现金流安全线（CNY）", value=5000, step=1000)
        auto_alert = st.checkbox("启用自动预警推送")
    with col_c2:
        st.subheader("邮件预警渠道")
        email_sender = st.text_input("发件人邮箱")
        email_password = st.text_input("邮箱授权码", type="password")
        email_receiver = st.text_input("接收预警邮箱")
    with col_c3:
        st.subheader("企业微信推送")
        wecom_corpid = st.text_input("企业 CorpID")
        wecom_secret = st.text_input("自建应用 Secret", type="password")
        wecom_agentid = st.text_input("自建应用 AgentID")
        wecom_userid = st.text_input("接收人 UserID")
        dashboard_url = st.text_input("跳转链接", value="https://your-app.streamlit.app")
    if st.button("测试发送个人预警"):
        if all([wecom_corpid, wecom_secret, wecom_agentid, wecom_userid]):
            success, msg = send_personal_alert(wecom_corpid, wecom_secret, wecom_agentid, wecom_userid, 1000.0, 5000.0, dashboard_url)
            st.success("测试成功！") if success else st.error(f"失败：{msg}")

st.markdown("---")
menu_options = ["账单清洗与导入", "资金全景与风控", "业财经营分析", "绿色出海合规", "微型团队管家", "跨境贸易工具箱"]
selected_page = st.radio("导航菜单", menu_options, horizontal=True, label_visibility="collapsed")
st.markdown("---")

# ================= 1. 账单清洗与导入 =================
if selected_page == "账单清洗与导入":
    st.header("账单清洗与导入")
    uploaded_files = st.file_uploader("上传流水 Excel（可多选）", type=["xlsx", "xls"], accept_multiple_files=True)
    if uploaded_files:
        all_dfs, quality_reports = [], []
        for file in uploaded_files:
            cleaned_df, qr = clean_and_align_data(pd.read_excel(file), filename=file.name)
            all_dfs.append(cleaned_df)
            quality_reports.append(qr)
        if all_dfs:
            st.session_state.merged_df = pd.concat(all_dfs, ignore_index=True)
            st.success(f"成功合并 {len(uploaded_files)} 个文件！")
            with st.expander("查看数据清洗质量报告"): st.json(quality_reports)
            
            st.markdown("---")
            cleaned_df = clean_entities(st.session_state.merged_df)
            st.session_state.merged_df = cleaned_df
            edited_df = st.data_editor(cleaned_df[['客户名', '客户名_标准', '客户标签']].head(100), hide_index=True, use_container_width=True)
            if st.button("保存修正结果"):
                new_rules = edited_df[edited_df['客户名'] != edited_df['客户名_标准']].rename(columns={'客户名': 'raw_name', '客户名_标准': 'std_name', '客户标签': 'tag'})
                if not new_rules.empty:
                    st.session_state.entity_mapping = new_rules if 'entity_mapping' not in st.session_state else pd.concat([st.session_state.entity_mapping, new_rules]).drop_duplicates(subset=['raw_name'], keep='last')
                    st.toast("规则已保存！")
                     # ================= 一键导出标准 Excel =================
            st.markdown("---")
            st.subheader("导出标准财务流水")
            st.markdown("清洗完成的数据，可一键导出为标准 Excel 格式，直接发给您的财务或代账公司。")
            
            # 1. 提取要导出的干净数据
            export_df = st.session_state.merged_df.copy()
            
            # 【核心修复1】强制把日期列变成干净的 YYYY-MM-DD 字符串，防止变成一长串时间戳
            if '日期' in export_df.columns:
                export_df['日期'] = pd.to_datetime(export_df['日期']).dt.strftime('%Y-%m-%d')
            
            # 2. 写入 BytesIO
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                export_df.to_excel(writer, index=False, sheet_name='标准流水')
                worksheet = writer.sheets['标准流水']
                
                # 【核心修复2】暴力调整列宽
                from openpyxl.utils import get_column_letter
                for i, col in enumerate(export_df.columns):
                    # 计算该列最长内容的长度（处理一下空值转字符串）
                    max_len = export_df[col].astype(str).map(len).max()
                    if pd.isna(max_len):
                        max_len = 0
                    
                    # 宽度 = max(列名长度, 最长内容长度) + 补充宽度 (给日期列额外加宽)
                    base_width = max(max_len, len(str(col)))
                    extra_width = 10 if col == '日期' else 5 # 给日期列多留点空间
                    
                    worksheet.column_dimensions[get_column_letter(i + 1)].width = base_width + extra_width

            excel_data = output.getvalue()
            
            st.download_button(
                label="下载清洗后的 Excel 文件",
                data=excel_data,
                file_name=f"NexusFlow_清洗后流水_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
    
    st.markdown("---")
    st.subheader("发票 OCR 与智能对账")
    uploaded_invoices = st.file_uploader("上传发票图片（可多选）", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key="inv_up")
    if uploaded_invoices and st.button("开始识别"):
        for inv in uploaded_invoices:
            with st.spinner("识别中..."):
                res = process_invoice_ocr(inv)
                st.code(res)
                st.session_state.invoices.append(res)
                
    if len(st.session_state.invoices) > 0:
        parsed_invoices = []
        for inv in st.session_state.invoices:
            try:
                match = re.search(r'\{.*\}', str(inv).replace('```json', '').replace('```', '').replace('\n', '').strip())
                if match:
                    data = json.loads(match.group())
                    amt = data.get('金额') or data.get('amount') or 0
                    if isinstance(amt, str): amt = float(amt.replace('¥', '').replace('$', '').replace(',', '').replace('元', ''))
                    parsed_invoices.append({'发票金额': amt, '发票日期': data.get('日期') or '', '发票号码': data.get('发票号码') or ''})
            except: pass
        if parsed_invoices:
            invoice_df = pd.DataFrame(parsed_invoices)
            st.dataframe(invoice_df)
            if st.session_state.merged_df is not None:
                reconciled_df, unmatched_invoices = auto_reconcile(st.session_state.merged_df, invoice_df)
                if reconciled_df is not None:
                    st.success(f"成功自动匹配 {len(reconciled_df[reconciled_df['核销状态'] == '已核销'])} 笔流水！")
                    if not unmatched_invoices.empty:
                        st.warning(f"发现 {len(unmatched_invoices)} 张未核销发票！")
                        st.dataframe(unmatched_invoices)
                        lang = st.selectbox("催款函语言", GLOBAL_LANGUAGES, key="recon_lang")
                        if st.button("生成催款函"):
                            with st.spinner("起草中..."): 
                                result = call_llm_agent(f"写一封催款函给未核销发票客户。语言：{lang}。数据：{unmatched_invoices.to_string()}。附中文翻译。不要在文中编写日期。")
                                render_ai_output_with_export(result, "未核销发票催款函")

# ================= 2. 资金全景与风控 =================
elif selected_page == "资金全景与风控":
    st.header("资金全景与风控")
    if st.session_state.merged_df is None:
        with st.container(border=True):
            st.markdown("### 欢迎使用资金全景与风控中枢")
            st.markdown("上传您的多平台流水后，系统将自动为您提供：")
            col_v1, col_v2, col_v3 = st.columns(3)
            col_v1.info("**多币种对齐**\n支持全球主流货币实时折算")
            col_v2.info("**现金流预测**\n基于蒙特卡洛模拟推演未来风险")
            col_v3.info("**智能风控**\n自动识别异常交易与坏账风险")
            st.markdown("---")
            st.markdown("**请点击顶部【账单清洗与导入】，上传您的 Excel 流水，开启智能分析。**")
    else:
        df = st.session_state.merged_df
        total_income, total_expense = df[df['金额'] > 0]['净金额_CNY'].sum(), df[df['金额'] < 0]['净金额_CNY'].sum() 
        balance = df['净金额_CNY'].sum()
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("累计总收入", f"¥{total_income:,.2f}")
        col2.metric("累计总支出", f"¥{total_expense:,.2f}")
        col3.metric("实际可用现金流", f"¥{balance:,.2f}")
        col4.metric("监控中客户数", f"{len(st.session_state.get('client_profiles', pd.DataFrame()))} 家")

        if balance < safety_line:
            st.error(f"危险：余额低于安全线 ¥{safety_line:,.2f}！")
            if auto_alert and 'last_alert_sent' not in st.session_state and all([wecom_corpid, wecom_secret, wecom_agentid, wecom_userid]):
                if send_personal_alert(wecom_corpid, wecom_secret, wecom_agentid, wecom_userid, balance, safety_line, dashboard_url)[0]: st.session_state.last_alert_sent = True

        st.markdown("---")
        st.subheader("情景模拟 (What-if)")
        what_if_input = st.text_area("描述假设情景：", "如果下个月新招程序员（多支出20000元），且大客户推迟30天付款。")
        if st.button("推演风险"):
            with st.spinner("推演中..."): 
                result = call_llm_agent(f"现金流¥{balance}。假设：{what_if_input}。请写压力测试报告，含3条止损建议。不要在文中编写日期。")
                render_ai_output_with_export(result, "情景模拟压力测试报告")

        st.markdown("---")
        st.subheader("现金流风险测试 (VaR)")
        with st.spinner("模拟中..."):
            sim_df, var_95 = monte_carlo_simulation(df)
            if sim_df is not None:
                fig_mc = go.Figure()
                for i in range(50): fig_mc.add_trace(go.Scatter(y=sim_df.iloc[:, i], mode='lines', line=dict(width=0.5, color='rgba(0, 150, 255, 0.1)'), showlegend=False))
                fig_mc.add_hline(y=safety_line, line_dash="dash", line_color="red")
                fig_mc.update_layout(template="plotly_dark")
                st.plotly_chart(fig_mc, use_container_width=True)
                st.error(f"VaR 指标：95% 置信度下，未来90天最低现金流不低于 **¥{var_95:,.2f}**。")
                st.caption("免责声明：本模型基于历史数据的统计学推演，不代表未来实际走势，仅供参考。")

# ================= 3. 业财经营分析 =================
elif selected_page == "业财经营分析":
    st.header("业财经营分析")
    if st.session_state.merged_df is None: st.info("请先上传财务数据。")
    else:
        col_e1, col_e2, col_e3 = st.columns(3)
        with col_e1:
            inventory_value = st.number_input("库存总值", value=50000)
            cogs = st.number_input("本月销售成本", value=20000)
        with col_e2:
            avg_order_value = st.number_input("平均客单价", value=500)
            total_orders = st.number_input("订单总数", value=100)
        with col_e3:
            old_c = st.number_input("上月活跃客户", value=80)
            new_c = st.number_input("本月新增客户", value=25)
        turnover_rate = cogs / inventory_value if inventory_value > 0 else 0
        churn_rate = (old_c - max(0, old_c - 10)) / old_c if old_c > 0 else 0
        r1, r2, r3 = st.columns(3)
        r1.metric("库存周转率", f"{turnover_rate:.2f} 次/月")
        r2.metric("平均客单价", f"¥{avg_order_value:,.2f}")
        r3.metric("客户流失率", f"{churn_rate*100:.1f}%")
        if st.button("生成业财诊断报告"):
            with st.spinner("分析中..."):
                result = call_llm_agent(f"可用现金流 ¥{st.session_state.merged_df['净金额_CNY'].sum():,.2f}，库存 ¥{inventory_value}，周转率 {turnover_rate:.2f}。客观陈述资金-库存特征，不超过200字。不要在文中编写日期。")
                render_ai_output_with_export(result, "业财经营诊断报告")

# ================= 4. 绿色出海合规 =================
elif selected_page == "绿色出海合规":
    st.header("绿色出海合规")
    df = st.session_state.merged_df
    if df is None: st.info("请先上传数据。")
    else:
        green_df = df[df['绿色标签'] == '绿色交易']
        green_ratio = (len(green_df) / len(df) * 100) if len(df) > 0 else 0
        with st.container(border=True):
            c1, c2, c3 = st.columns(3)
            c1.metric("绿色交易占比", f"{green_ratio:.1f}%")
            c2.metric("绿色交易总额", f"¥{green_df['净金额_CNY'].sum():,.2f}")
            c3.metric("ESG评分", f"{min(100, int(green_ratio * 1.5 + 30))} / 100")
        with st.container(border=True):
            if st.button("启动AI合规扫描"):
                with st.spinner("分析中..."):
                    result = call_llm_agent(f"客观数据比对：绿色占比 {green_ratio:.1f}%。结合欧盟CBAM，输出1.现状陈述。2.信息缺口清单。不要主观评价。不要在文中编写日期。")
                    render_ai_output_with_export(result, "绿色出海合规扫描报告")
                    st.caption("免责声明：上述合规风险分析由AI根据现有数据推演，不构成法律或税务合规意见。")

# ================= 5. 微型团队管家 =================
elif selected_page == "微型团队管家":
    st.header("微型团队管家")
    tabs = st.tabs(["经营简报", "营销文案", "资产中心"])
    
    with tabs[0]:
        st.subheader("月度经营简报生成")
        if st.session_state.merged_df is None:
            st.info("请先在【账单清洗与导入】页面上传流水数据，系统才能提取数据并生成简报。")
        else:
            if st.button("生成月度经营简报", key="gen_opc_report"):
                with st.spinner("AI正在汇总数据生成简报..."): 
                    df = st.session_state.merged_df
                    summary = f"共{len(df)}笔交易。总收入：¥{df[df['类型']=='收入']['金额_CNY'].sum():,.2f}，总支出：¥{df[df['类型']=='支出']['金额_CNY'].sum():,.2f}。"
                    result = call_llm_agent(f"请根据以下财务数据，生成一份简洁专业的月度经营简报（纯文字，包含数据总结、趋势分析、下月建议）。不要在文中编写日期。\n{summary}")
                    render_ai_output_with_export(result, "月度经营简报")
                    st.caption("免责声明：简报内容由AI自动汇总生成，仅供管理层内部参考。")

    with tabs[1]:
        st.subheader("多平台营销文案生成")
        pinfo = st.text_area("产品简介", "AI工具")
        plat = st.selectbox("发布平台", TARGET_PLATFORMS)
        lang = st.selectbox("语言", GLOBAL_LANGUAGES, key="opc_lang")
        if st.button("生成文案"):
            with st.spinner("撰写中..."): 
                result = call_llm_agent(f"为{plat}写{lang}营销文案。产品：{pinfo}。附中文翻译。不要在文中编写日期。")
                render_ai_output_with_export(result, "多平台营销文案")
                st.caption("免责声明：文案内容由AI生成，发布前请人工审核。")

    with tabs[2]:
        st.subheader("企业数字信用资产中心")
        
        if st.session_state.merged_df is not None:
            tx_count = len(st.session_state.merged_df)
            profile_count = len(st.session_state.get('client_profiles', pd.DataFrame()))
            total_flow = st.session_state.merged_df['净金额_CNY'].sum()
        else:
            tx_count = 0
            profile_count = 0
            total_flow = 0.0

        if tx_count == 0:
            st.info("请先在【账单清洗与导入】上传您的流水数据，系统将为您生成专属的数字信用资产。")
        else:
            st.markdown("这些数据是您向银行或保理机构申请低息贷款时，**证明企业经营真实性**的核心材料。")
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("已清洗真实交易", f"{tx_count} 笔", help="金融机构最看重真实的经营流水")
            col_b.metric("已建立信用画像客户", f"{profile_count} 个", help="证明您拥有稳定的供应链网络")
            col_c.metric("已验证资金流水总额", f"¥{total_flow:,.2f}", help="作为申请授信额度的参考基数")
            
            st.markdown("---")
            st.subheader("辅助生成融资申请材料")
            st.markdown("很多外贸个体户去银行贷款，常因流水混乱被拒。NexusFlow 可以为您生成一份**结构化的经营数据报告**，方便您提交给银行经理。")
            
            if st.button("生成《企业信用与融资参考报告》"):
                with st.spinner("正在汇总您的数字资产，生成报告..."):
                    prompt = f"""
                    你是一个资深的中小企业融资顾问。请根据以下企业的数字经营数据，撰写一份客观的《企业信用与融资参考报告》。
                    
                    数据背景：
                    该企业已通过系统清洗了 {tx_count} 笔真实交易，资金流水总额为 ¥{total_flow:,.2f}，系统中沉淀了 {profile_count} 个稳定的供应链伙伴画像。
                    
                    要求：
                    1. 报告需分为三部分：【企业经营真实性证明】、【资金流稳定性分析】、【供应链网络健康度】。
                    2. 语气必须客观、专业，展示出该企业具有极高的数据透明度。
                    3. 声明：该报告不构成任何信用评级，仅作为企业经营数据的客观汇总，供金融机构独立审批参考。
                    4. 不要编写任何日期。
                    """
                    result = call_llm_agent(prompt)
                    render_ai_output_with_export(result, "企业信用与融资参考报告")
                    
        st.caption("免责声明：本模块仅提供数据整理与文档生成辅助，不构成任何信用担保或贷款承诺。最终审批由持牌金融机构独立决策。")
# ================= 6. 跨境贸易工具箱 =================
elif selected_page == "跨境贸易工具箱":
    st.header("跨境贸易工具箱")
    tabs = st.tabs(["智能催收", "买家背调", "营销本地化"])
    with tabs[0]:
        cn = st.text_input("客户名称")
        amt = st.number_input("逾期金额(USD)", min_value=0.0, value=1000.0)
        lang = st.selectbox("语言", GLOBAL_LANGUAGES, key="trade_c")
        if st.button("生成催收邮件"):
            with st.spinner("起草中..."): 
                result = call_llm_agent(f"写催收邮件。客户{cn}，金额{amt}。语言{lang}。附中文翻译。不要在文中编写日期。")
                render_ai_output_with_export(result, "多语言催收邮件", is_text_area=True)
                st.caption("免责声明：翻译及邮件内容由AI生成，请核对客户信息和欠款金额后发送。")
    with tabs[1]:
        cp = st.text_input("海外买家名称")
        if st.button("生成透视报告"):
            with st.spinner("聚合中..."): 
                result = call_llm_agent(f"搜集并聚合关于'{cp}'的公开商业信息。客观报告，不提供评级。不要在文中编写日期。")
                render_ai_output_with_export(result, "海外买家背调报告")
                st.caption("免责声明：海外买家背景信息聚合自公开网络数据，系统不对其真实性、完整性做任何担保。")
    with tabs[2]:
        desc = st.text_area("产品描述", "蓝牙耳机")
        plat = st.selectbox("目标平台", TARGET_PLATFORMS, key="t_p")
        lang = st.selectbox("语言", GLOBAL_LANGUAGES, key="t_l")
        if st.button("生成本地化文案"):
            with st.spinner("本地化中..."): 
                result = call_llm_agent(f"为{plat}写{lang}营销文案。产品：{desc}。附中文翻译。不要在文中编写日期。")
                render_ai_output_with_export(result, "跨境营销本地化文案")
                st.caption("免责声明：文案内容由AI生成，发布前请人工审核。")

# ================= 页脚 =================
st.markdown("---")
st.caption("Built with Python + Streamlit + Pandas + Zhipu GLM-4V | NexusFlow 智能商业生态 6.0")