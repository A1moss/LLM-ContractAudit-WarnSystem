"""deidentify.py — 第二批合同脱敏（不改变分类语义）。

规则（只动个人/内部业务信息，不动决定分类的内容）：
- 法定代表人 / 法定代理人 姓名 → 「已脱敏」
- 手机号 1[3-9]xxxxxxxxx → 1**********
- 身份证号 18 位 → ******************
- 政府采购 / 合同编号（CSCG/XSCG/GXCG/LYCG… 前缀）→ [合同编号]

明确不动：公司名称、项目名称、合同标题、标的名称、条款正文（这些决定法理分类）。
"""
import re

# 法定代表人 / 法定代理人 姓名（中文，含 · 复姓点，2~6 字）
_NAME_RE = re.compile(r'(法定代表人|法定代理人)[：:]\s*[\u4e00-\u9fff·]{2,6}')

# 手机号
_PHONE_RE = re.compile(r'1[3-9]\d{9}')

# 身份证号（18 位，末位可 X）
_ID_RE = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')

# 政府采购 / 合同编号：常见前缀 + 数字/连字符
_CODE_RE = re.compile(
    r'(?<![A-Za-z0-9])(?:CSCG|XSCG|GXCG|LYCG|XJCG|KFCG|LPCG|TXCG|NXCG|JKCG|YZC|AHSK|QWZFCG|CSP)[A-Za-z0-9\-]*(?![A-Za-z0-9])'
)


def deidentify(text: str) -> str:
    """对合同文本脱敏，返回脱敏后的文本。"""
    t = _NAME_RE.sub(lambda m: m.group(1) + '：已脱敏', text)
    t = _PHONE_RE.sub('1**********', t)
    t = _ID_RE.sub('******************', t)
    t = _CODE_RE.sub('[合同编号]', t)
    return t


if __name__ == "__main__":
    import sys
    for f in sys.argv[1:]:
        s = open(f, encoding='utf-8').read()
        d = deidentify(s)
        print(f"{f}: 替换 {s.count('已脱敏') + s.count('1**********') + s.count('******************') + s.count('[合同编号]')} 处")
