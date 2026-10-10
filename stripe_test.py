import os
from stripe import StripeClient
from dotenv import load_dotenv
load_dotenv()
key=os.getenv('STRIPE_KEY')
client=StripeClient(key)
balance=client.balance.retrieve()
print(balance)
# 提取当前可用余额（Stripe里金额是以“分”或“便士”为单位的，所以0就是0）
amount = balance["available"][0]["amount"]

# 打印一下整理后的金额
print("当前账户可用余额（整数）:", amount)

# 如果余额小于 100（比如代表1英镑），就发出警告
if amount < 100:
    print("⚠️ 警告：您的余额已低于安全线！请立即采取行动！")
else:
    print("✅ 您的现金流状况良好。")