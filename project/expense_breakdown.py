"""
支出拆解自动化 - 最终版

═══════════════════════════════════════════════════════════════════
算法核心思路（哈希匹配 + 缓存）
═══════════════════════════════════════════════════════════════════

问题本质：
  输入：CSV 中 16 万行交易记录，每行有一个"业务描述"字段
  目标：将每行归类到 30 个分类中，累加金额

难点：
  - 业务描述 ≠ 分类名称（描述被"污染"了）
  - 例如：分类是 "1000002|技术服务费-基础技术服务费"
         描述是 "1000002|技术服务费-基础技术服务费（订单号12345）"

解法：
  1. 把 30 条规则分为两类：
     - A类（18条）：带编码前缀，如 "1000002|..."
     - B类（12条）：纯关键词，如 "推广平台自动充值"

  2. 对每行描述，按优先级尝试匹配：
     ┌─────────────────────────────────────────────────────┐
     │ 第一层：提取编码 → O(1) 哈希查表（最快，命中率90%+）│
     │ 第二层：忽略编码差异，按 "|" 后文字匹配            │
     │ 第三层：关键词搜索 → 遍历 B 类规则（较慢）        │
     │ 第四层：特殊规则兜底 → 硬编码条件（如"平台补贴"） │
     └─────────────────────────────────────────────────────┘

  3. 缓存已匹配的描述 → 相同描述只匹配一次

性能：
  - v1 原版：~60秒（逐行遍历 + 三层匹配）
  - 本版本：~5-10秒（编码O(1) + 缓存加速）

═══════════════════════════════════════════════════════════════════
"""

import os
import re

import pandas as pd

try:
    import xbot
except ImportError:  # 脱离影刀时可单独运行
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format='%(message)s')

    class _XbotShim:
        logging = _logging

    xbot = _XbotShim()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(
    BASE_DIR, '作业素材', '源文件', 'demo_account_202512_账务明细.csv'
)


# ═══════════════════════════════════════════════════════════════
# 预编译正则（模块加载时编译一次，避免每行重复编译）
# ═══════════════════════════════════════════════════════════════

# 匹配编码前缀：7位数字 + 可选字母 + "|"
# 例如："1000002|" 或 "1100011T|"
RE_CODE = re.compile(r'^(\d{7}[A-Za-z]?)\|')

# 括号内容（全角 / 半角），用于忽略"（退款）""（渠道）"这类限定词
RE_PAREN = re.compile(r'[（(][^）)]*[）)]')


def normalize_text(text):
    """去掉括号内容和空白，用于忽略编码差异后的文字匹配。"""
    return re.sub(r'\s+', '', RE_PAREN.sub('', text))


# ═══════════════════════════════════════════════════════════════
# 步骤 1：构建匹配规则
# ═══════════════════════════════════════════════════════════════

def build_rules(args):
    """
    将 30 条规则分为两类：

    参数 args：影刀传入的字典 {分类名: 任意值}

    返回：
      - code_map: {编码: 分类名}，用于 O(1) 查表
      - text_map: {编码后的文字: 分类名}，用于忽略编码差异
      - keyword_rules: [(关键词, 分类名), ...]，用于搜索匹配
      - all_keys: 所有分类名列表（用于初始化输出字典）

    示例：
      输入：{"1000002|技术服务费-基础技术服务费": 0, "推广平台自动充值": 1}
      输出：
        code_map = {"1000002": "1000002|技术服务费-基础技术服务费"}
        text_map = {"技术服务费-基础技术服务费": "1000002|技术服务费-基础技术服务费"}
        keyword_rules = [("推广平台自动充值", "推广平台自动充值")]
        all_keys = ["1000002|技术服务费-基础技术服务费", "推广平台自动充值"]
    """
    rule_map = args

    code_map = {}        # 编码 → 分类名（用于 O(1) 查表）
    text_map = {}        # 编码后的文字（去括号） → 分类名（用于忽略编码差异）
    keyword_rules = []   # [(关键词, 分类名), ...]（用于搜索）
    all_keys = []        # 所有分类名（用于输出）

    for rule_name in rule_map.keys():
        all_keys.append(rule_name)

        # 尝试提取编码前缀
        match = RE_CODE.match(rule_name)
        if match:
            # 有编码 → A 类规则
            code = match.group(1)  # 例如 "1000002"
            code_map[code] = rule_name
            # 第三层：同一业务名称可能挂在不同编码下（如 1000038 / 1000003）
            text_map[normalize_text(rule_name.split('|', 1)[1])] = rule_name
        else:
            # 无编码 → B 类规则（纯关键词）
            keyword_rules.append((rule_name, rule_name))

    xbot.logging.info(
        f'规则分类: 编码类={len(code_map)}, 文字类={len(text_map)}, 关键词类={len(keyword_rules)}'
    )
    return code_map, text_map, keyword_rules, all_keys


# ═══════════════════════════════════════════════════════════════
# 步骤 2：读取 CSV 源文件
# ═══════════════════════════════════════════════════════════════

def read_csv_source(csv_path):
    """
    读取账务明细 CSV

    注意事项：
      - 编码：GBK（不是 UTF-8）
      - 前 4 行是注释，第 5 行是表头
      - 列名可能带 \t，需要 strip()
    """
    df = pd.read_csv(csv_path, encoding='gbk', skiprows=4, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    xbot.logging.info(f'CSV 总行数: {len(df)}')
    return df


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def safe_float(val):
    """
    将字符串安全转为 float

    处理：
      - None → 0.0
      - 空字符串 → 0.0
      - "0.00" → 0.0
      - "nan" → 0.0
      - 正常数字 → float
      - 异常 → 0.0
    """
    if val is None or not isinstance(val, str):
        return 0.0
    s = val.strip()
    if s in ('', '0.00', 'nan', 'None'):
        return 0.0
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def extract_code(desc):
    """
    从业务描述中提取编码（如果有）

    示例：
      "1000002|技术服务费-基础技术服务费" → "1000002"
      "推广平台自动充值" → None
    """
    match = RE_CODE.match(desc)
    if match:
        return match.group(1)
    return None


# ═══════════════════════════════════════════════════════════════
# 步骤 3：核心匹配 + 累加（算法核心）
# ═══════════════════════════════════════════════════════════════

def match_and_accumulate(df, code_map, text_map, keyword_rules, all_keys):
    """
    对每行数据进行匹配 + 金额累加

    匹配策略（按优先级）：

    1. 缓存命中 → 直接用缓存结果（最快）
    2. 编码匹配 → 从描述提取编码，O(1) 查表（90%+ 命中率）
    3. 文字匹配 → 忽略编码差异，按 "|" 后的文字查表
    4. 关键词搜索 → 遍历 B 类规则（较慢，但缓存后只跑一次）
    5. 特殊规则 → 硬编码条件（如"平台补贴"）

    返回：
      - summary: {分类名: 累加金额}
    """
    # 初始化输出字典（所有分类初始为 0）
    summary = {kw: 0.0 for kw in all_keys}

    # ── 预提取列为 numpy 数组（避免 pandas 索引开销）──
    # 为什么？pandas 的 row.iloc[N] 每次都要创建 Series 对象，很慢
    col_desc = df.iloc[:, 14].fillna('').astype(str).values     # 业务描述（第 O 列）
    col_remark = df.iloc[:, 9].fillna('').astype(str).values    # 备注（第 J 列）
    col_income = df.iloc[:, 4].fillna('').astype(str).values    # 收入金额（第 E 列）
    col_expense = df.iloc[:, 5].fillna('').astype(str).values   # 支出金额（第 F 列）

    n = len(df)

    # 统计计数器（用于日志）
    code_match = 0      # 编码匹配次数
    text_match = 0      # 文字匹配次数（忽略编码差异）
    keyword_match = 0   # 关键词匹配次数
    special_match = 0   # 特殊规则匹配次数
    cache_hit = 0       # 缓存命中次数
    unmatched_count = 0 # 未匹配次数

    # 缓存字典：描述 → 匹配到的分类名（None 表示未匹配）
    # 为什么用缓存？因为 16 万行中，很多描述是重复的
    # 例如 "1600001|交易收款-交易收款" 可能重复上万次
    # 第一次匹配后，后续直接命中缓存，跳过所有匹配逻辑
    desc_cache = {}

    # ── 主循环：遍历每一行 ──
    for idx in range(n):
        # 拼接描述 = 业务描述 + 备注（有些关键词在备注里）
        desc = (col_desc[idx] + col_remark[idx]).strip()

        # 计算金额（收入 + 支出，支出是负数）
        income = safe_float(col_income[idx])
        expense = safe_float(col_expense[idx])
        amount = income + expense

        # 金额为 0 的行跳过（不累加）
        if amount == 0:
            continue

        # ════════════════════════════════════════════════════
        # 第一层：缓存命中 → 直接跳过
        # ════════════════════════════════════════════════════
        if desc in desc_cache:
            cached = desc_cache[desc]
            if cached is not None:
                summary[cached] += amount
                cache_hit += 1
            else:
                # 缓存记录为 None，表示这个描述之前未匹配过
                unmatched_count += 1
            continue  # 跳过后续匹配逻辑

        # ════════════════════════════════════════════════════
        # 第二层：编码匹配（O(1) 哈希查表）
        # ════════════════════════════════════════════════════
        # 为什么先做这一层？因为 90%+ 的描述都带编码前缀
        # 编码匹配是最快的（O(1)），要优先尝试

        code = extract_code(desc)
        if code and code in code_map:
            # 提取到编码，且在规则字典中
            rule_name = code_map[code]
            summary[rule_name] += amount
            desc_cache[desc] = rule_name  # 缓存结果
            code_match += 1
            continue

        # ════════════════════════════════════════════════════
        # 第三层：忽略编码差异，按 "|" 后的文字匹配
        # ════════════════════════════════════════════════════
        # 同一业务名称可能挂在不同编码下，例如：
        #   "1000038|技术服务费-返点积分（退款）"
        # 去掉括号后与 "1000003|技术服务费-返点积分" 同名，
        # 因此归入后者所在分类。

        if code:
            desc_only = col_desc[idx]
            if '|' in desc_only:
                text_key = normalize_text(desc_only.split('|', 1)[1])
                rule_name = text_map.get(text_key)
                if rule_name:
                    summary[rule_name] += amount
                    desc_cache[desc] = rule_name
                    text_match += 1
                    continue

        # ════════════════════════════════════════════════════
        # 第四层：关键词搜索（遍历 B 类规则）
        # ════════════════════════════════════════════════════
        # 这一层较慢（O(K)，K 是 B 类规则数），但只有编码匹配失败时才执行
        # 而且结果会被缓存，相同描述只跑一次

        matched = False
        for keyword, rule_name in keyword_rules:
            if keyword in desc:
                summary[rule_name] += amount
                desc_cache[desc] = rule_name
                keyword_match += 1
                matched = True
                break

        if matched:
            continue

        # ════════════════════════════════════════════════════
        # 第四层：特殊规则兜底
        # ════════════════════════════════════════════════════
        # 硬编码条件，处理一些无法用编码/关键词匹配的情况

        special_rule = match_special_rules(desc, amount)
        if special_rule:
            summary[special_rule] += amount
            desc_cache[desc] = special_rule
            special_match += 1
            continue

        # ════════════════════════════════════════════════════
        # 未匹配：记录到缓存（避免重复检查）
        # ════════════════════════════════════════════════════
        desc_cache[desc] = None
        unmatched_count += 1

    # ── 日志输出 ──
    xbot.logging.info(f'匹配统计:')
    xbot.logging.info(f'  编码匹配: {code_match} ({code_match/n*100:.1f}%)')
    xbot.logging.info(f'  文字匹配: {text_match} ({text_match/n*100:.1f}%)')
    xbot.logging.info(f'  关键词匹配: {keyword_match} ({keyword_match/n*100:.1f}%)')
    xbot.logging.info(f'  特殊规则: {special_match} ({special_match/n*100:.1f}%)')
    xbot.logging.info(f'  缓存命中: {cache_hit} ({cache_hit/n*100:.1f}%)')
    xbot.logging.info(f'  未匹配: {unmatched_count} ({unmatched_count/n*100:.1f}%)')
    xbot.logging.info(f'  缓存条目数: {len(desc_cache)}（唯一描述数）')

    if unmatched_count > 0:
        xbot.logging.warning(f'有 {unmatched_count} 条未匹配，请检查')

    return summary


# ═══════════════════════════════════════════════════════════════
# 特殊规则兜底（第四层）
# ═══════════════════════════════════════════════════════════════

def match_special_rules(desc, amount):
    """
    处理无法用编码/关键词匹配的异常情况

    为什么需要这一层？
      - 有些描述不包含任何关键词，但有明确的业务含义
      - 例如 "平台补贴" 可能是收入也可能是支出，需要根据金额正负判断

    返回：分类名 或 None
    """
    # ── 平台补贴 ──
    # 支出 → "1200001|其他支出-平台补贴预收"
    # 收入 → "1200002T|其他收入-平台补贴激励前返"
    if '平台补贴' in desc:
        if amount < 0:
            return '1200001|其他支出-平台补贴预收'
        else:
            return '1200002T|其他收入-平台补贴激励前返'

    # ── 营销托管 ──
    # 退款 → "营销托管新客计划"（正数）
    # 代扣 → "营销托管-平台营销托管服务费-扣款"（负数）
    if '营销托管' in desc:
        if '退款' in desc:
            return '营销托管新客计划'
        # 备注里可能写"扣款"，也可能写"代扣"
        if '代扣' in desc or '扣款' in desc:
            return '营销托管-平台营销托管服务费-扣款'

    # ── 推广联盟 ──
    if '推广联盟' in desc:
        return '推广联盟佣金代扣'

    # ── 平台保证金：扣除转移（现金解冻） ──
    if '保证金-平台-扣除转移' in desc:
        return '平台保证金--'

    # ── 提现相关 ──
    # "平台转账" / "本地退赔款" / 单独的 "转账" → 归入 "提现"
    if '平台转账' in desc or '本地退赔款' in desc:
        return '提现'
    if desc.strip() == '转账':
        return '提现'

    return None


# ═══════════════════════════════════════════════════════════════
# 主入口（影刀调用）
# ═══════════════════════════════════════════════════════════════

def main(args, csv_path=None):
    """
    影刀调用入口

    参数 args：匹配名称字典 {分类名: 任意值}
    参数 csv_path：账务明细 CSV 路径，默认使用项目内素材
    返回：{'summary': {分类名: 金额}, 'total': 总金额}
    """
    # 1. 构建规则
    code_map, text_map, keyword_rules, all_keys = build_rules(args)

    # 2. 读取 CSV
    df = read_csv_source(csv_path or CSV_PATH)

    # 3. 匹配 + 累加
    summary = match_and_accumulate(df, code_map, text_map, keyword_rules, all_keys)

    # 4. 返回结果
    return {'summary': summary, 'total': sum(summary.values())}
