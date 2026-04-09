import markdown
import pdfkit
from pdfkit.configuration import Configuration

with open('/Users/chengyu/PycharmProjects/investment_hub/xiaohongshu_daifa_mode.md', 'r', encoding='utf-8') as f:
    text = f.read()

html = markdown.markdown(text)
html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: 'PingFang SC', 'SimSun', sans-serif; line-height: 1.8; padding: 40px; }}
</style>
</head>
<body>{html}</body>
</html>"""

config = Configuration(wkhtmltopdf='/usr/local/bin/wkhtmltopdf')
pdfkit.from_string(html, '/Users/chengyu/PycharmProjects/investment_hub/xiaohongshu_daifa_mode.pdf', configuration=config)
print("PDF 生成完成")
