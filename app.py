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
from functools import lru_cache
from entity_aligner import clean_entities
import imaplib
import email
from email.header import decode_header
import json
# ================= 环境与配置 =================
load_dotenv()
# 初始化智谱 AI 客户端
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
client = ZhipuAI(api_key=ZHIPU_API_KEY) if ZHIPU_API_KEY else None

# ================= 页面设置 =================
st.set_page_config(page_title="跨境AI COO 智能商业生态", page_icon="🌍", layout="wide")
st.title("🌍 跨境 AI COO 智能商业生态")
st.markdown("**为一人公司与出海企业打造的 AI 驱动财务与运营工具**")

# ================= 初始化 Session State =================
if 'merged_df' not in st.session_state:
    st.session_state.merged_df = None
if 'invoices' not in st.session_state:
    st.session_state.invoices = []

# ================= 核心引擎（数据清洗与预测） =================

@st.cache_data(ttl=3600)
def get_exchange_rate_by_date(from_currency="USD", to_currency="CNY", date_str=None):
    """获取指定日期的历史汇率，带 1 小时缓存"""
    if from_currency == to_currency:
        return 1.0
    try:
        # 如果没传日期，就查最新；如果传了，就查历史
        if date_str:
            url = f"https://api.frankfurter.app/{date_str}?from={from_currency}&to={to_currency}"
        else:
            url = f"https://api.frankfurter.app/latest?from={from_currency}&to={to_currency}"
        response = requests.get(url, timeout=5)
        return response.json().get('rates', {}).get(to_currency, 7.2)
    except Exception:
        return 7.2  # 兜底

def clean_and_align_data(df):
    """
    [核心引擎] 数据清洗与对齐（全进阶版）
    """
    # ================= 1. 基础结构清洗 =================
    # 1.1 列名映射
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
    
    # 1.2 确保必备列存在
    required_cols = ['日期', '客户名', '金额', '类型', '币种']
    for col in required_cols:
        if col not in df.columns:
            df[col] = '未知' if col != '金额' else 0.0
            
    if '手续费' not in df.columns: df['手续费'] = 0.0
    if '描述' not in df.columns: df['描述'] = ''
    
    # 1.3 日期格式化（核心修复点：逐行处理时区）
    def parse_date(date_val):
        if pd.isna(date_val): return pd.Timestamp.now().normalize()
        try:
            # 尝试解析，如果带有 UTC 等信息
            dt = pd.to_datetime(date_val, errors='coerce')
            # 如果有时区信息，转为北京时间，并去掉时区，变成 naive 时间
            if dt.tz is not None:
                dt = dt.tz_convert('Asia/Shanghai').tz_localize(None)
            return dt.normalize() # 只保留年月日
        except:
            return pd.Timestamp.now().normalize()

    # 这里直接调用 apply 逐行处理，完美解决混合时区报错
    df['日期'] = df['日期'].apply(parse_date)
    
    # 1.4 空值与重复值处理
    df = df.dropna(subset=['日期', '金额']) # 日期和金额为空的直接删除
    df = df.drop_duplicates() # 删除完全相同的重复交易记录
    
    # ================= 2. 业务语义清洗 =================
    # 2.1 数值清洗
    def parse_amount(val):
        if pd.isna(val): return 0.0
        val_str = str(val).replace('¥', '').replace('$', '').replace(',', '').replace('元', '').replace(' ', '')
        try:
            return float(val_str)
        except ValueError:
            return 0.0
            
    df['金额'] = df['金额'].apply(parse_amount)
    df['手续费'] = df['手续费'].apply(parse_amount)
    
    # 2.2 收支方向校准
    def normalize_type(row):
        t = str(row['类型']).lower()
        amount = row['金额']
        if t in ['收入', '入账', 'income']:
            return abs(amount) 
        else: 
            return -abs(amount) 
            
    df['金额'] = df.apply(normalize_type, axis=1)
    
    # 2.3 多币种转换与手续费剥离
    
    def convert_currency_and_fee(row):
        currency = row['币种']
        amount = row['金额']
        fee = row['手续费']
        date_str = row['日期'].strftime('%Y-%m-%d') # 提取交易发生的历史日期
        
        # 调用新函数获取历史汇率
        rate = get_exchange_rate_by_date(currency, 'CNY', date_str)
        if rate == 1.0: rate = 1.0 # 如果是人民币，汇率就是1
        
        amount_cny = amount * rate
        fee_cny = fee * rate
        
        # 顺手算一个“今日汇率”来对比（生成汇兑损益参考）
        today_rate = get_exchange_rate_by_date(currency, 'CNY', None)
        forex_gain_loss = amount * (today_rate - rate) if today_rate else 0
        
        return pd.Series([amount_cny, fee_cny, rate, forex_gain_loss])
        
    # 新增记录：汇率 和 汇兑损益
    df[['金额_CNY', '手续费_CNY', '使用汇率', '汇兑损益']] = df.apply(convert_currency_and_fee, axis=1) 
    
    # 2.4 净到手金额
    df['净金额_CNY'] = df['金额_CNY'] - df['手续费_CNY']
    
    # 2.5 科目打标
    def classify_category(desc):
        desc = str(desc).lower()
        if any(k in desc for k in ['顺丰', '物流', '快递', 'ship', 'express']): return '物流费'
        if any(k in desc for k in ['采购', '阿里', '1688', 'purchase', 'supplier']): return '采购成本'
        if any(k in desc for k in ['工资', '薪', 'payroll', 'salary']): return '人力成本'
        if any(k in desc for k in ['广告', '推广', 'ad', 'marketing']): return '营销推广'
        if any(k in desc for k in ['税', 'tax']): return '税费'
        return '其他'

    df['科目分类'] = df['描述'].apply(classify_category)
    
    # 3. 排序
    df = df.sort_values('日期').reset_index(drop=True)
    # ================= 4. 异常值与逻辑校验 =================
    df['异常标记'] = ''
    
    # 4.1 未来日期检测
    today = pd.Timestamp.now().normalize()
    future_mask = df['日期'] > today
    df.loc[future_mask, '异常标记'] += '【未来日期】 '
    
    # 4.2 单笔金额异常检测（超过均值10倍）
    if len(df) > 3: # 样本太少不适用
        mean_amount = df['净金额_CNY'].abs().mean()
        outlier_mask = df['净金额_CNY'].abs() > (mean_amount * 10)
        df.loc[outlier_mask, '异常标记'] += '【大额异常，请复核】 '
        
    # 4.3 逻辑矛盾检测（余额为负）
    # 这里只是简单示例，真实余额需要按时间累加
    if df['净金额_CNY'].sum() < 0:
        df['异常标记'] += '【总现金流为负】 '
    return df
def predict_cash_flow(df, safety_line):
    """
    [预测引擎] 基于历史回款周期(DSO)预测未来现金流
    简化算法：计算历史平均每日流出/流入，结合当前余额进行线性推演。
    """
    if df is None or df.empty:
        return None
    
    # 计算历史日均净现金流
    days_span = (df['日期'].max() - df['日期'].min()).days + 1
    if days_span <= 0:
        days_span = 1
    
    total_income = df[df['类型'] == '收入']['金额_CNY'].sum()
    total_expense = df[df['类型'] == '支出']['金额_CNY'].sum()
    daily_net = (total_income - total_expense) / days_span
    
    current_balance = total_income - total_expense
    
    # 预测 30/60/90 天
    future_days = [30, 60, 90]
    predictions = {}
    for day in future_days:
        future_balance = current_balance + (daily_net * day)
        predictions[f'{day}天'] = future_balance
        
    # 生成预测数据框用于可视化
    forecast_dates = [datetime.now() + timedelta(days=i) for i in range(0, 91, 10)]
    forecast_balances = [current_balance + (daily_net * i) for i in range(0, 91, 10)]
    forecast_df = pd.DataFrame({'日期': forecast_dates, '预测余额': forecast_balances})
    
    return predictions, forecast_df

def process_invoice_ocr(image_file):
    """调用GLM-4V从发票图片中提取信息（Base64编码）"""
    if not client:
        return "ERROR: API Key未配置"
    try:
        image_data = image_file.getvalue()
        base64_image = base64.b64encode(image_data).decode('utf-8')
        image_url = f"data:image/jpeg;base64,{base64_image}"
        
        response = client.chat.completions.create(
            model="glm-4v",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "请提取这张发票图片中的金额、日期和发票号码，并以JSON格式输出。只输出JSON。"},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"ERROR: {str(e)}"

def call_llm_agent(prompt):
    """调用大模型生成内容（封装）"""
    if not client:
        return "API Key 未配置，无法调用 AI 助手。"
    try:
        response = client.chat.completions.create(
            model="glm-4",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI 调用失败: {str(e)}"

def send_email_alert(sender, password, receiver, balance, safety_line):
    """主动预警邮件发送"""
    if not (sender and password and receiver):
        return False, "邮箱配置不完整"
    try:
        mail_host = "smtp.qq.com"
        message = MIMEText(f"【现金流预警】当前余额 ¥{balance:,.2f} 已低于安全线 ¥{safety_line:,.2f}。请立即关注应收账款和供应链支出！", 'plain', 'utf-8')
        message['From'] = Header("AI COO 预警系统", 'utf-8')
        message['To'] = Header("创始人", 'utf-8')
        message['Subject'] = Header(" 紧急：现金流跌破安全线预警", 'utf-8')
        
        smtpObj = smtplib.SMTP_SSL(mail_host, 465)
        smtpObj.login(sender, password)
        smtpObj.sendmail(sender, [receiver], message.as_string())
        smtpObj.quit()
        return True, "发送成功"
    except Exception as e:
        return False, str(e)
def send_wechat_alert(webhook_url, balance, safety_line):
    """通过企业微信Webhook发送预警"""
    if not webhook_url:
        return False, "未配置Webhook"
    headers = {"Content-Type": "application/json"}
    data = {
        "msgtype": "text",
        "text": {
            "content": f"【AI COO 现金流预警】\n当前余额 ¥{balance:,.2f} 已跌破安全线 ¥{safety_line:,.2f}。\n请立即关注应收账款和供应链支出！"
        }
    }
    try:
        response = requests.post(webhook_url, headers=headers, json=data, timeout=5)
        if response.status_code == 200:
            return True, "发送成功"
        return False, f"HTTP {response.status_code}"
    except Exception as e:
        return False, str(e)   
    def send_wechat_alert(webhook_url, balance, safety_line):

     """通过企业微信Webhook发送预警"""
    if not webhook_url:
            return False, "未配置Webhook"
    headers = {"Content-Type": "application/json"}
    data = {
            "msgtype": "text",
            "text": {
                "content": f"【AI COO 现金流预警】\n当前余额 ¥{balance:,.2f} 已跌破安全线 ¥{safety_line:,.2f}。\n请立即关注应收账款和供应链支出！"
            }
        }
    try:
            response = requests.post(webhook_url, headers=headers, json=data, timeout=5)
            if response.status_code == 200:
                return True, "发送成功"
            return False, f"HTTP {response.status_code}"
    except Exception as e:
            return False, str(e)
# ================= 侧边栏：全局设置 =================
st.sidebar.header("⚙️ 系统与合规设置")
safety_line = st.sidebar.number_input("最低现金流安全线（CNY）", value=5000, step=1000)
auto_alert = st.sidebar.checkbox("启用自动邮件预警")

st.sidebar.markdown("---")
st.sidebar.header("📧 预警邮箱配置")
email_sender = st.sidebar.text_input("发件人邮箱")
email_password = st.sidebar.text_input("邮箱授权码", type="password")
email_receiver = st.sidebar.text_input("接收预警邮箱")
st.sidebar.markdown("---")
st.sidebar.header("📱 微信预警配置")
wechat_webhook = st.sidebar.text_input("企业微信机器人 Webhook URL", type="password")

# 👇 新增：手动测试按钮
if st.sidebar.button("测试发送微信预警"):
    if wechat_webhook:
        # 使用虚拟数据测试：假设余额1000，安全线5000
        success, msg = send_wechat_alert(wechat_webhook, 1000.0, 5000.0)
        if success:
            st.sidebar.success("✅ 测试成功！请查看手机企业微信。")
        else:
            st.sidebar.error(f"❌ 发送失败：{msg}")
    else:
        st.sidebar.warning("⚠️ 请先在输入框填入 Webhook URL")
st.sidebar.markdown("---")
st.sidebar.warning(" **免责声明**：本工具仅提供数据整理、风险提示与内容生成，不构成任何财务、法律或投资建议。使用者需自行承担相关决策风险。")

# ================= 主界面：模块化 Tab =================
tab_dashboard, tab_opc, tab_trade, tab_tools = st.tabs([
    " 财务仪表盘与预测", 
    " 一人公司 (OPC) AI 助手", 
    " 外贸出海 AI 加速器",
    " 数据接入与OCR"
])

# ================= Tab 1: 财务仪表盘与预测 =================
with tab_dashboard:
    st.header("📊 跨平台财务全景与现金流预测")
    
    if st.session_state.merged_df is None:
        st.info("请先在「数据接入与OCR」标签页上传多平台流水 Excel。")
    else:
        df = st.session_state.merged_df
       # 注意：收入为正，支出为负，所以用 sum() 可以直接相加算净额
        total_income = df[df['金额'] > 0]['净金额_CNY'].sum()
        total_expense = df[df['金额'] < 0]['净金额_CNY'].sum() 
        balance = df['净金额_CNY'].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("📈 累计总收入", f"¥{total_income:,.2f}")
        col2.metric("📉 累计总支出", f"¥{total_expense:,.2f}")
        col3.metric("💵 实际可用现金流", f"¥{balance:,.2f}")
        
        # 主动预警逻辑
        if balance < safety_line:
            st.error(f"⚠️ 危险：当前余额 ¥{balance:,.2f} 低于安全线 ¥{safety_line:,.2f}！")
            if auto_alert:
                # 1. 邮件预警
                if email_sender and email_password and email_receiver:
                    success, msg = send_email_alert(email_sender, email_password, email_receiver, balance, safety_line)
                    if success: st.success("邮件预警已发送。")
                    else: st.warning(f"邮件预警失败: {msg}")
                
                # 2. 微信预警 (新增！)
                if wechat_webhook:
                    success_wx, msg_wx = send_wechat_alert(wechat_webhook, balance, safety_line)
                    if success_wx: st.success("微信预警已发送。")
                    else: st.warning(f"微信预警失败: {msg_wx}")
        else:
            st.success("✅ 现金流健康。")
            
        # 预测引擎
        st.subheader("🔮 未来 30/60/90 天现金流预测（基于历史 DSO）")
        predictions, forecast_df = predict_cash_flow(df, safety_line)
        if predictions:
            p_col1, p_col2, p_col3 = st.columns(3)
            p_col1.metric("30天预测余额", f"¥{predictions['30天']:,.2f}")
            p_col2.metric("60天预测余额", f"¥{predictions['60天']:,.2f}")
            p_col3.metric("90天预测余额", f"¥{predictions['90天']:,.2f}")
            st.line_chart(forecast_df.set_index('日期'))
        
        st.subheader("📈 收支对比")
        chart_data = pd.DataFrame({"类别": ["收入", "支出"], "金额": [total_income, total_expense]})
        st.bar_chart(chart_data.set_index("类别"))
        
        # 数据导出
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 下载清洗后的标准流水表", data=output.getvalue(), file_name="cleaned_flow.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
          # ================= 新增：同比/环比分析 =================
        st.markdown("---")
        st.subheader("📈 周期性聚合与趋势分析")
        
        # 按周和按月聚合
        df_time = df.set_index('日期')
        monthly_data = df_time.resample('ME')['净金额_CNY'].sum().reset_index()
        monthly_data['环比增长率'] = monthly_data['净金额_CNY'].pct_change() * 100
        
        st.write("按月聚合及环比数据：")
        st.dataframe(monthly_data.style.format({'净金额_CNY': '¥{:.2f}', '环比增长率': '{:.2f}%'}))
        
        st.line_chart(monthly_data.set_index('日期')['净金额_CNY'])
# ================= Tab 2: 一人公司 (OPC) AI 助手 =================
with tab_opc:
    st.header("💼 微型团队 AI 运营助理")
    st.markdown("为独立开发者与一人公司解放精力，专注核心业务。")
    
    opc_tab1, opc_tab2, opc_tab3 = st.tabs(["行政与财务简报", "AI 营销内容", "法务与客服辅助"])
    
    with opc_tab1:
        st.subheader("📄 月度经营简报生成")
        if st.session_state.merged_df is not None:
            if st.button("生成月度经营简报"):
                df = st.session_state.merged_df
                summary = f"本月总收入：¥{df[df['类型']=='收入']['金额_CNY'].sum():,.2f}\n" \
                          f"本月总支出：¥{df[df['类型']=='支出']['金额_CNY'].sum():,.2f}\n" \
                          f"交易总笔数：{len(df)}笔"
                prompt = f"请根据以下财务数据，生成一份简洁专业的月度经营简报（包含数据总结、趋势分析、下月建议，纯文字）：\n{summary}"
                with st.spinner("正在生成简报..."):
                    report = call_llm_agent(prompt)
                st.markdown(report)
        else:
            st.info("需要先上传数据才能生成简报。")

    with opc_tab2:
        st.subheader("✍️ 多平台营销文案生成")
        product_info = st.text_area("请输入产品/服务简介：", "一款帮助SaaS创业者自动追踪现金流的AI工具。")
        if st.button("生成 SEO 与社媒文案"):
            prompt = f"请为以下产品生成：1篇SEO博客大纲、3条Twitter推文、1条LinkedIn帖子。产品描述：{product_info}"
            with st.spinner("正在生成文案..."):
                content = call_llm_agent(prompt)
            st.markdown(content)

    with opc_tab3:
        st.subheader("📑 标准化合同条款提示（不提供法律建议）")
        contract_text = st.text_area("粘贴合同文本片段（AI将标红风险提示）：", "双方同意本合同一经签署不可撤销，违约金为合同总额的100%。")
        if st.button("进行风险文本提示"):
            prompt = f"仅做文本风险提示，不提供法律解释。指出以下合同条款中可能对乙方不利的表述，并进行标红：\n{contract_text}"
            with st.spinner("正在分析..."):
                analysis = call_llm_agent(prompt)
            st.warning(analysis)

# ================= Tab 3: 外贸出海 AI 加速器 =================
with tab_trade:
    st.header("🚢 跨境贸易 AI 加速器")
    st.markdown("针对出海企业的痛点，提供多语言、风控与营销本地化支持。")
    
    trade_tab1, trade_tab2, trade_tab3 = st.tabs(["多语言智能催收", "海外风控信息聚合", "Listing 本地化营销"])
    
    with trade_tab1:
        st.subheader("✉️ 多语言催收邮件生成")
        client_name = st.text_input("客户名称：")
        overdue_days = st.number_input("逾期天数：", min_value=1, value=30)
        overdue_amount = st.number_input("逾期金额（USD）：", min_value=0.0, value=1000.0)
        target_lang = st.selectbox("目标语言：", ["英语", "西班牙语", "阿拉伯语"])
        
        if st.button("生成催收邮件"):
            prompt = f"请以专业的{target_lang}写一封催收邮件。客户：{client_name}，逾期{overdue_days}天，金额${overdue_amount}。语气需坚定但得体。同时给出该语言的中文对照。"
            with st.spinner("正在起草..."):
                email_content = call_llm_agent(prompt)
            st.text_area("生成结果：", email_content, height=300)

    with trade_tab2:
        st.subheader("🔍 买家背景信息透视（仅聚合公开信息）")
        company_name = st.text_input("海外买家/公司名称（如：ABC Trading LLC）：")
        if st.button("生成信息透视报告"):
            prompt = f"请帮我搜集并聚合关于'{company_name}'的公开商业信息（包括其主营业务、潜在关联方、公开的市场声誉等）。请以客观报告形式呈现，不要提供信用评级，不要做主观判断。"
            with st.spinner("正在聚合公开数据..."):
                report = call_llm_agent(prompt)
            st.markdown(report)

    with trade_tab3:
        st.subheader("🛒 跨境电商 Listing 本地化重塑")
        domestic_listing = st.text_area("请输入国内电商产品描述（中文）：", "高品质无线蓝牙耳机，超长续航，防水防汗。")
        if st.button("生成海外本土化文案"):
            prompt = f"请将以下中文产品描述，重塑为适合亚马逊/Shopify美国本土消费者的英文Listing。风格需地道、有吸引力，突出核心卖点。产品描述：{domestic_listing}"
            with st.spinner("正在本地化..."):
                listing = call_llm_agent(prompt)
            st.write(listing)

# ================= Tab 4: 数据接入与OCR =================
with tab_tools:
    st.header("🧾 多平台数据接入与发票OCR")
    st.markdown("支持上传微信/支付宝导出Excel、以及海外API拉取流水（当前为手动上传）。")
    
    # 多文件上传
    uploaded_files = st.file_uploader("上传流水 Excel（可多选）", type=["xlsx", "xls"], accept_multiple_files=True)
    
    if uploaded_files:
        all_dfs = []
        for file in uploaded_files:
            raw_df = pd.read_excel(file)
            cleaned_df = clean_and_align_data(raw_df)
            all_dfs.append(cleaned_df)
        
        if all_dfs:
            merged_df = pd.concat(all_dfs, ignore_index=True)
            st.session_state.merged_df = merged_df
            st.success(f"成功合并 {len(uploaded_files)} 个文件，并完成数据对齐与多币种折算！")
            
            # ============ 新增：实体名称清洗 ============
            st.markdown("---")
            st.subheader("🧠 AI 实体对齐与客户标签")
            st.write("系统正在自动对齐不同格式的客户名称，并自动打标签...")
            
            # 调用刚才写的引擎
            cleaned_df = clean_entities(st.session_state.merged_df)
            st.session_state.merged_df = cleaned_df  # 把清洗后的数据保存回去
            
            # 展示结果
            st.write("清洗效果预览（如果匹配错了，可以在下面直接改）：")
            
            # 用 data_editor 给用户一个修改表格的功能
            edited_df = st.data_editor(
                cleaned_df[['客户名', '客户名_标准', '客户标签']], 
                column_config={
                    "客户名": st.column_config.TextColumn("原始名字（系统读取）", disabled=True),
                    "客户名_标准": st.column_config.TextColumn("标准名字（你可以修改）"),
                    "客户标签": st.column_config.TextColumn("自动标签（你可以修改）")
                },
                hide_index=True,
                use_container_width=True
            )
            
            # 如果用户修改了，保存到新的 mapping.csv (人机协同闭环)
            if st.button("💾 保存修正结果并更新映射表"):
                new_rules = edited_df[edited_df['客户名'] != edited_df['客户名_标准']].copy()
                new_rules = new_rules.rename(columns={'客户名': 'raw_name', '客户名_标准': 'std_name', '客户标签': 'tag'})
                if not new_rules.empty:
                    if os.path.exists("mapping.csv"):
                        old_csv = pd.read_csv("mapping.csv")
                        final_csv = pd.concat([old_csv, new_rules]).drop_duplicates(subset=['raw_name'], keep='last')
                    else:
                        final_csv = new_rules
                    final_csv.to_csv("mapping.csv", index=False)
                    st.success("✅ 规则已保存！下次遇到同样的名字，系统直接秒懂。")
            
            with st.expander("👀 查看对齐后的全量标准流水数据"):
                st.dataframe(st.session_state.merged_df)
    st.markdown("---")
    st.subheader("📬 自动抓取邮件流水（PayPal/Stripe 等）")
    st.markdown("自动读取指定邮箱的收款确认邮件，并提取关键信息。")
    
    with st.expander("📧 邮箱 IMAP 配置"):
        imap_server = st.text_input("IMAP服务器地址", value="imap.qq.com", help="QQ邮箱为 imap.qq.com，网易为 imap.163.com")
        imap_user = st.text_input("邮箱账号")
        imap_pass = st.text_input("邮箱授权码（非登录密码）", type="password")
    
    if st.button("🚀 开始抓取并解析邮件"):
        if not (imap_server and imap_user and imap_pass):
            st.warning("请先完整填写 IMAP 配置。")
        else:
            try:
                with st.spinner("正在连接邮箱..."):
                    mail = imaplib.IMAP4_SSL(imap_server)
                    mail.login(imap_user, imap_pass)
                    mail.select("inbox")
                    
                    # 搜索最近30封邮件
                    status, messages = mail.search(None, "ALL")
                    mail_ids = messages[0].split()[-30:]
                    
                    extracted_data = []
                    for i in mail_ids:
                        status, msg_data = mail.fetch(i, "(RFC822)")
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                subject, encoding = decode_header(msg["Subject"])[0]
                                if isinstance(subject, bytes):
                                    subject = subject.decode(encoding if encoding else "utf-8")
                                
                                # 仅处理包含财务关键字的邮件
                                if any(k in subject.lower() for k in ["paypal", "stripe", "payment", "付款", "打款", "invoice"]):
                                    # 提取邮件正文
                                    body = ""
                                    if msg.is_multipart():
                                        for part in msg.walk():
                                            if part.get_content_type() == "text/plain":
                                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                                break
                                    else:
                                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                                    
                                    # 调用大模型解析邮件内容
                                    prompt = f"请从以下邮件内容中提取：金额(数字)、币种(USD/CNY等)、客户名、日期(YYYY-MM-DD)。以纯JSON格式输出，无其他内容。\n邮件主题：{subject}\n邮件正文：{body[:1000]}"
                                    ai_response = call_llm_agent(prompt)
                                    
                                    # 清洗大模型返回的JSON
                                    clean_json = ai_response.replace("```json", "").replace("```", "").strip()
                                    try:
                                        parsed = json.loads(clean_json)
                                        extracted_data.append(parsed)
                                    except:
                                        pass
                                    
                    mail.logout()
                    
                    if extracted_data:
                        st.success(f"成功抓取并解析了 {len(extracted_data)} 条财务流水！")
                        st.json(extracted_data)
                        # 存入 Session State
                        st.session_state.emails_parsed = extracted_data
                    else:
                        st.info("最近30封邮件中没有发现匹配的财务流水。")
                        
            except Exception as e:
                st.error(f"邮箱抓取失败：{str(e)}")   

        
    st.markdown("---")
    st.subheader("📑 发票批量识别")
    uploaded_invoices = st.file_uploader("上传发票图片（可多选）", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key="invoice_uploader")
    if uploaded_invoices:
        if st.button("开始批量识别"):
            for invoice in uploaded_invoices:
                with st.spinner(f"识别 {invoice.name} ..."):
                    result = process_invoice_ocr(invoice)
                    st.success(f"✅ {invoice.name} 识别结果：")
                    st.code(result, language="json")
                    st.session_state.invoices.append(result)
 # ================= 新增：流水与发票对账 =================
    st.markdown("---")
    st.subheader("🧾 智能对账台（流水-发票关联）")
    if len(st.session_state.invoices) > 0 and st.session_state.merged_df is not None:
        # 解析发票 JSON（容错处理）
        parsed_invoices = []
        for inv in st.session_state.invoices:
            try:
                import json
                # 去掉可能存在的 markdown 标记
                clean_str = str(inv).replace('```json', '').replace('```', '').strip()
                data = json.loads(clean_str)
                parsed_invoices.append({
                    '发票金额': float(data.get('金额', 0)),
                    '发票日期': data.get('日期', ''),
                    '发票号码': data.get('发票号码', '')
                })
            except:
                pass
        
        if parsed_invoices:
            invoice_df = pd.DataFrame(parsed_invoices)
            st.write("已识别的发票数据：")
            st.dataframe(invoice_df)
            
            # 对账逻辑：尝试把发票总金额与流水中的收入金额匹配
            # 这是一个简化版的匹配，真实场景要用更复杂的算法
            st.info("💡 正在尝试自动匹配（按金额相近度）...")
            
            # 打印提示，让用户确认
            st.warning("⚠️ 自动对账逻辑较复杂，目前系统为您展示了已识别的发票和流水，请结合左侧数据手动核销。下一版本将实现按金额和客户名的自动核销算法。")
    else:
        st.info("请先上传流水，并在下方上传发票图片进行 OCR 识别。")
       # ================= AI 自动生成催款函 =================
    st.markdown("---")
    st.subheader("📝 AI 自动生成催款函")
    st.markdown("基于未核销的发票，自动生成专业的中英双语催款邮件。")
            
    if st.button("✍️ 生成催款函"):
                with st.spinner("AI 正在撰写催款函..."):
                    # 把发票数据转为文本
                    invoice_text = invoice_df.to_string(index=False)
                    prompt = f"""
                    请根据以下未核销的发票数据，以专业、坚定但礼貌的语气，写一封中英双语的催款函。
                    发票数据：
                    {invoice_text}
                    
                    要求：
                    1. 包含具体的总金额和逾期天数。
                    2. 给出明确的付款截止日期（假设7天内）。
                    3. 中文版和英文版分开显示。
                    4. 留下“AI COO 智能财务系统”作为落款。
                    """
                    collection_letter = call_llm_agent(prompt)
                    st.markdown(collection_letter)
                    st.download_button("📥 下载催款函", data=collection_letter.encode('utf-8'), file_name="collection_letter.txt", mime="text/plain") 
# ================= 页脚 =================
st.markdown("---")
st.caption("Built with Python + Streamlit + Pandas + Zhipu GLM-4V | 跨境AI COO 智能商业生态 3.0")