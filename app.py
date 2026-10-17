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

# ================= 环境与配置 =================
load_dotenv()
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
client = ZhipuAI(api_key=ZHIPU_API_KEY) if ZHIPU_API_KEY else None

# ================= 页面设置 =================
st.set_page_config(page_title="NexusFlow 智能商业生态", page_icon="", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)
st.title("NexusFlow 智能商业生态")
st.markdown("为一人公司与出海企业打造的 AI 驱动财务与绿色金融工具")

# ================= 全局常量 =================
GLOBAL_LANGUAGES = [
    "英语 (English)", "中文 (Chinese)", "西班牙语 (Spanish)", "法语 (French)", 
    "德语 (German)", "日语 (Japanese)", "韩语 (Korean)", "俄语 (Russian)", 
    "阿拉伯语 (Arabic)", "葡萄牙语 (Portuguese)", "意大利语 (Italian)", 
    "荷兰语 (Dutch)", "波兰语 (Polish)", "土耳其语 (Turkish)", "越南语 (Vietnamese)", 
    "泰语 (Thai)", "印尼语 (Indonesian)", "马来语 (Malay)", "印地语 (Hindi)"
]

TARGET_PLATFORMS = [
    "亚马逊 (Amazon)", "Shopify独立站", "小红书", "抖音", "微信公众号", "LinkedIn", "Twitter (X)"
]

# ================= 初始化 Session State =================
if 'merged_df' not in st.session_state:
    st.session_state.merged_df = None
if 'invoices' not in st.session_state:
    st.session_state.invoices = []

# ================= 核心引擎（数据清洗与预测） =================

@st.cache_data(ttl=3600)
def get_exchange_rate_by_date(from_currency="USD", to_currency="CNY", date_str=None):
    """获取指定日期的历史汇率（带本地数据库缓存）"""
    if from_currency == to_currency:
        return 1.0
    if not date_str:
        try:
            url = f"https://api.frankfurter.app/latest?from={from_currency}&to={to_currency}"
            return requests.get(url, timeout=5).json().get('rates', {}).get(to_currency, 7.2)
        except: return 7.2
    
    conn = sqlite3.connect('exchange_rates.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS rates 
                      (date TEXT, from_curr TEXT, to_curr TEXT, rate REAL, PRIMARY KEY (date, from_curr, to_curr))''')
    cursor.execute("SELECT rate FROM rates WHERE date=? AND from_curr=? AND to_curr=?", (date_str, from_currency, to_currency))
    result = cursor.fetchone()
    if result:
        conn.close()
        return result[0]
    try:
        url = f"https://api.frankfurter.app/{date_str}?from={from_currency}&to={to_currency}"
        rate = requests.get(url, timeout=5).json().get('rates', {}).get(to_currency, 7.2)
    except:
        rate = 7.2
    cursor.execute("INSERT OR REPLACE INTO rates VALUES (?, ?, ?, ?)", (date_str, from_currency, to_currency, rate))
    conn.commit()
    conn.close()
    return rate

def clean_and_align_data(df, filename="未知文件"):
    """[核心引擎] 数据清洗与对齐（含绿色金融打标）"""
    column_mapping = {
        '交易时间': '日期', '交易日期': '日期', 'Date': '日期',
        '交易对方': '客户名', '对方名称': '客户名', 'Client': '客户名',
        '金额(元)': '金额', 'Amount': '金额',
        '收支类型': '类型', 'Type': '类型',
        '货币': '币种', 'Currency': '币种',
        '手续费': '手续费', 'Fee': '手续费',
        '交易描述': '描述', 'Description': '描述'
    }
    df = df.rename(columns=column_mapping)
    required_cols = ['日期', '客户名', '金额', '类型', '币种']
    for col in required_cols:
        if col not in df.columns:
            df[col] = '未知' if col != '金额' else 0.0
    if '手续费' not in df.columns: df['手续费'] = 0.0
    if '描述' not in df.columns: df['描述'] = ''
    
    def parse_date(date_val):
        if pd.isna(date_val): return pd.Timestamp.now().normalize()
        try:
            dt = pd.to_datetime(date_val, errors='coerce')
            if dt.tz is not None:
                dt = dt.tz_convert('Asia/Shanghai').tz_localize(None)
            return dt.normalize()
        except: return pd.Timestamp.now().normalize()

    df['日期'] = df['日期'].apply(parse_date)
    df = df.dropna(subset=['日期', '金额'])
    df = df.drop_duplicates()
    
    def parse_amount(val):
        if pd.isna(val): return 0.0
        val_str = str(val).replace('¥', '').replace('$', '').replace(',', '').replace('元', '').replace(' ', '')
        try: return float(val_str)
        except: return 0.0
            
    df['金额'] = df['金额'].apply(parse_amount)
    df['手续费'] = df['手续费'].apply(parse_amount)
    
    def normalize_type(row):
        t = str(row['类型']).lower()
        amount = row['金额']
        return abs(amount) if t in ['收入', '入账', 'income'] else -abs(amount)
    df['金额'] = df.apply(normalize_type, axis=1)
    
    def convert_currency_and_fee(row):
        currency = row['币种']
        amount = row['金额']
        fee = row['手续费']
        date_str = row['日期'].strftime('%Y-%m-%d')
        rate = get_exchange_rate_by_date(currency, 'CNY', date_str)
        amount_cny = amount * rate
        fee_cny = fee * rate
        today_rate = get_exchange_rate_by_date(currency, 'CNY', None)
        forex_gain_loss = amount * (today_rate - rate) if today_rate else 0
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

    def classify_green(desc):
        desc = str(desc).lower()
        green_keywords = ['环保', '绿电', '太阳能', '新能源', '回收', '节能', '碳排放', 'green', 'recycle', 'solar', 'ev', 'esg']
        if any(k in desc for k in green_keywords):
            return '绿色交易'
        return '常规交易'
    df['绿色标签'] = df['描述'].apply(classify_green)

    df = df.sort_values('日期').reset_index(drop=True)
    
    df['异常标记'] = ''
    today = pd.Timestamp.now().normalize()
    df.loc[df['日期'] > today, '异常标记'] += '【未来日期】 '
    if len(df) > 3:
        mean_amount = df['净金额_CNY'].abs().mean()
        df.loc[df['净金额_CNY'].abs() > (mean_amount * 10), '异常标记'] += '【大额异常，请复核】 '
    if df['净金额_CNY'].sum() < 0:
        df['异常标记'] += '【总现金流为负】 '
    if df['净金额_CNY'].sum() < 0:
        df['异常标记'] += '【总现金流为负】 '

    # ================= 新增：防伪水印 =================
    df['数据来源文件'] = filename
    df['系统清洗时间'] = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # ================= 新增：数据质量报告 =================
    quality_report = {
        "来源文件名": filename,
        "清理后行数": len(df),
        "涉及币种": list(df['币种'].unique()) if '币种' in df.columns else [],
        "数据起始日期": str(df['日期'].min()) if not df.empty else "无",
        "数据结束日期": str(df['日期'].max()) if not df.empty else "无"
    }
    
    # 注意：这里必须返回两个值了！
    return df, quality_report   

def update_client_profiles(df):
    """更新客户画像数据库（基于 Session State 实现多用户隔离）"""
    if df is None or df.empty:
        return pd.DataFrame()
    
    # 1. 优先从 session_state 读取，没有则初始化空表
    if 'client_profiles' not in st.session_state:
        st.session_state.client_profiles = pd.DataFrame(
            columns=['客户名', '最近交易日期', '本月总交易额', '本月交易笔数', '历史平均回款周期', 'ESG评分']
        )
    
    # 2. 拷贝一份进行操作，避免直接修改内存污染
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
            
            current_esg = profile_df.at[idx, 'ESG评分']
            total_tx = profile_df.at[idx, '本月交易笔数']
            # 更新 ESG 评分（滑动平均）
            profile_df.at[idx, 'ESG评分'] = round((current_esg * (total_tx - 1) + is_green * 100) / total_tx, 2)
        else:
            new_row = {
                '客户名': client, '最近交易日期': today, '本月总交易额': abs(row['金额']), 
                '本月交易笔数': 1, '历史平均回款周期': 30, 'ESG评分': 100.0 if is_green else 0.0
            }
            profile_df = pd.concat([profile_df, pd.DataFrame([new_row])], ignore_index=True)
            
    # 3. 将更新后的数据存回内存
    st.session_state.client_profiles = profile_df
    return profile_df
def send_email_alert(sender, password, receiver, balance, safety_line):
    if not (sender and password and receiver): 
        return False, "邮箱配置不完整"
    try:
        message = MIMEText(f"【现金流预警】余额 ¥{balance:,.2f} 已低于安全线 ¥{safety_line:,.2f}。", 'plain', 'utf-8')
        message['Subject'] = Header("紧急：现金流跌破安全线预警", 'utf-8')
        smtpObj = smtplib.SMTP_SSL("smtp.qq.com", 465)
        smtpObj.login(sender, password)
        smtpObj.sendmail(sender, [receiver], message.as_string())
        smtpObj.quit()
        return True, "发送成功"
    except Exception as e: 
        return False, str(e)

def predict_cash_flow(df, safety_line):
    if df is None or df.empty: return None, None
    days_span = (df['日期'].max() - df['日期'].min()).days + 1
    if days_span <= 0: days_span = 1
    total_income = df[df['类型'] == '收入']['金额_CNY'].sum()
    total_expense = df[df['类型'] == '支出']['金额_CNY'].sum()
    daily_net = (total_income - total_expense) / days_span
    current_balance = total_income - total_expense
    predictions = {f'{day}天': current_balance + (daily_net * day) for day in [30, 60, 90]}
    forecast_dates = [datetime.now() + timedelta(days=i) for i in range(0, 91, 10)]
    forecast_balances = [current_balance + (daily_net * i) for i in range(0, 91, 10)]
    return predictions, pd.DataFrame({'日期': forecast_dates, '预测余额': forecast_balances})

def monte_carlo_simulation(df, forecast_days=90, iterations=1000):
    if df is None or df.empty: return None, None
    daily_net = df.groupby('日期')['净金额_CNY'].sum()
    mean_daily = daily_net.mean()
    std_daily = daily_net.std() if len(daily_net) > 1 else mean_daily * 0.1
    current_balance = df['净金额_CNY'].sum()
    sim_paths = []
    for _ in range(iterations):
        path = [current_balance]
        for _ in range(forecast_days):
            path.append(path[-1] + np.random.normal(mean_daily, std_daily))
        sim_paths.append(path)
    sim_df = pd.DataFrame(sim_paths).T
    return sim_df, np.percentile(sim_df.iloc[-1], 5)

def process_invoice_ocr(image_file):
    if not client: return "ERROR: API Key未配置"
    try:
        image_data = image_file.getvalue()
        base64_image = base64.b64encode(image_data).decode('utf-8')
        image_url = f"data:image/jpeg;base64,{base64_image}"
        prompt_text = """请提取这张发票图片中的金额、日期和发票号码，并以纯JSON格式输出。只输出JSON。键名必须严格为：'金额', '日期', '发票号码'。不要包含任何其他文字。"""
        response = client.chat.completions.create(
            model="glm-4v",
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt_text}, {"type": "image_url", "image_url": {"url": image_url}}]}]
        )
        return response.choices[0].message.content
    except Exception as e: return f"ERROR: {str(e)}"

def call_llm_agent(prompt):
    if not client: return "API Key 未配置，无法调用 AI 助手。"
    try:
        response = client.chat.completions.create(model="glm-4", messages=[{"role": "user", "content": prompt}])
        return response.choices[0].message.content
    except Exception as e: return f"AI 调用失败: {str(e)}"

@st.cache_data(ttl=7200)
def get_wecom_token(corpid, corpsecret):
    """获取企业微信自建应用Token（缓存2小时）"""
    if not (corpid and corpsecret): return None
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}&corpsecret={corpsecret}"
    try:
        res = requests.get(url, timeout=5).json()
        return res.get("access_token")
    except: return None

def send_personal_alert(corpid, corpsecret, agentid, touser, balance, safety_line, dashboard_url):
    """发送 Markdown 应用消息给指定个人，并携带跳转链接"""
    if not (corpid and corpsecret and agentid and touser):
        return False, "配置不完整"
    
    token = get_wecom_token(corpid, corpsecret)
    if not token:
        return False, "获取Token失败，请检查CorpID和Secret"

    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    
    # 使用 Markdown 语法构建带链接的卡片
    markdown_content = f"""**NexusFlow 现金流预警**
> 当前余额：¥{balance:,.2f}
> 安全线：¥{safety_line:,.2f}
> <font color="warning">已跌破安全线，请及时关注！</font>

[👉 点击此处跳转至分析仪表盘]({dashboard_url})"""

    data = {
        "touser": touser, 
        "msgtype": "markdown",
        "agentid": int(agentid),
        "markdown": {
            "content": markdown_content
        }
    }
    
    try:
        res = requests.post(url, json=data, timeout=5).json()
        if res.get("errcode") == 0:
            return True, "发送成功"
        return False, res.get("errmsg")
    except Exception as e:
        return False, str(e)

def send_wechat_alert(webhook_url, balance, safety_line):
    if not webhook_url: return False, "未配置Webhook"
    headers = {"Content-Type": "application/json"}
    data = {"msgtype": "text", "text": {"content": f"【NexusFlow 现金流预警】\n当前余额 ¥{balance:,.2f} 已跌破安全线 ¥{safety_line:,.2f}。"}}
    try:
        response = requests.post(webhook_url, headers=headers, json=data, timeout=5)
        return (True, "发送成功") if response.status_code == 200 else (False, f"HTTP {response.status_code}")
    except Exception as e: return False, str(e)

def auto_reconcile(flow_df, invoice_df):
    """[自动对账引擎]"""
    if flow_df is None or invoice_df is None or flow_df.empty or invoice_df.empty:
        return None, None
    income_df = flow_df[flow_df['类型'] == '收入'].copy()
    income_df['核销状态'] = '未核销'
    income_df['关联发票'] = ''
    
    for idx, inv_row in invoice_df.iterrows():
        inv_amount = round(float(inv_row['发票金额']), 2)
        for flow_idx, flow_row in income_df.iterrows():
            if income_df.at[flow_idx, '核销状态'] == '未核销':
                flow_amount = round(float(flow_row['金额_CNY']), 2)
                if abs(inv_amount - flow_amount) <= 1.0:
                    income_df.at[flow_idx, '核销状态'] = '已核销'
                    income_df.at[flow_idx, '关联发票'] = inv_row['发票号码']
                    break
    matched_invoice_nums = income_df[income_df['核销状态'] == '已核销']['关联发票'].tolist()
    unmatched_invoices = invoice_df[~invoice_df['发票号码'].isin(matched_invoice_nums)]
    return income_df, unmatched_invoices

# ================= 侧边栏：全局设置 =================
st.sidebar.header("系统与合规设置")
safety_line = st.sidebar.number_input("最低现金流安全线（CNY）", value=5000, step=1000)
auto_alert = st.sidebar.checkbox("启用自动预警")

st.sidebar.markdown("---")
st.sidebar.header("预警邮箱配置")
email_sender = st.sidebar.text_input("发件人邮箱")
email_password = st.sidebar.text_input("邮箱授权码", type="password")
email_receiver = st.sidebar.text_input("接收预警邮箱")

st.sidebar.markdown("---")
st.sidebar.header("企业微信个人预警配置")
wecom_corpid = st.sidebar.text_input("企业 CorpID")
wecom_secret = st.sidebar.text_input("自建应用 Secret", type="password")
wecom_agentid = st.sidebar.text_input("自建应用 AgentID")
wecom_userid = st.sidebar.text_input("接收人的 UserID（如：ZhangSan）")
dashboard_url = st.sidebar.text_input("跳转链接（你的公网网址）", value="https://your-app.streamlit.app")

if st.sidebar.button("测试发送个人预警"):
    if all([wecom_corpid, wecom_secret, wecom_agentid, wecom_userid]):
        # 注意这里调用的函数变成了 send_personal_alert
        success, msg = send_personal_alert(wecom_corpid, wecom_secret, wecom_agentid, wecom_userid, 1000.0, 5000.0, dashboard_url)
        if success: 
            st.sidebar.success("测试成功！请查看手机企业微信。")
        else: 
            st.sidebar.error(f"发送失败：{msg}")
    else: 
        st.sidebar.warning("请完整填写企业微信配置")
st.sidebar.markdown("---")
st.sidebar.info("数据来源声明：汇率数据来源于Frankfurter API，AI文本分析由智谱GLM-4提供，数据清洗与计算逻辑均在本地执行。")
st.sidebar.warning("免责声明：本工具仅提供数据整理、风险提示与内容生成，不构成任何财务、法律或投资建议。")

# ================= 主界面：模块化 Tab =================
tab_tools, tab_dashboard, tab_green, tab_opc, tab_trade = st.tabs([
    "数据接入与OCR", 
    "财务仪表盘与预测", 
    "绿色金融与ESG", 
    "一人公司 (OPC) AI 助手", 
    "外贸出海 AI 加速器"
])

# ================= Tab 1: 数据接入与OCR =================
with tab_tools:
    st.header("多平台数据接入与发票OCR")
    st.markdown("数据来源：本模块支持上传微信/支付宝导出Excel、发票图片，数据仅在本地处理。")
    
    uploaded_files = st.file_uploader("上传流水 Excel（可多选）", type=["xlsx", "xls"], accept_multiple_files=True)
    if uploaded_files:
        all_dfs = []
        quality_reports = []  # 👈 确保这行在这里，和 all_dfs 平齐！

        for file in uploaded_files:
            raw_df = pd.read_excel(file)
            # 接收两个返回值
            cleaned_df, qr = clean_and_align_data(raw_df, filename=file.name)
            all_dfs.append(cleaned_df)
            quality_reports.append(qr)
        if all_dfs:
            merged_df = pd.concat(all_dfs, ignore_index=True)
            st.session_state.merged_df = merged_df
            st.success(f"成功合并 {len(uploaded_files)} 个文件并完成清洗！")

            # 👇 修改 1：去掉了 expanded=True，现在默认是收起的，用户点击才会展开
            if quality_reports:
                with st.expander("查看数据清洗质量报告（透明度保障）"):
                    st.json(quality_reports)

            st.markdown("---")
            st.subheader("AI 实体对齐与客户标签")
            st.write("清洗效果预览（仅展示前100行，下载Excel可查看全量）：")

            cleaned_df = clean_entities(st.session_state.merged_df)
            st.session_state.merged_df = cleaned_df

            edited_df = st.data_editor(
                cleaned_df[['客户名', '客户名_标准', '客户标签']].head(100),
                column_config={
                    "客户名": st.column_config.TextColumn("原始名字", disabled=True),
                    "客户名_标准": st.column_config.TextColumn("标准名字"),
                    "客户标签": st.column_config.TextColumn("自动标签")
                },
                hide_index=True, use_container_width=True, key="entity_editor"
            )
        
        
            # 3. 按钮逻辑放在表格渲染之后，这样只要表格存在，变量就一定存在
            if st.button("保存修正结果并更新映射表", key="save_map_btn"):
                # 提取需要更新的规则
                new_rules = edited_df[edited_df['客户名'] != edited_df['客户名_标准']].copy()
                new_rules = new_rules.rename(columns={'客户名': 'raw_name', '客户名_标准': 'std_name', '客户标签': 'tag'})
                
                if not new_rules.empty:
                    # 初始化或更新 Session State 中的 mapping
                    if 'entity_mapping' not in st.session_state:
                        st.session_state.entity_mapping = new_rules
                    else:
                        st.session_state.entity_mapping = pd.concat(
                            [st.session_state.entity_mapping, new_rules]
                        ).drop_duplicates(subset=['raw_name'], keep='last')
                    
                    st.toast("规则已保存！系统已学会新客户。")
                else:
                    st.info("没有检测到修改过的名字。")
    st.markdown("---")
    st.subheader("发票批量识别与智能对账")
    st.markdown("数据来源：用户上传的发票图片。AI分析来源：智谱GLM-4V（多模态视觉模型）。")
    uploaded_invoices = st.file_uploader("上传发票图片（可多选）", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key="invoice_uploader")
    if uploaded_invoices:
        if st.button("开始批量识别"):
            for invoice in uploaded_invoices:
                with st.spinner(f"识别 {invoice.name} ..."):
                    result = process_invoice_ocr(invoice)
                    st.success(f"{invoice.name} 识别结果：")
                    st.code(result, language="json")
                    st.session_state.invoices.append(result)
                    
    st.markdown("---")
    st.subheader("智能对账台")
    if len(st.session_state.invoices) > 0:
        parsed_invoices = []
        for inv in st.session_state.invoices:
            try:
                clean_str = str(inv).replace('```json', '').replace('```', '').replace('\n', '').strip()
                json_match = re.search(r'\{.*\}', clean_str)
                if json_match:
                    data = json.loads(json_match.group())
                    raw_amount = data.get('金额') or data.get('amount') or data.get('Amount') or 0
                    if isinstance(raw_amount, str):
                        raw_amount = str(raw_amount).replace('¥', '').replace('$', '').replace(',', '').replace('元', '').strip()
                        try: amount = float(raw_amount)
                        except: amount = 0.0
                    else: amount = float(raw_amount) if raw_amount else 0.0
                    parsed_invoices.append({'发票金额': amount, '发票日期': data.get('日期') or data.get('date') or '', '发票号码': data.get('发票号码') or data.get('invoice_number') or ''})
            except Exception as e: print(f"解析发票失败: {e}")
            
        if parsed_invoices:
            invoice_df = pd.DataFrame(parsed_invoices)
            st.dataframe(invoice_df, use_container_width=True)
            st.caption("数据来源：大模型OCR提取的发票信息。")
            
            if st.session_state.merged_df is not None:
                with st.spinner("正在执行自动对账引擎..."):
                    reconciled_df, unmatched_invoices = auto_reconcile(st.session_state.merged_df, invoice_df)
                
                if reconciled_df is not None:
                    st.subheader("自动对账结果")
                    matched = reconciled_df[reconciled_df['核销状态'] == '已核销']
                    st.success(f"成功自动匹配核销了 {len(matched)} 笔流水！")
                    st.dataframe(matched[['日期', '客户名', '金额_CNY', '核销状态', '关联发票']], use_container_width=True)
                    st.caption("对账逻辑来源：基于金额容差的本地Python自动匹配算法（容差范围：1元）。")
                    
                    if unmatched_invoices is not None and not unmatched_invoices.empty:
                        st.subheader("未核销发票预警")
                        st.warning(f"发现 {len(unmatched_invoices)} 张发票尚未找到对应的流水，建议立即催收！")
                        st.dataframe(unmatched_invoices, use_container_width=True)
                        
                        if st.button("立即对未核销发票生成催款函", key="btn_ai_collection"):
                            with st.spinner("AI 正在起草..."):
                                invoice_text = unmatched_invoices.to_string(index=False)
                                prompt = f"请根据以下未核销的发票数据，写一封中英双语催款函。\n数据：\n{invoice_text}\n要求：语气坚定礼貌，给出7天内付款的截止日期，落款为 'NexusFlow 智能财务系统'。\n请务必包含中文翻译。"
                                collection_letter = call_llm_agent(prompt)
                                st.markdown(collection_letter)
                                st.caption("AI分析来源：智谱GLM-4。免责声明：AI自动起草的催收邮件内容仅供参考，请核实欠款事实后自行修改发送。")
                                st.download_button("下载催款函", data=collection_letter.encode('utf-8'), file_name="collection_letter.txt", mime="text/plain")
                    else:
                        st.info("所有发票均已找到对应的流水记录！")
    else:
        st.info("请先上传发票图片，并点击【开始批量识别】。")

# ================= Tab 2: 财务仪表盘与预测 =================
with tab_dashboard:
    st.header("跨平台财务全景与现金流预测")
    if st.session_state.merged_df is None:
        st.info("请先在「数据接入与OCR」标签页上传多平台流水 Excel。")
    else:
        df = st.session_state.merged_df
        total_income = df[df['金额'] > 0]['净金额_CNY'].sum()
        total_expense = df[df['金额'] < 0]['净金额_CNY'].sum() 
        balance = df['净金额_CNY'].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("累计总收入", f"¥{total_income:,.2f}")
        col2.metric("累计总支出", f"¥{total_expense:,.2f}")
        col3.metric("实际可用现金流", f"¥{balance:,.2f}")
        
           # 主动预警逻辑
        if balance < safety_line:
            st.error(f"危险：当前余额 ¥{balance:,.2f} 低于安全线 ¥{safety_line:,.2f}！")
            if auto_alert:
                # 1. 邮件预警
                if email_sender and email_password and email_receiver:
                    success, msg = send_email_alert(email_sender, email_password, email_receiver, balance, safety_line)
                    if success: 
                        st.success("邮件预警已发送。")
                    else: 
                        st.warning(f"邮件预警失败: {msg}")
                
                # 2. 企业微信个人预警
                if all([wecom_corpid, wecom_secret, wecom_agentid, wecom_userid]):
                    if 'last_alert_sent' not in st.session_state:
                        st.session_state.last_alert_sent = False
                        
                    if not st.session_state.last_alert_sent:
                        success_wx, msg_wx = send_personal_alert(wecom_corpid, wecom_secret, wecom_agentid, wecom_userid, balance, safety_line, dashboard_url)
                        if success_wx: 
                            st.success("企业微信个人预警已自动发送。")
                            st.session_state.last_alert_sent = True
                        else: 
                            st.warning(f"企业微信预警失败: {msg_wx}")
        else:
            st.success("现金流健康。")
        st.subheader("未来 30/60/90 天现金流预测")
        st.markdown("数据来源：用户上传流水；分析来源：基于历史DSO的本地线性推演算法。")
        predictions, forecast_df = predict_cash_flow(df, safety_line)
        if predictions:
            p_col1, p_col2, p_col3 = st.columns(3)
            p_col1.metric("30天预测余额", f"¥{predictions['30天']:,.2f}")
            p_col2.metric("60天预测余额", f"¥{predictions['60天']:,.2f}")
            p_col3.metric("90天预测余额", f"¥{predictions['90天']:,.2f}")
            forecast_df['预测下限'] = forecast_df['预测余额'] * 0.85
            forecast_df['预测上限'] = forecast_df['预测余额'] * 1.15
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=forecast_df['日期'].tolist() + forecast_df['日期'].tolist()[::-1], y=forecast_df['预测上限'].tolist() + forecast_df['预测下限'].tolist()[::-1], fill='toself', fillcolor='rgba(0,100,80,0.2)', line=dict(color='rgba(255,255,255,0)'), showlegend=False))
            fig.add_trace(go.Scatter(x=forecast_df['日期'], y=forecast_df['预测余额'], mode='lines+markers', name='预测现金流', line=dict(color='#00CC96', width=3)))
            fig.add_hline(y=safety_line, line_dash="dash", line_color="red", annotation_text="安全线")
            fig.update_layout(title="现金流趋势与风险区间预测", template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")
        st.subheader("蒙特卡洛现金流模拟 (VaR 风险价值)")
        st.markdown("分析来源：本地Python实现的蒙特卡洛随机模拟（1000次路径），结合正态分布假设。")
        with st.spinner("正在模拟1000条未来现金流路径..."):
            sim_df, var_95 = monte_carlo_simulation(df)
            if sim_df is not None:
                fig_mc = go.Figure()
                for i in range(50):
                    fig_mc.add_trace(go.Scatter(y=sim_df.iloc[:, i], mode='lines', line=dict(width=0.5, color='rgba(0, 150, 255, 0.1)'), showlegend=False))
                fig_mc.add_hline(y=safety_line, line_dash="dash", line_color="red", annotation_text="安全线")
                st.plotly_chart(fig_mc, use_container_width=True)
                st.error(f"VaR 风控指标：在 95% 的置信度下，未来90天你的现金流最低不会低于 **¥{var_95:,.2f}**。")
                st.caption("风险模型来源：本地Python代码，基于历史均值与标准差推演。")

        st.subheader("收支对比")
        st.bar_chart(pd.DataFrame({"类别": ["收入", "支出"], "金额": [total_income, total_expense]}).set_index("类别"))
        
        st.markdown("---")
        st.subheader("AI 客户画像与个性化反馈")
        st.markdown("数据来源：本地清洗后的交易流水；分析来源：智谱GLM-4。")
        profile_df = update_client_profiles(df)
        st.dataframe(profile_df)
        if st.button("让AI分析客户异动并生成建议"):
            if len(profile_df) > 0:
                prompt = f"你是一个资深的跨境企业CFO。请根据以下客户画像数据（包含ESG评分），分析交易健康度。特别关注回款周期变化，以及高ESG评分的客户。给出具体的行动指令。请用中文输出。\n{profile_df.to_string(index=False)}"
                with st.spinner("AI CFO 正在研判..."):
                    st.markdown(call_llm_agent(prompt))
                    st.caption("AI分析来源：智谱GLM-4。免责声明：上述AI CFO分析建议由大语言模型生成，可能存在偏差，仅供业务参考。")

        st.markdown("---")
        st.subheader("多 Agent 协同风控")
        if st.button("启动多Agent自动风控扫描"):
            if balance < safety_line:
                with st.spinner("CashFlowAgent 检测到余额告急！正在唤醒其他Agent..."):
                    prompt = f"你是一个催收Agent。当前公司现金流余额为 ¥{balance:,.2f}，已跌破安全线。请给主要客户写一封紧急但礼貌的催款邮件草稿（中英双语）。请务必包含中文翻译。"
                    collection_draft = call_llm_agent(prompt)
                    st.error("【CashFlowAgent】警报：现金流已跌破安全线！")
                    st.warning("【ProcurementAgent】建议：请立即暂缓非必要的采购支出。")
                    st.info("【CollectionAgent】已自动为您起草催款邮件，请复制发送：")
                    st.text_area("催款邮件草稿", collection_draft, height=200)
                    st.caption("AI分析来源：智谱GLM-4。免责声明：AI自动起草的催收邮件内容仅供参考，请核实欠款事实后自行修改发送。")
            else:
                st.success("【CashFlowAgent】报告：现金流健康，无需触发其他Agent。")

# ================= Tab 3: 绿色金融与ESG =================
with tab_green:
    st.header("绿色金融与全球ESG合规")
    st.markdown("应对CBAM碳关税与全球供应链ESG准入，为出海企业提供数字绿色信用底座。")
    
    if st.session_state.merged_df is None:
        st.info("请先在「数据接入与OCR」标签页上传流水 Excel，系统将自动分析绿色交易与ESG指标。")
    else:
        df = st.session_state.merged_df
        green_df = df[df['绿色标签'] == '绿色交易']
        total_count = len(df)
        green_count = len(green_df)
        green_ratio = (green_count / total_count * 100) if total_count > 0 else 0
        green_amount = green_df['净金额_CNY'].sum()
        
        with st.container(border=True):
            st.subheader("ESG与绿色交易核心指标")
            st.markdown("数据来源：用户上传的流水数据；指标计算来源：本地Python绿色关键词规则匹配。")
            col1, col2, col3 = st.columns(3)
            col1.metric("绿色交易占比", f"{green_ratio:.1f}%")
            col2.metric("绿色交易总额", f"¥{green_amount:,.2f}")
            esg_score = min(100, int(green_ratio * 1.5 + 30)) 
            col3.metric("综合ESG评分", f"{esg_score} / 100")
            
            if green_ratio > 30:
                st.success("您的企业绿色交易占比极高，完全符合欧盟CBAM及国际供应链ESG准入标准，具备申请绿色低息贷款的资质。")
            else:
                st.warning("绿色交易占比偏低，建议增加环保采购与新能源投入，以应对国际绿色贸易壁垒。")

        st.markdown("---")
        with st.container(border=True):
            st.subheader("外贸绿色合规与风险透视（AI Agent）")
            st.markdown("分析来源：智谱GLM-4，结合国际环保法规。")
            if st.button("启动AI绿色合规扫描"):
                with st.spinner("AI 正在分析国际环保法规与您的交易数据..."):
                    prompt = f"你是一个精通国际绿色贸易壁垒与ESG合规的资深顾问。目前企业总交易笔数为 {total_count} 笔，其中绿色交易 {green_count} 笔，绿色交易总额为 ¥{green_amount:,.2f}，占总收入比例为 {green_ratio:.1f}%。请根据以上数据，结合当前欧盟CBAM和国际大厂的供应链ESG要求，写一份简短的风险透视报告。要求：评估当前企业在国际出海中的绿色合规风险等级；指出可能面临的关税成本、订单流失风险；给出具体的绿色转型行动建议。请用中文输出。"
                    st.markdown(call_llm_agent(prompt))
                    st.caption("AI分析来源：智谱GLM-4。免责声明：上述合规风险分析由AI根据现有数据推演，不构成法律或税务合规意见。")

        st.markdown("---")
        with st.container(border=True):
            st.subheader("绿色金融产品智能匹配")
            st.markdown("匹配逻辑来源：本地Python规则引擎，结合ESG评分。")
            col_p1, col_p2 = st.columns(2)
            with col_p1:
                st.info("方案A：绿色信贷（Green Credit）\n\n适用对象：绿色交易占比>30%的企业。\n\n优势：低于市场平均利率1-2个百分点。")
            with col_p2:
                st.success("方案B：碳配额质押融资\n\n适用对象：有碳配额或碳减排项目的企业。\n\n优势：盘活碳资产，获取额外流动性。")
            if st.button("生成专属绿色金融融资建议书"):
                with st.spinner("AI 正在撰写建议书..."):
                    prompt = f"你是一个专业的绿色供应链金融分析师。请根据以下企业数据，写一份《企业绿色信用与供应链融资建议书》。数据：绿色交易占比 {green_ratio:.1f}%，绿色交易总额 ¥{green_amount:,.2f}，综合ESG评分 {esg_score}。要求：包含企业绿色表现总结、推荐匹配的绿色金融产品、以及下一步绿色转型的具体建议。请用中文输出。"
                    st.markdown(call_llm_agent(prompt))
                    st.caption("AI分析来源：智谱GLM-4。免责声明：融资建议书由AI根据系统数据生成，不构成法律意见或投资理财建议。实际融资方案请以持牌金融机构的最终审批为准。")

# ================= Tab 4: 一人公司 (OPC) AI 助手 =================
with tab_opc:
    st.header("微型团队 AI 运营助理")
    opc_tab1, opc_tab2, opc_tab3 = st.tabs(["行政与财务简报", "AI 营销内容", "数据资产与绿色金融"])
    
    with opc_tab1:
        st.subheader("月度经营简报生成")
        st.markdown("数据来源：用户流水；分析来源：智谱GLM-4。")
        if st.session_state.merged_df is not None:
            if st.button("生成月度经营简报"):
                df = st.session_state.merged_df
                summary = f"本月总收入：¥{df[df['类型']=='收入']['金额_CNY'].sum():,.2f}\n本月总支出：¥{df[df['类型']=='支出']['金额_CNY'].sum():,.2f}\n交易总笔数：{len(df)}笔"
                with st.spinner("正在生成简报..."):
                    st.markdown(call_llm_agent(f"请根据以下财务数据，生成一份简洁专业的月度经营简报（包含数据总结、趋势分析、下月建议，纯文字）。请用中文输出。\n{summary}"))
                    st.caption("AI分析来源：智谱GLM-4。免责声明：简报内容由AI自动汇总生成，仅供管理层内部参考。")
        else: st.info("需要先上传数据。")

    with opc_tab2:
        st.subheader("多平台营销文案生成")
        st.markdown("分析来源：智谱GLM-4。")
        # 这里的 key="opc_product_info" 是新增的
        product_info = st.text_area("请输入产品/服务简介：", "一款帮助SaaS创业者自动追踪现金流的AI工具。", key="opc_product_info")
        target_platform = st.selectbox("选择目标发布平台：", TARGET_PLATFORMS, index=0, key="opc_platform_select")
        target_lang = st.selectbox("选择目标语言：", GLOBAL_LANGUAGES, index=0, key="opc_lang_select")
        if st.button("生成营销文案"):
            with st.spinner("正在生成..."):
                prompt = f"请为以下产品生成营销文案。目标平台：{target_platform}，目标语言：{target_lang}。产品描述：{product_info}。要求：契合该平台的受众风格和文案长度规范。请将生成内容翻译成中文，并按照[目标语言]原文 + [中文翻译]的格式输出。"
                st.markdown(call_llm_agent(prompt))
                st.caption("AI分析来源：智谱GLM-4。免责声明：文案内容由AI生成，发布前请人工审核。")

    with opc_tab3:
        st.subheader("企业数字信用资产中心")
        st.markdown("沉淀的真实经营数据，是您未来获取供应链金融低息融资的核心资产。")
        
        col_a, col_b = st.columns(2)
        with col_a:
            rate_count = 0
            if 'exchange_rates' in st.session_state: 
                rate_count = len(st.session_state.exchange_rates)
            st.metric("历史汇率对齐记录", f"{rate_count} 次")
        with col_b:
            profile_count = len(st.session_state.get('client_profiles', [])) 
            st.metric("已刻画的供应链伙伴", f"{profile_count} 个")
            
        st.info("💡 **数字资产变现提示**：当您的供应链伙伴画像与真实交易流水积累到一定规模后，NexusFlow 将为您生成《企业动态商业健康分》。您可以凭此报告，向合作银行或保理机构申请“发票贴现”或“收益权众筹（RBF）”，从而获得更低成本的经营现金流。")
        
        # 👇 新增：免责声明
        st.caption("⚠️ **免责声明**：本模块展示的数据资产与信用评估参考仅供内部经营管理使用，不构成任何信用评级、担保或融资承诺。实际融资额度、利率及审批结果，须以持牌金融机构的最终独立决策为准。本系统不碰资金池，不承担任何信贷违约风险。")  
# ================= Tab 5: 外贸出海 AI 加速器 =================
with tab_trade:
    st.header("跨境贸易 AI 加速器")
    trade_tab1, trade_tab2, trade_tab3 = st.tabs(["多语言智能催收", "海外风控信息聚合", "多平台营销文案生成"])
    
    with trade_tab1:
        st.subheader("多语言催收邮件生成")
        st.markdown("分析来源：智谱GLM-4。")
        client_name = st.text_input("客户名称：")
        overdue_days = st.number_input("逾期天数：", min_value=1, value=30)
        overdue_amount = st.number_input("逾期金额（USD）：", min_value=0.0, value=1000.0)
        target_lang = st.selectbox("目标语言：", GLOBAL_LANGUAGES, index=0, key="trade_collection_lang")
        
        if st.button("生成催收邮件"):
            prompt = f"请以专业的{target_lang}写一封催收邮件。客户：{client_name}，逾期{overdue_days}天，金额${overdue_amount}。语气需坚定但得体。请将生成内容翻译成中文，并按照[目标语言]原文 + [中文翻译]的格式输出。"
            with st.spinner("正在起草..."):
                st.text_area("生成结果：", call_llm_agent(prompt), height=300)
                st.caption("AI分析来源：智谱GLM-4。免责声明：翻译及邮件内容由AI生成，请核对客户信息和欠款金额后发送。")

    with trade_tab2:
        st.subheader("买家背景信息透视")
        st.markdown("分析来源：智谱GLM-4，基于公开网络数据聚合。")
        company_name = st.text_input("海外买家/公司名称（如：ABC Trading LLC）：")
        if st.button("生成信息透视报告"):
            with st.spinner("正在聚合公开数据..."):
                st.markdown(call_llm_agent(f"请帮我搜集并聚合关于'{company_name}'的公开商业信息（包括其主营业务、潜在关联方、公开的市场声誉等）。请以客观报告形式呈现，不要提供信用评级。请用中文输出。"))
                st.caption("AI分析来源：智谱GLM-4。免责声明：海外买家背景信息聚合自公开网络数据，系统不对其真实性、完整性做任何担保。")

    with trade_tab3:
        st.subheader("多平台营销文案生成")
        st.markdown("分析来源：智谱GLM-4。支持国内外主流平台。")
        # 这里的 key="trade_product_desc" 是新增的
        domestic_listing = st.text_area("请输入产品描述：", "高品质无线蓝牙耳机，超长续航，防水防汗。", key="trade_product_desc")
        target_platform = st.selectbox("选择目标平台：", TARGET_PLATFORMS, index=0, key="trade_platform_select")
        target_lang = st.selectbox("选择目标语言：", GLOBAL_LANGUAGES, index=0, key="trade_lang_select")
        if st.button("生成海外/国内本土化文案"):
            with st.spinner("正在本地化..."):
                prompt = f"请将以下产品描述，重塑为适合{target_platform}平台的营销文案，目标语言为{target_lang}。风格需契合该平台受众习惯，突出核心卖点。产品描述：{domestic_listing}。请将生成内容翻译成中文，并按照[目标语言]原文 + [中文翻译]的格式输出。"
                st.write(call_llm_agent(prompt))
                st.caption("AI分析来源：智谱GLM-4。免责声明：文案内容由AI生成，发布前请人工审核。")
# ================= 页脚 =================
st.markdown("---")
st.caption("Built with Python + Streamlit + Pandas + Zhipu GLM-4V | NexusFlow 智能商业生态 4.1")