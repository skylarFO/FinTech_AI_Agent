import pandas as pd
import re
import os
from rapidfuzz import process,fuzz
# 1. 读取你手动维护的映射表（如果不存在就给个默认空表）
def load_mapping(mapping_path="mapping.csv"):
    if os.path.exists(mapping_path):
        df = pd.read_csv(mapping_path)
        # 做成字典： {'ABC Co., Ltd': 'ABC公司'}
        return dict(zip(df['raw_name'], df['std_name']))
    return {}

# 2. 正则表达式打标签（提取关键词）
def extract_tag(company_name):
    if pd.isna(company_name):
        return "未知"
    name_str = str(company_name).lower()
    
    # 规则：包含某个词就打个标签
    rules = {
        r"科技|tech|technology": "科技类",
        r"贸易|trading|trade": "贸易类",
        r"电子|electronic": "电子类",
        r"纺织|textile": "纺织类",
        r"咨询|consulting": "咨询类"
    }
    
    for pattern, tag in rules.items():
        if re.search(pattern, name_str):
            return tag
    return "待核对" # 没见过的名字，标为待核对

# 3. 核心清洗函数：结合精准映射 + 模糊匹配
def clean_entities(df, std_entities_list=None):
    """
    df: 你的数据框，必须包含 '客户名' 列
    std_entities_list: 标准名字列表，用来做模糊匹配（可以从你的映射表里提取）
    """
    mapping = load_mapping()
    
    # 如果没传标准名字列表，就从映射表里提取一套
    if not std_entities_list:
        # 提取 mapping.csv 里的所有标准名，去重
        if os.path.exists("mapping.csv"):
            mapping_df = pd.read_csv("mapping.csv")
            std_entities_list = mapping_df['std_name'].unique().tolist()
        else:
            std_entities_list = []
    
    def align_single(name):
        if pd.isna(name): return name
        
        # A. 优先查你预存的Excel映射表 (精准匹配)
        if str(name) in mapping:
            return mapping[str(name)]
        
        # B. 如果映射表没有，用模糊匹配去猜（比如 ABC Co., Ltd -> ABC公司）
        if std_entities_list:
            match, score, _ = process.extractOne(str(name), std_entities_list, scorer=fuzz.WRatio)
            if score >= 80:  # 匹配度大于80%才敢认（防止张冠李戴）
                return match
        
        # C. 完全没见过，原样返回，并打上标记
        return f"【待核对】{name}"

    # 应用对齐逻辑
    df['客户名_标准'] = df['客户名'].apply(align_single)
    # 应用标签逻辑
    df['客户标签'] = df['客户名_标准'].apply(extract_tag)
    
    return df
