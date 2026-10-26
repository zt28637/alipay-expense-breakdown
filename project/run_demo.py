"""本地运行入口：读取匹配名称对应表，调用 expense_breakdown.main，核对总额。

用法：
    python run_demo.py
"""

import os

import openpyxl

from expense_breakdown import main

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RULE_XLSX = os.path.join(BASE_DIR, '作业素材', '匹配名称对应表', '匹配名称对应表.xlsx')
EXPECTED_TOTAL = -90692.46


def load_rules(path=RULE_XLSX):
    """读取匹配表的分类名称（B 列，第 3~32 行）。"""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rules = {}
    for row in ws.iter_rows(min_row=3, max_row=32, values_only=True):
        name = str(row[1]).strip() if row[1] else ''
        if name:
            rules[name] = row[2]
    wb.close()
    return rules


def main_demo():
    rules = load_rules()
    result = main(rules)
    summary = result['summary']
    total = result['total']

    print(f'\n规则数量: {len(rules)}')
    print(f'\n{"分类":<42}{"金额":>14}')
    print('-' * 58)
    for name, amount in sorted(summary.items(), key=lambda x: x[1]):
        if amount:
            print(f'{name:<42}{amount:>14.2f}')
    print('-' * 58)
    print(f'{"合计":<42}{total:>14.2f}')
    print(f'{"期望":<42}{EXPECTED_TOTAL:>14.2f}')
    print(f'{"差额":<42}{total - EXPECTED_TOTAL:>14.4f}')
    ok = abs(total - EXPECTED_TOTAL) < 0.01
    print(f'\n核验结果: {"通过" if ok else "失败"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main_demo())
